"""Tool Calling 基础单元测试（不依赖数据库）。"""

from __future__ import annotations

import pytest

from app.llm.tools import get_tool_registry


def test_tools_registered():
    reg = get_tool_registry()
    names = {t.spec().name for t in reg.all()}
    expected = {
        "move_to_location",
        "move_to_entity",
        "interact_with_object",
        "avoid_danger",
        "face_entity",
        "write_memory",
        "search_memory",
        "react_to_pet",
        "make_sound",
        "update_emotion",
        "wait",
        # 阶段 19：社交 + 生活类工具
        "request_interaction",
        "socialize",
        "end_chat",
        "work_at_location",
        "have_meal",
        "rest_at",
        "browse_shop",
        "observe_environment",
    }
    assert expected.issubset(names)
    # 阶段 19：旧的 talk_to_entity 已被 request_interaction 替代
    assert "talk_to_entity" not in names


def test_entity_filtering():
    reg = get_tool_registry()
    human = {s.name for s in reg.list_specs("human")}
    animal = {s.name for s in reg.list_specs("animal")}
    # 动物不能调交互物体（需要手）
    assert "interact_with_object" in human
    assert "interact_with_object" not in animal
    # 动物专属
    assert "react_to_pet" in animal
    assert "react_to_pet" not in human
    # 人类专属（社交 + 生活）
    assert "request_interaction" in human
    assert "request_interaction" not in animal
    assert "socialize" in human
    assert "work_at_location" in human


def test_openai_functions_shape():
    reg = get_tool_registry()
    fns = reg.openai_functions("human")
    assert fns and all("function" in fn and "name" in fn["function"] for fn in fns)
    # 校验 parameters_schema 有 required / properties
    for fn in fns:
        props = fn["function"]["parameters"].get("properties")
        assert isinstance(props, dict)


def test_prompt_catalog_human_readable():
    catalog = get_tool_registry().prompt_catalog("human")
    assert "move_to_location" in catalog
    assert "request_interaction" in catalog
