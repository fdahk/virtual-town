"""TraceContext 与 ErrorCode 基础单元测试（阶段 14.1）。"""

from __future__ import annotations

import pytest

from app.core.errors import AppError, ErrorCode, TaskDuplicate
from app.core.trace_context import TraceContext, current


def test_trace_and_span_nesting() -> None:
    # 无外层 trace 时读取到 None
    snap = current()
    assert snap.trace_id is None
    assert snap.span_id is None

    with TraceContext.start_trace("world_tick", simulation_id="sim_test") as s1:
        assert s1.trace_id is not None
        assert s1.span_id is not None
        assert s1.parent_span_id is None
        assert s1.simulation_id == "sim_test"
        assert s1.category == "world_tick"
        trace_id = s1.trace_id

        with TraceContext.span("agent.perceive", agent_id="npc_a"):
            inner = current()
            assert inner.trace_id == trace_id
            assert inner.span_id != s1.span_id
            assert inner.parent_span_id == s1.span_id
            assert inner.agent_id == "npc_a"

        # 离开子 span 后 span_id 恢复
        after = current()
        assert after.span_id == s1.span_id
        assert after.agent_id is None  # attrs 覆盖已恢复

    # 离开 trace 后全部清空
    final = current()
    assert final.trace_id is None
    assert final.simulation_id is None


def test_trace_snapshot_apply_round_trip() -> None:
    with TraceContext.start_trace("task.agent_decision", simulation_id="sim_x", agent_id="a_x") as snap:
        payload = snap.as_log_extra()
    # 应用一份独立 snapshot 到当前 ctx
    from app.core.trace_context import TraceSnapshot

    ts = TraceSnapshot(**{k: v for k, v in payload.items() if k != "extra"})
    with TraceContext.apply(ts):
        recovered = current()
        assert recovered.trace_id == snap.trace_id
        assert recovered.simulation_id == "sim_x"
        assert recovered.agent_id == "a_x"


def test_error_code_taxonomy_prefixes() -> None:
    # 每个错误码都必须带模块前缀（或 SYSTEM 兜底）
    allowed_prefixes = (
        "WORLD_",
        "AGENT_",
        "MEMORY_",
        "LLM_",
        "TOOL_",
        "PLAYER_",
        "WS_",
        "DB_",
        "TASK_",
        # SYSTEM 沿用旧值无前缀
        "INTERNAL_ERROR",
        "INVALID_ARGUMENTS",
        "PERMISSION_DENIED",
        "TARGET_NOT_FOUND",
        "TARGET_NOT_REACHABLE",
        "OUT_OF_RANGE",
        "STATE_CONFLICT",
    )
    for code in ErrorCode:
        assert any(
            code.value.startswith(prefix) or code.value == prefix
            for prefix in allowed_prefixes
        ), f"error code {code.value} has no module prefix"


def test_app_error_trace_id_in_dict() -> None:
    # AppError.to_dict() 在 trace 激活时应携带 trace_id
    with TraceContext.start_trace("http.get"):
        err = TaskDuplicate("dup task", details={"task_id": "x"})
        d = err.to_dict()
        assert d["code"] == ErrorCode.TASK_DUPLICATE.value
        assert d["details"] == {"task_id": "x"}
        assert d["retryable"] is False
        assert d.get("trace_id", "").startswith("trace_")
