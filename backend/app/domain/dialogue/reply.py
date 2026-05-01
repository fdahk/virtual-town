"""
NPC 结构化回复（阶段 16.4）。

扩展旧的 ``NPCReply``（reply / emotion / memory_writes），新增：

- ``animation``：smile / nod / wave / shake_head / shrug，供前端播放。
- ``relationship_delta``：familiarity / trust / affection / fear 四个 -2..+2 整数。
- ``tool_calls``：受限的一组工具（face_entity / update_emotion / wait /
  make_sound / avoid_danger），让 NPC 说话同时可以转身、改情绪、走开等。

LLM 失败时走规则兜底，保证仿真不因模型抖动失联。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.core.logging import get_logger
from app.llm.client import get_llm_client
from app.prompts import render_prompt
from app.schemas.agent import AgentProfile

logger = get_logger(__name__)


# 允许在对话回复里出现的工具白名单（与 dialogue_reply/v1.jinja2 对齐）
REPLY_TOOL_WHITELIST: frozenset[str] = frozenset(
    {"face_entity", "update_emotion", "wait", "make_sound", "avoid_danger"}
)


# ---------------------------------------------------------------------------
# 输出 Schema
# ---------------------------------------------------------------------------


class RelationshipDelta(BaseModel):
    familiarity: int = 0
    trust: int = 0
    affection: int = 0
    fear: int = 0


class ReplyMemoryWrite(BaseModel):
    memory_type: str = "chat"
    description: str = ""
    importance: int = 3


class ReplyToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class DialogueReplyOutput(BaseModel):
    reply_text: str = Field(default="", max_length=400)
    emotion: str | None = None
    animation: str | None = None
    relationship_delta: RelationshipDelta = Field(default_factory=RelationshipDelta)
    memory_writes: list[ReplyMemoryWrite] = Field(default_factory=list)
    tool_calls: list[ReplyToolCall] = Field(default_factory=list)

    def filtered_tool_calls(self) -> list[ReplyToolCall]:
        return [tc for tc in self.tool_calls if tc.tool in REPLY_TOOL_WHITELIST][:1]


# ---------------------------------------------------------------------------
# 生成入口
# ---------------------------------------------------------------------------


async def generate_structured_reply(
    *,
    npc_profile: AgentProfile,
    player_name: str,
    player_text: str,
    memories: list[str],
    dialogue_window: list[dict[str, Any]] | None = None,
    relationship_summary: str | None = None,
    state_summary: str | None = None,
) -> DialogueReplyOutput:
    client = get_llm_client()
    if client.settings.llm_is_configured:
        user_prompt, meta = render_prompt(
            "dialogue_reply",
            {
                "profile_block": _profile_block(npc_profile),
                "state_block": state_summary or "- (无)",
                "relationship_block": relationship_summary or "- (尚未建立)",
                "memory_block": (
                    "\n".join(f"- {m}" for m in memories[:6]) or "- 暂无相关记忆"
                ),
                "dialogue_block": (
                    "\n".join(
                        f"- {m.get('speaker', 'player')}: {m.get('text', '')}"
                        for m in (dialogue_window or [])[-6:]
                    )
                    or "-"
                ),
                "player_text": player_text,
            },
        )
        data = await client.chat_json(
            system="你必须只输出 JSON，并严格遵守 system 规则。",
            user=user_prompt,
            model=client.settings.model_for_role(meta.model_role or "dialogue"),
            temperature=0.5,
            max_tokens=500,
            prompt_template_id=meta.id,
            prompt_version=meta.version,
            caller_module="dialogue.reply",
        )
        if data is not None:
            try:
                out = DialogueReplyOutput.model_validate(data)
                # Clamp relationship deltas（避免 LLM 返回 +99）
                out.relationship_delta = _clamp_delta(out.relationship_delta)
                out.tool_calls = out.filtered_tool_calls()
                if out.reply_text.strip():
                    return out
            except ValidationError:
                logger.warning("dialogue_reply schema invalid; fallback to rule")

    return _rule_reply(npc_profile, player_name, player_text, memories)


# ---------------------------------------------------------------------------
# 规则兜底
# ---------------------------------------------------------------------------


def _rule_reply(
    npc: AgentProfile, player_name: str, player_text: str, memories: list[str]
) -> DialogueReplyOutput:
    if memories:
        hint = memories[0]
        reply = f"我还有印象：{hint}。"
        emotion = "thoughtful"
    else:
        name = npc.name
        personality = ",".join(npc.personality[:2]) if npc.personality else ""
        if "热情" in personality or "friendly" in personality:
            reply = f"{player_name}，你好呀！这个我还真不太清楚～"
            emotion = "friendly"
        elif "内向" in personality or "introvert" in personality:
            reply = "这个……我不太确定。"
            emotion = "shy"
        else:
            reply = f"我不太确定，{player_name}。"
            emotion = "neutral"
    return DialogueReplyOutput(
        reply_text=reply,
        emotion=emotion,
        animation=None,
        relationship_delta=RelationshipDelta(familiarity=1),
        memory_writes=[
            ReplyMemoryWrite(
                memory_type="chat",
                description=f"{player_name} 问我：{player_text}"[:160],
                importance=3,
            )
        ],
        tool_calls=[],
    )


def _clamp_delta(d: RelationshipDelta) -> RelationshipDelta:
    def c(v: int) -> int:
        return max(-2, min(2, int(v or 0)))

    return RelationshipDelta(
        familiarity=c(d.familiarity),
        trust=c(d.trust),
        affection=c(d.affection),
        fear=c(d.fear),
    )


def _profile_block(profile: AgentProfile) -> str:
    parts = [f"- 名字：{profile.name}"]
    if profile.occupation:
        parts.append(f"- 职业：{profile.occupation}")
    if profile.personality:
        parts.append(f"- 性格：{', '.join(profile.personality)}")
    if profile.background:
        parts.append(f"- 背景：{profile.background[:140]}")
    return "\n".join(parts)


__all__ = [
    "REPLY_TOOL_WHITELIST",
    "DialogueReplyOutput",
    "RelationshipDelta",
    "ReplyMemoryWrite",
    "ReplyToolCall",
    "generate_structured_reply",
]
