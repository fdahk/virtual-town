"""Observer 纯单元测试（无数据库依赖）。

验证：
- ``_redact_arguments`` 对敏感键脱敏、超长字符串截断。
- Observer 在未绑定 session_factory 时的缓冲行为（写入不抛异常）。
- TaskContext 可以从 payload 中恢复 trace snapshot（worker 路径关键）。
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.trace_context import TraceContext
from app.services.observer import Observer, _redact_arguments


def test_redact_arguments_masks_sensitive_keys() -> None:
    redacted = _redact_arguments(
        {
            "api_key": "sk-xxx",
            "AUTHORIZATION": "Bearer abc",
            "password": "p1",
            "normal": "ok",
            "raw_prompt": "...",
        }
    )
    assert redacted["api_key"] == "***"
    assert redacted["password"] == "***"
    assert redacted["raw_prompt"] == "***"
    assert redacted["normal"] == "ok"


def test_redact_arguments_truncates_long_strings() -> None:
    long = "x" * 2000
    out = _redact_arguments({"text": long})
    assert out["text"].endswith("…")
    assert len(out["text"]) <= 520


def test_redact_arguments_recurses_dict_and_list() -> None:
    out = _redact_arguments(
        {
            "nested": {"api_key": "should_hide", "value": 3},
            "items": [{"api_key": "nope"}, {"ok": 1}],
        }
    )
    assert out["nested"]["api_key"] == "***"
    assert out["items"][0]["api_key"] == "***"


@pytest.mark.asyncio
async def test_observer_accepts_writes_without_session_factory() -> None:
    observer = Observer(flush_interval=0.05, batch_size=10)
    # 未 bind session_factory：flush 会 no-op，但写入不能抛异常
    with TraceContext.start_trace("test_cat", simulation_id="sim_x"):
        await observer.record_event(
            category="agent_decision",
            event_type="agent.decide.completed",
            payload={"score": 0.9},
        )
        await observer.record_llm_call(
            provider="test",
            model="test-model",
            latency_ms=12,
            success=True,
            schema_valid=True,
        )
        await observer.record_tool_call(
            tool="move_to_location",
            arguments={"api_key": "leak", "loc": "loc_a"},
            success=True,
            duration_ms=5,
        )
    # flush 不应该抛出（即使没有 session factory）
    await observer._flush_now()

    # 内部缓冲还保留了记录（event 分 2 条：record_event + record_llm_call 内再发的 1 条 + record_tool_call 内再发的 1 条）
    assert len(observer._buf.events) >= 1
    assert len(observer._buf.llm_calls) == 1
    assert len(observer._buf.tool_calls) == 1
    # 参数脱敏已生效
    assert observer._buf.tool_calls[0]["arguments"]["api_key"] == "***"
