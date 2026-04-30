"""
LLM 客户端。

- 使用 OpenAI 兼容协议（DashScope、OpenAI、DeepSeek 都支持）。
- 未配置 API key 或调用失败时返回 None，调用方走规则 fallback。
- JSON Schema 化输出通过 `response_format={"type": "json_object"}` + prompt 双重约束。
- 每次调用经 ``Observer.record_llm_call`` 审计（provider / model / latency / 重试 /
  schema 合法率 / fallback），不记录 API Key 和完整 prompt。
"""

from __future__ import annotations

import json
import time
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
from app.core.trace_context import TraceContext

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
        prompt_template_id: str | None = None,
        prompt_version: str | None = None,
        caller_module: str | None = None,
    ) -> dict[str, Any] | None:
        """
        请求模型返回 JSON；失败时返回 None。
        - 调用方需要自行做 Pydantic 校验。
        - 始终允许模型返回包含额外字段的 JSON 以增强兼容性。
        - 调用过程全程经 Observer 审计，字段见 ``record_llm_call``。
        """
        model_id = model or self.settings.llm_chat_model

        client = self._ensure_client()
        if client is None:
            await self._record(
                model=model_id,
                success=False,
                latency_ms=0,
                error_code="LLM_NOT_CONFIGURED",
                input_summary=user[:256],
                prompt_template_id=prompt_template_id,
                prompt_version=prompt_version,
                caller_module=caller_module,
                schema_valid=None,
            )
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
            "model": model_id,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        }

        attempt_count = 0
        started = time.perf_counter()
        with TraceContext.span("llm.generate_reply"):
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
                        attempt_count += 1
                        resp = await client.post("/chat/completions", json=payload)
                        resp.raise_for_status()
                        data = resp.json()
                        content = data["choices"][0]["message"]["content"]
                        try:
                            result = json.loads(content)
                        except json.JSONDecodeError as jsex:
                            latency_ms = int((time.perf_counter() - started) * 1000)
                            await self._record(
                                model=model_id,
                                success=False,
                                latency_ms=latency_ms,
                                retry_count=attempt_count - 1,
                                error_code="LLM_RETURN_INVALID",
                                input_summary=user_content[:256],
                                prompt_template_id=prompt_template_id,
                                prompt_version=prompt_version,
                                caller_module=caller_module,
                                schema_valid=False,
                            )
                            logger.warning("LLM returned non-json: %s", jsex)
                            return None
                        latency_ms = int((time.perf_counter() - started) * 1000)
                        await self._record(
                            model=model_id,
                            success=True,
                            latency_ms=latency_ms,
                            retry_count=attempt_count - 1,
                            error_code=None,
                            input_summary=user_content[:256],
                            prompt_template_id=prompt_template_id,
                            prompt_version=prompt_version,
                            caller_module=caller_module,
                            schema_valid=True,
                        )
                        return result
            except httpx.TimeoutException as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                await self._record(
                    model=model_id,
                    success=False,
                    latency_ms=latency_ms,
                    retry_count=attempt_count,
                    error_code="LLM_TIMEOUT",
                    input_summary=user_content[:256],
                    prompt_template_id=prompt_template_id,
                    prompt_version=prompt_version,
                    caller_module=caller_module,
                    schema_valid=None,
                )
                logger.warning("LLM chat_json timeout: %s", exc)
                return None
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                await self._record(
                    model=model_id,
                    success=False,
                    latency_ms=latency_ms,
                    retry_count=attempt_count,
                    error_code="LLM_UPSTREAM_ERROR",
                    input_summary=user_content[:256],
                    prompt_template_id=prompt_template_id,
                    prompt_version=prompt_version,
                    caller_module=caller_module,
                    schema_valid=None,
                )
                logger.warning("LLM chat_json failed: %s", exc)
                return None
        return None

    async def embed(self, text: str) -> list[float] | None:
        client = self._ensure_client()
        if client is None:
            return None
        started = time.perf_counter()
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
                await self._record(
                    model=self.settings.llm_embedding_model,
                    success=False,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    error_code="MEMORY_EMBEDDING_FAILED",
                    input_summary=text[:128],
                    prompt_template_id="embedding",
                    prompt_version="1",
                    caller_module="embedding",
                    schema_valid=None,
                )
                return None
            await self._record(
                model=self.settings.llm_embedding_model,
                success=True,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code=None,
                input_summary=text[:128],
                prompt_template_id="embedding",
                prompt_version="1",
                caller_module="embedding",
                schema_valid=None,
            )
            return vector
        except Exception as exc:
            logger.warning("LLM embed failed: %s", exc)
            await self._record(
                model=self.settings.llm_embedding_model,
                success=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="LLM_UPSTREAM_ERROR",
                input_summary=text[:128],
                prompt_template_id="embedding",
                prompt_version="1",
                caller_module="embedding",
                schema_valid=None,
            )
            return None

    async def _record(
        self,
        *,
        model: str,
        success: bool,
        latency_ms: int,
        error_code: str | None,
        input_summary: str,
        prompt_template_id: str | None,
        prompt_version: str | None,
        caller_module: str | None,
        schema_valid: bool | None,
        retry_count: int = 0,
    ) -> None:
        try:
            from app.services.observer import get_observer

            await get_observer().record_llm_call(
                provider=self.settings.llm_provider,
                model=model,
                prompt_template_id=prompt_template_id,
                prompt_version=prompt_version,
                caller_module=caller_module,
                input_summary=input_summary,
                latency_ms=latency_ms,
                retry_count=retry_count,
                success=success,
                schema_valid=schema_valid,
                fallback_used=not success,
                error_code=error_code,
            )
        except Exception:
            # 观测失败绝不能影响业务链路
            logger.debug("observer record_llm_call failed", exc_info=True)


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
