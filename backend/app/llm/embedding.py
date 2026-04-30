"""
嵌入服务。

MVP 行为：
- 若 LLM 可用：调用 LLMClient.embed，使用真实向量。
- 若 LLM 不可用：返回 None，记忆检索退化为关键字匹配。
- 未来可缓存、可批处理。
"""

from __future__ import annotations

from app.core.config import get_settings
from app.llm.client import get_llm_client


class EmbeddingService:
    async def embed(self, text: str) -> list[float] | None:
        settings = get_settings()
        if not settings.llm_is_configured:
            return None
        return await get_llm_client().embed(text)


_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service
