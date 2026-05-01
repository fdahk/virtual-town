"""
阶段 19：交互请求 / 同意 / 拒绝 协议服务。

职责：
- 持久化 ``InteractionRequest`` 记录。
- 调用 ``interaction_evaluator.evaluate_interaction_request`` 决策。
- 通过事件总线广播 WebSocket 事件（pending / accepted / declined / cancelled）。
- 调整双方 ``EngineAgent`` 的状态机（AWAITING_RESPONSE / BUSY_REFUSING / CHATTING）。
- 当 accept 时，由调用方负责接管对话流（player_service.talk 或 npc_dialogue_loop）。

设计原则：
- 协议判定走规则层（``interaction_evaluator``），不依赖 LLM 抖动。
- LLM 仅生成软拒台词，失败回落规则模板。
- Observer 审计：每个 request 写入 ``interaction.request_*`` 观测事件，
  方便 dashboard 看接受率 / 软拒率 / 平均响应时延。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.event_bus import get_event_bus
from app.core.logging import get_logger
from app.core.time import iso, utcnow
from app.db.models import Agent, InteractionRequest
from app.domain.dialogue.interaction_evaluator import (
    EvaluationResult,
    evaluate_interaction_request,
)
from app.websocket.gateway import WS_BROADCAST_TOPIC

logger = get_logger(__name__)


# 软拒后 NPC 进入 BUSY_REFUSING 的真实秒数（让前端气泡有时间显示拒绝台词）
SOFT_REFUSE_DURATION_REAL_SECONDS = 3.0


class InteractionService:
    async def create_request(
        self,
        session: AsyncSession,
        *,
        requester_id: str,
        target_id: str,
        kind: str = "chat",
        reason: str | None = None,
        requester_priority: int = 3,
        simulation_id: str | None = None,
    ) -> tuple[InteractionRequest, EvaluationResult]:
        """创建一条 interaction_request 并立即评估。

        - 写入 InteractionRequest（status=pending）。
        - 调用评估器决定 accept / soft_decline / hard_decline。
        - 设置 status / decline_kind / npc_line / resolved_at。
        - 触发 WebSocket 事件 + Observer 审计。
        - 同步更新 EngineAgent 的状态：
          * accept：requester 进入 CHATTING，target 进入 CHATTING（由调用方决定真正派发对话）。
          * soft_decline：requester 状态恢复，target 短暂 BUSY_REFUSING（含台词）。
          * hard_decline：双方状态不变，仅返回原因。
        """
        settings = get_settings()
        now = utcnow()
        engine = self._get_engine()

        target_priority = 0
        if engine is not None:
            target_eng = engine.get_agent(target_id)
            if target_eng is not None:
                target_priority = target_eng.current_priority

        record = InteractionRequest(
            id=str(uuid.uuid4()),
            simulation_id=simulation_id,
            requester_id=requester_id,
            target_id=target_id,
            kind=kind,
            status="pending",
            reason=(reason or "")[:500],
            requester_priority=max(0, min(requester_priority, 10)),
            target_priority_at_request=target_priority,
            created_at=now,
            expires_at=now + timedelta(
                seconds=settings.interaction_request_timeout_seconds
            ),
        )
        session.add(record)
        await session.flush()

        # 广播 pending：让前端给 requester 显示 ❓ 气泡，target 显示「有人想找你」气泡
        await self._broadcast_pending(record)

        # 把 requester 置为 AWAITING_RESPONSE（仅在仍是 IDLE 时，避免覆盖 MOVING/CHATTING）
        if engine is not None:
            req_eng = engine.get_agent(requester_id)
            if req_eng is not None and req_eng.state in {"IDLE", "WAITING"}:
                req_eng.state = "AWAITING_RESPONSE"
                req_eng.dirty = True

        started = time.perf_counter()
        eval_result = await evaluate_interaction_request(
            session,
            requester_id=requester_id,
            target_id=target_id,
            kind=kind,
            reason=reason,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        record.resolved_at = utcnow()
        if eval_result.decision == "accept":
            record.status = "accepted"
        else:
            record.status = "declined"
            record.decline_kind = (
                "soft" if eval_result.decision == "soft_decline" else "hard"
            )
            record.npc_line = eval_result.npc_line
            # reason 字段拼接评估器返回的判定原因
            existing = (record.reason or "").strip()
            tag = f"[{eval_result.reason}]"
            record.reason = f"{existing} {tag}".strip()[:500]

        await session.flush()

        # 应用引擎侧状态机
        if engine is not None:
            await self._apply_engine_state(record, eval_result)

        # WebSocket 广播 + Observer 审计
        if eval_result.decision == "accept":
            await self._broadcast_accepted(record)
        else:
            await self._broadcast_declined(record)

        await self._audit(record, eval_result, elapsed_ms=elapsed_ms)

        # 接受 + 双方都是 NPC（非玩家）→ 派发 NPC-NPC 自主对话循环
        if eval_result.decision == "accept" and kind == "chat":
            try:
                await self._maybe_dispatch_npc_dialogue(session, record)
            except Exception:
                logger.debug("dispatch npc dialogue failed", exc_info=True)

        return record, eval_result

    async def _maybe_dispatch_npc_dialogue(
        self, session: AsyncSession, record: InteractionRequest
    ) -> None:
        """如果 requester 和 target 都是 human NPC（非玩家），派发自主对话循环。"""
        requester = await session.get(Agent, record.requester_id)
        target = await session.get(Agent, record.target_id)
        if requester is None or target is None:
            return
        if requester.entity_type != "human" or target.entity_type != "human":
            return

        # 引擎层会在 dialogue_loop 内 end_chatting 释放双方
        from app.db.session import get_session_factory
        from app.domain.dialogue.npc_dialogue_loop import run_npc_dialogue

        factory = get_session_factory()
        requester_id = record.requester_id
        target_id = record.target_id
        sim_id = record.simulation_id
        reason = record.reason

        async def _runner() -> None:
            try:
                await run_npc_dialogue(
                    factory,
                    initiator_id=requester_id,
                    target_id=target_id,
                    simulation_id=sim_id,
                    starter_topic=reason,
                )
            except Exception:
                logger.exception("npc_dialogue_loop unhandled error")

        # 用独立 task 跑，避免阻塞当前请求 → 调用方立刻拿到 accept 响应
        asyncio.create_task(_runner(), name=f"npc-dlg:{record.id}")

    async def cancel_request(
        self, session: AsyncSession, *, request_id: str, requester_id: str | None = None
    ) -> InteractionRequest | None:
        """取消一条 pending 请求（只有 requester 可取消）。"""
        record = await session.get(InteractionRequest, request_id)
        if record is None:
            return None
        if requester_id is not None and record.requester_id != requester_id:
            return None
        if record.status != "pending":
            return record
        record.status = "cancelled"
        record.resolved_at = utcnow()
        await session.flush()
        await self._broadcast_cancelled(record)

        engine = self._get_engine()
        if engine is not None:
            req_eng = engine.get_agent(record.requester_id)
            if req_eng is not None and req_eng.state == "AWAITING_RESPONSE":
                req_eng.state = "IDLE"
                req_eng.dirty = True
        return record

    # ------------------------------------------------------------------
    # 引擎状态联动
    # ------------------------------------------------------------------

    async def _apply_engine_state(
        self, record: InteractionRequest, evt: EvaluationResult
    ) -> None:
        engine = self._get_engine()
        if engine is None:
            return
        req_eng = engine.get_agent(record.requester_id)
        target_eng = engine.get_agent(record.target_id)

        if evt.decision == "accept":
            # 双方都进入 CHATTING，由后续 talk / npc_dialogue_loop 接管
            if target_eng is not None:
                engine.start_chatting(target_eng.id)
            if req_eng is not None and not req_eng.is_player:
                engine.start_chatting(req_eng.id)
            elif req_eng is not None:
                # 玩家发起：玩家 state 不强制改，由前端进入对话面板
                req_eng.state = "CHATTING"
                req_eng.dirty = True
            return

        # 拒绝：requester 恢复 IDLE
        if req_eng is not None and req_eng.state == "AWAITING_RESPONSE":
            req_eng.state = "IDLE"
            req_eng.dirty = True

        # soft_decline：target 短暂 BUSY_REFUSING（用真实秒做超时；
        # 这里仅设标志，让前端气泡显示拒绝台词，不冻结 target 决策）
        if evt.decision == "soft_decline" and target_eng is not None:
            engine.mark_busy_refusing(
                target_eng.id,
                line=record.npc_line or "",
                duration_seconds=SOFT_REFUSE_DURATION_REAL_SECONDS,
            )

    # ------------------------------------------------------------------
    # 广播
    # ------------------------------------------------------------------

    def _payload(self, record: InteractionRequest) -> dict[str, Any]:
        return {
            "request_id": record.id,
            "requester_id": record.requester_id,
            "target_id": record.target_id,
            "kind": record.kind,
            "status": record.status,
            "decline_kind": record.decline_kind,
            "npc_line": record.npc_line,
            "reason": record.reason,
            "created_at": iso(record.created_at),
            "resolved_at": iso(record.resolved_at) if record.resolved_at else None,
        }

    async def _broadcast(self, record: InteractionRequest, evt_type: str) -> None:
        bus = get_event_bus()
        sim_id = record.simulation_id or self._current_sim_id()
        if sim_id is None:
            return
        await bus.publish(
            WS_BROADCAST_TOPIC,
            {
                "simulation_id": sim_id,
                "type": evt_type,
                "payload": self._payload(record),
            },
        )

    async def _broadcast_pending(self, record: InteractionRequest) -> None:
        await self._broadcast(record, "interaction.request_pending")

    async def _broadcast_accepted(self, record: InteractionRequest) -> None:
        await self._broadcast(record, "interaction.accepted")

    async def _broadcast_declined(self, record: InteractionRequest) -> None:
        await self._broadcast(record, "interaction.declined")

    async def _broadcast_cancelled(self, record: InteractionRequest) -> None:
        await self._broadcast(record, "interaction.cancelled")

    # ------------------------------------------------------------------
    # 审计
    # ------------------------------------------------------------------

    async def _audit(
        self,
        record: InteractionRequest,
        evt: EvaluationResult,
        *,
        elapsed_ms: int,
    ) -> None:
        try:
            from app.services.observer import get_observer

            await get_observer().record_event(
                category="dialogue",
                event_type=f"interaction.{record.status}",
                title=f"{record.requester_id} → {record.target_id} ({record.kind})",
                level="INFO",
                entity_id=record.target_id,
                duration_ms=elapsed_ms,
                payload={
                    "request_id": record.id,
                    "requester_id": record.requester_id,
                    "target_id": record.target_id,
                    "kind": record.kind,
                    "decision": evt.decision,
                    "decline_kind": record.decline_kind,
                    "reason": evt.reason,
                },
            )
        except Exception:
            logger.debug("observer audit interaction failed", exc_info=True)

    # ------------------------------------------------------------------
    # 引擎引用（避免循环导入）
    # ------------------------------------------------------------------

    def _get_engine(self) -> Any | None:
        try:
            from app.services.simulation_runtime import get_simulation_runtime

            return get_simulation_runtime().engine
        except Exception:
            return None

    def _current_sim_id(self) -> str | None:
        engine = self._get_engine()
        if engine is None:
            return None
        sim = engine.get_simulation()
        return sim.id if sim is not None else None


_service: InteractionService | None = None


def get_interaction_service() -> InteractionService:
    global _service
    if _service is None:
        _service = InteractionService()
    return _service


__all__ = [
    "SOFT_REFUSE_DURATION_REAL_SECONDS",
    "InteractionService",
    "get_interaction_service",
]
