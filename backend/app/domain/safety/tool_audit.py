"""
Tool calling 权限越权审计（阶段 18.2）。

每次 LLM 调用被 ``ToolExecutor`` 拒绝（permission_denied / invalid_args /
world_state_invalid / not_found）时，把"这次拒绝"计入 Redis 滑窗计数。

达到阈值时：
- 通过 Observer 记录 ``security.tool_escalation`` 事件；
- 让调用方（agent_decision / reply）临时降低 temperature 或改走规则兜底。

MVP 中阈值与窗口从 ``Settings.security_tool_violation_*`` 读。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)


def _violation_key(agent_id: str) -> str:
    return f"security:tool_violation:{agent_id}"


@dataclass
class ToolViolationStatus:
    count: int
    escalated: bool
    threshold: int


async def record_tool_violation(
    *,
    agent_id: str,
    tool: str,
    error_code: str,
    reason: str | None = None,
) -> ToolViolationStatus:
    """记录一次拒绝，并返回是否触发告警。"""
    settings = get_settings()
    threshold = int(settings.security_tool_violation_threshold or 5)
    window = float(settings.security_tool_violation_window_seconds or 300.0)

    count = 0
    escalated = False
    try:
        redis = get_redis()
        client = await redis._ensure_client()  # type: ignore[attr-defined]
        if client is not None:
            count = int(await client.incr(_violation_key(agent_id)))
            if count == 1:
                await client.expire(_violation_key(agent_id), int(window))
            escalated = count >= threshold
    except Exception as exc:
        logger.debug("tool violation redis failed: %s", exc)

    try:
        from app.services.observer import CATEGORY_ERROR, get_observer

        await get_observer().record_event(
            category=CATEGORY_ERROR,
            event_type="security.tool_violation"
            + (".escalated" if escalated else ""),
            title=f"tool denied: {tool}",
            level="WARNING" if escalated else "INFO",
            entity_id=agent_id,
            payload={
                "tool": tool,
                "error_code": error_code,
                "reason": reason,
                "count": count,
                "threshold": threshold,
            },
        )
    except Exception:
        pass

    return ToolViolationStatus(count=count, escalated=escalated, threshold=threshold)


async def reset_violations(agent_id: str) -> None:
    try:
        await get_redis().delete(_violation_key(agent_id))
    except Exception:
        pass


__all__ = [
    "ToolViolationStatus",
    "record_tool_violation",
    "reset_violations",
]
