"""
多轮对话窗口存储（阶段 16.3）。

Redis key：``dialogue:{conversation_id}:recent_messages``，TTL 30 分钟。
每条消息是一个 JSON：{speaker, speaker_id, text, emotion, created_at}。

- 每次 `append` 会把新消息 lpush 到 head，并 ltrim 到 20 条内。
- `recent(limit=6)` 返回按时间顺序（旧 → 新）的列表，供 prompt 拼装。
- Redis 不可用时这里退化为进程内 deque（仅当前进程可见），避免对话链断掉。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.core.redis_client import get_redis, key_dialogue_recent

logger = get_logger(__name__)


CONVERSATION_WINDOW_SIZE = 20
CONVERSATION_TTL_SECONDS = 60 * 30  # 30 分钟


class ConversationWindow:
    """维护单个对话的最近 N 条消息。"""

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id


class _LocalFallback:
    """Redis 不可用时的进程内兜底。"""

    def __init__(self) -> None:
        self._store: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=CONVERSATION_WINDOW_SIZE)
        )
        self._lock = asyncio.Lock()

    async def append(self, key: str, item: dict[str, Any]) -> None:
        async with self._lock:
            self._store[key].appendleft(item)

    async def recent(self, key: str, limit: int) -> list[dict[str, Any]]:
        async with self._lock:
            items = list(self._store.get(key, deque()))[:limit]
        # 旧 → 新
        return list(reversed(items))


class ConversationStore:
    def __init__(self) -> None:
        self._local = _LocalFallback()

    async def append(
        self,
        conversation_id: str,
        *,
        speaker: str,
        speaker_id: str,
        text: str,
        emotion: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "speaker": speaker,
            "speaker_id": speaker_id,
            "text": text,
            "emotion": emotion,
            "created_at": now,
            "meta": meta or {},
        }
        key = key_dialogue_recent(conversation_id)
        ok = False
        try:
            ok = await get_redis().list_push(
                key,
                item,
                max_len=CONVERSATION_WINDOW_SIZE,
                ttl_seconds=CONVERSATION_TTL_SECONDS,
            )
        except Exception as exc:
            logger.debug("redis conversation append failed: %s", exc)
        if not ok:
            await self._local.append(key, item)

    async def recent(
        self,
        conversation_id: str,
        *,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        key = key_dialogue_recent(conversation_id)
        items: list[dict[str, Any]] = []
        try:
            items = await get_redis().list_range(key, limit=limit)
        except Exception as exc:
            logger.debug("redis conversation recent failed: %s", exc)
        if items:
            # redis lrange 从 head 返回（最新在前），反转为 旧→新
            return list(reversed(items))
        return await self._local.recent(key, limit)


_store: ConversationStore | None = None


def get_conversation_store() -> ConversationStore:
    global _store
    if _store is None:
        _store = ConversationStore()
    return _store


__all__ = [
    "CONVERSATION_TTL_SECONDS",
    "CONVERSATION_WINDOW_SIZE",
    "ConversationStore",
    "ConversationWindow",
    "get_conversation_store",
]
