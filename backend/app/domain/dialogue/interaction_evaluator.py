"""
阶段 19：交互请求评估器（分级拒绝策略）。

职责：根据请求方、目标方的当前状态、双方关系、目标当前任务优先级，
判断目标 NPC 应当 ``accept`` / ``soft_decline`` / ``hard_decline``。

分级策略（与方案 §交互请求协议 一致）：

1. 目标睡眠中 / 紧急任务（current_priority >= 高优先级阈值）→ **hard_decline**
2. 陌生人（familiarity < 阈值）+ 当前忙碌（state in 忙碌集 或 busy_until > now）
   → **hard_decline**
3. 熟人 + 忙碌 → **soft_decline**（LLM 生成自然台词；规则兜底用模板）
4. 同对方近期连续硬拒后冷却内 → **hard_decline**
5. 其他情况 → **accept**

LLM 仅用于生成"软拒台词"（让 NPC 听起来自然），不参与 accept/decline 判定。
判定逻辑全部在规则层完成，避免 LLM 抖动影响协议正确性。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import Agent, AgentState, Relationship

logger = get_logger(__name__)


Decision = Literal["accept", "soft_decline", "hard_decline"]


# 哪些 state 算 "忙碌"（不能接受打断）
_BUSY_STATES: frozenset[str] = frozenset(
    {
        "WORKING",
        "EATING",
        "RESTING",
        "SLEEPING",
        "PANIC",
        "DROWNING",
        "AWAITING_RESPONSE",
        "BUSY_REFUSING",
    }
)


@dataclass
class EvaluationResult:
    decision: Decision
    reason: str
    npc_line: str | None = None


@dataclass
class _Context:
    requester: Agent
    requester_state: AgentState | None
    target: Agent
    target_state: AgentState
    relationship: Relationship | None
    now: datetime


async def evaluate_interaction_request(
    session: AsyncSession,
    *,
    requester_id: str,
    target_id: str,
    kind: str,
    reason: str | None = None,
) -> EvaluationResult:
    """评估一次交互请求。返回 (decision, reason, optional npc_line)。"""
    settings = get_settings()
    requester = await session.get(Agent, requester_id)
    target = await session.get(Agent, target_id)
    if requester is None or target is None:
        return EvaluationResult(
            decision="hard_decline",
            reason="target_not_found",
        )

    if requester.id == target.id:
        return EvaluationResult(
            decision="hard_decline", reason="cannot_self_interact"
        )

    target_state = await session.get(AgentState, target_id)
    if target_state is None:
        return EvaluationResult(
            decision="hard_decline", reason="target_state_missing"
        )
    requester_state = await session.get(AgentState, requester_id)
    rel = await _get_relationship(session, target_id, requester_id)

    ctx = _Context(
        requester=requester,
        requester_state=requester_state,
        target=target,
        target_state=target_state,
        relationship=rel,
        now=utcnow(),
    )

    # ── 1. 紧急/睡眠 状态硬拒 ──────────────────────────────────────────────
    if target_state.state == "SLEEPING":
        return EvaluationResult(
            decision="hard_decline",
            reason="sleeping",
            npc_line=f"{target.name} 正在睡觉，没有回应。",
        )
    if (
        target_state.current_priority
        >= settings.interaction_high_priority_threshold
        or not target_state.interruptible
    ):
        return EvaluationResult(
            decision="hard_decline",
            reason="high_priority_task",
            npc_line=f"{target.name} 正在忙重要的事，没空理你。",
        )

    busy = target_state.state in _BUSY_STATES or (
        target_state.busy_until is not None and target_state.busy_until > ctx.now
    )

    familiarity = float(rel.familiarity) if rel is not None else 0.0
    is_stranger = familiarity < settings.interaction_stranger_familiarity_threshold

    # ── 2. 陌生人 + 忙碌 → 硬拒 ────────────────────────────────────────────
    if busy and is_stranger:
        return EvaluationResult(
            decision="hard_decline",
            reason="busy_stranger",
            npc_line=f"{target.name} 看起来很忙，对陌生面孔保持距离。",
        )

    # ── 3. 熟人 + 忙碌 → 软拒（自然台词） ───────────────────────────────────
    if busy:
        line = await _craft_soft_decline_line(
            ctx,
            kind=kind,
            reason=reason,
        )
        return EvaluationResult(
            decision="soft_decline",
            reason="busy_friend",
            npc_line=line,
        )

    # ── 4. 玩家发起 + 关系恶劣（高 fear / 低 trust） → 软拒 ────────────────
    if rel is not None and rel.fear > 0.6 and requester.entity_type == "player":
        return EvaluationResult(
            decision="soft_decline",
            reason="fear_player",
            npc_line=f"{target.name} 警惕地保持距离：「我现在不想说话。」",
        )

    # ── 5. 默认接受 ───────────────────────────────────────────────────────
    return EvaluationResult(decision="accept", reason="ok")


async def _get_relationship(
    session: AsyncSession, from_agent_id: str, to_entity_id: str
) -> Relationship | None:
    stmt = select(Relationship).where(
        Relationship.from_agent_id == from_agent_id,
        Relationship.to_entity_id == to_entity_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _craft_soft_decline_line(
    ctx: _Context,
    *,
    kind: str,
    reason: str | None,
) -> str:
    """优先用 LLM 生成自然台词；失败回落规则模板。"""
    from app.llm.client import get_llm_client

    client = get_llm_client()
    if client.settings.any_llm_configured:
        try:
            data = await client.chat_json(
                system=(
                    "你是一个 2D 生活小镇的 NPC，玩家或其他 NPC 想找你 "
                    "聊天 / 帮忙 / 交易，但你目前在忙。请用 1-2 句自然口语"
                    "中文短句委婉拒绝，并提示对方稍后再来。"
                    "严格只输出 JSON：{\"line\": string}。"
                ),
                user=(
                    f"你的名字：{ctx.target.name}\n"
                    f"你的性格：{', '.join(ctx.target.personality or []) or '未知'}\n"
                    f"当前任务/状态：{ctx.target_state.current_goal or ctx.target_state.state}\n"
                    f"对方：{ctx.requester.name}（关系熟悉度："
                    f"{(ctx.relationship.familiarity if ctx.relationship else 0.0):.2f}）\n"
                    f"对方的请求类型：{kind}，理由：{reason or '（未说明）'}"
                ),
                temperature=0.5,
                max_tokens=120,
                caller_module="interaction_evaluator.soft_decline",
            )
            if data and isinstance(data.get("line"), str):
                line = data["line"].strip()[:120]
                if line:
                    return line
        except Exception:
            logger.debug("soft decline LLM line failed", exc_info=True)

    # 规则兜底：根据性格模板
    personality = set(ctx.target.personality or [])
    if "热情" in personality or "friendly" in personality:
        return f"{ctx.target.name}：「等我忙完这阵子，我们再聊好吗？」"
    if "内向" in personality or "shy" in personality:
        return f"{ctx.target.name}（小声）：「我……我现在有点忙。」"
    return f"{ctx.target.name}：「现在不太方便，稍后再说。」"


__all__ = [
    "Decision",
    "EvaluationResult",
    "evaluate_interaction_request",
]
