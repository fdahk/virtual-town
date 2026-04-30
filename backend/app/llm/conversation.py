"""
NPC 对话生成。

- 优先使用 LLM + Pydantic 校验（JSON 输出）。
- 失败时使用规则兜底：基于记忆文案摘取关键字的简单回复。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.core.logging import get_logger
from app.llm.client import get_llm_client
from app.schemas.agent import AgentProfile

logger = get_logger(__name__)


class NPCReply(BaseModel):
    reply: str = Field(..., max_length=400)
    emotion: str | None = None
    memory_writes: list[str] = Field(default_factory=list)


JSON_SCHEMA_HINT = '{"reply": "string", "emotion": "string|null", "memory_writes": ["string"]}'

SYSTEM_PROMPT = (
    "你是一个 2D 生活小镇中的 NPC，需要以第一人称自然地用简短中文回答玩家。"
    "严格要求："
    "\n1. 回答必须简短、口语、符合角色性格。"
    "\n2. 如果你没有记忆支持，要坦诚地说“不太确定”。"
    "\n3. 必须只输出 JSON，字段为 {reply, emotion, memory_writes}。"
)


async def generate_npc_reply(
    *,
    npc_profile: AgentProfile,
    player_name: str,
    player_text: str,
    memories: list[str],
) -> dict[str, Any]:
    client = get_llm_client()
    if client.settings.llm_is_configured:
        user_prompt = _build_user_prompt(npc_profile, player_name, player_text, memories)
        data = await client.chat_json(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            json_schema_hint=JSON_SCHEMA_HINT,
            temperature=0.5,
            max_tokens=400,
        )
        if data is not None:
            try:
                parsed = NPCReply.model_validate(data)
                return parsed.model_dump()
            except ValidationError as exc:
                logger.warning("NPCReply validation failed: %s", exc)

    return _rule_reply(npc_profile, player_name, player_text, memories)


def _build_user_prompt(
    npc: AgentProfile, player_name: str, player_text: str, memories: list[str]
) -> str:
    memory_block = "\n".join(f"- {m}" for m in memories) or "- 暂无可用记忆"
    personality = ", ".join(npc.personality) or "未知"
    return (
        f"角色档案：\n"
        f"- 名字：{npc.name}\n"
        f"- 职业：{npc.occupation or '未知'}\n"
        f"- 性格：{personality}\n"
        f"- 背景：{npc.background or ''}\n\n"
        f"相关记忆（按相关度排序）：\n{memory_block}\n\n"
        f"玩家「{player_name}」对你说：\n{player_text}\n\n"
        "请用角色口吻简短回答。"
    )


def _rule_reply(
    npc: AgentProfile, player_name: str, player_text: str, memories: list[str]
) -> dict[str, Any]:
    """规则兜底：从记忆里挑一条相关描述，编个简短回应。"""
    if memories:
        hint = memories[0]
        if npc.occupation:
            reply = f"嗯……我还有印象：{hint}。"
        else:
            reply = f"我记得：{hint}。"
    else:
        name = npc.name
        personality = ",".join(npc.personality[:2]) if npc.personality else ""
        if "热情" in personality or "friendly" in personality:
            reply = f"{player_name}，你好呀！这个我还真不太清楚～"
        elif "内向" in personality or "introvert" in personality:
            reply = "这个……我不太确定。"
        else:
            reply = f"我不太确定，{player_name}。"
    return {"reply": reply, "emotion": "neutral", "memory_writes": []}
