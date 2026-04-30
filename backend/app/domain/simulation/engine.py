"""
SimulationEngine：世界推进器。

职责（与 `仿真循环设计模块实施方案.md` 对齐）：
1. 读取输入队列（玩家移动、交互）；
2. 推进每个 Agent 的已有路径（world tick）；
3. 到达目标时触发事件；
4. 达到 AI tick 间隔时重新为每个 Agent 选择下一步（规则版 + 可选 LLM）；
5. 将增量通过事件总线广播给 WebSocket。

MVP 中：一个进程持有一个 SimulationEngine，全局 simulation 也是单例（single-player）。
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.event_bus import get_event_bus
from app.core.logging import get_logger
from app.core.redis_client import (
    get_redis,
    key_agent_runtime,
    key_scene_active_entities,
)
from app.core.time import iso, utcnow
from app.core.trace_context import TraceContext
from app.db.models import (
    Agent,
    AgentAction,
    AgentState,
    DialogueMessage,
    Location,
    MapScene,
    Portal,
    Simulation,
    Task,
    WorldEvent,
)
from app.services.event_router import WORLD_EVENT_TOPIC
from app.domain.simulation.rule_agent import (
    AgentDecision,
    decide_animal_action,
    decide_human_action,
)
from app.domain.world.scene_cache import get_scene_cache
from app.schemas.agent import AgentRuntimeState
from app.schemas.simulation import (
    Simulation as SimulationSchema,
    SimulationDeltaPayload,
    WorldEvent as WorldEventSchema,
)
from app.schemas.world import TilePosition
from app.services.agent_service import runtime_from_orm
from app.websocket.gateway import WS_BROADCAST_TOPIC

logger = get_logger(__name__)


@dataclass
class PlayerInputEvent:
    kind: str  # "move_path" / "teleport" / "interact"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineAgent:
    """引擎内存中的 agent 运行态副本。减少每 tick 的 ORM 负担。"""

    id: str
    entity_type: str
    name: str
    scene_id: str
    x: int
    y: int
    state: str
    path: list[tuple[int, int]] = field(default_factory=list)
    current_goal: str | None = None
    current_action_id: str | None = None
    last_decision_at: datetime | None = None
    status_effects: list[str] = field(default_factory=list)
    emotion: str | None = None
    facing: str = "down"
    energy: float = 1.0
    hunger: float = 0.2
    social_need: float = 0.3
    fear: float = 0.0
    dirty: bool = True  # 是否需要同步到数据库
    is_player: bool = False


class SimulationEngine:
    """
    并发模型：
    - 单一 asyncio task 驱动 world tick。
    - 玩家意图通过队列注入，避免竞态。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._simulation: Simulation | None = None
        self._agents: dict[str, EngineAgent] = {}
        self._agents_meta: dict[str, Agent] = {}
        self._locations: dict[str, dict[str, Any]] = {}
        self._portals_by_scene: dict[str, list[Portal]] = {}
        self._portals: dict[str, Portal] = {}
        self._player_id: str | None = None
        self._input_queue: deque[PlayerInputEvent] = deque()
        self._ai_tick_interval_real: float = 2.0  # 真实秒：每 N 秒执行一次 AI tick
        self._last_ai_tick: float = 0.0
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._stopping = False
        self._rng = random.Random()
        self._step = 0
        self._pending_events: list[WorldEvent] = []
        self._world_time: datetime = utcnow()
        # 每个 Agent 上次反思/日结的游戏时间
        self._last_reflect_at: dict[str, datetime] = {}
        self._last_summary_day: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        async with self._session_factory() as session:
            await self._load_state(session)
        self._running = True
        self._stopping = False
        self._task = asyncio.create_task(self._main_loop(), name="sim-engine")
        logger.info("simulation engine started", extra={"simulation_id": self._sim_id()})

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._running = False

    def _sim_id(self) -> str:
        return self._simulation.id if self._simulation else "no-sim"

    # ------------------------------------------------------------------
    # 状态加载
    # ------------------------------------------------------------------

    async def _load_state(self, session: AsyncSession) -> None:
        await get_scene_cache().refresh(session)

        sim = (await session.execute(select(Simulation).limit(1))).scalar_one_or_none()
        if sim is None:
            sim = Simulation(
                status="running" if get_settings().simulation_autostart else "idle",
                world_time=datetime(2026, 4, 30, 7, 30, tzinfo=utcnow().tzinfo),
                world_tick_hz=get_settings().simulation_world_tick_hz,
                ai_tick_minutes=get_settings().simulation_ai_tick_minutes,
                speed_multiplier=get_settings().simulation_speed_default,
                current_step=0,
            )
            session.add(sim)
            await session.commit()
            await session.refresh(sim)
        self._simulation = sim
        self._step = sim.current_step
        self._world_time = sim.world_time

        agents = (await session.execute(select(Agent))).scalars().all()
        self._agents_meta = {a.id: a for a in agents}
        states = (await session.execute(select(AgentState))).scalars().all()
        state_by_id = {s.agent_id: s for s in states}

        self._agents.clear()
        for a in agents:
            st = state_by_id.get(a.id)
            if st is None:
                # 没有运行态的 agent 跳过。Seed 脚本应当补齐所有运行态。
                logger.warning("agent %s has no runtime state", a.id)
                continue
            eng = EngineAgent(
                id=a.id,
                entity_type=a.entity_type,
                name=a.name,
                scene_id=st.scene_id,
                x=st.x,
                y=st.y,
                state=st.state,
                path=[(p["x"], p["y"]) for p in (st.path or [])],
                current_goal=st.current_goal,
                current_action_id=st.current_action_id,
                last_decision_at=st.last_decision_at,
                status_effects=list(st.status_effects or []),
                emotion=st.emotion,
                facing=st.facing,
                energy=st.energy,
                hunger=st.hunger,
                social_need=st.social_need,
                fear=st.fear,
                dirty=False,
                is_player=(a.entity_type == "player"),
            )
            if eng.is_player:
                self._player_id = a.id
            self._agents[a.id] = eng

        # Location & portal 缓存
        loc_rows = (await session.execute(select(Location))).scalars().all()
        self._locations = {
            l.id: {
                "id": l.id,
                "scene_id": l.scene_id,
                "name": l.name,
                "location_type": l.location_type,
                "bounds": l.bounds,
                "entry_tiles": l.entry_tiles,
                "open_hours": l.open_hours,
                "tags": l.tags,
            }
            for l in loc_rows
        }
        portals = (await session.execute(select(Portal))).scalars().all()
        self._portals = {p.id: p for p in portals}
        self._portals_by_scene.clear()
        for p in portals:
            self._portals_by_scene.setdefault(p.from_scene_id, []).append(p)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        try:
            while not self._stopping:
                if self._simulation is None or self._simulation.status != "running":
                    await asyncio.sleep(0.2)
                    continue
                await self._tick()
                # world tick：HZ 受 speed_multiplier 影响
                hz = max(self._simulation.world_tick_hz * self._simulation.speed_multiplier, 0.5)
                await asyncio.sleep(1.0 / hz)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("simulation engine crashed")

    async def _tick(self) -> None:
        sim = self._simulation
        assert sim is not None

        # 推进游戏内时间：一次 world tick = 1 游戏分钟 / speed_multiplier
        # 这样 5 Hz + speed=1 => 游戏时间 5 分钟 / 真实秒
        minutes_per_tick = 1
        self._world_time = self._world_time + timedelta(minutes=minutes_per_tick)
        self._step += 1
        # 每个 world tick 作为一条 trace 的顶层 span。
        # 子模块（决策、工具、LLM）在同一 trace 下嵌套 span。
        with TraceContext.start_trace(
            "world_tick",
            simulation_id=self._sim_id(),
        ):
            await self._tick_inner()

    async def _tick_inner(self) -> None:
        sim = self._simulation
        assert sim is not None

        entity_updates: list[AgentRuntimeState] = []
        now_dt = self._world_time

        # 1. 处理玩家输入
        while self._input_queue:
            evt = self._input_queue.popleft()
            await self._handle_input(evt)

        # 2. 每个 Agent 推进已有路径 / 触发到达
        for agent in self._agents.values():
            if agent.is_player:
                continue
            if agent.state == "CHATTING":
                # 对话中不移动
                continue
            if agent.path:
                next_tile = agent.path[0]
                agent.facing = _face_towards((agent.x, agent.y), next_tile)
                agent.x, agent.y = next_tile
                agent.path.pop(0)
                agent.state = "MOVING" if agent.path else "IDLE"
                agent.dirty = True
                if not agent.path:
                    await self._on_agent_arrived(agent)

        # 3. 玩家按路径自动前进
        player = self._current_player()
        if player is not None and player.path:
            next_tile = player.path[0]
            player.facing = _face_towards((player.x, player.y), next_tile)
            player.x, player.y = next_tile
            player.path.pop(0)
            player.state = "MOVING" if player.path else "IDLE"
            player.dirty = True
            await self._check_player_environment(player)

        # 4. AI Decision Tick（每个 Agent 独立按游戏分钟间隔决策）
        await self._decide_all(now_dt)
        await self._maybe_reflect_batch(now_dt)

        # 5. 汇总 updates
        for agent in self._agents.values():
            if agent.dirty:
                entity_updates.append(self._to_runtime_state(agent))

        # 6. 持久化 + 广播
        events_to_broadcast = list(self._pending_events)
        self._pending_events.clear()
        await self._persist_tick(sim, entity_updates, events_to_broadcast)
        await self._broadcast_delta(entity_updates, events_to_broadcast)

        for agent in self._agents.values():
            agent.dirty = False

    def _to_runtime_state(self, a: EngineAgent) -> AgentRuntimeState:
        return AgentRuntimeState(
            agent_id=a.id,
            scene_id=a.scene_id,
            position=TilePosition(x=a.x, y=a.y),
            state=a.state,
            emotion=a.emotion,
            energy=a.energy,
            hunger=a.hunger,
            social_need=a.social_need,
            fear=a.fear,
            status_effects=a.status_effects,
            current_action_id=a.current_action_id,
            current_goal=a.current_goal,
            facing=a.facing,
            updated_at=utcnow(),
        )

    # ------------------------------------------------------------------
    # AI 决策
    # ------------------------------------------------------------------

    async def _decide_all(self, now_dt: datetime) -> None:
        """
        决策策略：LLM 为主，规则为兜底。

        - 未配置 LLM（LLM_ENABLED=false 或无 key）：全部走规则版。
        - 配置 LLM + ``task_queue_mode=async``（默认）：主循环**不阻塞等待 LLM**，
          而是投递 ``agent_decision`` 任务到 TaskQueue。Worker 异步调 LLM，
          决策结果通过工具副作用回写世界状态，主循环在下一次 tick 时才看到。
          本轮仍然先走规则兜底，确保 NPC 永远有即时行为。
        - ``task_queue_mode=sync``：沿用旧路径——主循环内直接 await LLM 决策，
          成功则应用；全失败回退规则（保留用于回归/调试）。
        - 每个 Agent 独立按 ai_tick_minutes 游戏分钟间隔决策，避免全局时钟相互阻塞。
        - Engine 主循环必须永不因 LLM 而卡住；任何异常被吞掉改用规则决策。
        """
        from app.core.config import get_settings
        from app.llm.agent_decision import decide_with_llm

        settings = get_settings()
        llm_enabled = settings.llm_is_configured
        async_mode = settings.task_queue_mode == "async"
        ai_interval = self._simulation.ai_tick_minutes if self._simulation else 5
        grids = {
            sid: g
            for sid in get_scene_cache().all_scene_ids()
            if (g := get_scene_cache().get_grid(sid))
        }

        for agent in self._agents.values():
            if agent.is_player:
                continue
            if agent.state in {"CHATTING", "SLEEPING"}:
                continue
            if agent.path:
                continue
            # 每个 Agent 独立检查自身的决策冷却时间
            if (
                agent.last_decision_at is not None
                and (now_dt - agent.last_decision_at).total_seconds() < ai_interval * 60
            ):
                continue
            meta = self._agents_meta.get(agent.id)
            if meta is None:
                continue

            applied = False
            if llm_enabled and async_mode:
                # 异步路径：投递任务，主循环立即返回；worker 完成后下一轮 tick
                # 通过数据库状态感知变化。本轮先让规则兜底生成即时行为，避免静止。
                try:
                    await self._enqueue_decision_task(agent.id, now_dt)
                except Exception:
                    logger.debug("enqueue agent_decision failed", exc_info=True)
            elif llm_enabled:
                try:
                    async with self._session_factory() as llm_session:
                        results = await decide_with_llm(
                            llm_session,
                            agent_id=agent.id,
                            simulation_id=self._sim_id(),
                            world_time=now_dt,
                        )
                        await llm_session.commit()
                        if results is not None:
                            any_success = any(r.success for r in results)
                            if any_success:
                                agent.last_decision_at = now_dt
                                agent.dirty = True
                                applied = True
                                if not agent.path and agent.state == "IDLE":
                                    agent.state = "WAITING"
                                self._pending_events.append(
                                    _new_event(
                                        simulation_id=self._sim_id(),
                                        event_type="llm.task_finished",
                                        actor_entity_id=agent.id,
                                        scene_id=agent.scene_id,
                                        description=f"{agent.name}：LLM 决策完成",
                                        importance=1,
                                        payload={"results": [r.model_dump() for r in results]},
                                        created_at=utcnow(),
                                    )
                                )
                            else:
                                logger.warning(
                                    "LLM tools all failed for %s; fallback to rule", agent.id
                                )
                except Exception:
                    logger.exception("LLM decision failed for %s; fallback to rule", agent.id)

            if applied:
                continue

            fake_state = _ORMStateView(agent)
            try:
                if meta.entity_type == "animal":
                    nearby = None
                    p = self._current_player()
                    if p is not None and p.scene_id == agent.scene_id:
                        if abs(p.x - agent.x) + abs(p.y - agent.y) <= 6:
                            nearby = (p.x, p.y)
                    decision = decide_animal_action(
                        agent_row=meta,
                        state_row=fake_state,
                        grids=grids,
                        rng=self._rng,
                        nearby_player=nearby,
                    )
                else:
                    decision = decide_human_action(
                        agent_row=meta,
                        state_row=fake_state,
                        world_time=now_dt,
                        locations=self._locations,
                        grids=grids,
                        portals_by_scene=self._portals_by_scene,
                    )
            except Exception:
                logger.exception("decide failed for %s", agent.id)
                continue
            self._apply_decision(agent, decision, now_dt)

    def _apply_decision(
        self, agent: EngineAgent, decision: AgentDecision, now_dt: datetime
    ) -> None:
        agent.last_decision_at = now_dt
        agent.current_goal = decision.description or agent.current_goal
        agent.dirty = True
        if decision.path:
            agent.path = list(decision.path)
            if agent.path and agent.path[0] == (agent.x, agent.y):
                agent.path.pop(0)
            agent.state = "MOVING" if agent.path else "INTERACTING"
        elif decision.action_type == "sleep":
            agent.state = "SLEEPING"
        elif decision.action_type == "wait":
            # WAITING 与 IDLE 区分：WAITING 表示主动等待或暂时受阻，
            # IDLE 保留给初始/空闲状态，避免卡死的 NPC 外观与正常空闲混淆
            agent.state = "WAITING"
        else:
            agent.state = "INTERACTING"
        self._pending_events.append(
            _new_event(
                simulation_id=self._sim_id(),
                event_type="agent.action_started",
                actor_entity_id=agent.id,
                location_id=decision.target_location_id,
                scene_id=agent.scene_id,
                description=f"{agent.name}：{decision.description}",
                importance=2,
                payload={"action": decision.action_type, "goal": decision.description},
                created_at=utcnow(),
            )
        )

    async def _on_agent_arrived(self, agent: EngineAgent) -> None:
        # 到达目标：若位于 Portal tile 则自动切换 scene
        portal = self._find_portal_at(agent.scene_id, agent.x, agent.y)
        if portal is not None and portal.interaction_type == "auto_enter":
            await self._teleport(agent, portal.to_scene_id, (portal.to_tile["x"], portal.to_tile["y"]))
            return
        agent.state = "INTERACTING"
        self._pending_events.append(
            _new_event(
                simulation_id=self._sim_id(),
                event_type="agent.action_finished",
                actor_entity_id=agent.id,
                scene_id=agent.scene_id,
                description=f"{agent.name} 到达目的地",
                importance=2,
                payload={},
                created_at=utcnow(),
            )
        )

    # ------------------------------------------------------------------
    # 玩家
    # ------------------------------------------------------------------

    def _current_player(self) -> EngineAgent | None:
        if self._player_id is None:
            return None
        return self._agents.get(self._player_id)

    def enqueue_input(self, evt: PlayerInputEvent) -> None:
        self._input_queue.append(evt)

    async def _handle_input(self, evt: PlayerInputEvent) -> None:
        player = self._current_player()
        if player is None:
            return
        if evt.kind == "move_path":
            path = evt.payload.get("path") or []
            player.path = [(p["x"], p["y"]) for p in path]
            player.state = "MOVING"
            player.dirty = True
        elif evt.kind == "teleport":
            scene_id = evt.payload["scene_id"]
            x = int(evt.payload["x"])
            y = int(evt.payload["y"])
            await self._teleport(player, scene_id, (x, y))
        elif evt.kind == "dialogue":
            # 注入对话广播；具体回复由 PlayerService 完成
            self._pending_events.append(
                _new_event(
                    simulation_id=self._sim_id(),
                    event_type="dialogue.message_created",
                    actor_entity_id=evt.payload.get("speaker_id"),
                    target_entity_id=evt.payload.get("target_id"),
                    scene_id=player.scene_id,
                    description=evt.payload.get("preview", ""),
                    importance=3,
                    payload=evt.payload,
                    created_at=utcnow(),
                )
            )

    async def _check_player_environment(self, player: EngineAgent) -> None:
        grid = get_scene_cache().get_grid(player.scene_id)
        if grid is None:
            return
        tile = grid.get(player.x, player.y)
        if tile is not None and tile.hazard_type == "deep_water":
            if "drowning" not in player.status_effects:
                player.status_effects.append("drowning")
                player.state = "DROWNING"
                player.dirty = True
                self._pending_events.append(
                    _new_event(
                        simulation_id=self._sim_id(),
                        event_type="world.hazard_triggered",
                        actor_entity_id=player.id,
                        scene_id=player.scene_id,
                        description=f"{player.name} 掉进了河里！",
                        importance=7,
                        payload={"hazard_type": tile.hazard_type, "new_status": "DROWNING"},
                        created_at=utcnow(),
                    )
                )
        else:
            if "drowning" in player.status_effects:
                player.status_effects.remove("drowning")
                player.state = "IDLE"
                player.dirty = True
        portal = self._find_portal_at(player.scene_id, player.x, player.y)
        if portal is not None and portal.interaction_type == "auto_enter":
            await self._teleport(
                player,
                portal.to_scene_id,
                (portal.to_tile["x"], portal.to_tile["y"]),
            )

    def _find_portal_at(self, scene_id: str, x: int, y: int) -> Portal | None:
        for p in self._portals_by_scene.get(scene_id, []):
            ft = p.from_tile
            if int(ft.get("x")) == x and int(ft.get("y")) == y:
                return p
        return None

    async def _teleport(
        self, agent: EngineAgent, to_scene_id: str, to_tile: tuple[int, int]
    ) -> None:
        from_scene = agent.scene_id
        agent.scene_id = to_scene_id
        agent.x, agent.y = to_tile
        agent.state = "IDLE"
        agent.path = []
        agent.dirty = True
        self._pending_events.append(
            _new_event(
                simulation_id=self._sim_id(),
                event_type="world.scene_changed",
                actor_entity_id=agent.id,
                scene_id=to_scene_id,
                description=f"{agent.name} 进入新场景",
                importance=3,
                payload={
                    "entity_id": agent.id,
                    "from_scene_id": from_scene,
                    "to_scene_id": to_scene_id,
                    "position": {"x": to_tile[0], "y": to_tile[1]},
                },
                created_at=utcnow(),
            )
        )

    # ------------------------------------------------------------------
    # 持久化 / 广播
    # ------------------------------------------------------------------

    async def _persist_tick(
        self,
        sim: Simulation,
        entity_updates: list[AgentRuntimeState],
        events: list[WorldEvent],
    ) -> None:
        async with self._session_factory() as session:
            db_sim = await session.get(Simulation, sim.id)
            if db_sim is not None:
                db_sim.current_step = self._step
                db_sim.world_time = self._world_time
            for upd in entity_updates:
                state = await session.get(AgentState, upd.agent_id)
                if state is not None:
                    state.scene_id = upd.scene_id
                    state.x = upd.position.x
                    state.y = upd.position.y
                    state.state = upd.state
                    state.emotion = upd.emotion
                    state.energy = upd.energy
                    state.hunger = upd.hunger
                    if upd.social_need is not None:
                        state.social_need = upd.social_need
                    if upd.fear is not None:
                        state.fear = upd.fear
                    state.status_effects = upd.status_effects
                    state.current_action_id = upd.current_action_id
                    state.current_goal = upd.current_goal
                    state.facing = upd.facing
                    agent_entry = self._agents.get(upd.agent_id)
                    state.path = (
                        [{"x": p[0], "y": p[1]} for p in agent_entry.path]
                        if agent_entry is not None
                        else []
                    )
                    state.last_decision_at = (
                        agent_entry.last_decision_at if agent_entry else None
                    )
            for evt in events:
                session.add(evt)
            await session.commit()
        # 同步内存 ORM 对象，保证 snapshot() / get_simulation() 返回最新值
        if self._simulation is not None:
            self._simulation.current_step = self._step
            self._simulation.world_time = self._world_time

        # 事件统一出口：每条 WorldEvent 通过事件总线分发给 EventRouter，
        # 由 Router 触发 Observer 埋点 / 未来的 memory / task 分发。
        bus = get_event_bus()
        for evt in events:
            try:
                await bus.publish(
                    WORLD_EVENT_TOPIC,
                    {
                        "id": evt.id,
                        "simulation_id": evt.simulation_id,
                        "event_type": evt.event_type,
                        "source": evt.source,
                        "actor_entity_id": evt.actor_entity_id,
                        "target_entity_id": evt.target_entity_id,
                        "location_id": evt.location_id,
                        "scene_id": evt.scene_id,
                        "description": evt.description,
                        "importance": evt.importance,
                        "payload": evt.payload,
                        "created_at": iso(evt.created_at),
                    },
                )
            except Exception:
                logger.debug("world event publish failed", exc_info=True)

        # Redis 热写：同步 agent runtime + scene active_entities 供 Redis 直读。
        await self._sync_runtime_cache(entity_updates)

    async def _sync_runtime_cache(
        self, entity_updates: list[AgentRuntimeState]
    ) -> None:
        if not entity_updates:
            return
        redis = get_redis()
        # 按场景聚合
        scene_members: dict[str, list[str]] = {}
        for upd in entity_updates:
            try:
                await redis.set_json(
                    key_agent_runtime(upd.agent_id),
                    {
                        "agent_id": upd.agent_id,
                        "scene_id": upd.scene_id,
                        "x": upd.position.x,
                        "y": upd.position.y,
                        "state": upd.state,
                        "emotion": upd.emotion,
                        "facing": upd.facing,
                        "energy": upd.energy,
                        "hunger": upd.hunger,
                        "updated_at": iso(upd.updated_at),
                    },
                    ttl_seconds=300,
                )
            except Exception:
                continue
            scene_members.setdefault(upd.scene_id, []).append(upd.agent_id)
        for scene_id, members in scene_members.items():
            try:
                await redis.set_add(
                    key_scene_active_entities(scene_id),
                    *members,
                    ttl_seconds=300,
                )
            except Exception:
                continue

    async def _broadcast_delta(
        self, entity_updates: list[AgentRuntimeState], events: list[WorldEvent]
    ) -> None:
        # 每个 tick 都广播，保证前端世界时钟实时更新，即使当轮无实体变化也推送
        # 递增 delta 序号：前端可以检测乱序与丢失（§14.5）
        self._delta_seq = getattr(self, "_delta_seq", 0) + 1
        payload = SimulationDeltaPayload(
            step=self._step,
            world_time=self._world_time,
            entity_updates=entity_updates,
            events=[WorldEventSchema.model_validate(e) for e in events],
        )
        data = payload.model_dump(mode="json")
        data["seq"] = self._delta_seq
        await get_event_bus().publish(
            WS_BROADCAST_TOPIC,
            {
                "simulation_id": self._sim_id(),
                "type": "simulation.delta",
                "payload": data,
            },
        )

    # ------------------------------------------------------------------
    # 对外查询
    # ------------------------------------------------------------------

    def snapshot(self) -> tuple[Simulation, list[AgentRuntimeState]]:
        assert self._simulation is not None
        entities = [self._to_runtime_state(a) for a in self._agents.values()]
        return self._simulation, entities

    def get_simulation(self) -> Simulation | None:
        return self._simulation

    def set_status(self, status: str) -> None:
        if self._simulation is not None:
            self._simulation.status = status

    def set_speed(self, multiplier: float) -> None:
        if self._simulation is not None:
            self._simulation.speed_multiplier = multiplier

    def get_agent(self, agent_id: str) -> EngineAgent | None:
        return self._agents.get(agent_id)

    def find_agent_by_name(self, name: str) -> EngineAgent | None:
        for a in self._agents.values():
            if a.name == name:
                return a
        return None

    async def reload_from_db(self) -> None:
        async with self._session_factory() as session:
            await self._load_state(session)

    # ------------------------------------------------------------------
    # 异步任务投递（阶段 12 §6）
    # ------------------------------------------------------------------

    async def _enqueue_decision_task(self, agent_id: str, now_dt: datetime) -> None:
        """把 agent_decision 投递到 TaskQueue。

        - 幂等 key：``agent_decision:{agent_id}:{simulation_step}``，同一步同一 agent 不会重复入库。
        - 主循环**不等待**任务执行结果；即使任务 failed，规则兜底早已给出即时行为。
        - 任务完成后 worker 的工具调用会改写 AgentState / AgentAction，
          下一轮 world tick 通过 ``reload_from_db`` 或状态同步感知。
        """
        from app.domain.tasks.queue import get_task_queue

        queue = get_task_queue()
        if queue is None:
            return
        agent = self._agents.get(agent_id)
        if agent is None or agent.is_player:
            return
        await queue.enqueue(
            task_type="agent_decision",
            payload={
                "agent_id": agent_id,
                "simulation_id": self._sim_id(),
                "world_time": now_dt.isoformat(),
            },
            entity_id=agent_id,
            simulation_id=self._sim_id(),
            simulation_step=self._step,
            priority=5,
            deadline_seconds=60.0,
        )
        agent.last_decision_at = now_dt
        agent.dirty = True

    # ------------------------------------------------------------------
    # 反思 / 日结
    # ------------------------------------------------------------------

    async def _maybe_reflect_batch(self, now_dt: datetime) -> None:
        """
        为 1-2 个"累积事件最多"的 Agent 跑反思/日结。
        - 避免每 tick 全量反思（LLM 成本高）。
        - 严格守护：任何失败被吞，保证引擎不崩溃。
        """
        from app.domain.memory.reflection import maybe_daily_summary, maybe_reflect

        # 每 10 个游戏分钟最多触发一次
        candidates = [
            agent
            for agent in self._agents.values()
            if not agent.is_player
            and (
                agent.id not in self._last_reflect_at
                or (now_dt - self._last_reflect_at[agent.id]).total_seconds() >= 600
            )
        ]
        if not candidates:
            return
        # 取 1 个跑反思 + 尝试所有跑日结（日结内部有 day 粒度去重，廉价）
        self._rng.shuffle(candidates)
        target = candidates[0]
        try:
            async with self._session_factory() as session:
                reflections = await maybe_reflect(
                    session, target.id, world_time=now_dt
                )
                if reflections:
                    self._pending_events.append(
                        _new_event(
                            simulation_id=self._sim_id(),
                            event_type="memory.created",
                            actor_entity_id=target.id,
                            scene_id=target.scene_id,
                            description=f"{target.name} 形成了新的想法：{reflections[0].description[:40]}…",
                            importance=4,
                            payload={"reflection_ids": [m.id for m in reflections]},
                            created_at=utcnow(),
                        )
                    )
                self._last_reflect_at[target.id] = now_dt
        except Exception:
            logger.exception("reflection failed for %s", target.id)

        # 日结：一天一次
        day_key = now_dt.strftime("%Y-%m-%d")
        for agent in self._agents.values():
            if agent.is_player:
                continue
            if self._last_summary_day.get(agent.id) == day_key:
                continue
            try:
                async with self._session_factory() as session:
                    mem = await maybe_daily_summary(
                        session, agent.id, world_time=now_dt
                    )
                    if mem is not None:
                        self._pending_events.append(
                            _new_event(
                                simulation_id=self._sim_id(),
                                event_type="memory.created",
                                actor_entity_id=agent.id,
                                scene_id=agent.scene_id,
                                description=f"{agent.name} 写下了今日总结：{mem.description[:40]}…",
                                importance=3,
                                payload={"summary_id": mem.id},
                                created_at=utcnow(),
                            )
                        )
                        self._last_summary_day[agent.id] = day_key
            except Exception:
                logger.exception("daily summary failed for %s", agent.id)


class _ORMStateView:
    """将 EngineAgent 伪装为 AgentState，满足 rule_agent 入参要求。"""

    def __init__(self, a: EngineAgent) -> None:
        self.agent_id = a.id
        self.scene_id = a.scene_id
        self.x = a.x
        self.y = a.y
        self.state = a.state
        self.energy = a.energy


def _face_towards(from_: tuple[int, int], to: tuple[int, int]) -> str:
    dx, dy = to[0] - from_[0], to[1] - from_[1]
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


def _new_event(
    *,
    simulation_id: str,
    event_type: str,
    description: str,
    created_at: datetime,
    source: str = "world",
    actor_entity_id: str | None = None,
    target_entity_id: str | None = None,
    location_id: str | None = None,
    scene_id: str | None = None,
    importance: int = 1,
    payload: dict[str, Any] | None = None,
) -> WorldEvent:
    return WorldEvent(
        simulation_id=simulation_id,
        event_type=event_type,
        source=source,
        actor_entity_id=actor_entity_id,
        target_entity_id=target_entity_id,
        location_id=location_id,
        scene_id=scene_id,
        description=description,
        importance=importance,
        payload=payload or {},
        created_at=created_at,
    )
