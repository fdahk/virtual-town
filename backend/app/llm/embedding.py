"""
嵌入服务。

行为：
- 若 LLM 可用：调用 LLMClient.embed，使用真实向量。
- 若 LLM 不可用：返回 None，记忆检索退化为关键字匹配。
- Redis 缓存：相同文本（sha256 前 16 字节为 key）命中缓存时直接返回，
  TTL 1 小时，避免对相同内容重复调用 Embedding API。
  缓存不可用时静默降级，不影响主流程。
"""

from __future__ import annotations

import hashlib
import json

from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.client import get_llm_client

logger = get_logger(__name__)

_EMBED_CACHE_TTL = 3600  # 1 小时


def _text_hash(text: str) -> str:
    """取 SHA-256 的前 32 个十六进制字符作为缓存 key 后缀，兼顾唯一性与 key 长度。"""
    return hashlib.sha256(text.encode()).hexdigest()[:32]


class EmbeddingService:
    async def embed(self, text: str) -> list[float] | None:
        settings = get_settings()
        if not settings.llm_is_configured:
            return None

        # 先查 Redis 缓存
        cached = await self._cache_get(text)
        if cached is not None:
            return cached

        vec = await get_llm_client().embed(text)

        # 写回缓存（best-effort）
        if vec is not None:
            await self._cache_set(text, vec)

        return vec

    async def _cache_get(self, text: str) -> list[float] | None:
        """从 Redis 读取 embedding 缓存，失败返回 None。"""
        try:
            from app.core.redis_client import get_redis, key_embedding_cache

            raw = await get_redis().get_json(key_embedding_cache(_text_hash(text)))
            if isinstance(raw, list):
                return raw
        except Exception as exc:
            logger.debug("embedding cache get failed: %s", exc)
        return None

    async def _cache_set(self, text: str, vec: list[float]) -> None:
        """把 embedding 结果写入 Redis 缓存（best-effort）。"""
        try:
            from app.core.redis_client import get_redis, key_embedding_cache

            await get_redis().set_json(
                key_embedding_cache(_text_hash(text)),
                vec,
                ttl_seconds=_EMBED_CACHE_TTL,
            )
        except Exception as exc:
            logger.debug("embedding cache set failed: %s", exc)


_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service
