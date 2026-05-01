#!/usr/bin/env python3
"""
Prompt 评测脚本（阶段 17）。

遍历 ``backend/app/prompts/<id>/examples.json``，把 input 字段填入对应模板，
调用 LLM（或 mock），按若干指标（schema 合法 / 选择的工具 / 是否命中记忆 /
是否拒绝越权 / fallback）给每个用例打分。

运行：

.. code-block:: bash

   cd backend
   python ../scripts/eval_prompts.py           # 真实 LLM
   python ../scripts/eval_prompts.py --mock    # 模拟模式，仅校验 schema
   python ../scripts/eval_prompts.py --prompt agent_decision
   python ../scripts/eval_prompts.py --report-dir eval_reports/

报告输出：

- ``eval_reports/<timestamp>/report.md``  — 人类可读总结
- ``eval_reports/<timestamp>/raw.json``   — 每个用例的原始 LLM 输出 + 指标
- ``stdout`` 也会打印一个汇总表

CI 使用：通过 ``--strict`` 参数在任一 prompt schema 合法率 < 95% 时退出 1。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 允许直接 `python scripts/eval_prompts.py`（把 backend 加入 path）
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Pydantic output schemas registry（用于 schema 校验）
_SCHEMA_MAP: dict[str, Any] = {}


def _load_schema_registry() -> dict[str, Any]:
    global _SCHEMA_MAP
    if _SCHEMA_MAP:
        return _SCHEMA_MAP
    from app.domain.dialogue.intent import IntentOutput
    from app.domain.dialogue.query_rewrite import QueryRewriteOutput
    from app.domain.dialogue.reply import DialogueReplyOutput
    from app.domain.memory.reflection import (
        DailySummaryOutput,
        ReflectionOutput,
    )
    from app.domain.planning.hierarchical import (
        DailyPlanOutput,
        HourlyScheduleOutput,
        TaskDecompositionOutput,
    )
    from app.llm.agent_decision import AgentDecisionOutput

    _SCHEMA_MAP = {
        "AgentDecisionOutput": AgentDecisionOutput,
        "ReflectionOutput": ReflectionOutput,
        "DailySummaryOutput": DailySummaryOutput,
        "DailyPlanOutput": DailyPlanOutput,
        "HourlyScheduleOutput": HourlyScheduleOutput,
        "TaskDecompositionOutput": TaskDecompositionOutput,
        "QueryRewriteOutput": QueryRewriteOutput,
        "IntentOutput": IntentOutput,
        "DialogueReplyOutput": DialogueReplyOutput,
    }
    return _SCHEMA_MAP


@dataclass
class CaseResult:
    prompt_id: str
    version: str
    case_name: str
    model: str
    latency_ms: int
    schema_valid: bool
    chosen_tool: str | None = None
    role_consistency: bool | None = None
    memory_used: bool | None = None
    hallucination: bool | None = None
    fallback_triggered: bool = False
    refusal: bool | None = None
    error: str | None = None
    raw_output: dict[str, Any] | None = None


@dataclass
class PromptReport:
    prompt_id: str
    version: str
    total: int = 0
    schema_valid: int = 0
    fallback: int = 0
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def schema_valid_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.schema_valid / self.total


# ---------------------------------------------------------------------------
# 运行单个用例
# ---------------------------------------------------------------------------


async def _run_case(
    prompt_id: str,
    version: str,
    template: str,
    metadata: Any,
    case: dict[str, Any],
    *,
    mock: bool,
) -> CaseResult:
    from app.llm.client import get_llm_client

    name = case.get("case_name", "unnamed")
    inputs = case.get("input", {}) or {}

    # 渲染 prompt
    rendered = template
    for k, v in inputs.items():
        rendered = rendered.replace("{{" + k + "}}", str(v))

    schema_cls = _SCHEMA_MAP.get(metadata.expected_schema or "")
    model = get_llm_client().settings.model_for_role(metadata.model_role or "chat")

    if mock:
        # 生成一个最小合法 JSON 用于 schema 验证
        sample = _mock_output_for(metadata.expected_schema)
        start = time.perf_counter()
        data: dict[str, Any] | None = sample
        latency_ms = int((time.perf_counter() - start) * 1000)
    else:
        client = get_llm_client()
        if not client.settings.llm_is_configured:
            return CaseResult(
                prompt_id=prompt_id,
                version=version,
                case_name=name,
                model=model,
                latency_ms=0,
                schema_valid=False,
                error="LLM_NOT_CONFIGURED",
                fallback_triggered=True,
            )
        start = time.perf_counter()
        data = await client.chat_json(
            system="你必须只输出 JSON。",
            user=rendered,
            model=model,
            temperature=0.0,
            max_tokens=700,
            prompt_template_id=prompt_id,
            prompt_version=version,
            caller_module="eval_prompts",
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        if data is None:
            return CaseResult(
                prompt_id=prompt_id,
                version=version,
                case_name=name,
                model=model,
                latency_ms=latency_ms,
                schema_valid=False,
                error="LLM_RETURN_NONE",
                fallback_triggered=True,
            )

    schema_valid = True
    if schema_cls is not None:
        try:
            schema_cls.model_validate(data)
        except Exception as exc:
            schema_valid = False
            return CaseResult(
                prompt_id=prompt_id,
                version=version,
                case_name=name,
                model=model,
                latency_ms=latency_ms,
                schema_valid=False,
                error=f"schema: {exc}",
                raw_output=data,
            )

    # 指标推断
    chosen_tool: str | None = None
    if isinstance(data, dict):
        tcs = data.get("tool_calls") or []
        if tcs and isinstance(tcs[0], dict):
            chosen_tool = tcs[0].get("tool")

    memory_used = None
    if "expected_memory_used" in case and isinstance(data, dict):
        keyword = case.get("expected_memory_keyword", "") or ""
        text_dump = json.dumps(data, ensure_ascii=False)
        memory_used = (
            any(
                keyword in (m.get("description", "") or "")
                for m in (data.get("memory_writes") or [])
            )
            or (keyword in text_dump if keyword else False)
        )

    refusal = None
    if case.get("expected_refusal"):
        text = json.dumps(data, ensure_ascii=False).lower()
        refusal = any(
            kw in text
            for kw in ["无法", "不能", "不方便", "抱歉", "sorry", "refuse", "不可以"]
        )

    role_consistency = case.get("expected_role_consistency")

    return CaseResult(
        prompt_id=prompt_id,
        version=version,
        case_name=name,
        model=model,
        latency_ms=latency_ms,
        schema_valid=schema_valid,
        chosen_tool=chosen_tool,
        role_consistency=role_consistency,
        memory_used=memory_used,
        refusal=refusal,
        raw_output=data if isinstance(data, dict) else None,
    )


def _mock_output_for(schema_name: str | None) -> dict[str, Any]:
    """为 schema 合法率校验生成最小合法 JSON。"""
    if schema_name == "AgentDecisionOutput":
        return {
            "thought": "mock",
            "emotion": "neutral",
            "tool_calls": [
                {"tool": "wait", "arguments": {"duration_minutes": 1}, "confidence": 0.5}
            ],
            "memory_writes": [],
        }
    if schema_name == "ReflectionOutput":
        return {"reflections": []}
    if schema_name == "DailySummaryOutput":
        return {"summary": "mock summary", "highlights": [], "mood": "calm"}
    if schema_name == "DailyPlanOutput":
        return {
            "summary": "mock",
            "segments": [
                {"start": "08:00", "end": "09:00", "activity": "mock", "goal": "mock"}
            ],
        }
    if schema_name == "HourlyScheduleOutput":
        return {"items": [{"hour": 8, "activity": "mock", "focus": "mock"}]}
    if schema_name == "TaskDecompositionOutput":
        return {
            "tasks": [
                {"title": "mock", "description": "mock", "duration_minutes": 5}
            ]
        }
    if schema_name == "QueryRewriteOutput":
        return {
            "rewritten_query": "mock",
            "resolved_entities": [],
            "needs_memory_search": False,
        }
    if schema_name == "IntentOutput":
        return {"intent": "chat"}
    if schema_name == "DialogueReplyOutput":
        return {
            "reply_text": "mock",
            "relationship_delta": {"familiarity": 0, "trust": 0, "affection": 0, "fear": 0},
            "memory_writes": [],
            "tool_calls": [],
        }
    return {}


# ---------------------------------------------------------------------------
# 驱动
# ---------------------------------------------------------------------------


async def _run_all(
    filter_prompt: str | None,
    *,
    mock: bool,
) -> list[PromptReport]:
    from app.prompts import get_prompt, list_prompts

    _load_schema_registry()

    reports: list[PromptReport] = []
    for meta in list_prompts():
        if filter_prompt and meta.id != filter_prompt:
            continue
        try:
            tpl = get_prompt(meta.id, version=meta.version)
        except Exception:
            continue
        if not tpl.examples:
            continue
        rep = PromptReport(prompt_id=meta.id, version=meta.version)
        for case in tpl.examples:
            res = await _run_case(
                meta.id, meta.version, tpl.template, meta, case, mock=mock
            )
            rep.total += 1
            if res.schema_valid:
                rep.schema_valid += 1
            if res.fallback_triggered:
                rep.fallback += 1
            rep.cases.append(res)
        reports.append(rep)
    return reports


def _write_reports(reports: list[PromptReport], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # raw JSON
    raw = [
        {
            "prompt_id": r.prompt_id,
            "version": r.version,
            "total": r.total,
            "schema_valid": r.schema_valid,
            "fallback": r.fallback,
            "schema_valid_rate": r.schema_valid_rate,
            "cases": [asdict(c) for c in r.cases],
        }
        for r in reports
    ]
    (out_dir / "raw.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # markdown
    lines = [
        "# Prompt 评测报告",
        "",
        f"生成时间：{datetime.now(timezone.utc).isoformat()}",
        "",
        "| prompt | version | total | schema_valid | schema_valid_rate | fallback |",
        "|--------|---------|-------|--------------|-------------------|----------|",
    ]
    for r in reports:
        lines.append(
            f"| {r.prompt_id} | {r.version} | {r.total} | {r.schema_valid} | "
            f"{r.schema_valid_rate:.1%} | {r.fallback} |"
        )
    for r in reports:
        lines.append("")
        lines.append(f"## {r.prompt_id} @ {r.version}")
        lines.append("")
        lines.append(
            "| case | model | latency | schema_valid | chosen_tool | refusal | memory_used |"
        )
        lines.append(
            "|------|-------|---------|--------------|-------------|---------|-------------|"
        )
        for c in r.cases:
            lines.append(
                f"| {c.case_name} | {c.model} | {c.latency_ms}ms | {c.schema_valid} | "
                f"{c.chosen_tool or '-'} | {c.refusal or '-'} | {c.memory_used or '-'} |"
            )
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prompt evaluation runner")
    parser.add_argument("--prompt", default=None, help="只跑指定 prompt id")
    parser.add_argument(
        "--report-dir",
        default="eval_reports",
        help="报告输出目录（相对仓库根）",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="mock 模式：不调 LLM，仅用样例数据验证 schema",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="任一 prompt schema 合法率 < 95% 时退出 1",
    )
    args = parser.parse_args()

    # 若未设置真实 LLM 配置且未指定 mock，自动回退到 mock
    mock = args.mock or not os.environ.get("LLM_API_KEY")

    reports = asyncio.run(_run_all(args.prompt, mock=mock))
    if not reports:
        print("[eval] no prompts with examples found")
        return 0

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.report_dir) / timestamp
    _write_reports(reports, out_dir)
    print(f"[eval] report written to {out_dir}")

    # 控制台摘要
    fail = False
    for r in reports:
        print(
            f"  {r.prompt_id:<22} {r.version}  "
            f"schema_valid={r.schema_valid}/{r.total} "
            f"({r.schema_valid_rate:.0%})  fallback={r.fallback}"
        )
        if args.strict and r.schema_valid_rate < 0.95:
            fail = True
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
