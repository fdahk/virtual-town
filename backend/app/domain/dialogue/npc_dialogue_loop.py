"""
阶段 19：NPC-NPC 自主对话循环。

触发：当 ``request_interaction`` 被对方接受（双方均 entity_type=human），
``InteractionService`` 异步派发本循环，让两个 NPC 轮流说话直到一方 ``end_chat``
或达到全局轮次上限。

设计要点：
- 双方各自调用 LLM 生成台词，prompt 含双方人物档案 + 关系摘要 + 上一轮台词。
- 任一方台词为空 / 触发 end_chat / 满 ``settings.npc_dialog_max_turns`` 轮次时强制结束。
- 每轮台词通过事件总线广播 ``dialogue.npc_to_npc_message``：前端可旁观，玩家在
  附近时头顶气泡可显示最近一句。
- 结束后双方写入 ``chat`` 类型记忆，触发 ``RelationshipChange`` 升温。
- 整个循环受护栏：超 30 秒真实时间 / 5 秒单轮 LLM 超时 → 立即结束。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.event_bus import get_event_bus
from app.core.logging import get_logger
from app.core.time import iso, utcnow
from app.db.models import Agent, AgentState, DialogueMessage, Relationship
from app.domain.planning.relationship import (
    RelationshipChange,
    apply_relationship_delta,
)
from app.llm.client import get_llm_client
from app.services.memory_service import get_memory_service
from app.websocket.gateway import WS_BROADCAST_TOPIC

logger = get_logger(__name__)


_LOOP_HARD_TIMEOUT_SECONDS = 60.0


class _LineOutput(BaseModel):
    """单轮 LLM 输出 schema。"""

    line: str = Field(default="", max_length=240)
    emotion: str | None = None
    end_chat: bool = False
    relationship_delta: dict[str, int] = Field(default_factory=dict)


_SYSTEM_PROMPT = (
    "你正在扮演一个 2D 生活小镇中的居民，正在和邻居进行简短的日常对话。"
    "严格只输出 JSON：{\"line\": string, \"emotion\": string, "
    "\"end_chat\": boolean, "
    "\"relationship_delta\": {\"familiarity\": -2..2, \"trust\": -2..2, "
    "\"affection\": -2..2, \"fear\": -2..2}}。"
    "规则：1) line 必须是 1-2 句中文短句，符合人物性格与场景；"
    "2) 当聊天已经形成自然结束（道别 / 分手 / 各自有事）时，把 end_chat 置 true；"
    "3) 不要重复对方原句；4) 不要发明角色不知道的事实。"
)


def _user_prompt(
    *,
    me: Agent,
    other: Agent,
    relationship: Relationship | None,
    history: list[dict[str, str]],
    turn_index: int,
    max_turns: int,
) -> str:
    rel_block = "- 暂无关系记录"
    if relationship is not None:
        rel_block = (
            f"- familiarity={float(relationship.familiarity):.2f} "
            f"trust={float(relationship.trust):.2f} "
            f"affection={float(relationship.affection):.2f} "
            f"fear={float(relationship.fear):.2f}"
        )
        if relationship.summary:
            rel_block += f"\n- summary: {relationship.summary[:160]}"

    history_lines = []
    for h in history[-6:]:
        history_lines.append(f"- {h['speaker_name']}: {h['line']}")
    history_block = "\n".join(history_lines) or "- （还没说过话）"

    personality = ", ".join(me.personality or []) or "未知"
    return (
        f"你是 {me.name}，职业：{me.occupation or '未知'}，性格：{personality}。\n"
        f"对方是 {other.name}（{other.occupation or '居民'}）。\n"
        f"# 当前关系\n{rel_block}\n\n"
        f"# 已有对话历史\n{history_block}\n\n"
        f"# 进度\n这是第 {turn_index + 1}/{max_turns} 轮。"
        f"如果你认为对话已经聊得差不多，请把 end_chat=true 并说一句自然的告别。"
    )


async def run_npc_dialogue(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    initiator_id: str,
    target_id: str,
    simulation_id: str | None,
    starter_topic: str | None = None,
) -> dict[str, Any]:
    """运行一次 NPC-NPC 对话循环；阻塞协程直到结束。

    注意：调用方一般会把它丢到独立的 ``asyncio.create_task``，避免阻塞决策管线。
    返回汇总：``{turns, ended_by, last_speaker_id, errors}``。
    """
    settings = get_settings()
    max_turns = max(1, settings.npc_dialog_max_turns)
    started_real = time.time()
    history: list[dict[str, str]] = []
    conversation_id = f"npc-conv:{initiator_id}:{target_id}:{int(started_real)}"
    last_speaker_id: str | None = None
    ended_by = "max_turns"
    accumulated_delta = {
        initiator_id: RelationshipChange(),
        target_id: RelationshipChange(),
    }
    errors: list[str] = []

    try:
        async with session_factory() as session:
            initiator = await session.get(Agent, initiator_id)
            target = await session.get(Agent, target_id)
            if initiator is None or target is None:
                return {
                    "turns": 0,
                    "ended_by": "agent_not_found",
                    "last_speaker_id": None,
                    "errors": ["agent missing"],
                }

            speaker_order = [initiator_id, target_id]
            speakers_meta = {initiator_id: initiator, target_id: target}

            for turn_index in range(max_turns * 2):  # 每方各 max_turns 句
                if time.time() - started_real > _LOOP_HARD_TIMEOUT_SECONDS:
                    ended_by = "hard_timeout"
                    break

                speaker_id = speaker_order[turn_index % 2]
                listener_id = speaker_order[(turn_index + 1) % 2]
                me = speakers_meta[speaker_id]
                other = speakers_meta[listener_id]

                rel = await _get_relationship(session, speaker_id, listener_id)

                line_output = await _generate_one_line(
                    me=me,
                    other=other,
                    relationship=rel,
                    history=history,
                    turn_index=turn_index,
                    max_turns=max_turns * 2,
                )
                if line_output is None:
                    ended_by = "llm_failed"
                    errors.append(f"turn={turn_index} llm_failed")
                    break

                if not line_output.line.strip():
                    ended_by = "empty_line"
                    break

                # 累计 delta（双向：从对方视角微小升温）
                accumulated_delta[speaker_id].familiarity += (
                    float(line_output.relationship_delta.get("familiarity", 0)) * 0.05
                )
                accumulated_delta[speaker_id].trust += (
                    float(line_output.relationship_delta.get("trust", 0)) * 0.05
                )
                accumulated_delta[speaker_id].affection += (
                    float(line_output.relationship_delta.get("affection", 0)) * 0.05
                )
                accumulated_delta[speaker_id].fear += (
                    float(line_output.relationship_delta.get("fear", 0)) * 0.05
                )
                # 默认双向也升温一点（社交本身增加熟悉度）
                accumulated_delta[listener_id].familiarity += 0.04

                history.append(
                    {
                        "speaker_id": speaker_id,
                        "speaker_name": me.name,
                        "line": line_output.line,
                        "emotion": line_output.emotion or "",
                    }
                )
                last_speaker_id = speaker_id

                # 落库 + 广播
                await _persist_message(
                    session,
                    conversation_id=conversation_id,
                    speaker_id=speaker_id,
                    target_id=listener_id,
                    text=line_output.line,
                    emotion=line_output.emotion,
                )

                # 阶段 20：开关启用时，对话每条消息也为双方各写一条 chat 记忆。
                # 走 working scope（30 分钟过期）—— 留在短期上下文里方便决策"刚说了什么"。
                # importance 较低，TTL 过期后由 consolidation 合并成主题摘要。
                if settings.memory_dialogue_per_message:
                    await _write_per_message_memories(
                        session,
                        speaker=me,
                        listener=other,
                        line=line_output.line,
                        emotion=line_output.emotion,
                    )
                await session.commit()
                await _broadcast_message(
                    simulation_id=simulation_id,
                    conversation_id=conversation_id,
                    speaker_id=speaker_id,
                    target_id=listener_id,
                    speaker_name=me.name,
                    text=line_output.line,
                    emotion=line_output.emotion,
                    turn_index=turn_index,
                )

                if line_output.end_chat:
                    ended_by = "natural_end"
                    break

                # 让出协程，避免长循环饥饿其他任务
                await asyncio.sleep(0.05)

            # 写入双方记忆 + 应用关系 delta
            await _wrap_up(
                session,
                initiator=initiator,
                target=target,
                history=history,
                accumulated=accumulated_delta,
            )
            await session.commit()
    except Exception as exc:
        logger.exception("npc_dialogue_loop crashed: %s", exc)
        errors.append(str(exc))
    finally:
        # 释放双方 CHATTING 状态
        try:
            from app.services.simulation_runtime import get_simulation_runtime

            engine = get_simulation_runtime().engine
            engine.end_chatting(initiator_id)
            engine.end_chatting(target_id)
            # 标记 last_social_at + 衰减社交需求（阶段 19+）
            now = utcnow()
            decay = float(get_settings().needs_social_decay_after_chat)
            for aid in (initiator_id, target_id):
                a = engine.get_agent(aid)
                if a is not None:
                    a.last_social_at = now
                    if decay > 0:
                        a.social_need = max(0.0, a.social_need * (1.0 - decay))
                    a.dirty = True
        except Exception:
            logger.debug("end_chatting after npc loop failed", exc_info=True)

        # 发送对话结束事件
        await _broadcast_ended(
            simulation_id=simulation_id,
            conversation_id=conversation_id,
            initiator_id=initiator_id,
            target_id=target_id,
            turns=len(history),
            ended_by=ended_by,
        )

    return {
        "turns": len(history),
        "ended_by": ended_by,
        "last_speaker_id": last_speaker_id,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _generate_one_line(
    *,
    me: Agent,
    other: Agent,
    relationship: Relationship | None,
    history: list[dict[str, str]],
    turn_index: int,
    max_turns: int,
) -> _LineOutput | None:
    client = get_llm_client()
    if not client.settings.any_llm_configured:
        # LLM 不可用：用最简单的"问候/告别"模板做兜底
        return _rule_line(me, other, history, turn_index, max_turns)

    user = _user_prompt(
        me=me,
        other=other,
        relationship=relationship,
        history=history,
        turn_index=turn_index,
        max_turns=max_turns,
    )
    try:
        data = await asyncio.wait_for(
            client.chat_json(
                system=_SYSTEM_PROMPT,
                user=user,
                temperature=0.55,
                max_tokens=200,
                caller_module="npc_dialogue_loop",
                prompt_template_id="npc_dialogue_loop",
                prompt_version="v1",
            ),
            timeout=8.0,
        )
    except asyncio.TimeoutError:
        return _rule_line(me, other, history, turn_index, max_turns)
    except Exception:
        logger.debug("npc dialogue llm failed", exc_info=True)
        return _rule_line(me, other, history, turn_index, max_turns)

    if data is None:
        return _rule_line(me, other, history, turn_index, max_turns)
    try:
        return _LineOutput.model_validate(data)
    except ValidationError:
        return _rule_line(me, other, history, turn_index, max_turns)


def _rule_line(
    me: Agent,
    other: Agent,
    history: list[dict[str, str]],
    turn_index: int,
    max_turns: int,
) -> _LineOutput | None:
    """规则兜底：问候 → 一句寒暄 → 告别。"""
    if turn_index == 0:
        return _LineOutput(
            line=f"嗨 {other.name}，今天过得怎么样？",
            emotion="friendly",
            end_chat=False,
        )
    if turn_index == 1:
        return _LineOutput(
            line=f"还不错，最近{me.occupation or '小镇'}里挺忙的。",
            emotion="neutral",
            end_chat=False,
        )
    if turn_index >= 3 or turn_index >= max_turns - 2:
        return _LineOutput(
            line=f"那我先忙了，{other.name}，回头再聊！",
            emotion="friendly",
            end_chat=True,
        )
    return _LineOutput(
        line="嗯嗯，能见到你真好。",
        emotion="neutral",
        end_chat=False,
    )


async def _write_per_message_memories(
    session: AsyncSession,
    *,
    speaker: Agent,
    listener: Agent,
    line: str,
    emotion: str | None,
) -> None:
    """阶段 20：对话每条消息都让双方各写一条 chat 记忆（working scope）。

    - 主语视角："我对 listener 说：xxx"
    - 客语视角："听 speaker 说：xxx"
    importance=2 / scope=working：30 分钟内决策可拿到"刚刚的对话"，过期后由
    consolidation 合并成"今天和 X 聊了什么"的主题摘要。
    """
    if not line.strip():
        return
    ms = get_memory_service()
    line_short = line[:200]
    try:
        await ms.write(
            session,
            agent_id=speaker.id,
            memory_type="chat",
            scope="working",
            description=f"我对{listener.name}说：{line_short}",
            importance=2,
            subject=speaker.name,
            predicate="said_to",
            object_=listener.name,
            keywords=[listener.name, "chat", emotion or ""][:8],
            commit=False,
        )
        await ms.write(
            session,
            agent_id=listener.id,
            memory_type="chat",
            scope="working",
            description=f"听{speaker.name}说：{line_short}",
            importance=2,
            subject=speaker.name,
            predicate="said_to",
            object_=listener.name,
            keywords=[speaker.name, "chat", emotion or ""][:8],
            commit=False,
        )
    except Exception:
        logger.debug("per-message memory write failed", exc_info=True)


async def _persist_message(
    session: AsyncSession,
    *,
    conversation_id: str,
    speaker_id: str,
    target_id: str,
    text: str,
    emotion: str | None,
) -> None:
    msg = DialogueMessage(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        speaker_id=speaker_id,
        target_id=target_id,
        text=text,
        emotion=emotion,
        created_at=utcnow(),
        meta={"source": "npc_dialogue_loop"},
    )
    session.add(msg)


async def _broadcast_message(
    *,
    simulation_id: str | None,
    conversation_id: str,
    speaker_id: str,
    target_id: str,
    speaker_name: str,
    text: str,
    emotion: str | None,
    turn_index: int,
) -> None:
    if simulation_id is None:
        return
    await get_event_bus().publish(
        WS_BROADCAST_TOPIC,
        {
            "simulation_id": simulation_id,
            "type": "dialogue.npc_to_npc_message",
            "payload": {
                "conversation_id": conversation_id,
                "speaker_id": speaker_id,
                "target_id": target_id,
                "speaker_name": speaker_name,
                "text": text,
                "emotion": emotion,
                "turn_index": turn_index,
                "created_at": iso(utcnow()),
            },
        },
    )


async def _broadcast_ended(
    *,
    simulation_id: str | None,
    conversation_id: str,
    initiator_id: str,
    target_id: str,
    turns: int,
    ended_by: str,
) -> None:
    if simulation_id is None:
        return
    await get_event_bus().publish(
        WS_BROADCAST_TOPIC,
        {
            "simulation_id": simulation_id,
            "type": "dialogue.npc_to_npc_ended",
            "payload": {
                "conversation_id": conversation_id,
                "initiator_id": initiator_id,
                "target_id": target_id,
                "turns": turns,
                "ended_by": ended_by,
                "ended_at": iso(utcnow()),
            },
        },
    )


async def _get_relationship(
    session: AsyncSession, from_agent_id: str, to_entity_id: str
) -> Relationship | None:
    from sqlalchemy import select

    stmt = select(Relationship).where(
        Relationship.from_agent_id == from_agent_id,
        Relationship.to_entity_id == to_entity_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _wrap_up(
    session: AsyncSession,
    *,
    initiator: Agent,
    target: Agent,
    history: list[dict[str, str]],
    accumulated: dict[str, RelationshipChange],
) -> None:
    """对话结束：写记忆 + 应用关系 delta。"""
    if not history:
        return

    ms = get_memory_service()
    summary = "我和 %s 进行了一次对话：%s" % (
        target.name,
        " / ".join(h["line"][:40] for h in history[:4]),
    )
    summary_for_target = "我和 %s 进行了一次对话：%s" % (
        initiator.name,
        " / ".join(h["line"][:40] for h in history[:4]),
    )

    try:
        await ms.write(
            session,
            agent_id=initiator.id,
            memory_type="chat",
            scope="short_term",
            description=summary[:320],
            importance=4,
            keywords=[target.name, "chat"],
            commit=False,
        )
        await ms.write(
            session,
            agent_id=target.id,
            memory_type="chat",
            scope="short_term",
            description=summary_for_target[:320],
            importance=4,
            keywords=[initiator.name, "chat"],
            commit=False,
        )
    except Exception:
        logger.debug("npc dialogue memory write failed", exc_info=True)

    # 应用关系 delta：双方各自有自己的视角
    try:
        await apply_relationship_delta(
            session,
            from_agent_id=initiator.id,
            to_entity_id=target.id,
            change=accumulated[initiator.id],
            flush=True,
        )
        await apply_relationship_delta(
            session,
            from_agent_id=target.id,
            to_entity_id=initiator.id,
            change=accumulated[target.id],
            flush=True,
        )
    except Exception:
        logger.debug("npc dialogue relationship delta failed", exc_info=True)


__all__ = ["run_npc_dialogue"]
