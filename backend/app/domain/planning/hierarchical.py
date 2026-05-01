"""
层次化规划（阶段 15.1-2）：daily_plan / hourly_schedule / task_decomposition。

三个层级：

- DailyPlan：一天内 4-7 段高层安排。由 LLM 或 schedule_template 规则生成。
- HourlyPlan：把当前 segment 拆到小时粒度（1-4 条）。
- TaskPlan：把当前小时拆为 2-4 个 5/10/30 分钟子任务。

存储：``agent_plans`` 表（跨 tick / 重启保持）+ Redis 缓存（读热路径）。

获取接口：``PlanningService.get_current_task(agent, now)`` 给 agent_decision
返回"当前应该推进的子任务"。任何层级缺失都会按顺序触发生成（LLM 优先 /
schedule_template 兜底），不抛异常。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.db.models import Agent, AgentPlan, Relationship
from app.llm.client import get_llm_client
from app.prompts import render_prompt
from app.services.memory_service import get_memory_service
from app.schemas.memory import MemorySearchRequest

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pydantic output schemas for LLM
# ---------------------------------------------------------------------------


class DailyPlanSegment(BaseModel):
    start: str = "00:00"
    end: str = "00:00"
    activity: str = ""
    location_id: str | None = None
    goal: str = ""
    priority: str = "normal"


class DailyPlanOutput(BaseModel):
    summary: str = ""
    segments: list[DailyPlanSegment] = Field(default_factory=list)


class HourlyItem(BaseModel):
    hour: int = 0
    activity: str = ""
    location_id: str | None = None
    focus: str = ""


class HourlyScheduleOutput(BaseModel):
    items: list[HourlyItem] = Field(default_factory=list)


class TaskItem(BaseModel):
    title: str = ""
    description: str = ""
    duration_minutes: int = 10
    location_id: str | None = None
    target_entity_id: str | None = None
    tool_hint: str | None = None


class TaskDecompositionOutput(BaseModel):
    tasks: list[TaskItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Plan dataclasses (runtime view)
# ---------------------------------------------------------------------------


@dataclass
class DailyPlan:
    id: str
    day_key: str
    summary: str
    segments: list[DailyPlanSegment]
    source: str = "rule"  # llm / rule
    version: int = 1


@dataclass
class HourlyPlan:
    id: str
    day_key: str
    hour: int
    items: list[HourlyItem]
    parent_plan_id: str | None = None
    source: str = "rule"


@dataclass
class TaskPlan:
    id: str
    day_key: str
    hour: int
    tasks: list[TaskItem]
    parent_plan_id: str | None = None
    source: str = "rule"
    cursor: int = 0  # 当前指向哪一个子任务
    activated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _day_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _hour_key(dt: datetime) -> int:
    return dt.hour


def _parse_hhmm(value: str, default: time) -> time:
    try:
        hh, mm = value.split(":")
        return time(hour=int(hh) % 24, minute=int(mm) % 60)
    except Exception:
        return default


class PlanningService:
    """跨 tick 的规划单例。计划结果可来自 LLM 或 schedule_template 规则。"""

    # ------------------------------------------------------------------
    # Daily plan
    # ------------------------------------------------------------------

    async def load_daily_plan(
        self,
        session: AsyncSession,
        *,
        agent_id: str,
        day_key: str,
    ) -> DailyPlan | None:
        stmt = (
            select(AgentPlan)
            .where(
                AgentPlan.agent_id == agent_id,
                AgentPlan.plan_type == "daily_plan",
                AgentPlan.day_key == day_key,
                AgentPlan.status == "active",
            )
            .order_by(desc(AgentPlan.version))
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        content = row.content or {}
        segs = [
            DailyPlanSegment.model_validate(s)
            for s in (content.get("segments") or [])
        ]
        return DailyPlan(
            id=row.id,
            day_key=row.day_key,
            summary=content.get("summary", ""),
            segments=segs,
            source=row.source,
            version=row.version,
        )

    async def ensure_daily_plan(
        self,
        session: AsyncSession,
        agent: Agent,
        *,
        world_time: datetime,
        force_refresh: bool = False,
    ) -> DailyPlan:
        """拿到当天的 daily plan；缺失则按"LLM → rule"生成一条并落库。"""
        day_key = _day_key(world_time)
        if not force_refresh:
            existing = await self.load_daily_plan(
                session, agent_id=agent.id, day_key=day_key
            )
            if existing is not None:
                return existing

        out = await self._generate_daily_plan(session, agent, world_time=world_time)
        row = AgentPlan(
            agent_id=agent.id,
            plan_type="daily_plan",
            day_key=day_key,
            content=out.model_dump(),
            summary=out.summary[:200] if out.summary else None,
            status="active",
            version=1,
            source="llm" if get_llm_client().settings.llm_is_configured else "rule",
        )
        if force_refresh:
            # supersede existing active row
            old_stmt = select(AgentPlan).where(
                AgentPlan.agent_id == agent.id,
                AgentPlan.plan_type == "daily_plan",
                AgentPlan.day_key == day_key,
                AgentPlan.status == "active",
            )
            for old in (await session.execute(old_stmt)).scalars().all():
                old.status = "superseded"
                row.version = max(row.version, (old.version or 0) + 1)
        session.add(row)
        await session.flush()
        dp = DailyPlan(
            id=row.id,
            day_key=day_key,
            summary=out.summary,
            segments=list(out.segments),
            source=row.source,
            version=row.version,
        )
        await self._cache_daily_plan(agent.id, dp)
        return dp

    async def _cache_daily_plan(self, agent_id: str, plan: DailyPlan) -> None:
        try:
            await get_redis().set_json(
                f"agent:{agent_id}:daily_plan:{plan.day_key}",
                {
                    "id": plan.id,
                    "summary": plan.summary,
                    "segments": [s.model_dump() for s in plan.segments],
                    "source": plan.source,
                    "version": plan.version,
                },
                ttl_seconds=60 * 60 * 12,
            )
        except Exception:
            pass

    async def _generate_daily_plan(
        self,
        session: AsyncSession,
        agent: Agent,
        *,
        world_time: datetime,
    ) -> DailyPlanOutput:
        """LLM 优先；失败走 schedule_template 规则。"""
        client = get_llm_client()
        if client.settings.llm_is_configured:
            # profile / goals / relationships / memory blocks
            profile_block = self._profile_block(agent)
            goals = agent.long_term_goals or []
            goals_block = "\n".join(f"- {g}" for g in goals) or "- 暂无"

            rel_stmt = (
                select(Relationship)
                .where(Relationship.from_agent_id == agent.id)
                .order_by(desc(Relationship.familiarity))
                .limit(5)
            )
            rels = (await session.execute(rel_stmt)).scalars().all()
            rel_block = (
                "\n".join(
                    f"- {r.to_entity_id}: fam={r.familiarity:.1f} "
                    f"trust={r.trust:.1f} aff={r.affection:.1f}"
                    + (f" | {r.summary}" if r.summary else "")
                    for r in rels
                )
                or "- 暂无"
            )

            recent_mems = await get_memory_service().search(
                session,
                agent.id,
                MemorySearchRequest(query="昨天", limit=6),
                now=world_time,
            )
            mem_block = (
                "\n".join(
                    f"- imp={r.memory.importance} {r.memory.description}"
                    for r in recent_mems[:6]
                )
                or "- 暂无相关记忆"
            )

            user_prompt, meta = render_prompt(
                "daily_plan",
                {
                    "profile_block": profile_block,
                    "goals_block": goals_block,
                    "relationships_block": rel_block,
                    "memory_block": mem_block,
                    "today": _day_key(world_time),
                },
            )
            data = await client.chat_json(
                system="你必须只输出 JSON，不要包含 markdown 或解释。",
                user=user_prompt,
                model=client.settings.model_for_role(meta.model_role or "planning"),
                temperature=0.4,
                max_tokens=900,
                prompt_template_id=meta.id,
                prompt_version=meta.version,
                caller_module="planning.daily_plan",
            )
            if data is not None:
                try:
                    return DailyPlanOutput.model_validate(data)
                except ValidationError:
                    logger.warning("daily_plan schema invalid, fallback to rule")

        # 规则兜底：从 schedule_template 直接翻译
        return self._rule_daily_plan(agent)

    def _rule_daily_plan(self, agent: Agent) -> DailyPlanOutput:
        template = agent.schedule_template or []
        segments: list[DailyPlanSegment] = []
        for item in template:
            segments.append(
                DailyPlanSegment(
                    start=item.get("start", "07:00"),
                    end=item.get("end", "08:00"),
                    activity=item.get("activity", "日常活动"),
                    location_id=item.get("location_id"),
                    goal=item.get("description", item.get("activity", "")),
                    priority="normal",
                )
            )
        if not segments:
            segments = [
                DailyPlanSegment(
                    start="07:00",
                    end="22:00",
                    activity="在小镇中散步",
                    location_id=None,
                    goal="随兴所至",
                    priority="low",
                ),
                DailyPlanSegment(
                    start="22:00",
                    end="07:00",
                    activity="回家休息",
                    location_id=agent.home_location_id,
                    goal="睡觉",
                    priority="high",
                ),
            ]
        return DailyPlanOutput(summary=agent.lifestyle or "", segments=segments)

    # ------------------------------------------------------------------
    # Hourly schedule / Task decomposition
    # ------------------------------------------------------------------

    def pick_segment(
        self, plan: DailyPlan, world_time: datetime
    ) -> DailyPlanSegment | None:
        """给定游戏时间，选当前活跃 segment。"""
        if not plan.segments:
            return None
        now_t = world_time.time().replace(second=0, microsecond=0)
        for seg in plan.segments:
            start = _parse_hhmm(seg.start, time(0, 0))
            end = _parse_hhmm(seg.end, time(23, 59))
            if start <= end:
                if start <= now_t < end:
                    return seg
            else:
                # 跨夜段，如 22:00 - 07:00
                if now_t >= start or now_t < end:
                    return seg
        # 兜底：选最近的一个
        return plan.segments[0]

    async def ensure_task_plan(
        self,
        session: AsyncSession,
        agent: Agent,
        *,
        world_time: datetime,
        daily: DailyPlan,
        segment: DailyPlanSegment,
    ) -> TaskPlan:
        """获取当前小时的 task_decomposition。缺失则现场生成。"""
        day_key = _day_key(world_time)
        hour_key = _hour_key(world_time)

        stmt = (
            select(AgentPlan)
            .where(
                AgentPlan.agent_id == agent.id,
                AgentPlan.plan_type == "task_decomposition",
                AgentPlan.day_key == day_key,
                AgentPlan.hour_key == hour_key,
                AgentPlan.status == "active",
            )
            .order_by(desc(AgentPlan.version))
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            tasks = [
                TaskItem.model_validate(t)
                for t in (row.content or {}).get("tasks", [])
            ]
            return TaskPlan(
                id=row.id,
                day_key=day_key,
                hour=hour_key,
                tasks=tasks,
                parent_plan_id=row.parent_plan_id,
                source=row.source,
                cursor=(row.content or {}).get("cursor", 0),
            )

        out = await self._generate_task_decomposition(
            session, agent, world_time=world_time, segment=segment
        )
        row = AgentPlan(
            agent_id=agent.id,
            plan_type="task_decomposition",
            day_key=day_key,
            hour_key=hour_key,
            parent_plan_id=daily.id,
            content={"tasks": [t.model_dump() for t in out.tasks], "cursor": 0},
            summary=(out.tasks[0].title if out.tasks else None),
            status="active",
            version=1,
            source="llm" if get_llm_client().settings.llm_is_configured else "rule",
        )
        session.add(row)
        await session.flush()
        return TaskPlan(
            id=row.id,
            day_key=day_key,
            hour=hour_key,
            tasks=list(out.tasks),
            parent_plan_id=daily.id,
            source=row.source,
        )

    async def _generate_task_decomposition(
        self,
        session: AsyncSession,
        agent: Agent,
        *,
        world_time: datetime,
        segment: DailyPlanSegment,
    ) -> TaskDecompositionOutput:
        client = get_llm_client()
        if client.settings.llm_is_configured:
            user_prompt, meta = render_prompt(
                "task_decomposition",
                {
                    "profile_block": self._profile_block(agent),
                    "hour_block": (
                        f"- segment: {segment.activity} ({segment.start}-{segment.end})\n"
                        f"- goal: {segment.goal}\n"
                        f"- location: {segment.location_id or '未指定'}"
                    ),
                    "perception_block": "- (本接口不提供实时感知，保持 tool_hint 保守)",
                    "tool_catalog": (
                        "- move_to_location\n"
                        "- request_interaction\n"
                        "- socialize\n"
                        "- interact_with_object\n"
                        "- work_at_location\n"
                        "- have_meal\n"
                        "- rest_at\n"
                        "- wait\n"
                        "- face_entity"
                    ),
                },
            )
            data = await client.chat_json(
                system="你必须只输出 JSON。",
                user=user_prompt,
                model=client.settings.model_for_role(meta.model_role or "planning"),
                temperature=0.4,
                max_tokens=500,
                prompt_template_id=meta.id,
                prompt_version=meta.version,
                caller_module="planning.task_decomposition",
            )
            if data is not None:
                try:
                    return TaskDecompositionOutput.model_validate(data)
                except ValidationError:
                    logger.warning("task_decomposition schema invalid, fallback")

        # Rule fallback：根据 segment 推断一两个任务
        tasks: list[TaskItem] = []
        if segment.location_id:
            tasks.append(
                TaskItem(
                    title=f"前往{segment.activity}",
                    description=segment.goal or segment.activity,
                    duration_minutes=10,
                    location_id=segment.location_id,
                    tool_hint="move_to_location",
                )
            )
        tasks.append(
            TaskItem(
                title=segment.activity or "处理当前事务",
                description=segment.goal or "在该时间段进行相应活动",
                duration_minutes=30,
                tool_hint="wait",
            )
        )
        return TaskDecompositionOutput(tasks=tasks)

    # ------------------------------------------------------------------
    # 对外总入口
    # ------------------------------------------------------------------

    async def get_current_context(
        self,
        session: AsyncSession,
        agent: Agent,
        *,
        world_time: datetime,
    ) -> dict[str, Any]:
        """供 agent_decision 使用：返回一个 dict 带 current_task / daily / hour 信息。"""
        daily = await self.ensure_daily_plan(session, agent, world_time=world_time)
        seg = self.pick_segment(daily, world_time) or (
            daily.segments[0] if daily.segments else None
        )
        if seg is None:
            return {
                "daily_summary": daily.summary,
                "segment": None,
                "current_task": None,
            }
        task_plan = await self.ensure_task_plan(
            session,
            agent,
            world_time=world_time,
            daily=daily,
            segment=seg,
        )
        current = task_plan.tasks[task_plan.cursor] if task_plan.tasks else None
        return {
            "daily_summary": daily.summary,
            "daily_source": daily.source,
            "segment": seg.model_dump(),
            "current_task": current.model_dump() if current else None,
            "cursor": task_plan.cursor,
            "tasks_total": len(task_plan.tasks),
        }

    async def revise_on_interrupt(
        self,
        session: AsyncSession,
        *,
        agent_id: str,
        reason: str,
        world_time: datetime,
    ) -> None:
        """外部事件打断时，把当前 task_decomposition 置 superseded，
        下次 ensure 会自动重新生成。"""
        day_key = _day_key(world_time)
        hour_key = _hour_key(world_time)
        stmt = select(AgentPlan).where(
            AgentPlan.agent_id == agent_id,
            AgentPlan.plan_type == "task_decomposition",
            AgentPlan.day_key == day_key,
            AgentPlan.hour_key == hour_key,
            AgentPlan.status == "active",
        )
        rows = (await session.execute(stmt)).scalars().all()
        for row in rows:
            row.status = "superseded"
            row.summary = (row.summary or "") + f" [interrupt: {reason[:40]}]"
        await session.flush()

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _profile_block(agent: Agent) -> str:
        parts = [f"- 名字：{agent.name}", f"- 类型：{agent.entity_type}"]
        if agent.occupation:
            parts.append(f"- 职业：{agent.occupation}")
        if agent.personality:
            parts.append(f"- 性格：{', '.join(agent.personality)}")
        if agent.background:
            parts.append(f"- 背景：{agent.background[:120]}")
        if agent.lifestyle:
            parts.append(f"- 生活习惯：{agent.lifestyle[:120]}")
        if agent.long_term_goals:
            parts.append(f"- 长期目标：{' / '.join(agent.long_term_goals)}")
        return "\n".join(parts)


_service: PlanningService | None = None


def get_planning_service() -> PlanningService:
    global _service
    if _service is None:
        _service = PlanningService()
    return _service


__all__ = [
    "DailyPlan",
    "DailyPlanOutput",
    "DailyPlanSegment",
    "HourlyItem",
    "HourlyPlan",
    "HourlyScheduleOutput",
    "PlanningService",
    "TaskDecompositionOutput",
    "TaskItem",
    "TaskPlan",
    "get_planning_service",
]
