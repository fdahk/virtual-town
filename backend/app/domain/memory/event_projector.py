"""
阶段 19++ / 20：世界事件 → 记忆投影器。

让 NPC"看见的"事情被持久化为记忆，从而被后续 ``MemoryService.search`` 召回，
影响 LLM 决策与对话上下文。

设计原则
--------
1. 只在主进程订阅 ``WORLD_EVENT_TOPIC``（与 ``event_router`` 同一总线）。
2. 仅当事件包含 ``actor_entity_id``（明确归属到某个 NPC）才投影；
   公共环境事件（天气切换、纯物体状态变化）不写入个人记忆。
3. 事件类型 → 记忆类型的简单映射（白名单），其他类型按 importance 阈值过滤。
4. 跨进程频次抑制由 Redis SET NX EX 保证（API 进程与 worker 进程并发安全）；
   Redis 不可用时回落到进程内 dict（best-effort）。
5. 写入失败一律吞掉异常（不阻塞引擎广播链路）。

记忆分层（阶段 20）
-------------------
- importance ≥ 4 的事件 → ``short_term`` scope（24 小时过期，进默认检索）。
- importance < 4 的事件 → ``working`` scope（30 分钟过期，进默认检索但快速归零）。
- importance < ``MEMORY_PROJECTION_DEFAULT_MIN_IMPORTANCE`` 的非白名单事件直接丢弃，
  避免把每一个 tick 的鸡毛蒜皮全堆进 DB（仍可通过调低配置接收）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.core.time import utcnow

logger = get_logger(__name__)


# 事件类型 → (memory_type, importance, debounce_minutes, 关键词前缀)
_EVENT_RULES: dict[str, tuple[str, int, int, str]] = {
    "world.sign_noticed":      ("event", 4, 360, "sign"),
    "world.storm_warning":     ("event", 6, 60,  "storm"),
    "world.scene_changed":     ("event", 2, 30,  "scene"),
    "world.hazard_triggered":  ("event", 7, 5,   "hazard"),
    "world.fire_started":      ("event", 5, 30,  "fire"),
    "world.fire_extinguished": ("event", 4, 60,  "fire"),
    "world.fire_spread":       ("event", 5, 30,  "fire"),
    "world.object_interacted": ("event", 3, 30,  "interact"),
    "nature.fish_caught":      ("event", 3, 30,  "fish"),
    "nature.fruit_picked":     ("event", 3, 30,  "fruit"),
    "nature.mushroom_picked":  ("event", 3, 30,  "mushroom"),
    "nature.bench_rest":       ("event", 2, 60,  "rest"),
    "nature.firefly_appeared": ("event", 3, 240, "firefly"),
    "nature.bird_appeared":    ("event", 2, 120, "bird"),
    "weather.thunder":         ("event", 4, 30,  "thunder"),
    "agent.interacted":        ("event", 3, 5,   "interact"),
    "animal.reacted":          ("event", 3, 5,   "animal"),
}

# 黑名单：永不投影（避免噪声 / 已被工具自身写过）
_BLOCK_EVENT_TYPES: frozenset[str] = frozenset({
    "agent.action_started",
    "agent.action_finished",
    "llm.task_finished",
    "weather.condition_changed",
    "world.object_state_changed",
    "world.object_spawned",
    "world.object_despawned",
    "interaction.request_pending",
    "interaction.accepted",
    "interaction.declined",
    "interaction.cancelled",
    "dialogue.message_created",
    "dialogue.npc_to_npc_message",
    "dialogue.npc_to_npc_ended",
})

_DEFAULT_MEMORY_TYPE = "thought"

# 阶段 20：低重要度走 working scope（30 分钟自动过期，承接"鸡毛蒜皮"）；
# 高重要度才占用 short_term 的 24 小时档位。
_WORKING_SCOPE_THRESHOLD = 4

# 进程内 fallback debounce（Redis 不可用时使用）
_INPROC_DEBOUNCE: dict[str, datetime] = {}


async def project_world_event_to_memory(payload: dict[str, Any]) -> None:
    """把一条已广播的 WorldEvent 投影到 actor 的短期记忆。

    payload 字段对齐 ``WorldEvent`` schema：``event_type / actor_entity_id /
    description / importance / payload / created_at`` 等。

    阶段 21 增强：当事件由玩家发起（``source == "player"`` 或 ``actor_entity_id``
    以 ``player_`` 开头）时，跳过给玩家自己写记忆（玩家不参与 LLM 决策），
    改为给附近 NPC 写「目击者记忆」——让 NPC "看见" 玩家做了什么，
    叙事链路才不会断。
    """
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.memory_projection_enabled:
        return

    actor_id = payload.get("actor_entity_id")
    if not actor_id:
        return

    event_type = (payload.get("event_type") or "").strip()
    if not event_type or event_type in _BLOCK_EVENT_TYPES:
        return

    description = (payload.get("description") or "").strip()
    if not description:
        return

    importance_hint = int(payload.get("importance") or 0)

    rule = _EVENT_RULES.get(event_type)
    if rule is not None:
        memory_type, base_importance, debounce_minutes, kw_prefix = rule
        importance = max(base_importance, importance_hint)
    else:
        min_importance = settings.memory_projection_default_min_importance
        if importance_hint < min_importance:
            return
        memory_type = _DEFAULT_MEMORY_TYPE
        importance = importance_hint
        debounce_minutes = settings.memory_projection_default_debounce_minutes
        kw_prefix = event_type.split(".")[-1] if "." in event_type else event_type

    # 玩家行为 → witness 投射（不给玩家自己写记忆，给附近 NPC 写）
    source = (payload.get("source") or "").strip()
    is_player_action = source == "player" or actor_id.startswith("player_")
    if is_player_action:
        try:
            await _project_to_witnesses(
                payload=payload,
                event_type=event_type,
                description=description,
                kw_prefix=kw_prefix,
                debounce_minutes=debounce_minutes,
            )
        except Exception:
            logger.debug("project to witnesses failed", exc_info=True)
        return

    # 频次抑制：(actor + event_type + target) 在窗口内只写一次
    target_id = payload.get("target_entity_id") or "_"
    debounce_key = f"mem-proj:{actor_id}:{event_type}:{target_id}"
    if not await _claim_debounce(debounce_key, minutes=debounce_minutes):
        return

    try:
        await _write_memory(
            actor_id=actor_id,
            event_type=event_type,
            memory_type=memory_type,
            importance=importance,
            description=description,
            kw_prefix=kw_prefix,
            payload_data=payload.get("payload") or {},
        )
    except Exception:
        logger.debug("project_world_event_to_memory write failed", exc_info=True)


_WITNESS_RADIUS = 8
"""曼哈顿距离 ≤ 8 视为目击者。与 ``_perceive`` 中其他 Agent 的 10 格相比稍小，
让"近距离亲历"才入记忆，避免对面街角的 NPC 也被刷记忆。"""

_WITNESS_MAX = 6
"""单次玩家行为最多投射给 6 个 NPC。22 NPC 全场景刷记忆会让 working scope
快速膨胀，6 个目击者已足够支撑后续 LLM 决策上下文。"""


async def _project_to_witnesses(
    *,
    payload: dict[str, Any],
    event_type: str,
    description: str,
    kw_prefix: str,
    debounce_minutes: int,
) -> None:
    """玩家行为投射：给场景内、距 actor ≤ 8 格的 NPC 各写一条目击记忆。

    与 ``_write_memory`` 不同：
    - 目击者 importance 比玩家自己经历的事件低 1 档（你听见的不如亲历），
      最多 4，避免压住 NPC 自己的决策记忆。
    - 描述前缀 "目击：" 让 LLM 在召回时容易判断这是间接信息。
    - 单 actor 全局 debounce 一次（key 只包含 event_type + target），
      避免一个玩家走来走去刷满 22×N 条记忆。
    """
    scene_id = payload.get("scene_id")
    actor_x = payload.get("payload", {}).get("actor_x")
    actor_y = payload.get("payload", {}).get("actor_y")
    if scene_id is None or actor_x is None or actor_y is None:
        # 没位置信息无法判定目击范围，保守跳过（player_service 写 evt 时会塞这俩字段）
        return

    # 全局 debounce：一次玩家行为整体只投一波（不再 per-NPC debounce）
    actor_id = payload.get("actor_entity_id") or "_"
    target_id = payload.get("target_entity_id") or "_"
    global_key = f"mem-proj-witness:{actor_id}:{event_type}:{target_id}"
    if not await _claim_debounce(global_key, minutes=debounce_minutes):
        return

    from app.db.models import Agent, AgentState
    from app.db.session import get_session_factory
    from app.services.memory_service import get_memory_service
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as session:
        # 同场景内的人类 NPC（动物不参与 LLM 决策，不写）
        rows = (
            await session.execute(
                select(AgentState, Agent)
                .join(Agent, Agent.id == AgentState.agent_id)
                .where(AgentState.scene_id == scene_id)
                .where(Agent.entity_type == "human")
                .where(Agent.is_active.is_(True))
                .where(Agent.id != actor_id)  # 跳过玩家自己
            )
        ).all()

        # 按曼哈顿距离排序取最近的 _WITNESS_MAX 个
        with_dist: list[tuple[int, Agent]] = []
        for state, agent in rows:
            dist = abs(state.x - int(actor_x)) + abs(state.y - int(actor_y))
            if dist <= _WITNESS_RADIUS:
                with_dist.append((dist, agent))
        with_dist.sort(key=lambda t: t[0])
        witnesses = [a for _, a in with_dist[:_WITNESS_MAX]]
        if not witnesses:
            return

        ms = get_memory_service()
        # 比 actor 第一人称低 1 档，封顶 4
        witness_importance = min(4, max(1, int(payload.get("importance") or 3) - 1))
        scope = "short_term" if witness_importance >= _WORKING_SCOPE_THRESHOLD else "working"
        witness_desc = f"目击：{description}"[:320]
        kw = [kw_prefix, event_type, "witness"]
        for npc in witnesses:
            try:
                await ms.write(
                    session,
                    agent_id=npc.id,
                    memory_type="event",
                    scope=scope,
                    description=witness_desc,
                    importance=witness_importance,
                    importance_detail={"base": float(witness_importance), "source": "witness"},
                    keywords=[k[:64] for k in kw][:8],
                    commit=False,
                )
            except Exception:
                logger.debug("witness memory write failed for npc=%s", npc.id, exc_info=True)
        await session.commit()


async def _write_memory(
    *,
    actor_id: str,
    event_type: str,
    memory_type: str,
    importance: int,
    description: str,
    kw_prefix: str,
    payload_data: dict[str, Any],
) -> None:
    """实际把记忆写入 DB（独立 session，不阻塞调用方）。"""
    from app.db.models import Agent
    from app.db.session import get_session_factory
    from app.domain.planning import ImportanceContext, compute_importance
    from app.services.memory_service import get_memory_service

    factory = get_session_factory()
    async with factory() as session:
        agent = await session.get(Agent, actor_id)
        if agent is None:
            return

        # 关键词：事件类型 + 目标 / 公告内容前几字
        kw: list[str] = [kw_prefix, event_type]
        if "notice_text" in payload_data:
            text = str(payload_data["notice_text"])[:40]
            if text:
                kw.append(text)
        if "name" in payload_data:
            name = str(payload_data["name"])[:40]
            if name:
                kw.append(name)

        # importance 计算：以白名单 / hint 给的值为主导，让 compute_importance 的
        # detail 仅作为可解释明细。这样"路人擦肩而过"不会因 first-person novelty
        # 加分被拉到 5；相反真正稀有的事件由调用方 hint 控制。
        try:
            calc = compute_importance(
                ImportanceContext(
                    memory_type=memory_type if memory_type in {"event", "thought", "chat", "summary"} else "event",
                    is_first_person=True,
                    extras={"source": f"world_event:{event_type}"},
                )
            )
            importance_detail = calc.detail
        except Exception:
            importance_detail = None
        final_importance = max(1, min(importance, 10))

        # 阶段 20：低 importance 走 working scope（30 分钟），高 importance 才进 short_term。
        scope = "short_term" if final_importance >= _WORKING_SCOPE_THRESHOLD else "working"
        ms = get_memory_service()
        await ms.write(
            session,
            agent_id=actor_id,
            memory_type=memory_type if memory_type in {"event", "thought", "chat", "summary"} else "event",
            scope=scope,
            description=description[:320],
            importance=final_importance,
            importance_detail=importance_detail or {},
            keywords=[k[:64] for k in kw][:8],
            commit=True,
        )


async def _claim_debounce(key: str, *, minutes: int) -> bool:
    """尝试在 Redis 上 SET NX EX 占位；占位成功（首次/已过期）→ True。

    Redis 不可用时回落到进程内 dict（best-effort，不跨进程）。
    """
    ttl_seconds = max(30, minutes * 60)
    # Redis 路径
    try:
        from app.core.redis_client import get_redis

        redis = get_redis()
        client = await redis._ensure_client()  # type: ignore[attr-defined]
        if client is not None:
            ok = await client.set(key, "1", ex=ttl_seconds, nx=True)
            return bool(ok)
    except Exception:
        logger.debug("redis claim_debounce failed; fallback to in-proc", exc_info=True)

    # 进程内 fallback
    now = utcnow()
    last = _INPROC_DEBOUNCE.get(key)
    if last is not None:
        elapsed = (now - last).total_seconds() / 60.0
        if elapsed < minutes:
            return False
    _INPROC_DEBOUNCE[key] = now
    # 简单清理：超过 1 万项时丢掉一半
    if len(_INPROC_DEBOUNCE) > 10000:
        for k in list(_INPROC_DEBOUNCE.keys())[:5000]:
            _INPROC_DEBOUNCE.pop(k, None)
    return True


__all__ = ["project_world_event_to_memory"]
