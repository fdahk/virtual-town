"""
Query 改写（阶段 16.1）：代词消解 + 自包含改写 + 记忆检索提示。

流程：
1. 把最近对话窗口最多 4 条 + 场景可见实体 + 原文送给 LLM。
2. LLM 返回 ``rewritten_query`` / ``resolved_entities`` / ``needs_memory_search``。
3. LLM 失败时走规则兜底：不做消解，原文原样返回；根据关键词（"记得 / 昨天 / 以前"等）
   推断 ``needs_memory_search``。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Agent, AgentState
from app.llm.client import get_llm_client
from app.prompts import render_prompt

logger = get_logger(__name__)

_MEMORY_HINT_PATTERNS = (
    "记得", "记住", "昨天", "前天", "上次", "以前", "当时", "那天", "之前",
)
_PRONOUNS = ("他", "她", "它", "那个", "那里", "那位", "那家", "那儿")


class ResolvedEntity(BaseModel):
    mention: str = ""
    entity_id: str = ""
    confidence: float = 0.5


class QueryRewriteOutput(BaseModel):
    rewritten_query: str = ""
    resolved_entities: list[ResolvedEntity] = Field(default_factory=list)
    needs_memory_search: bool = False
    intent_hint: str | None = None


async def rewrite_query(
    session: AsyncSession,
    *,
    player: Agent,
    target: Agent,
    scene_id: str,
    raw_text: str,
    recent_dialogue: list[dict[str, Any]] | None = None,
) -> QueryRewriteOutput:
    """
    返回 QueryRewriteOutput。永不抛异常：LLM 失败时退化为规则实现。
    """
    recent_dialogue = recent_dialogue or []
    client = get_llm_client()

    # 场景可见实体候选（最多 20 个）
    scene_entities = await _fetch_scene_entities(session, scene_id=scene_id)

    if client.settings.llm_is_configured:
        dialogue_block = (
            "\n".join(
                f"- {m.get('speaker', 'player')}: {m.get('text', '')}"
                for m in recent_dialogue[-4:]
            )
            or "-"
        )
        scene_block = (
            "\n".join(f"- {eid}: {name}" for eid, name in scene_entities)
            or "-"
        )
        user_prompt, meta = render_prompt(
            "query_rewrite",
            {
                "player_block": f"- 玩家：{player.name} ({player.id})",
                "target_block": f"- NPC：{target.name} ({target.id})",
                "scene_entities_block": scene_block,
                "dialogue_block": dialogue_block,
                "raw_text": raw_text,
            },
        )
        data = await client.chat_json(
            system="你必须只输出 JSON。",
            user=user_prompt,
            model=client.settings.model_for_role(meta.model_role or "query_rewrite"),
            temperature=0.1,
            max_tokens=400,
            prompt_template_id=meta.id,
            prompt_version=meta.version,
            caller_module="dialogue.query_rewrite",
        )
        if data is not None:
            try:
                out = QueryRewriteOutput.model_validate(data)
                # 确保 resolved_entities 里的 entity_id 至少能在候选里找到
                known_ids = {eid for eid, _ in scene_entities} | {player.id, target.id}
                out.resolved_entities = [
                    e for e in out.resolved_entities if e.entity_id in known_ids
                ]
                if not out.rewritten_query.strip():
                    out.rewritten_query = raw_text
                return out
            except ValidationError:
                logger.warning("query_rewrite schema invalid, fallback")

    return _rule_rewrite(raw_text, recent_dialogue, scene_entities)


async def _fetch_scene_entities(
    session: AsyncSession, *, scene_id: str, limit: int = 20
) -> list[tuple[str, str]]:
    # 找出当前场景内所有 agent 的 (id, name)
    stmt = (
        select(Agent.id, Agent.name)
        .join(AgentState, AgentState.agent_id == Agent.id)
        .where(AgentState.scene_id == scene_id)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [(r[0], r[1]) for r in rows]


def _rule_rewrite(
    raw_text: str,
    recent_dialogue: list[dict[str, Any]],
    scene_entities: list[tuple[str, str]],
) -> QueryRewriteOutput:
    out = QueryRewriteOutput(rewritten_query=raw_text)

    # 1) memory hint：命中 "记得/昨天/以前" 等触发
    for pat in _MEMORY_HINT_PATTERNS:
        if pat in raw_text:
            out.needs_memory_search = True
            break

    # 2) 代词消解兜底：取最近对话里最后一个被 player 或 npc 提及的 entity_id
    if any(p in raw_text for p in _PRONOUNS):
        candidate_id: str | None = None
        for msg in reversed(recent_dialogue[-4:]):
            # speaker_id 若是 NPC / player 本人之外的实体，直接用
            sid = msg.get("speaker_id")
            if sid and sid not in {"player", "self"}:
                candidate_id = sid
                break
            # 尝试从文本里按名字匹配场景内实体
            text = msg.get("text", "")
            for eid, name in scene_entities:
                if name and name in text:
                    candidate_id = eid
                    break
            if candidate_id:
                break
        if candidate_id:
            out.resolved_entities.append(
                ResolvedEntity(
                    mention=next((p for p in _PRONOUNS if p in raw_text), "他"),
                    entity_id=candidate_id,
                    confidence=0.45,
                )
            )
            out.needs_memory_search = True

    # 3) 极简 intent_hint
    if any(p in raw_text for p in ("在哪", "怎么去", "在哪里")):
        out.intent_hint = "ask_location"
    elif out.needs_memory_search:
        out.intent_hint = "ask_memory"
    elif any(p in raw_text for p in ("帮我", "帮帮", "能不能")):
        out.intent_hint = "request_action"
    else:
        out.intent_hint = "chat"
    return out


__all__ = [
    "QueryRewriteOutput",
    "ResolvedEntity",
    "rewrite_query",
]
