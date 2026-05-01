"""Prompt registry & metadata 单元测试（阶段 17）。"""

from __future__ import annotations

import json

import pytest

from app.prompts import (
    PROMPT_DIR,
    get_prompt,
    list_prompts,
    load,
    render_prompt,
)


REQUIRED_PROMPTS = [
    "agent_decision",
    "reflection",
    "daily_summary",
    "daily_plan",
    "task_decomposition",
    "relationship_summary",
    "query_rewrite",
    "intent_classify",
    "dialogue_reply",
]


@pytest.mark.parametrize("prompt_id", REQUIRED_PROMPTS)
def test_prompt_dir_has_metadata_and_template(prompt_id: str) -> None:
    tpl = get_prompt(prompt_id)
    assert tpl.template.strip()
    assert tpl.metadata.id == prompt_id
    assert tpl.metadata.version.startswith("v")
    # 关键字段必须齐
    assert tpl.metadata.expected_schema is not None or tpl.metadata.model_role


def test_list_prompts_contains_all() -> None:
    ids = {meta.id for meta in list_prompts()}
    for pid in REQUIRED_PROMPTS:
        assert pid in ids, f"missing prompt: {pid}"


def test_render_prompt_substitutes_placeholders() -> None:
    # agent_decision 里有 {{profile_block}}
    rendered, meta = render_prompt(
        "agent_decision",
        {
            "profile_block": "X_PROFILE",
            "state_block": "X_STATE",
            "perception_block": "X_PERC",
            "plan_block": "X_PLAN",
            "memory_block": "X_MEM",
            "tool_catalog": "X_TOOLS",
        },
    )
    for marker in ("X_PROFILE", "X_STATE", "X_PERC", "X_PLAN", "X_MEM", "X_TOOLS"):
        assert marker in rendered
    assert meta.id == "agent_decision"


def test_legacy_load_works() -> None:
    """旧代码 load('agent_decision_v1') 仍能拿到模板（从 agent_decision/v1.jinja2 回退）。"""
    text = load("agent_decision_v1")
    assert "{{profile_block}}" in text


@pytest.mark.parametrize("prompt_id", ["agent_decision", "query_rewrite", "dialogue_reply", "daily_plan"])
def test_examples_json_valid(prompt_id: str) -> None:
    ex_path = PROMPT_DIR / prompt_id / "examples.json"
    if not ex_path.exists():
        pytest.skip(f"{prompt_id} has no examples.json yet")
    data = json.loads(ex_path.read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    assert cases, f"{prompt_id} examples must be non-empty"
    for c in cases:
        assert "case_name" in c
        assert "input" in c
