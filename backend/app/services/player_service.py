"""
PlayerService：玩家角色的生命周期与交互。

- 创建玩家：若已有玩家则复用（single-player MVP）。
- 移动：服务端执行 A* 寻路，把 path 送进 SimulationEngine 队列。
- 交互：抚摸动物、使用物品、向 NPC 发起对话。
- 对话：先用记忆检索 + LLM 生成；无 LLM 时走规则回复。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import (
    ErrorCode,
    InvalidArguments,
    OutOfRange,
    PermissionDenied,
    StateConflict,
    TargetNotFound,
)
from app.core.event_bus import get_event_bus
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    Agent,
    AgentState,
    DialogueMessage,
    Relationship,
    WorldEvent,
    WorldObject,
)
from app.domain.dialogue import (
    classify_intent,
    get_conversation_store,
    generate_structured_reply,
    rewrite_query,
)
from app.domain.planning import (
    ImportanceContext,
    RELATIONSHIP_DELTA_THRESHOLD,
    compute_importance,
    should_update_summary,
)
from app.domain.safety import (
    check_player_limit,
    check_player_message,
)
from app.domain.safety.rate_limit import PlayerRateLimited
from app.domain.simulation.engine import PlayerInputEvent
from app.domain.world.grid import astar
from app.domain.world.scene_cache import get_scene_cache
from app.llm.tools.base import ToolCall, ToolContext
from app.llm.tools.executor import get_tool_executor
from app.schemas.agent import (
    AgentProfile,
    ApproachNpcRequest,
    CreatePlayerRequest,
    EndChatRequest,
    PlayerInteractRequest,
    PlayerInteractResponse,
    PlayerMoveRequest,
    PlayerMoveResponse,
    PlayerTalkRequest,
    PlayerTalkResponse,
)
from app.schemas.memory import MemorySearchRequest
from app.schemas.world import TilePosition
from app.services.agent_service import profile_from_orm
from app.services.memory_service import get_memory_service
from app.services.simulation_runtime import get_simulation_runtime
from app.websocket.gateway import WS_BROADCAST_TOPIC

logger = get_logger(__name__)

INTERACTION_RADIUS = 2


class PlayerService:
    # ------------------------------------------------------------------
    # 玩家档案
    # ------------------------------------------------------------------

    async def create_player(
        self, session: AsyncSession, request: CreatePlayerRequest
    ) -> AgentProfile:
        existing = await self._current_player_row(session)
        if existing is not None:
            existing.name = request.name
            existing.personality = request.personality
            existing.background = request.background or ""
            appearance = existing.appearance or {}
            if request.sprite_key:
                appearance["sprite_sheet"] = request.sprite_key
            if request.appearance_description:
                appearance["description"] = request.appearance_description
            existing.appearance = appearance
            await session.commit()
            await session.refresh(existing)
            runtime = get_simulation_runtime()
            await runtime.reload()
            return profile_from_orm(existing)

        agent = Agent(
            id=str(uuid.uuid4()),
            entity_type="player",
            name=request.name,
            personality=request.personality,
            background=request.background or "",
            appearance={
                "sprite_sheet": request.sprite_key or "player_default",
                "description": request.appearance_description or "",
                "scale": 1.0,
            },
            long_term_goals=[],
            schedule_template=[],
        )
        session.add(agent)
        await session.flush()
        # 默认出生在室外中心
        default_scene, default_pos = await self._default_spawn(session)
        session.add(
            AgentState(
                agent_id=agent.id,
                scene_id=default_scene,
                x=default_pos[0],
                y=default_pos[1],
                state="IDLE",
                path=[],
                energy=1.0,
                hunger=0.2,
                social_need=0.3,
                fear=0.0,
            )
        )
        await session.commit()
        await session.refresh(agent)
        await get_simulation_runtime().reload()
        return profile_from_orm(agent)

    async def get_current_player(self, session: AsyncSession) -> AgentProfile:
        row = await self._current_player_row(session)
        if row is None:
            raise TargetNotFound("player not created yet")
        return profile_from_orm(row)

    async def _current_player_row(self, session: AsyncSession) -> Agent | None:
        stmt = select(Agent).where(Agent.entity_type == "player").limit(1)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _default_spawn(self, session: AsyncSession) -> tuple[str, tuple[int, int]]:
        # 选择第一个 outdoor scene 的左上第二排中央
        from app.db.models import MapScene

        scene = (
            await session.execute(
                select(MapScene).where(MapScene.scene_type == "outdoor").limit(1)
            )
        ).scalar_one_or_none()
        if scene is None:
            raise StateConflict("no outdoor scene available, seed the world first")
        await get_scene_cache().refresh(session)
        grid = get_scene_cache().get_grid(scene.id)
        if grid is None:
            raise StateConflict("scene cache not warm")
        # 从场景中心向外寻找可走 tile
        cx, cy = scene.width // 2, scene.height // 2
        for r in range(max(scene.width, scene.height)):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if grid.is_walkable(cx + dx, cy + dy, avoid_hazards=True):
                        return scene.id, (cx + dx, cy + dy)
        raise StateConflict("no walkable tile in outdoor scene")

    # ------------------------------------------------------------------
    # 移动
    # ------------------------------------------------------------------

    async def move(
        self, session: AsyncSession, request: PlayerMoveRequest
    ) -> PlayerMoveResponse:
        engine = get_simulation_runtime().engine
        player_eng = engine._current_player()  # type: ignore[attr-defined]
        if player_eng is None:
            raise TargetNotFound("player not exists")

        if request.target is None and request.direction is None:
            raise InvalidArguments("must provide target or direction")

        grid = get_scene_cache().get_grid(player_eng.scene_id)
        if grid is None:
            raise StateConflict("scene grid missing")

        start = (player_eng.x, player_eng.y)
        if request.target is not None:
            goal = (request.target.x, request.target.y)
        else:
            dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[
                request.direction  # type: ignore[index]
            ]
            goal = (player_eng.x + dx, player_eng.y + dy)

        if not grid.in_bounds(*goal):
            return PlayerMoveResponse(accepted=False, reason="out of map")
        path = astar(grid, start, goal, avoid_hazards=False)
        if not path:
            return PlayerMoveResponse(accepted=False, reason="not reachable")
        # 去掉起点
        move_path = path[1:]
        get_simulation_runtime().enqueue_player_input(
            PlayerInputEvent(
                kind="move_path",
                payload={"path": [{"x": x, "y": y} for x, y in move_path]},
            )
        )
        return PlayerMoveResponse(
            accepted=True,
            path=[TilePosition(x=x, y=y) for x, y in move_path],
        )

    # ------------------------------------------------------------------
    # 追踪靠近 NPC
    # ------------------------------------------------------------------

    async def approach_npc(
        self, session: AsyncSession, request: ApproachNpcRequest  # noqa: ARG002
    ) -> PlayerMoveResponse:
        """
        计算从玩家当前位置到目标 NPC 交互半径内的路径并加入引擎队列。
        若玩家已在交互半径内，返回 reason="in_range"（path 为空）。
        """
        engine = get_simulation_runtime().engine
        player_eng = engine._current_player()  # type: ignore[attr-defined]
        if player_eng is None:
            raise TargetNotFound("player not exists")

        npc_eng = engine.get_agent(request.npc_id)
        if npc_eng is None:
            raise TargetNotFound(f"agent {request.npc_id} not found")
        if npc_eng.scene_id != player_eng.scene_id:
            return PlayerMoveResponse(accepted=False, reason="different_scene")

        npc_pos = (npc_eng.x, npc_eng.y)
        player_pos = (player_eng.x, player_eng.y)
        dist = abs(npc_pos[0] - player_pos[0]) + abs(npc_pos[1] - player_pos[1])
        if dist <= INTERACTION_RADIUS:
            return PlayerMoveResponse(accepted=True, path=[], reason="in_range")

        grid = get_scene_cache().get_grid(player_eng.scene_id)
        if grid is None:
            raise StateConflict("scene grid missing")

        path = astar(grid, player_pos, npc_pos, avoid_hazards=False)
        if not path:
            return PlayerMoveResponse(accepted=False, reason="not_reachable")

        # 截断路径：当离 NPC 距离 ≤ INTERACTION_RADIUS 时停下
        approach_path: list[tuple[int, int]] = []
        for tile in path[1:]:  # 去掉起点
            approach_path.append(tile)
            remaining = abs(tile[0] - npc_pos[0]) + abs(tile[1] - npc_pos[1])
            if remaining <= INTERACTION_RADIUS:
                break

        if not approach_path:
            return PlayerMoveResponse(accepted=True, path=[], reason="in_range")

        get_simulation_runtime().enqueue_player_input(
            PlayerInputEvent(
                kind="move_path",
                payload={"path": [{"x": x, "y": y} for x, y in approach_path]},
            )
        )
        return PlayerMoveResponse(
            accepted=True,
            path=[TilePosition(x=x, y=y) for x, y in approach_path],
        )

    # ------------------------------------------------------------------
    # 结束对话（释放 NPC CHATTING 状态）
    # ------------------------------------------------------------------

    async def end_chat(
        self, session: AsyncSession, request: EndChatRequest  # noqa: ARG002
    ) -> dict:
        engine = get_simulation_runtime().engine
        engine.end_chatting(request.npc_id)
        return {"ok": True}

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------

    async def interact(
        self, session: AsyncSession, request: PlayerInteractRequest
    ) -> PlayerInteractResponse:
        engine = get_simulation_runtime().engine
        player_eng = engine._current_player()  # type: ignore[attr-defined]
        if player_eng is None:
            raise TargetNotFound("player not exists")

        settings = get_settings()
        rl = await check_player_limit(
            player_eng.id,
            "interact",
            limit=settings.security_player_rate_limit,
            window_seconds=settings.security_rate_window_seconds,
        )
        if not rl.allowed:
            raise PlayerRateLimited(
                f"交互过于频繁，请 {rl.retry_after:.1f}s 后再试",
                details={"retry_after_seconds": rl.retry_after},
            )

        if request.object_id:
            return await self._interact_object(
                session, player_eng, request.object_id, request.interaction_type
            )
        if request.entity_id:
            return await self._interact_entity(
                session, player_eng, request.entity_id, request.interaction_type
            )
        raise InvalidArguments("object_id or entity_id required")

    async def _interact_object(
        self,
        session: AsyncSession,
        player_eng: Any,
        object_id: str,
        interaction_type: str,
    ) -> PlayerInteractResponse:
        obj = await session.get(WorldObject, object_id)
        if obj is None:
            raise TargetNotFound(f"object {object_id} not found")
        if interaction_type not in (obj.available_interactions or []):
            raise InvalidArguments(
                f"interaction {interaction_type} not allowed on {obj.name}"
            )
        pos = obj.position or {}
        ox, oy = int(pos.get("x", 0)), int(pos.get("y", 0))
        if abs(ox - player_eng.x) + abs(oy - player_eng.y) > INTERACTION_RADIUS:
            raise OutOfRange("too far to interact")
        evt = WorldEvent(
            simulation_id=get_simulation_runtime().engine.get_simulation().id,  # type: ignore[union-attr]
            event_type="world.object_interacted",
            source="player",
            actor_entity_id=player_eng.id,
            scene_id=player_eng.scene_id,
            description=f"{player_eng.name} 对 {obj.name} 做了 {interaction_type}",
            importance=2,
            payload={
                "object_id": obj.id,
                "interaction": interaction_type,
            },
            created_at=utcnow(),
        )
        session.add(evt)
        await session.commit()
        return PlayerInteractResponse(
            success=True,
            events=[evt.id],
            message=f"你对「{obj.name}」执行了「{interaction_type}」",
        )

    async def _interact_entity(
        self,
        session: AsyncSession,
        player_eng: Any,
        entity_id: str,
        interaction_type: str,
    ) -> PlayerInteractResponse:
        target = await session.get(Agent, entity_id)
        if target is None:
            raise TargetNotFound(f"entity {entity_id} not found")
        state = await session.get(AgentState, entity_id)
        if state is None:
            raise StateConflict("target has no state")
        if state.scene_id != player_eng.scene_id:
            raise OutOfRange("target in another scene")
        if abs(state.x - player_eng.x) + abs(state.y - player_eng.y) > INTERACTION_RADIUS:
            raise OutOfRange("too far to interact")

        reaction = None
        emotion = None
        description = ""
        if target.entity_type == "animal":
            reaction, emotion, description = self._animal_reaction(target, interaction_type)
            await self._update_relationship(
                session,
                from_agent_id=target.id,
                to_entity_id=player_eng.id,
                familiarity_delta=0.05 if reaction in {"enjoy", "tolerate"} else 0.0,
                affection_delta=0.1 if reaction == "enjoy" else -0.05,
                fear_delta=0.15 if reaction in {"escape", "threaten"} else -0.02,
            )
        else:
            description = f"你尝试和 {target.name} 互动：{interaction_type}"

        evt = WorldEvent(
            simulation_id=get_simulation_runtime().engine.get_simulation().id,  # type: ignore[union-attr]
            event_type="animal.reacted" if target.entity_type == "animal" else "agent.interacted",
            source="player",
            actor_entity_id=player_eng.id,
            target_entity_id=target.id,
            scene_id=player_eng.scene_id,
            description=description,
            importance=3,
            payload={"interaction": interaction_type, "reaction": reaction, "emotion": emotion},
            created_at=utcnow(),
        )
        session.add(evt)

        # 写入动物和玩家记忆
        ms = get_memory_service()
        await ms.write(
            session,
            agent_id=target.id,
            memory_type="event",
            scope="short_term",
            description=description,
            importance=4,
            subject=player_eng.name,
            predicate=interaction_type,
            object_=target.name,
            keywords=[player_eng.name, interaction_type, target.name],
            commit=False,
        )
        await session.commit()

        # 广播
        await get_event_bus().publish(
            WS_BROADCAST_TOPIC,
            {
                "simulation_id": get_simulation_runtime().engine.get_simulation().id,  # type: ignore[union-attr]
                "type": "animal.reacted" if target.entity_type == "animal" else "agent.interacted",
                "payload": {
                    "actor_entity_id": player_eng.id,
                    "target_entity_id": target.id,
                    "reaction": reaction,
                    "emotion": emotion,
                    "description": description,
                },
            },
        )
        return PlayerInteractResponse(
            success=True,
            events=[evt.id],
            reaction={"reaction": reaction, "emotion": emotion} if reaction else None,
            message=description,
        )

    def _animal_reaction(
        self, animal: Agent, interaction: str
    ) -> tuple[str, str, str]:
        personality = set(animal.personality or [])
        species = animal.species or ""
        if interaction == "pet":
            if "fearful" in personality or "胆小" in personality or "shy" in personality:
                return ("escape", "scared", f"{animal.name} 被吓了一跳，转身跑开。")
            if "guard" in personality or "警觉" in personality:
                return ("tolerate", "wary", f"{animal.name} 警觉地看着你，勉强让你摸了一下。")
            return ("enjoy", "happy", f"{animal.name} 开心地靠近你，享受抚摸。")
        if interaction == "feed":
            return ("enjoy", "grateful", f"{animal.name} 高兴地吃下了食物。")
        if interaction == "call":
            if "friendly" in personality or "亲人" in personality:
                return ("approach", "happy", f"{animal.name} 欢快地跑了过来。")
            return ("tolerate", "curious", f"{animal.name} 看了过来，却没有立刻过来。")
        if interaction == "scare":
            return ("escape", "scared", f"{animal.name} 被吓到立刻躲开。")
        return ("tolerate", None, f"{animal.name} 没什么反应。")

    async def _update_relationship(
        self,
        session: AsyncSession,
        *,
        from_agent_id: str,
        to_entity_id: str,
        familiarity_delta: float = 0.0,
        affection_delta: float = 0.0,
        trust_delta: float = 0.0,
        fear_delta: float = 0.0,
    ) -> None:
        stmt = select(Relationship).where(
            Relationship.from_agent_id == from_agent_id,
            Relationship.to_entity_id == to_entity_id,
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = Relationship(
                from_agent_id=from_agent_id,
                to_entity_id=to_entity_id,
                familiarity=0.0,
                trust=0.0,
                affection=0.0,
                fear=0.0,
            )
            session.add(row)
        row.familiarity = _clamp(row.familiarity + familiarity_delta)
        row.trust = _clamp(row.trust + trust_delta)
        row.affection = _clamp(row.affection + affection_delta, lo=-1.0)
        row.fear = _clamp(row.fear + fear_delta)
        await session.flush()

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------

    async def talk(
        self, session: AsyncSession, request: PlayerTalkRequest
    ) -> PlayerTalkResponse:
        """玩家对 NPC 说话（阶段 16）。

        流水线：
            rate_limit → content_safety → query_rewrite → memory_search →
            structured_reply → tool_calls → 落库 + 广播
        """
        settings = get_settings()
        engine = get_simulation_runtime().engine
        player_eng = engine._current_player()  # type: ignore[attr-defined]
        if player_eng is None:
            raise TargetNotFound("player not exists")
        target = await session.get(Agent, request.target_entity_id)
        if target is None or target.entity_type == "player":
            raise TargetNotFound("target agent not found")
        state = await session.get(AgentState, target.id)
        if state is None or state.scene_id != player_eng.scene_id:
            raise OutOfRange("target in another scene")
        if abs(state.x - player_eng.x) + abs(state.y - player_eng.y) > INTERACTION_RADIUS + 1:
            raise OutOfRange("too far to talk")

        # 令 NPC 进入 CHATTING 状态：停止移动，专注对话
        engine.start_chatting(target.id)

        # 阶段 18：限流 + 内容检查
        rl = await check_player_limit(
            player_eng.id,
            "talk",
            limit=settings.security_player_rate_limit,
            window_seconds=settings.security_rate_window_seconds,
        )
        if not rl.allowed:
            raise PlayerRateLimited(
                f"说话过于频繁，请 {rl.retry_after:.1f}s 后再试",
                details={"retry_after_seconds": rl.retry_after},
            )
        content = check_player_message(
            request.text, max_length=settings.security_max_message_length
        )
        if not content.ok:
            err = PermissionDenied(
                content.reason or "消息被拒绝",
                code=content.error_code or ErrorCode.PLAYER_ACTION_REJECTED.value,
            )
            raise err

        # 多轮对话：使用 player + target 作为稳定会话 id（方便同一对会话续接）
        conversation_id = f"conv:{player_eng.id}:{target.id}"

        # Query 改写 + 意图识别（LLM 优先，规则兜底）
        player_agent = await session.get(Agent, player_eng.id)
        assert player_agent is not None
        conv_store = get_conversation_store()
        dialogue_window = await conv_store.recent(conversation_id, limit=6)
        rewrite = await rewrite_query(
            session,
            player=player_agent,
            target=target,
            scene_id=player_eng.scene_id,
            raw_text=request.text,
            recent_dialogue=dialogue_window,
        )
        rewritten = rewrite.rewritten_query or request.text
        intent = await classify_intent(
            raw_text=rewritten,
            target_entity_type=target.entity_type,
            rewrite_hint=rewrite.intent_hint,
        )

        # 记忆检索（基于 rewritten 更准）
        search = await get_memory_service().search(
            session,
            target.id,
            MemorySearchRequest(
                query=rewritten,
                limit=6,
                include_short_term=True,
                include_long_term=True,
            ),
            now=utcnow(),
        )
        context_memories = [r.memory.description for r in search]

        # 关系摘要（给模型看）
        rel = await self._get_relationship(
            session, from_agent_id=target.id, to_entity_id=player_eng.id
        )
        relationship_block = None
        if rel is not None:
            relationship_block = (
                f"- familiarity={rel.familiarity:.1f} trust={rel.trust:.1f} "
                f"affection={rel.affection:.1f} fear={rel.fear:.1f}"
                + (f"\n- summary: {rel.summary}" if rel.summary else "")
            )

        # 生成结构化回复
        profile = profile_from_orm(target)
        reply = await generate_structured_reply(
            npc_profile=profile,
            player_name=player_eng.name,
            player_text=rewritten,
            memories=context_memories,
            dialogue_window=dialogue_window,
            relationship_summary=relationship_block,
            state_summary=(
                f"- 当前状态={state.state} 情绪={state.emotion or '平静'}"
            ),
        )
        reply_text = reply.reply_text or "……"
        emotion = reply.emotion

        # 应用 NPC 的 tool_calls（受白名单约束）
        tool_results: list[dict] = []
        for tc in reply.filtered_tool_calls():
            try:
                sim_id_inner = engine.get_simulation().id if engine.get_simulation() else "manual"  # type: ignore[union-attr]
                ctx = ToolContext(
                    session=session,
                    agent_id=target.id,
                    entity_type=target.entity_type,
                    scene_id=state.scene_id,
                    position=(state.x, state.y),
                    simulation_id=sim_id_inner,
                    world_time=utcnow(),
                    source="dialogue",
                )
                r = await get_tool_executor().execute_one(
                    ctx, ToolCall(tool=tc.tool, arguments=tc.arguments)
                )
                tool_results.append(r.model_dump())
            except Exception:
                logger.exception("dialogue tool_call crashed: %s", tc.tool)

        now = utcnow()
        player_msg = DialogueMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            speaker_id=player_eng.id,
            target_id=target.id,
            text=request.text,
            created_at=now,
            meta={"rewritten": rewritten, "intent": intent.intent},
        )
        npc_msg = DialogueMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            speaker_id=target.id,
            target_id=player_eng.id,
            text=reply_text,
            emotion=emotion,
            animation=reply.animation,
            created_at=now,
            meta={
                "relationship_delta": reply.relationship_delta.model_dump(),
                "tool_calls": [tc.model_dump() for tc in reply.filtered_tool_calls()],
            },
        )
        session.add_all([player_msg, npc_msg])

        # Redis 对话窗口
        await conv_store.append(
            conversation_id,
            speaker="player",
            speaker_id=player_eng.id,
            text=request.text,
            meta={"intent": intent.intent, "rewritten": rewritten},
        )
        await conv_store.append(
            conversation_id,
            speaker="npc",
            speaker_id=target.id,
            text=reply_text,
            emotion=emotion,
            meta={"animation": reply.animation},
        )

        # 记忆写入：NPC 侧 ask + reply，玩家侧一条聚合
        ms = get_memory_service()
        ask_calc = compute_importance(
            ImportanceContext(
                memory_type="chat",
                emotion=emotion,
                is_first_person=True,
                involves_player=True,
                relationship_familiarity=(
                    min((rel.familiarity if rel else 0.0) / 10.0, 1.0)
                    if rel
                    else 0.0
                ),
            )
        )
        player_mem = await ms.write(
            session,
            agent_id=target.id,
            memory_type="chat",
            scope="short_term",
            description=f"{player_eng.name} 问我：{request.text}",
            importance=ask_calc.importance,
            importance_detail=ask_calc.detail,
            subject=player_eng.name,
            predicate="asked",
            object_=target.name,
            keywords=[player_eng.name, target.name, intent.intent],
            commit=False,
        )
        reply_calc = compute_importance(
            ImportanceContext(
                memory_type="chat",
                emotion=emotion,
                is_first_person=True,
                involves_player=True,
                relationship_familiarity=(
                    min((rel.familiarity if rel else 0.0) / 10.0, 1.0)
                    if rel
                    else 0.0
                ),
            )
        )
        reply_mem = await ms.write(
            session,
            agent_id=target.id,
            memory_type="chat",
            scope="short_term",
            description=f"我回答 {player_eng.name}：{reply_text}",
            importance=reply_calc.importance,
            importance_detail=reply_calc.detail,
            subject=target.name,
            predicate="replied",
            object_=player_eng.name,
            keywords=[player_eng.name, target.name],
            commit=False,
        )
        # 处理 LLM 返回的额外 memory_writes
        for mw in reply.memory_writes[:2]:
            extra_calc = compute_importance(
                ImportanceContext(
                    memory_type=(
                        mw.memory_type
                        if mw.memory_type in {"event", "thought", "chat", "summary"}
                        else "chat"
                    ),
                    emotion=emotion,
                    is_first_person=True,
                    involves_player=True,
                )
            )
            await ms.write(
                session,
                agent_id=target.id,
                memory_type=(
                    mw.memory_type
                    if mw.memory_type in {"event", "thought", "chat", "summary"}
                    else "chat"
                ),
                scope="short_term",
                description=mw.description[:320],
                importance=max(extra_calc.importance, min(mw.importance, 10)),
                importance_detail=extra_calc.detail,
                commit=False,
            )
        # 玩家侧也保留一条聚合记忆
        player_self_calc = compute_importance(
            ImportanceContext(
                memory_type="chat",
                emotion=emotion,
                is_first_person=True,
            )
        )
        await ms.write(
            session,
            agent_id=player_eng.id,
            memory_type="chat",
            scope="short_term",
            description=f"我问 {target.name}：{request.text} ；对方回答：{reply_text}",
            importance=player_self_calc.importance,
            importance_detail=player_self_calc.detail,
            subject=player_eng.name,
            predicate="talked_to",
            object_=target.name,
            keywords=[player_eng.name, target.name],
            commit=False,
        )

        # 关系升温（LLM 建议的 delta / 至少一份默认升温）
        deltas = reply.relationship_delta
        fam = max(0.04, deltas.familiarity * 0.03)
        trust = max(0.0, deltas.trust * 0.03)
        aff = deltas.affection * 0.04
        fear = max(0.0, deltas.fear * 0.03)
        await self._update_relationship(
            session,
            from_agent_id=target.id,
            to_entity_id=player_eng.id,
            familiarity_delta=fam,
            trust_delta=trust,
            affection_delta=aff,
            fear_delta=fear,
        )
        await session.commit()

        # 关系摘要阈值：累计 delta 超 threshold 时投递 relationship_update 任务
        try:
            if await should_update_summary(
                from_agent_id=target.id,
                to_entity_id=player_eng.id,
                threshold=RELATIONSHIP_DELTA_THRESHOLD,
            ):
                from app.domain.tasks.queue import get_task_queue

                queue = get_task_queue()
                if queue is not None:
                    await queue.enqueue(
                        task_type="relationship_update",
                        payload={
                            "from_agent_id": target.id,
                            "to_entity_id": player_eng.id,
                            "regenerate_summary": True,
                        },
                        entity_id=target.id,
                        idempotency_extra=f"summary:{int(utcnow().timestamp()) // 60}",
                        deadline_seconds=90.0,
                    )
        except Exception:
            logger.debug("enqueue relationship_update failed", exc_info=True)

        # 广播
        bus = get_event_bus()
        sim_id = engine.get_simulation().id  # type: ignore[union-attr]
        await bus.publish(
            WS_BROADCAST_TOPIC,
            {
                "simulation_id": sim_id,
                "type": "dialogue.message_created",
                "payload": {
                    "conversation_id": conversation_id,
                    "speaker_id": player_eng.id,
                    "target_id": target.id,
                    "text": request.text,
                    "created_at": now.isoformat(),
                    "rewritten": rewritten,
                    "intent": intent.intent,
                },
            },
        )
        await bus.publish(
            WS_BROADCAST_TOPIC,
            {
                "simulation_id": sim_id,
                "type": "dialogue.message_created",
                "payload": {
                    "conversation_id": conversation_id,
                    "speaker_id": target.id,
                    "target_id": player_eng.id,
                    "text": reply_text,
                    "emotion": emotion,
                    "animation": reply.animation,
                    "created_at": now.isoformat(),
                    "tool_calls": [tc.model_dump() for tc in reply.filtered_tool_calls()],
                },
            },
        )

        return PlayerTalkResponse(
            conversation_id=conversation_id,
            reply=reply_text,
            emotion=emotion,
            memory_ids=[player_mem.id, reply_mem.id],
            citations=[
                {
                    "memory_id": r.memory.id,
                    "description": r.memory.description,
                    "score": r.score,
                }
                for r in search
            ],
        )

    async def _get_relationship(
        self, session: AsyncSession, *, from_agent_id: str, to_entity_id: str
    ) -> Relationship | None:
        stmt = select(Relationship).where(
            Relationship.from_agent_id == from_agent_id,
            Relationship.to_entity_id == to_entity_id,
        )
        return (await session.execute(stmt)).scalar_one_or_none()


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


_service: PlayerService | None = None


def get_player_service() -> PlayerService:
    global _service
    if _service is None:
        _service = PlayerService()
    return _service
