"""
LLM 客户端。

- 使用 OpenAI 兼容协议（DashScope、OpenAI、DeepSeek 都支持）。
- 未配置 API key 或调用失败时返回 None，调用方走规则 fallback。
- JSON Schema 化输出通过 `response_format={"type": "json_object"}` + prompt 双重约束。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LLMError(RuntimeError):
    """LLM 调用失败的封装错误。"""


class LLMClient:
    """最小 OpenAI 兼容客户端。"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient | None:
        if not self.settings.llm_is_configured:
            return None
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.settings.llm_base_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {self.settings.llm_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.settings.llm_chat_timeout_seconds,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
        json_schema_hint: str | None = None,
    ) -> dict[str, Any] | None:
        """
        请求模型返回 JSON；失败时返回 None。
        - 调用方需要自行做 Pydantic 校验。
        - 始终允许模型返回包含额外字段的 JSON 以增强兼容性。
        """
        client = self._ensure_client()
        if client is None:
            return None

        if json_schema_hint:
            user_content = (
                f"{user}\n\n"
                "请严格按以下 JSON Schema 描述输出合法 JSON，不要输出任何解释或 markdown：\n"
                f"{json_schema_hint}"
            )
        else:
            user_content = user

        payload = {
            "model": model or self.settings.llm_chat_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        }

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.settings.llm_max_retries + 1),
                wait=wait_exponential(multiplier=0.5, max=4),
                retry=retry_if_exception_type(
                    (httpx.TimeoutException, httpx.TransportError)
                ),
                reraise=True,
            ):
                with attempt:
                    resp = await client.post("/chat/completions", json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    return json.loads(content)
        except Exception as exc:
            logger.warning("LLM chat_json failed: %s", exc)
            return None

    async def embed(self, text: str) -> list[float] | None:
        client = self._ensure_client()
        if client is None:
            return None
        try:
            resp = await client.post(
                "/embeddings",
                json={"model": self.settings.llm_embedding_model, "input": text},
            )
            resp.raise_for_status()
            data = resp.json()
            vector = data["data"][0]["embedding"]
            expected_dim = self.settings.llm_embedding_dim
            if len(vector) != expected_dim:
                logger.warning(
                    "embedding dim mismatch: got %d expected %d", len(vector), expected_dim
                )
                return None
            return vector
        except Exception as exc:
            logger.warning("LLM embed failed: %s", exc)
            return None


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
