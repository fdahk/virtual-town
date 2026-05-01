"""
对话处理单元测试（阶段 16）。

聚焦于不依赖数据库 / LLM 的规则路径：

- query_rewrite 的规则兜底（代词消解、needs_memory_search）
- intent classify 规则
- dialogue reply 规则兜底 schema
"""

from __future__ import annotations

import pytest

from app.domain.dialogue.intent import classify_intent
from app.domain.dialogue.query_rewrite import _rule_rewrite
from app.domain.dialogue.reply import (
    DialogueReplyOutput,
    RelationshipDelta,
    _rule_reply,
)
from app.schemas.agent import AgentAppearance, AgentProfile


def _profile(name: str = "小芳", personality=None) -> AgentProfile:
    return AgentProfile(
        id="npc_test",
        entity_type="human",
        name=name,
        appearance=AgentAppearance(),
        personality=personality or ["热情"],
        background="",
    )


def test_rule_rewrite_memory_hint() -> None:
    out = _rule_rewrite(
        "你还记得昨天的事吗？",
        recent_dialogue=[],
        scene_entities=[],
    )
    assert out.needs_memory_search is True
    assert out.intent_hint == "ask_memory"


def test_rule_rewrite_pronoun_resolution() -> None:
    # 上一轮里 NPC 提到小王的消息
    dialogue = [
        {"speaker": "player", "speaker_id": "player_1", "text": "小王今天会来吗？"},
        {"speaker": "npc", "speaker_id": "npc_xiaowang", "text": "嗯，我应该会来。"},
    ]
    out = _rule_rewrite(
        "他喜欢喝什么？",
        recent_dialogue=dialogue,
        scene_entities=[("npc_xiaowang", "小王")],
    )
    assert out.resolved_entities
    assert out.resolved_entities[0].entity_id == "npc_xiaowang"
    assert out.needs_memory_search is True


def test_rule_rewrite_no_pronoun_no_memory() -> None:
    out = _rule_rewrite(
        "早安！",
        recent_dialogue=[],
        scene_entities=[],
    )
    assert out.needs_memory_search is False
    assert not out.resolved_entities


@pytest.mark.asyncio
async def test_rule_intent_location() -> None:
    # 无 LLM：走规则
    out = await classify_intent(raw_text="图书馆在哪里？")
    assert out.intent == "ask_location"


@pytest.mark.asyncio
async def test_rule_intent_pet_animal() -> None:
    out = await classify_intent(raw_text="过来，让我摸摸你", target_entity_type="animal")
    assert out.intent == "pet_animal"


@pytest.mark.asyncio
async def test_rule_intent_threaten() -> None:
    out = await classify_intent(raw_text="你小心点，别惹我")
    assert out.intent == "threaten"
    assert out.urgency == "high"


def test_rule_reply_fallback_schema() -> None:
    profile = _profile("小芳", personality=["热情"])
    out = _rule_reply(profile, "阿德", "你还好吗？", memories=[])
    assert isinstance(out, DialogueReplyOutput)
    assert out.reply_text
    assert isinstance(out.relationship_delta, RelationshipDelta)
    # 白名单过滤：规则兜底没有 tool_calls
    assert out.tool_calls == []


def test_rule_reply_uses_memory() -> None:
    profile = _profile("小芳", personality=["热情"])
    out = _rule_reply(
        profile,
        "阿德",
        "小王喜欢喝什么？",
        memories=["小王昨天点了美式咖啡"],
    )
    assert "美式" in out.reply_text or "咖啡" in out.reply_text


def test_reply_tool_whitelist() -> None:
    from app.domain.dialogue.reply import REPLY_TOOL_WHITELIST, ReplyToolCall

    # 白名单外的工具会被 filtered_tool_calls 过滤掉
    out = DialogueReplyOutput(
        reply_text="hello",
        tool_calls=[
            ReplyToolCall(tool="interact_with_object", arguments={}),
            ReplyToolCall(tool="face_entity", arguments={"target_entity_id": "x"}),
        ],
    )
    filtered = out.filtered_tool_calls()
    assert len(filtered) == 1
    assert filtered[0].tool == "face_entity"
    assert "face_entity" in REPLY_TOOL_WHITELIST
