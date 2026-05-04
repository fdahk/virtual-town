"""
阶段 19：社交类工具。

工具集（NPC + 玩家共用）：

- ``request_interaction``：发起一次交互请求（chat / help / trade）。
  - 走 ``InteractionService.create_request``，被同步评估并广播 WS 事件。
  - 接受 → 双方都进入 CHATTING；拒绝 → 返回原因/台词。
  - 玩家也可使用，但通常通过 ``POST /api/players/me/interaction-requests`` 包装。
- ``end_chat``：主动结束当前对话。把自身和对方的 CHATTING 状态释放。
- ``socialize``：社交动机驱动的"主动找人聊天"快捷方式：
  在感知到的"附近熟人"中按好感度排前的随机一人，发起 chat 请求。

注意：``respond_to_interaction`` 协议判定走规则评估器（见
``interaction_evaluator``），不开放给 LLM——避免模型抖动绕过分级拒绝策略。
"""

from __future__ import annotations

import random
from typing import Iterable, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import Agent, AgentState, Relationship
from app.llm.tools.base import Tool, ToolCall, ToolContext, ToolResult, ToolSpec

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# request_interaction
# ---------------------------------------------------------------------------


class _RequestInteractionArgs(BaseModel):
    target_entity_id: str
    kind: Literal["chat", "help", "trade"] = "chat"
    reason: str = Field(default="", max_length=200)
    priority: int = Field(default=3, ge=0, le=10)


class RequestInteractionTool(Tool):
    """发起一次交互请求（请求-同意-拒绝协议）。"""

    name = "request_interaction"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=(
                "向附近的另一个实体发起交互请求（聊天 / 帮忙 / 交易）。"
                "对方会按当前状态、关系、紧迫度自动决定接受 / 软拒 / 硬拒。"
            ),
            owner_module="dialogue",
            allowed_entity_types=["human", "player"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_entity_id": {
                        "type": "string",
                        "description": "目标实体 id（NPC 或玩家）。",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["chat", "help", "trade"],
                        "description": "请求类型。",
                    },
                    "reason": {"type": "string", "description": "请求理由（向对方说明）。"},
                    "priority": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 10,
                        "description": "请求紧迫度 0-10。仅作为参考，不能强制对方接受。",
                    },
                },
                "required": ["target_entity_id"],
            },
            rerun_on_failure=False,
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _RequestInteractionArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        if args.target_entity_id == ctx.agent_id:
            return self.fail(
                call, "INVALID_ARGUMENTS", "cannot interact with yourself"
            )

        target_state = await ctx.session.get(AgentState, args.target_entity_id)
        if target_state is None:
            return self.fail(
                call, "TARGET_NOT_FOUND", f"agent {args.target_entity_id} not found"
            )
        if target_state.scene_id != ctx.scene_id:
            return self.fail(call, "OUT_OF_RANGE", "target in another scene")
        if (
            abs(target_state.x - ctx.position[0])
            + abs(target_state.y - ctx.position[1])
            > 4
        ):
            return self.fail(
                call, "OUT_OF_RANGE", "too far to talk", retryable=True
            )

        # 阶段 19：连续硬拒后冷却（避免 NPC 反复骚扰同一人）
        if await _refusal_cooldown_active(ctx.agent_id, args.target_entity_id):
            return self.fail(
                call,
                "STATE_CONFLICT",
                "cooldown active after recent hard refusals",
                retryable=False,
            )

        from app.services.interaction_service import get_interaction_service

        record, evt = await get_interaction_service().create_request(
            ctx.session,
            requester_id=ctx.agent_id,
            target_id=args.target_entity_id,
            kind=args.kind,
            reason=args.reason,
            requester_priority=args.priority,
            simulation_id=ctx.simulation_id,
        )

        result_payload = {
            "request_id": record.id,
            "status": record.status,
            "decision": evt.decision,
            "decline_kind": record.decline_kind,
            "npc_line": record.npc_line,
            "reason": evt.reason,
        }

        # 即使被拒，工具自身视为执行成功（请求被对方拒绝是业务结果，不是工具失败）
        memory_candidates = []
        if evt.decision == "accept":
            await _reset_refusal_counter(ctx.agent_id, args.target_entity_id)
            memory_candidates.append(
                {
                    "memory_type": "chat",
                    "scope": "short_term",
                    "description": (
                        f"我发起了与 {args.target_entity_id} 的 {args.kind}，对方接受了"
                    ),
                    "importance": 4,
                    "keywords": [args.target_entity_id, args.kind, "accepted"],
                }
            )
        else:
            if record.decline_kind == "hard":
                await _bump_refusal_counter(ctx.agent_id, args.target_entity_id)
            memory_candidates.append(
                {
                    "memory_type": "thought",
                    "scope": "short_term",
                    "description": (
                        f"我想找 {args.target_entity_id} {args.kind}，但对方"
                        f"{'委婉拒绝' if record.decline_kind == 'soft' else '直接拒绝'}了"
                    ),
                    "importance": 3,
                    "keywords": [args.target_entity_id, "declined", evt.reason],
                }
            )

        return ToolResult(
            tool=call.tool,
            success=True,
            result=result_payload,
            memory_candidates=memory_candidates,
        )


# ---------------------------------------------------------------------------
# 拒绝冷却计数器（Redis 滑窗，best-effort，失败不阻塞业务）
# ---------------------------------------------------------------------------


def _refusal_key(requester_id: str, target_id: str) -> str:
    return f"social:refusal:{requester_id}:{target_id}"


async def _refusal_cooldown_active(requester_id: str, target_id: str) -> bool:
    """连续 2 次硬拒后激活冷却，直到 TTL（30 仿真分钟，按真实秒数估算）。"""
    try:
        from app.core.config import get_settings
        from app.core.redis_client import get_redis

        redis = get_redis()
        client = await redis._ensure_client()  # type: ignore[attr-defined]
        if client is None:
            return False
        raw = await client.get(_refusal_key(requester_id, target_id))
        if raw is None:
            return False
        return int(raw) >= 2
    except Exception:
        return False


async def _bump_refusal_counter(requester_id: str, target_id: str) -> None:
    try:
        from app.core.config import get_settings
        from app.core.redis_client import get_redis

        redis = get_redis()
        client = await redis._ensure_client()  # type: ignore[attr-defined]
        if client is None:
            return
        key = _refusal_key(requester_id, target_id)
        await client.incr(key)
        # TTL（秒）：``social_cooldown_after_refusal_minutes * 12``（下限 60）
        ttl = max(60, get_settings().social_cooldown_after_refusal_minutes * 12)
        await client.expire(key, ttl)
    except Exception:
        pass


async def _reset_refusal_counter(requester_id: str, target_id: str) -> None:
    try:
        from app.core.redis_client import get_redis

        await get_redis().delete(_refusal_key(requester_id, target_id))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# end_chat（主动结束对话）
# ---------------------------------------------------------------------------


class _EndChatArgs(BaseModel):
    reason: str = Field(default="", max_length=120)
    target_entity_id: str | None = None


class EndChatTool(Tool):
    name = "end_chat"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="主动结束当前对话，恢复双方自主行动。",
            owner_module="dialogue",
            allowed_entity_types=["human", "player"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "结束的内心原因。"},
                    "target_entity_id": {
                        "type": "string",
                        "description": "（可选）一并释放的对方 id。",
                    },
                },
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _EndChatArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        from app.services.simulation_runtime import get_simulation_runtime

        try:
            engine = get_simulation_runtime().engine
        except Exception:
            return self.fail(call, "INTERNAL_ERROR", "engine not running")

        engine.end_chatting(ctx.agent_id)
        if args.target_entity_id:
            engine.end_chatting(args.target_entity_id)
        return self.ok(
            call,
            {"reason": args.reason, "target_entity_id": args.target_entity_id},
        )


# ---------------------------------------------------------------------------
# socialize（主动社交：基于好感度自动选择目标）
# ---------------------------------------------------------------------------


class _SocializeArgs(BaseModel):
    topic: str = Field(default="", max_length=120)
    # 阶段 19+：默认放宽到陌生人，避免 NPC 永远孤独。
    # 实际策略：先找熟人，再降级到陌生人；该开关只控制"是否允许降级"。
    only_friends: bool = False
    max_distance: int = Field(default=8, ge=1, le=20)


class SocializeTool(Tool):
    """`socialize`：基于好感度从附近熟人中挑选一个发起 chat 请求。

    决策建议：当 ``social_need ≥ 阈值`` 且当前空闲时，LLM 可调用此工具，
    无需手动选择 target_entity_id。

    阶段 19+：先找熟人，找不到时自动降级到陌生人——避免 NPC 在新场景或
    seed 关系不全时永远 ``TARGET_NOT_FOUND``。
    """

    name = "socialize"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=(
                "主动找附近的人聊天。系统会优先选熟人，没有熟人时也可以"
                "礼貌地与陌生邻居打招呼。"
            ),
            owner_module="dialogue",
            allowed_entity_types=["human"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "想聊的话题。"},
                    "only_friends": {
                        "type": "boolean",
                        "description": (
                            "true 表示严格只找熟人（familiarity≥0.2）。默认 false："
                            "找不到熟人时自动降级到陌生人。"
                        ),
                    },
                    "max_distance": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "搜索半径（曼哈顿距离）。",
                    },
                },
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _SocializeArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        # 找同场景内附近的人类 NPC
        nearby_states = (
            await ctx.session.execute(
                select(AgentState).where(
                    AgentState.scene_id == ctx.scene_id,
                    AgentState.agent_id != ctx.agent_id,
                )
            )
        ).scalars().all()

        candidates: list[tuple[str, float, AgentState]] = []
        for st in nearby_states:
            dist = abs(st.x - ctx.position[0]) + abs(st.y - ctx.position[1])
            if dist > args.max_distance:
                continue
            other = await ctx.session.get(Agent, st.agent_id)
            if other is None or other.entity_type not in {"human", "player"}:
                continue
            rel = (
                await ctx.session.execute(
                    select(Relationship).where(
                        Relationship.from_agent_id == ctx.agent_id,
                        Relationship.to_entity_id == st.agent_id,
                    )
                )
            ).scalar_one_or_none()
            familiarity = float(rel.familiarity) if rel else 0.0
            candidates.append((st.agent_id, familiarity, st))

        if not candidates:
            return self.fail(
                call,
                "TARGET_NOT_FOUND",
                "no one nearby to socialize with",
                retryable=True,
            )

        # 阶段 19+：分层挑选——先熟人池，再陌生人池
        friend_pool = [c for c in candidates if c[1] >= 0.2]
        if friend_pool:
            pool = friend_pool
        elif args.only_friends:
            return self.fail(
                call,
                "TARGET_NOT_FOUND",
                "no friends nearby to socialize with",
                retryable=True,
            )
        else:
            # 没熟人时降级：从陌生人里挑距离最近的几个
            pool = sorted(
                candidates,
                key=lambda c: abs(c[2].x - ctx.position[0])
                + abs(c[2].y - ctx.position[1]),
            )

        # 按好感度高到低排序，挑前 3 中随机一个（避免每次找同一个人）
        pool.sort(key=lambda c: c[1], reverse=True)
        picked = random.choice(pool[: min(3, len(pool))])
        target_id = picked[0]

        from app.services.interaction_service import get_interaction_service

        record, evt = await get_interaction_service().create_request(
            ctx.session,
            requester_id=ctx.agent_id,
            target_id=target_id,
            kind="chat",
            reason=args.topic or "想找人聊聊",
            requester_priority=2,
            simulation_id=ctx.simulation_id,
        )

        # 阶段 19+：写一条社交意图记忆，让 NPC 记住"我今天主动找过谁"
        target_obj = await ctx.session.get(Agent, target_id)
        target_name = target_obj.name if target_obj is not None else target_id
        if evt.decision == "accept":
            mem_desc = f"我主动去找 {target_name} 聊了聊"
            mem_type = "chat"
            mem_imp = 4
        elif record.decline_kind == "soft":
            mem_desc = f"我想找 {target_name} 聊天，对方说有点忙，下次再说"
            mem_type = "thought"
            mem_imp = 3
        else:
            mem_desc = f"我想找 {target_name} 聊天，对方现在没空"
            mem_type = "thought"
            mem_imp = 3
        memory_candidates = [
            {
                "memory_type": mem_type,
                "scope": "short_term",
                "description": mem_desc,
                "importance": mem_imp,
                "keywords": [target_name, "socialize", evt.decision],
            }
        ]

        return ToolResult(
            tool=call.tool,
            success=True,
            result={
                "request_id": record.id,
                "target_entity_id": target_id,
                "status": record.status,
                "decline_kind": record.decline_kind,
                "npc_line": record.npc_line,
            },
            memory_candidates=memory_candidates,
        )


# ---------------------------------------------------------------------------
# go_to_entity（主动追踪：跨场景前往某个 NPC）
# ---------------------------------------------------------------------------


class _GoToEntityArgs(BaseModel):
    target_entity_id: str
    reason: str = Field(default="", max_length=200)


class GoToEntityTool(Tool):
    """`go_to_entity`：派 NPC 前往指定 NPC 的当前位置（跨场景 + 实时追踪）。

    与 ``request_interaction`` 的差异：
    - ``request_interaction`` 要求目标在身边 4 格内，否则 ``OUT_OF_RANGE``；
    - ``go_to_entity`` 不需要目标在视野内，引擎会基于当前 (scene, x, y)
      规划跨场景路径，目标移动时每 tick 自动重新规划，到达 ≤2 格距离时
      自动清除追踪并立刻让 LLM 重新决策 → 此时再调用 ``request_interaction``。

    适用场景：``social_need`` 高、想找的熟人不在同场景；或者要"过去打个招呼"。
    """

    name = "go_to_entity"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=(
                "前往指定 NPC 当前所在地（跨场景 + 移动追踪）。到达后会自动停下，"
                "下一轮决策时再调用 request_interaction 发起聊天 / 帮忙等请求。"
            ),
            owner_module="dialogue",
            allowed_entity_types=["human"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_entity_id": {
                        "type": "string",
                        "description": "想要前往的 NPC id。",
                    },
                    "reason": {
                        "type": "string",
                        "description": "前往动机（写进 NPC 当前目标）。",
                    },
                },
                "required": ["target_entity_id"],
            },
            rerun_on_failure=False,
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _GoToEntityArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        if args.target_entity_id == ctx.agent_id:
            return self.fail(call, "INVALID_ARGUMENTS", "cannot pursue yourself")

        target_state = await ctx.session.get(AgentState, args.target_entity_id)
        if target_state is None:
            return self.fail(
                call, "TARGET_NOT_FOUND", f"agent {args.target_entity_id} not found"
            )
        target_agent = await ctx.session.get(Agent, args.target_entity_id)
        if target_agent is None or target_agent.entity_type != "human":
            return self.fail(call, "TARGET_NOT_FOUND", "target is not a human NPC")

        from app.services.simulation_runtime import get_simulation_runtime

        try:
            engine = get_simulation_runtime().engine
        except Exception:
            return self.fail(call, "INTERNAL_ERROR", "engine not running")

        outcome = engine.start_pursuit(
            ctx.agent_id,
            args.target_entity_id,
            reason=args.reason or f"去找 {target_agent.name}",
        )
        if not outcome.get("ok"):
            reason = outcome.get("reason", "UNKNOWN")
            # UNREACHABLE 同时把目标加进 agent 的不可达黑名单（与 stuck 决策共享一个 key）
            if reason == "UNREACHABLE":
                try:
                    from app.core.redis_client import get_redis, key_agent_unreachable

                    await get_redis().set_add(
                        key_agent_unreachable(ctx.agent_id),
                        args.target_entity_id,
                        ttl_seconds=300,
                    )
                except Exception:
                    pass
            return self.fail(
                call,
                reason if reason in {"TARGET_NOT_FOUND", "INVALID_ARGUMENTS"} else "UNREACHABLE",
                f"start_pursuit failed: {reason}",
                retryable=(reason == "UNREACHABLE"),
            )

        memory_candidates = [
            {
                "memory_type": "thought",
                "scope": "short_term",
                "description": f"我准备去 {target_agent.name} 那里 —— {args.reason or '想找他/她聊聊'}",
                "importance": 3,
                "keywords": [target_agent.name, "go_to_entity", "pursuit"],
            }
        ]

        return ToolResult(
            tool=call.tool,
            success=True,
            result={
                "target_entity_id": args.target_entity_id,
                "scene": outcome.get("scene"),
                "x": outcome.get("x"),
                "y": outcome.get("y"),
                "hops": outcome.get("hops", 0),
                "status": outcome.get("reason"),
            },
            memory_candidates=memory_candidates,
        )


def build_tools() -> Iterable[Tool]:
    return [
        RequestInteractionTool(),
        EndChatTool(),
        SocializeTool(),
        GoToEntityTool(),
    ]
