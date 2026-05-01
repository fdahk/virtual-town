"""
限流（阶段 18.1 / 18.3）。

实现：固定窗口计数（fixed-window counter）。MVP 下精度足够；
关键特征：

- **Redis 优先**：使用 INCR + EXPIRE；Redis 不可用时 fallback 进程内字典。
- 每 IP、每玩家各有独立 key。
- 拒绝时抛出 ``PlayerRateLimited`` 异常（错误码 ``PLAYER_RATE_LIMITED``）。

用法见 ``app.middlewares.rate_limit`` 与 ``PlayerService``。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Literal

from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)


_local_buckets: dict[str, tuple[int, float]] = {}
_local_lock = asyncio.Lock()


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after: float


class PlayerRateLimited(AppError):
    code = ErrorCode.PLAYER_RATE_LIMITED.value
    http_status = 429
    retryable = True


async def _incr_window(
    key: str,
    *,
    limit: int,
    window_seconds: float,
) -> RateLimitResult:
    """固定窗口计数，返回是否允许 + 剩余配额。"""
    if limit <= 0:
        return RateLimitResult(allowed=True, remaining=0, retry_after=0.0)

    now = time.time()

    # Redis 路径
    try:
        redis = get_redis()
        client = await redis._ensure_client()  # type: ignore[attr-defined]
        if client is not None:
            bucket_id = int(now // window_seconds)
            bkey = f"{key}:{bucket_id}"
            count = await client.incr(bkey)
            if count == 1:
                await client.expire(bkey, int(window_seconds) + 1)
            allowed = count <= limit
            remaining = max(limit - int(count), 0)
            retry_after = max(
                (bucket_id + 1) * window_seconds - now if not allowed else 0.0,
                0.0,
            )
            return RateLimitResult(
                allowed=allowed,
                remaining=remaining,
                retry_after=retry_after,
            )
    except Exception as exc:
        logger.debug("rate_limit redis failed: %s", exc)

    # fallback：进程内 fixed window
    async with _local_lock:
        count, reset_at = _local_buckets.get(key, (0, now + window_seconds))
        if now >= reset_at:
            count = 0
            reset_at = now + window_seconds
        count += 1
        _local_buckets[key] = (count, reset_at)
        allowed = count <= limit
        remaining = max(limit - count, 0)
        retry_after = max(reset_at - now if not allowed else 0.0, 0.0)
        return RateLimitResult(
            allowed=allowed,
            remaining=remaining,
            retry_after=retry_after,
        )


async def check_player_limit(
    player_id: str,
    action: Literal["talk", "interact", "generic"] = "generic",
    *,
    limit: int,
    window_seconds: float,
) -> RateLimitResult:
    return await _incr_window(
        f"rl:player:{player_id}:{action}",
        limit=limit,
        window_seconds=window_seconds,
    )


async def check_ip_limit(
    ip: str,
    *,
    limit: int,
    window_seconds: float,
) -> RateLimitResult:
    return await _incr_window(
        f"rl:ip:{ip}",
        limit=limit,
        window_seconds=window_seconds,
    )


__all__ = [
    "PlayerRateLimited",
    "RateLimitResult",
    "check_ip_limit",
    "check_player_limit",
]
