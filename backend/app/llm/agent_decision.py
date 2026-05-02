"""
LLM 驱动的 Agent 决策管线。

实现 `智能NPC与记忆模块实施方案.md` 的 perceive → retrieve → plan → execute 流程：

1. perceive：从引擎内存读取 Agent 自身 + 附近实体 + 附近地点 + 当前 hazard。
   - 读取 Redis 不可达黑名单，从感知列表中剔除短期内无法到达的地点/实体，
     防止 LLM 反复选择必然失败的目标（性能优化）。
2. retrieve：MemoryService.search 取最相关的 k 条记忆。
3. plan：retrieve + planning_context 并发执行后，拼 prompt 调 LLM chat_json。
4. execute：ToolExecutor 依次执行 LLM 返回的 tool_calls，任何失败都不会打断仿真。

未配置 LLM 或调用失败时返回 None，由引擎 fallback 到规则版 rule_agent。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Agent, AgentState, Location, WorldObject
from app.domain.planning import get_planning_service
from app.llm.client import get_llm_client
from app.llm.tools.base import ToolCall, ToolContext, ToolResult
from app.llm.tools.executor import get_tool_executor
from app.llm.tools.registry import get_tool_registry
from app.prompts import render_prompt
from app.schemas.memory import MemorySearchRequest
from app.services.memory_service import get_memory_service

logger = get_logger(__name__)


class _LLMToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.5
    thought: str | None = None


class _MemoryWrite(BaseModel):
    memory_type: str = "thought"
    description: str
    importance: int = 3


class AgentDecisionOutput(BaseModel):
    thought: str = ""
    emotion: str | None = None
    tool_calls: list[_LLMToolCall] = Field(default_factory=list)
    memory_writes: list[_MemoryWrite] = Field(default_factory=list)


async def decide_with_llm(
    session: AsyncSession,
    *,
    agent_id: str,
    simulation_id: str,
    world_time: datetime,
) -> list[ToolResult] | None:
    """
    用 LLM 驱动决策。返回 ToolResult 列表。
    若未接入 LLM 或失败返回 None，调用方应走规则兜底。
    """
    client = get_llm_client()
    if not client.settings.llm_is_configured:
        return None

    agent = await session.get(Agent, agent_id)
    state = await session.get(AgentState, agent_id)
    if agent is None or state is None:
        return None

    perception = await _perceive(session, agent, state)

    # retrieve + planning_context 并发执行，两者互相不依赖，可合并等待
    async def _safe_plan_ctx() -> dict[str, Any]:
        try:
            return await get_planning_service().get_current_context(
                session, agent, world_time=world_time
            )
        except Exception:
            logger.debug("planning context failed", exc_info=True)
            return {}

    memories, plan_ctx = await asyncio.gather(
        _retrieve(session, agent_id, perception, world_time),
        _safe_plan_ctx(),
    )

    registry = get_tool_registry()
    tool_catalog = registry.prompt_catalog(entity_type=agent.entity_type)

    social_block = await _social_block(session, agent, state, world_time)

    prompt, meta = render_prompt(
        "agent_decision",
        {
            "profile_block": _profile_block(agent),
            "state_block": _state_block(state, world_time),
            "perception_block": perception["text"],
            "plan_block": _plan_block(plan_ctx),
            "memory_block": _memory_block(memories),
            "social_block": social_block,
            "tool_catalog": tool_catalog,
        },
    )

    data = await client.chat_json(
        system=(
            "你必须严格按要求只输出 JSON，不加任何额外文字。"
            "调用的 tool 必须来自给定目录，arguments 字段必须匹配描述。"
        ),
        user=prompt,
        model=client.settings.model_for_role(meta.model_role or "chat"),
        temperature=0.4,
        max_tokens=700,
        prompt_template_id=meta.id,
        prompt_version=meta.version,
        caller_module="agent_decision",
    )
    if data is None:
        return None
    try:
        plan = AgentDecisionOutput.model_validate(data)
    except ValidationError as exc:
        logger.warning("agent_decision validation failed: %s", exc)
        return None

    executor = get_tool_executor()
    ctx = ToolContext(
        session=session,
        agent_id=agent_id,
        entity_type=agent.entity_type,
        scene_id=state.scene_id,
        position=(state.x, state.y),
        simulation_id=simulation_id,
        world_time=world_time,
        source="llm",
    )
    calls = [
        ToolCall(
            tool=tc.tool,
            arguments=tc.arguments,
            confidence=tc.confidence,
            thought=tc.thought,
        )
        for tc in plan.tool_calls[:3]
    ]
    results = await executor.execute_batch(ctx, calls)

    ms = get_memory_service()
    from app.domain.planning import ImportanceContext, compute_importance

    # 阶段 19+：消费工具产生的 memory_candidates（之前 request_interaction /
    # socialize / interact 等工具的候选记忆都没人写库，导致 NPC 找不到社交记忆）
    _MEM_TYPES = {"event", "thought", "chat", "summary"}
    for r in results:
        for cand in (r.memory_candidates or [])[:3]:
            try:
                mtype = cand.get("memory_type", "thought")
                if mtype not in _MEM_TYPES:
                    mtype = "thought"
                desc = str(cand.get("description") or "").strip()
                if not desc:
                    continue
                imp_hint = int(cand.get("importance", 3) or 3)
                calc = compute_importance(
                    ImportanceContext(
                        memory_type=mtype,
                        emotion=plan.emotion,
                        is_first_person=True,
                        extras={"source": f"tool:{r.tool}"},
                    )
                )
                kw = cand.get("keywords") or []
                if not isinstance(kw, list):
                    kw = []
                await ms.write(
                    session,
                    agent_id=agent_id,
                    memory_type=mtype,
                    scope=cand.get("scope") or "short_term",
                    description=desc[:400],
                    importance=max(calc.importance, max(1, min(imp_hint, 10))),
                    importance_detail=calc.detail,
                    keywords=[str(k)[:64] for k in kw][:8],
                    commit=False,
                )
            except Exception:
                logger.debug("tool memory_candidate write failed", exc_info=True)

    # 写 LLM 自己提议的 memory_writes（带 importance 五因素明细）
    for mw in plan.memory_writes[:4]:
        try:
            base_type = (
                mw.memory_type
                if mw.memory_type in _MEM_TYPES
                else "thought"
            )
            calc = compute_importance(
                ImportanceContext(
                    memory_type=base_type,
                    emotion=plan.emotion,
                    is_first_person=True,
                    extras={"llm_suggested": mw.importance},
                )
            )
            await ms.write(
                session,
                agent_id=agent_id,
                memory_type=base_type,
                scope="short_term",
                description=mw.description[:400],
                importance=max(calc.importance, max(1, min(mw.importance, 10))),
                importance_detail=calc.detail,
                commit=False,
            )
        except Exception:
            logger.exception("memory write from LLM failed")

    # 把情绪也同步到引擎（worker 进程中引擎可能未起，容错）
    if plan.emotion:
        try:
            from app.services.simulation_runtime import get_simulation_runtime

            engine_agent = get_simulation_runtime().engine.get_agent(agent_id)
            if engine_agent is not None:
                engine_agent.emotion = plan.emotion
                engine_agent.dirty = True
        except RuntimeError:
            # worker 进程或测试环境中没有起仿真引擎；情绪通过下次 tick 重新加载
            logger.debug("engine not started; skip emotion sync for %s", agent_id)
        except Exception:
            logger.debug("emotion sync failed", exc_info=True)

    return results


# ---------------------------------------------------------------------------
# 感知 & 检索
# ---------------------------------------------------------------------------


async def _get_unreachable_set(agent_id: str) -> set[str]:
    """读取 Redis 不可达黑名单（best-effort，失败返回空集合）。"""
    try:
        from app.core.redis_client import get_redis, key_agent_unreachable

        members = await get_redis().set_members(key_agent_unreachable(agent_id))
        return set(members)
    except Exception:
        return set()


_NEARBY_OBJECT_RADIUS = 6
"""LLM 感知半径：曼哈顿 ≤ 6 格的 WorldObject 才会出现在 perception 里。
比其他 Agent 的 10 格更紧，避免把整张地图扔给模型；但够远到 NPC 走两步就
能交互（场景平均人口密度下，半径 6 大约 50~80 个候选物品，按重要度截断）。"""

_NEARBY_OBJECT_CAP = 8
"""单次 perception 最多列 8 个对象。再多就让 prompt token 失控；优先级：
有特殊状态（火 / 告示 / 成熟 / 钓鱼活跃）> 普通可交互 > 距离最近。"""


async def _perceive(
    session: AsyncSession, agent: Agent, state: AgentState
) -> dict[str, Any]:
    """只读取数据库层的场景 & 附近实体 + 附近可交互物品，非侵入。

    同时读取 Redis 不可达黑名单，过滤掉当前 ai_tick 窗口内已知不可达的
    地点/实体，防止 LLM 反复选择必然失败的目标。

    阶段 21 增强：把 ``WorldObject`` 列表（含 id / name / 可用交互动作）
    一并拼进 perception，让 LLM 能调用 ``interact_with_object(object_id=...)``。
    历史问题：原版只查 ``AgentState`` 与 ``Location``，模型根本拿不到
    object_id，于是 22 NPC 几乎从不调用 ``interact_with_object`` —— 看起来
    像"NPC 不主动与环境交互"。
    """
    # 并发读：不可达集合 + 附近实体 + 同场景物品 三者互相独立
    unreachable_set, agent_state_rows, world_object_rows = await asyncio.gather(
        _get_unreachable_set(agent.id),
        session.execute(
            select(AgentState).where(
                AgentState.scene_id == state.scene_id,
                AgentState.agent_id != agent.id,
            )
        ),
        session.execute(
            select(WorldObject).where(WorldObject.scene_id == state.scene_id)
        ),
    )
    rows = agent_state_rows.scalars().all()
    world_objects = world_object_rows.scalars().all()

    nearby_entities: list[tuple[str, int, int, str]] = []
    for r in rows:
        dist = abs(r.x - state.x) + abs(r.y - state.y)
        if dist > 10:
            continue
        # 不可达黑名单过滤：跳过已知此轮无法抵达的实体
        if r.agent_id in unreachable_set:
            continue
        other = await session.get(Agent, r.agent_id)
        if other is None:
            continue
        nearby_entities.append((other.name, r.x, r.y, other.entity_type))
    nearby_entities.sort(key=lambda t: abs(t[1] - state.x) + abs(t[2] - state.y))

    # 当前场景的地点集，过滤不可达条目后呈现给 LLM
    locs = (
        await session.execute(
            select(Location).where(Location.scene_id == state.scene_id)
        )
    ).scalars().all()
    reachable_locs = [l for l in locs if l.id not in unreachable_set]
    loc_items = [f"{l.id}: {l.name}" for l in reachable_locs[:16]]

    text_lines: list[str] = []
    text_lines.append(f"- 你当前位置 scene={state.scene_id} (x={state.x}, y={state.y}), 状态={state.state}")
    if state.status_effects:
        text_lines.append(f"- 身上有效果：{', '.join(state.status_effects)}")
    if nearby_entities:
        text_lines.append("- 附近实体：")
        for name, x, y, et in nearby_entities[:6]:
            text_lines.append(f"    {name}({et}) @ ({x},{y})")
    else:
        text_lines.append("- 周围没有人。")
    if loc_items:
        text_lines.append("- 当前场景的地点 id 供你选择（示例 id 开头）：")
        text_lines.append("    " + "; ".join(loc_items))
    if unreachable_set:
        text_lines.append(f"- 本轮不可到达（已屏蔽）：{', '.join(sorted(unreachable_set)[:8])}")

    # 附近可交互物品（DB 直查，主进程 / worker 都能用）
    _append_nearby_objects(state, world_objects, text_lines)

    # 自然事件感知 / 天气：从引擎内存读取（如果可用）
    await _append_natural_context(session, state, text_lines)

    return {"text": "\n".join(text_lines), "nearby": nearby_entities}


def _append_nearby_objects(
    state: AgentState,
    world_objects: list[WorldObject],
    text_lines: list[str],
) -> None:
    """把附近 WorldObject 拼成 LLM 可用的 id 列表。

    过滤策略：
    - 距离 > _NEARBY_OBJECT_RADIUS 直接丢
    - 没有 ``available_interactions`` 的（纯装饰，如 river_fence）丢
    - 优先级：有"显著 state"（fire / 告示 / mushroom_present / fruit_ripe / fishing_active）
      > 普通可交互 > 按距离排序
    - 最多输出 _NEARBY_OBJECT_CAP 条
    """
    nearby: list[tuple[int, int, WorldObject]] = []  # (priority, dist, obj)
    for obj in world_objects:
        # WorldObject.position 是 JSONB dict；中间表用属性访问可能拿不到，先按 dict 处理
        try:
            ox = int(obj.position["x"])
            oy = int(obj.position["y"])
        except (KeyError, TypeError, ValueError):
            continue
        dist = abs(ox - state.x) + abs(oy - state.y)
        if dist > _NEARBY_OBJECT_RADIUS:
            continue
        actions = list(obj.available_interactions or [])
        if not actions:
            continue

        # 优先级越小越靠前
        priority = 9
        s = obj.state or {}
        if s.get("on_fire"):
            priority = 0
        elif s.get("notice_text"):
            priority = 1
        elif s.get("mushroom_present") or s.get("fruit_ripe"):
            priority = 2
        elif s.get("fishing_active"):
            priority = 3
        nearby.append((priority, dist, obj))

    if not nearby:
        text_lines.append("- 附近没有可交互的物品。")
        return

    nearby.sort(key=lambda t: (t[0], t[1]))
    lines = ["- 附近可交互物品（用 interact_with_object 工具，object_id 必须从下方挑选）："]
    for _prio, dist, obj in nearby[:_NEARBY_OBJECT_CAP]:
        actions = ",".join((obj.available_interactions or [])[:4])
        try:
            ox = int(obj.position["x"])
            oy = int(obj.position["y"])
            pos_text = f"@({ox},{oy}) {dist}格"
        except Exception:
            pos_text = f"{dist}格外"
        # 用 state 关键键给一个状态提示，方便 LLM 决策
        s = obj.state or {}
        hint = ""
        if s.get("on_fire"):
            hint = " ⚠正在燃烧"
        elif s.get("mushroom_present"):
            hint = " 🍄可采"
        elif s.get("fruit_ripe"):
            hint = " 🍎可摘"
        elif s.get("fishing_active"):
            hint = " 🐟有鱼"
        elif s.get("notice_text"):
            hint = f" 📜「{str(s.get('notice_text'))[:30]}」"
        lines.append(f"    [{obj.id}] {obj.name} {pos_text} 可做:{actions}{hint}")
    text_lines.extend(lines)


async def _append_natural_context(
    session: AsyncSession, state: AgentState, text_lines: list[str]
) -> None:
    """
    将附近的自然事件状态（火焰、告示牌、钓鱼点等）拼入感知文本，
    供 LLM 理解并做出情境化决策。只读，不修改任何状态。
    """
    try:
        from app.services.simulation_runtime import get_simulation_runtime

        engine = get_simulation_runtime().engine
        if engine is None:
            return
        weather = engine._weather  # type: ignore[attr-defined]
        objects = engine._objects  # type: ignore[attr-defined]
    except Exception:
        return

    # 天气描述
    weather_desc = {
        "sunny": "晴天", "cloudy": "多云", "rainy": "下雨",
        "stormy": "暴风雨", "foggy": "有雾",
    }.get(weather.condition, weather.condition)
    text_lines.append(f"- 当前天气：{weather_desc}，气温约 {weather.temperature:.0f}°C")

    # 附近特殊状态对象
    fire_names: list[str] = []
    sign_texts: list[str] = []
    fishing_names: list[str] = []
    ripe_names: list[str] = []
    firefly_names: list[str] = []

    for obj in objects.values():
        if obj.scene_id != state.scene_id:
            continue
        dist = abs(obj.x - state.x) + abs(obj.y - state.y)
        if obj.state.get("on_fire") and dist <= 8:
            fire_names.append(f"{obj.name}({dist}格外)")
        notice = obj.state.get("notice_text")
        if notice and dist <= 5:
            sign_texts.append(f"「{notice[:40]}」")
        if obj.state.get("fishing_active") and dist <= 4:
            fishing_names.append(obj.name)
        if (obj.state.get("fruit_ripe") or obj.state.get("mushroom_present")) and dist <= 3:
            ripe_names.append(obj.name)
        if obj.state.get("firefly_active") and dist <= 6:
            firefly_names.append(obj.name)

    if fire_names:
        text_lines.append(f"- ⚠ 附近正在燃烧：{', '.join(fire_names[:3])}")
    if sign_texts:
        text_lines.append(f"- 附近告示牌：{'; '.join(sign_texts[:2])}")
    if fishing_names:
        text_lines.append(f"- 附近有鱼儿活跃的钓鱼点：{', '.join(fishing_names[:2])}")
    if ripe_names:
        text_lines.append(f"- 附近有可采摘的果实/蘑菇：{', '.join(ripe_names[:3])}")
    if firefly_names:
        text_lines.append(f"- 夜色中可见萤火虫飞舞于：{', '.join(firefly_names[:2])}")


async def _retrieve(
    session: AsyncSession, agent_id: str, perception: dict[str, Any], world_time: datetime
) -> list[dict[str, Any]]:
    query = " ".join([p[0] for p in perception.get("nearby", [])]) or "今天要做什么"
    results = await get_memory_service().search(
        session,
        agent_id,
        MemorySearchRequest(query=query, limit=6),
        now=world_time,
    )
    return [
        {
            "id": r.memory.id,
            "description": r.memory.description,
            "importance": r.memory.importance,
            "score": r.score,
        }
        for r in results
    ]


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------


def _profile_block(agent: Agent) -> str:
    lines = [f"- 名字：{agent.name}", f"- 类型：{agent.entity_type}"]
    if agent.species:
        lines.append(f"- 物种：{agent.species}")
    if agent.occupation:
        lines.append(f"- 职业：{agent.occupation}")
    if agent.personality:
        lines.append(f"- 性格：{', '.join(agent.personality)}")
    if agent.background:
        lines.append(f"- 背景：{agent.background}")
    if agent.lifestyle:
        lines.append(f"- 生活习惯：{agent.lifestyle}")
    if agent.long_term_goals:
        lines.append(f"- 长期目标：{' / '.join(agent.long_term_goals)}")
    return "\n".join(lines)


def _state_block(state: AgentState, world_time: datetime) -> str:
    return (
        f"- 游戏时间：{world_time.strftime('%Y-%m-%d %H:%M')}\n"
        f"- 精力：{state.energy:.2f} 饥饿：{state.hunger:.2f} 社交：{state.social_need:.2f}\n"
        f"- 情绪：{state.emotion or '平静'}"
    )


def _memory_block(memories: list[dict[str, Any]]) -> str:
    if not memories:
        return "- 暂无相关记忆"
    return "\n".join(
        f"- [{m['id'][:8]} imp={m['importance']}] {m['description']}"
        for m in memories
    )


async def _social_block(
    session: AsyncSession,
    agent: Agent,
    state: AgentState,
    world_time: datetime,
) -> str:
    """阶段 19：社交动机块。

    暴露给 LLM：
    - 当前社交需求（social_need 0..1）和阈值。
    - 距离上次社交多久（仿真分钟）。
    - 同场景附近熟人（familiarity 排前 3）。

    用于驱动 ``socialize`` / ``request_interaction`` 的调用决策。
    """
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.db.models import Relationship

    settings = get_settings()
    threshold = settings.social_need_trigger_threshold / 100.0
    need = float(state.social_need or 0.0)
    last_social_at = getattr(state, "last_social_at", None)

    minutes_since = None
    if last_social_at is not None:
        try:
            minutes_since = int((world_time - last_social_at).total_seconds() / 60)
        except Exception:
            minutes_since = None

    # 全部熟人（familiarity > 0.2）+ 当前位置；排前 5 给 LLM
    rels_rows = (
        await session.execute(
            select(Relationship).where(
                Relationship.from_agent_id == agent.id,
                Relationship.familiarity > 0.2,
            )
        )
    ).scalars().all()
    rel_by_target = {r.to_entity_id: r for r in rels_rows}
    if not rel_by_target:
        nearby_friends_block = "- 附近暂无熟人"
        global_friends_block = "- 你目前还没有特别熟的人，可以主动认识陌生人"
    else:
        # 同场景的熟人按距离排序（最多 3 人）；不同场景的熟人按好感度排序（最多 3 人）
        target_ids = list(rel_by_target.keys())
        states_rows = (
            await session.execute(
                select(AgentState).where(AgentState.agent_id.in_(target_ids))
            )
        ).scalars().all()
        agents_rows = (
            await session.execute(select(Agent).where(Agent.id.in_(target_ids)))
        ).scalars().all()
        agents_by_id = {a.id: a for a in agents_rows}

        nearby: list[tuple[str, str, float, int]] = []
        far: list[tuple[str, str, float, str]] = []
        for st in states_rows:
            other = agents_by_id.get(st.agent_id)
            if other is None or other.entity_type not in {"human", "player"}:
                continue
            rel = rel_by_target[st.agent_id]
            fam = float(rel.familiarity)
            if st.scene_id == state.scene_id:
                dist = abs(st.x - state.x) + abs(st.y - state.y)
                nearby.append((st.agent_id, other.name, fam, dist))
            else:
                far.append((st.agent_id, other.name, fam, st.scene_id))
        nearby.sort(key=lambda t: (t[3], -t[2]))
        far.sort(key=lambda t: -t[2])
        if nearby:
            items = ", ".join(
                f"{name}({fid}, fam={fam:.2f}, dist={dist})"
                for fid, name, fam, dist in nearby[:3]
            )
            nearby_friends_block = f"- 同场景熟人：{items}"
        else:
            nearby_friends_block = "- 同场景暂无熟人"
        if far:
            items = ", ".join(
                f"{name}({fid}, fam={fam:.2f}, scene={scene})"
                for fid, name, fam, scene in far[:3]
            )
            global_friends_block = f"- 别处的熟人：{items}（如想见面，可调用 go_to_entity）"
        else:
            global_friends_block = "- 暂无外场景熟人"

    lines: list[str] = []
    lines.append(
        f"- 社交需求：{need:.2f}（阈值 {threshold:.2f}，"
        f"{'高于阈值，建议发起社交' if need >= threshold else '低于阈值，不急'}）"
    )
    if minutes_since is not None:
        lines.append(f"- 距离上次社交：{minutes_since} 仿真分钟")
    else:
        lines.append("- 还没有过社交记录")
    lines.append(nearby_friends_block)
    lines.append(global_friends_block)
    lines.append(
        "- 工具提示："
        "socialize（自动从附近挑人发起 chat）/ "
        "request_interaction（向身边 ≤4 格的实体发起 chat/help/trade）/ "
        "go_to_entity（先把自己派去某个 NPC 那里，到了再发 request_interaction）。"
    )
    return "\n".join(lines)


def _plan_block(plan_ctx: dict[str, Any]) -> str:
    """层次化计划摘要块（阶段 15）。"""
    if not plan_ctx:
        return "- 暂无计划"
    lines: list[str] = []
    if plan_ctx.get("daily_summary"):
        lines.append(f"- 今日主线：{plan_ctx['daily_summary']}")
    seg = plan_ctx.get("segment") or {}
    if seg:
        lines.append(
            f"- 当前时段：{seg.get('activity', '')} "
            f"({seg.get('start', '?')}-{seg.get('end', '?')}) "
            f"目标：{seg.get('goal', '')}"
        )
    cur = plan_ctx.get("current_task") or {}
    if cur:
        hint = cur.get("tool_hint")
        hint_str = f"（建议工具：{hint}）" if hint else ""
        lines.append(
            f"- 当前任务：{cur.get('title', '')}{hint_str}；"
            f"{cur.get('description', '')}"
        )
    if not lines:
        return "- 暂无计划"
    return "\n".join(lines)
