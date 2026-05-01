"""
SimulationEngine：世界推进器。

职责（与 `仿真循环设计模块实施方案.md` 对齐）：
1. 读取输入队列（玩家移动、交互）；
2. 推进每个 Agent 的已有路径（world tick）；
3. 到达目标时触发事件；
4. 达到 AI tick 间隔时重新为每个 Agent 选择下一步（规则版 + 可选 LLM）；
5. 将增量通过事件总线广播给 WebSocket。

MVP 中：一个进程持有一个 SimulationEngine，全局 simulation 也是单例（single-player）。
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.event_bus import get_event_bus
from app.core.logging import get_logger
from app.core.redis_client import (
    get_redis,
    key_agent_runtime,
    key_scene_active_entities,
)
from app.core.time import iso, utcnow
from app.core.trace_context import TraceContext
from app.db.models import (
    Agent,
    AgentAction,
    AgentState,
    DialogueMessage,
    Location,
    MapScene,
    Portal,
    Simulation,
    Task,
    WorldEvent,
    WorldObject,
)
from app.services.event_router import WORLD_EVENT_TOPIC
from app.domain.simulation.natural_events import (
    NaturalEventContext,
    WeatherState,
    tick_all as _tick_natural_all,
)
from app.domain.simulation.rule_agent import (
    AgentDecision,
    NaturalWorldContext,
    decide_animal_action,
    decide_human_action,
)
from app.domain.world.scene_cache import get_scene_cache
from app.schemas.agent import AgentRuntimeState
from app.schemas.simulation import (
    Simulation as SimulationSchema,
    SimulationDeltaPayload,
    WorldEvent as WorldEventSchema,
)
from app.schemas.world import TilePosition
from app.services.agent_service import runtime_from_orm
from app.websocket.gateway import WS_BROADCAST_TOPIC

logger = get_logger(__name__)


@dataclass
class PlayerInputEvent:
    kind: str  # "move_path" / "teleport" / "interact"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineAgent:
    """引擎内存中的 agent 运行态副本。减少每 tick 的 ORM 负担。"""

    id: str
    entity_type: str
    name: str
    scene_id: str
    x: int
    y: int
    state: str
    path: list[tuple[int, int]] = field(default_factory=list)
    current_goal: str | None = None
    current_action_id: str | None = None
    last_decision_at: datetime | None = None
    status_effects: list[str] = field(default_factory=list)
    emotion: str | None = None
    facing: str = "down"
    energy: float = 1.0
    hunger: float = 0.2
    social_need: float = 0.3
    fear: float = 0.0
    dirty: bool = True  # 是否需要同步到数据库
    is_player: bool = False
    # 阶段 19：忙碌 / 可打断 / 优先级 / 上次社交
    busy_until: datetime | None = None
    interruptible: bool = True
    current_priority: int = 0
    last_social_at: datetime | None = None


@dataclass
class EngineObject:
    """引擎内存中的 WorldObject 运行态副本，用于自然事件系统读写对象状态。"""

    id: str
    scene_id: str
    name: str
    object_type: str
    x: int
    y: int
    width: int = 1
    height: int = 1
    blocks_movement: bool = False
    available_interactions: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    dirty: bool = False  # state 被修改后置 True，引擎在 _persist_tick 写回 DB
    pending_create: bool = False  # 由 spawn_object 创建，尚未写入 DB
    pending_delete: bool = False  # 由 despawn_object 标记，下次 persist 时从 DB 删除


class SimulationEngine:
    """
    并发模型：
    - 单一 asyncio task 驱动 world tick。
    - 玩家意图通过队列注入，避免竞态。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._simulation: Simulation | None = None
        self._agents: dict[str, EngineAgent] = {}
        self._agents_meta: dict[str, Agent] = {}
        self._locations: dict[str, dict[str, Any]] = {}
        self._portals_by_scene: dict[str, list[Portal]] = {}
        self._portals: dict[str, Portal] = {}
        self._player_id: str | None = None
        self._input_queue: deque[PlayerInputEvent] = deque()
        self._ai_tick_interval_real: float = 2.0  # 真实秒：每 N 秒执行一次 AI tick
        self._last_ai_tick: float = 0.0
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._stopping = False
        self._rng = random.Random()
        self._step = 0
        self._pending_events: list[WorldEvent] = []
        self._world_time: datetime = utcnow()
        # 每个 Agent 上次反思/日结的游戏时间
        self._last_reflect_at: dict[str, datetime] = {}
        self._last_summary_day: dict[str, str] = {}
        # CHATTING 进入的真实时间戳（用于超时恢复，独立于游戏时间）
        self._chatting_since_real: dict[str, float] = {}
        # 自然事件系统：WorldObject 内存缓存 + 全局天气
        self._objects: dict[str, EngineObject] = {}
        self._weather: WeatherState = WeatherState()
        # 阶段 19+：基础需求演化与自主社交邂逅
        self._last_needs_evolve_at: datetime | None = None
        self._last_encounter_scan_at: datetime | None = None
        # 邂逅冷却：agent_id → 上次主动发起邂逅的世界时间
        self._encounter_cooldown_until: dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        async with self._session_factory() as session:
            await self._load_state(session)
        self._running = True
        self._stopping = False
        # 引擎启动后立刻向所有连接的客户端广播一次当前天气，
        # 解决"页面刷新后天气叠加层失去状态"问题
        self.broadcast_weather_state()
        self._task = asyncio.create_task(self._main_loop(), name="sim-engine")
        logger.info("simulation engine started", extra={"simulation_id": self._sim_id()})

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._running = False

    def _sim_id(self) -> str:
        return self._simulation.id if self._simulation else "no-sim"

    # ------------------------------------------------------------------
    # 状态加载
    # ------------------------------------------------------------------

    async def _load_state(self, session: AsyncSession) -> None:
        await get_scene_cache().refresh(session)

        sim = (await session.execute(select(Simulation).limit(1))).scalar_one_or_none()
        if sim is None:
            sim = Simulation(
                status="running" if get_settings().simulation_autostart else "idle",
                world_time=datetime(2026, 4, 30, 7, 30, tzinfo=utcnow().tzinfo),
                world_tick_hz=get_settings().simulation_world_tick_hz,
                ai_tick_minutes=get_settings().simulation_ai_tick_minutes,
                speed_multiplier=get_settings().simulation_speed_default,
                current_step=0,
            )
            session.add(sim)
            await session.commit()
            await session.refresh(sim)
        self._simulation = sim
        self._step = sim.current_step
        self._world_time = sim.world_time

        agents = (await session.execute(select(Agent))).scalars().all()
        self._agents_meta = {a.id: a for a in agents}
        states = (await session.execute(select(AgentState))).scalars().all()
        state_by_id = {s.agent_id: s for s in states}

        self._agents.clear()
        for a in agents:
            st = state_by_id.get(a.id)
            if st is None:
                # 没有运行态的 agent 跳过。Seed 脚本应当补齐所有运行态。
                logger.warning("agent %s has no runtime state", a.id)
                continue
            eng = EngineAgent(
                id=a.id,
                entity_type=a.entity_type,
                name=a.name,
                scene_id=st.scene_id,
                x=st.x,
                y=st.y,
                state=st.state,
                path=[(p["x"], p["y"]) for p in (st.path or [])],
                current_goal=st.current_goal,
                current_action_id=st.current_action_id,
                last_decision_at=st.last_decision_at,
                status_effects=list(st.status_effects or []),
                emotion=st.emotion,
                facing=st.facing,
                energy=st.energy,
                hunger=st.hunger,
                social_need=st.social_need,
                fear=st.fear,
                dirty=False,
                is_player=(a.entity_type == "player"),
                busy_until=st.busy_until,
                interruptible=st.interruptible,
                current_priority=st.current_priority,
                last_social_at=st.last_social_at,
            )
            if eng.is_player:
                self._player_id = a.id
            self._agents[a.id] = eng

        # Location & portal 缓存
        loc_rows = (await session.execute(select(Location))).scalars().all()
        self._locations = {
            l.id: {
                "id": l.id,
                "scene_id": l.scene_id,
                "name": l.name,
                "location_type": l.location_type,
                "bounds": l.bounds,
                "entry_tiles": l.entry_tiles,
                "open_hours": l.open_hours,
                "tags": l.tags,
            }
            for l in loc_rows
        }
        portals = (await session.execute(select(Portal))).scalars().all()
        self._portals = {p.id: p for p in portals}
        self._portals_by_scene.clear()
        for p in portals:
            self._portals_by_scene.setdefault(p.from_scene_id, []).append(p)

        # 加载所有 WorldObject 到内存缓存，供自然事件系统读写
        objects = (await session.execute(select(WorldObject))).scalars().all()
        self._objects = {
            o.id: EngineObject(
                id=o.id,
                scene_id=o.scene_id,
                name=o.name,
                object_type=o.object_type,
                x=int(o.position.get("x", 0)),
                y=int(o.position.get("y", 0)),
                width=int(o.size.get("width", 1)),
                height=int(o.size.get("height", 1)),
                blocks_movement=o.blocks_movement,
                available_interactions=list(o.available_interactions or []),
                state=dict(o.state or {}),
                tags=list(o.tags or []),
                dirty=False,
            )
            for o in objects
        }

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        try:
            while not self._stopping:
                if self._simulation is None or self._simulation.status != "running":
                    await asyncio.sleep(0.2)
                    continue
                await self._tick()
                # world tick：HZ 受 speed_multiplier 影响
                hz = max(self._simulation.world_tick_hz * self._simulation.speed_multiplier, 0.5)
                await asyncio.sleep(1.0 / hz)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("simulation engine crashed")

    async def _tick(self) -> None:
        sim = self._simulation
        assert sim is not None

        # 推进游戏内时间：一次 world tick = 1 游戏分钟 / speed_multiplier
        # 这样 5 Hz + speed=1 => 游戏时间 5 分钟 / 真实秒
        minutes_per_tick = 1
        self._world_time = self._world_time + timedelta(minutes=minutes_per_tick)
        self._step += 1
        # 每个 world tick 作为一条 trace 的顶层 span。
        # 子模块（决策、工具、LLM）在同一 trace 下嵌套 span。
        with TraceContext.start_trace(
            "world_tick",
            simulation_id=self._sim_id(),
        ):
            await self._tick_inner()

    async def _tick_inner(self) -> None:
        sim = self._simulation
        assert sim is not None

        entity_updates: list[AgentRuntimeState] = []
        now_dt = self._world_time

        # 1. 处理玩家输入
        while self._input_queue:
            evt = self._input_queue.popleft()
            await self._handle_input(evt)

        # 2. 每个 Agent 推进已有路径 / 触发到达
        for agent in self._agents.values():
            if agent.is_player:
                continue
            if agent.state == "CHATTING":
                # 对话中不移动
                continue
            if agent.path:
                next_tile = agent.path[0]
                agent.facing = _face_towards((agent.x, agent.y), next_tile)
                agent.x, agent.y = next_tile
                agent.path.pop(0)
                agent.state = "MOVING" if agent.path else "IDLE"
                agent.dirty = True
                if not agent.path:
                    await self._on_agent_arrived(agent)

        # 3. 玩家按路径自动前进
        player = self._current_player()
        if player is not None and player.path:
            next_tile = player.path[0]
            player.facing = _face_towards((player.x, player.y), next_tile)
            player.x, player.y = next_tile
            player.path.pop(0)
            player.state = "MOVING" if player.path else "IDLE"
            player.dirty = True
            await self._check_player_environment(player)

        # 阶段 19+：基础需求随时间演化（驱动 LLM 主动选 socialize / have_meal / rest_at）
        self._evolve_basic_needs(now_dt)

        # 4. AI Decision Tick（每个 Agent 独立按游戏分钟间隔决策）
        await self._decide_all(now_dt)
        # 阶段 19+：周期性扫描自主社交邂逅（两个空闲 NPC 靠近 + 一方 social_need 高 → 自动 chat）
        await self._scan_social_encounters(now_dt)
        # 反思/日结通过 daily_reflection 任务异步执行，不阻塞世界 tick
        await self._enqueue_reflect_batch(now_dt)

        # 4.5 自然事件 tick（修改 EngineObject.state，生成 WorldEvent）
        self._tick_natural_events(now_dt)

        # 5. 汇总 updates
        for agent in self._agents.values():
            if agent.dirty:
                entity_updates.append(self._to_runtime_state(agent))

        # 6. 持久化 + 广播
        events_to_broadcast = list(self._pending_events)
        self._pending_events.clear()
        await self._persist_tick(sim, entity_updates, events_to_broadcast)
        await self._broadcast_delta(entity_updates, events_to_broadcast)

        for agent in self._agents.values():
            agent.dirty = False
        for obj in self._objects.values():
            obj.dirty = False

    def _to_runtime_state(self, a: EngineAgent) -> AgentRuntimeState:
        return AgentRuntimeState(
            agent_id=a.id,
            scene_id=a.scene_id,
            position=TilePosition(x=a.x, y=a.y),
            state=a.state,
            emotion=a.emotion,
            energy=a.energy,
            hunger=a.hunger,
            social_need=a.social_need,
            fear=a.fear,
            status_effects=a.status_effects,
            current_action_id=a.current_action_id,
            current_goal=a.current_goal,
            facing=a.facing,
            updated_at=utcnow(),
            busy_until=a.busy_until,
            interruptible=a.interruptible,
            current_priority=a.current_priority,
            last_social_at=a.last_social_at,
        )

    # ------------------------------------------------------------------
    # 阶段 19+：基础需求演化 + 自主社交邂逅
    # ------------------------------------------------------------------

    def _evolve_basic_needs(self, now_dt: datetime) -> None:
        """让 ``social_need`` / ``hunger`` / ``energy`` 按真实游戏时间增长，
        驱动 LLM 自主选择 socialize / have_meal / rest_at。
        """
        settings = get_settings()
        last = self._last_needs_evolve_at
        if last is None:
            self._last_needs_evolve_at = now_dt
            return
        elapsed_minutes = (now_dt - last).total_seconds() / 60.0
        if elapsed_minutes <= 0:
            return
        self._last_needs_evolve_at = now_dt

        social_dx = settings.needs_social_growth_per_minute * elapsed_minutes
        hunger_dx = settings.needs_hunger_growth_per_minute * elapsed_minutes
        energy_dx = settings.needs_energy_decay_per_minute * elapsed_minutes

        for agent in self._agents.values():
            if agent.is_player:
                continue
            if agent.entity_type != "human":
                continue
            if agent.state == "SLEEPING":
                # 睡眠：恢复精力，社交/饥饿停滞
                agent.energy = min(1.0, agent.energy + energy_dx * 4)
                agent.dirty = True
                continue
            if agent.state == "EATING":
                # 用餐：饥饿快速衰减
                agent.hunger = max(0.0, agent.hunger - hunger_dx * 6)
                agent.dirty = True
                continue
            if agent.state == "RESTING":
                agent.energy = min(1.0, agent.energy + energy_dx * 3)
                agent.dirty = True
                continue
            if agent.state == "CHATTING":
                # 对话中：社交需求被持续满足
                agent.social_need = max(0.0, agent.social_need - social_dx * 2)
                agent.dirty = True
                continue

            # 默认：随时间累积需求
            new_social = min(1.0, agent.social_need + social_dx)
            new_hunger = min(1.0, agent.hunger + hunger_dx)
            new_energy = max(0.0, agent.energy - energy_dx)
            if (
                abs(new_social - agent.social_need) > 1e-4
                or abs(new_hunger - agent.hunger) > 1e-4
                or abs(new_energy - agent.energy) > 1e-4
            ):
                agent.social_need = new_social
                agent.hunger = new_hunger
                agent.energy = new_energy
                agent.dirty = True

    async def _scan_social_encounters(self, now_dt: datetime) -> None:
        """阶段 19+：自主社交邂逅扫描器。

        当两个空闲 / 闲置的 NPC 在同一场景内靠得很近，且至少一方
        ``social_need`` 已超过阈值时，由引擎层主动发起一次 ``chat``
        请求（走完整的 InteractionService 评估 / 广播 / 派发流程）。

        这弥补了 LLM async 路径下 ``socialize`` 工具滞后于 rule 兜底的问题：
        即使 NPC 已经按日程在路上走，引擎也能在 idle 间隙触发自发对话。

        防抖：``self._encounter_cooldown_until`` 记录每个 NPC 上次主动发起
        的截止时间；同一 NPC 在冷却内不会重复主动出击。
        """
        settings = get_settings()
        last = self._last_encounter_scan_at
        scan_interval_min = max(1, settings.social_encounter_scan_minutes)
        if last is not None and (now_dt - last).total_seconds() < scan_interval_min * 60:
            return
        self._last_encounter_scan_at = now_dt

        threshold = settings.social_need_trigger_threshold / 100.0
        max_dist = max(1, settings.social_encounter_distance)

        # 过滤"想找人 + 现在能找"的候选
        def _is_idle(a: EngineAgent) -> bool:
            if a.state in {"IDLE", "WAITING"} and not a.path:
                return True
            return False

        def _on_cooldown(a: EngineAgent) -> bool:
            until = self._encounter_cooldown_until.get(a.id)
            return until is not None and until > now_dt

        eager: list[EngineAgent] = []
        for a in self._agents.values():
            if a.is_player or a.entity_type != "human":
                continue
            if not _is_idle(a) or _on_cooldown(a):
                continue
            if a.busy_until is not None and a.busy_until > now_dt:
                continue
            if a.social_need < threshold:
                continue
            eager.append(a)
        if not eager:
            return

        # 按 social_need 高的排前
        eager.sort(key=lambda a: a.social_need, reverse=True)

        chosen_pairs: list[tuple[str, str]] = []
        used: set[str] = set()
        for initiator in eager:
            if initiator.id in used:
                continue
            best_target: EngineAgent | None = None
            best_dist = max_dist + 1
            for other in self._agents.values():
                if other.id == initiator.id or other.is_player:
                    continue
                if other.entity_type != "human":
                    continue
                if other.scene_id != initiator.scene_id:
                    continue
                if other.id in used:
                    continue
                if not _is_idle(other):
                    continue
                if other.busy_until is not None and other.busy_until > now_dt:
                    continue
                d = abs(other.x - initiator.x) + abs(other.y - initiator.y)
                if d > max_dist:
                    continue
                if d < best_dist:
                    best_dist = d
                    best_target = other
            if best_target is None:
                continue
            chosen_pairs.append((initiator.id, best_target.id))
            used.add(initiator.id)
            used.add(best_target.id)
            cooldown = timedelta(minutes=settings.social_encounter_cooldown_minutes)
            self._encounter_cooldown_until[initiator.id] = now_dt + cooldown
            self._encounter_cooldown_until[best_target.id] = now_dt + cooldown

        if not chosen_pairs:
            return

        # 异步派发，避免阻塞 tick
        asyncio.create_task(
            self._dispatch_social_encounters(chosen_pairs),
            name=f"social-encounter:{self._step}",
        )

    async def _dispatch_social_encounters(
        self, pairs: list[tuple[str, str]]
    ) -> None:
        """逐对调用 InteractionService.create_request（独立 session，避免阻塞主 tick）。"""
        from app.services.interaction_service import get_interaction_service

        svc = get_interaction_service()
        for requester_id, target_id in pairs:
            try:
                async with self._session_factory() as session:
                    await svc.create_request(
                        session,
                        requester_id=requester_id,
                        target_id=target_id,
                        kind="chat",
                        reason="（自然邂逅）",
                        requester_priority=2,
                        simulation_id=self._sim_id(),
                    )
                    await session.commit()
            except Exception:
                logger.debug(
                    "social encounter dispatch failed for %s -> %s",
                    requester_id,
                    target_id,
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # AI 决策
    # ------------------------------------------------------------------

    async def _decide_all(self, now_dt: datetime) -> None:
        """
        决策策略：LLM 为主，规则为兜底。

        - 未配置 LLM（LLM_ENABLED=false 或无 key）：全部走规则版。
        - 配置 LLM + ``task_queue_mode=async``（默认）：主循环**不阻塞等待 LLM**，
          而是投递 ``agent_decision`` 任务到 TaskQueue。Worker 异步调 LLM，
          决策结果通过工具副作用回写世界状态，主循环在下一次 tick 时才看到。
          本轮仍然先走规则兜底，确保 NPC 永远有即时行为。
        - ``task_queue_mode=sync``：沿用旧路径——主循环内直接 await LLM 决策，
          成功则应用；全失败回退规则（保留用于回归/调试）。
        - 每个 Agent 独立按 ai_tick_minutes 游戏分钟间隔决策，避免全局时钟相互阻塞。
        - Engine 主循环必须永不因 LLM 而卡住；任何异常被吞掉改用规则决策。
        """
        from app.core.config import get_settings
        from app.llm.agent_decision import decide_with_llm

        settings = get_settings()
        llm_enabled = settings.llm_is_configured
        async_mode = settings.task_queue_mode == "async"
        ai_interval = self._simulation.ai_tick_minutes if self._simulation else 5
        grids = {
            sid: g
            for sid in get_scene_cache().all_scene_ids()
            if (g := get_scene_cache().get_grid(sid))
        }

        for agent in self._agents.values():
            if agent.is_player:
                continue
            if agent.state == "SLEEPING":
                continue
            if agent.state == "CHATTING":
                # 超时自动解除：真实时间超过 90 秒后 NPC 恢复自主行动
                import time as _time
                since = self._chatting_since_real.get(agent.id)
                if since is None or (_time.time() - since) > 90.0:
                    agent.state = "IDLE"
                    self._chatting_since_real.pop(agent.id, None)
                    agent.dirty = True
                else:
                    continue
            if agent.state == "BUSY_REFUSING":
                # 阶段 19：软拒后短暂展示台词，3 秒真实时间后自动恢复
                import time as _time
                since = self._chatting_since_real.get(agent.id)
                if since is None or (_time.time() - since) > 3.0:
                    agent.state = "IDLE"
                    agent.current_goal = None
                    self._chatting_since_real.pop(agent.id, None)
                    agent.dirty = True
                else:
                    continue
            if agent.state == "AWAITING_RESPONSE":
                # 等待对方响应中，不主动决策；超时由 interaction service 处理
                import time as _time
                since = self._chatting_since_real.get(agent.id)
                if since is None or (_time.time() - since) > 30.0:
                    agent.state = "IDLE"
                    self._chatting_since_real.pop(agent.id, None)
                    agent.dirty = True
                else:
                    continue
            if agent.path:
                continue
            # 阶段 19：busy_until 内的 NPC 跳过决策（工作/吃饭/休息中）
            if agent.busy_until is not None and agent.busy_until > now_dt:
                # 状态保持，确保前端气泡能持续显示
                continue
            elif agent.busy_until is not None and agent.busy_until <= now_dt:
                # 忙碌结束：清理标志，让 NPC 回到日程
                agent.busy_until = None
                agent.current_priority = 0
                agent.interruptible = True
                if agent.state in {"WORKING", "EATING", "RESTING"}:
                    agent.state = "IDLE"
                    agent.dirty = True
            # 每个 Agent 独立检查自身的决策冷却时间
            if (
                agent.last_decision_at is not None
                and (now_dt - agent.last_decision_at).total_seconds() < ai_interval * 60
            ):
                continue
            meta = self._agents_meta.get(agent.id)
            if meta is None:
                continue

            applied = False
            if llm_enabled and async_mode:
                # 异步路径：投递任务，主循环立即返回；worker 完成后下一轮 tick
                # 通过数据库状态感知变化。本轮先让规则兜底生成即时行为，避免静止。
                try:
                    await self._enqueue_decision_task(agent.id, now_dt)
                except Exception:
                    logger.debug("enqueue agent_decision failed", exc_info=True)
            elif llm_enabled:
                try:
                    async with self._session_factory() as llm_session:
                        results = await decide_with_llm(
                            llm_session,
                            agent_id=agent.id,
                            simulation_id=self._sim_id(),
                            world_time=now_dt,
                        )
                        await llm_session.commit()
                        if results is not None:
                            any_success = any(r.success for r in results)
                            if any_success:
                                agent.last_decision_at = now_dt
                                agent.dirty = True
                                applied = True
                                if not agent.path and agent.state == "IDLE":
                                    agent.state = "WAITING"
                                self._pending_events.append(
                                    _new_event(
                                        simulation_id=self._sim_id(),
                                        event_type="llm.task_finished",
                                        actor_entity_id=agent.id,
                                        scene_id=agent.scene_id,
                                        description=f"{agent.name}：LLM 决策完成",
                                        importance=1,
                                        payload={"results": [r.model_dump() for r in results]},
                                        created_at=utcnow(),
                                    )
                                )
                            else:
                                logger.warning(
                                    "LLM tools all failed for %s; fallback to rule", agent.id
                                )
                except Exception:
                    logger.exception("LLM decision failed for %s; fallback to rule", agent.id)

            if applied:
                continue

            fake_state = _ORMStateView(agent)
            try:
                if meta.entity_type == "animal":
                    nearby = None
                    p = self._current_player()
                    if p is not None and p.scene_id == agent.scene_id:
                        if abs(p.x - agent.x) + abs(p.y - agent.y) <= 6:
                            nearby = (p.x, p.y)
                    decision = decide_animal_action(
                        agent_row=meta,
                        state_row=fake_state,
                        grids=grids,
                        rng=self._rng,
                        nearby_player=nearby,
                    )
                else:
                    natural_ctx = self._build_natural_ctx_for_agent(agent)
                    decision = decide_human_action(
                        agent_row=meta,
                        state_row=fake_state,
                        world_time=now_dt,
                        locations=self._locations,
                        grids=grids,
                        portals_by_scene=self._portals_by_scene,
                        natural_ctx=natural_ctx,
                    )
            except Exception:
                logger.exception("decide failed for %s", agent.id)
                continue
            self._apply_decision(agent, decision, now_dt)

    def _apply_decision(
        self, agent: EngineAgent, decision: AgentDecision, now_dt: datetime
    ) -> None:
        agent.last_decision_at = now_dt
        agent.current_goal = decision.description or agent.current_goal
        agent.dirty = True
        if decision.path:
            agent.path = list(decision.path)
            if agent.path and agent.path[0] == (agent.x, agent.y):
                agent.path.pop(0)
            agent.state = "MOVING" if agent.path else "INTERACTING"
        elif decision.action_type == "sleep":
            agent.state = "SLEEPING"
        elif decision.action_type == "wait":
            # WAITING 与 IDLE 区分：WAITING 表示主动等待或暂时受阻，
            # IDLE 保留给初始/空闲状态，避免卡死的 NPC 外观与正常空闲混淆
            agent.state = "WAITING"
        else:
            agent.state = "INTERACTING"
        self._pending_events.append(
            _new_event(
                simulation_id=self._sim_id(),
                event_type="agent.action_started",
                actor_entity_id=agent.id,
                location_id=decision.target_location_id,
                scene_id=agent.scene_id,
                description=f"{agent.name}：{decision.description}",
                importance=2,
                payload={"action": decision.action_type, "goal": decision.description},
                created_at=utcnow(),
            )
        )

    # ------------------------------------------------------------------
    # 自然事件系统
    # ------------------------------------------------------------------

    def _tick_natural_events(self, now_dt: datetime) -> None:
        """
        运行所有自然事件处理器（不阻塞 asyncio）。
        主场景 = 第一个 outdoor 场景（也是自然事件发生的主要舞台）。
        """
        outdoor_scene_id = self._primary_outdoor_scene_id()
        if outdoor_scene_id is None:
            return
        ctx = NaturalEventContext(
            objects=self._objects,
            agents=self._agents,
            scene_id=outdoor_scene_id,
            world_time=now_dt,
            sim_id=self._sim_id(),
            rng=self._rng,
            weather=self._weather,
        )
        new_events = _tick_natural_all(ctx)
        self._pending_events.extend(new_events)

    def _primary_outdoor_scene_id(self) -> str | None:
        """返回第一个存在 Agent 的室外场景 ID，用于自然事件的主场景。"""
        for agent in self._agents.values():
            if not agent.is_player:
                return agent.scene_id
        return None

    def _build_natural_ctx_for_agent(self, agent: EngineAgent) -> "NaturalWorldContext":
        """构建当前 Agent 周围的自然环境上下文，供规则决策使用。"""
        nearby_fires: list[tuple[int, int, str]] = []
        nearby_fishing: list[tuple[int, int, str]] = []
        nearby_benches: list[tuple[int, int, str]] = []
        nearby_signs: list[tuple[int, int, str, str]] = []
        nearby_ripe: list[tuple[int, int, str]] = []

        for obj in self._objects.values():
            if obj.scene_id != agent.scene_id:
                continue
            dist = abs(obj.x - agent.x) + abs(obj.y - agent.y)
            if obj.state.get("on_fire") and dist <= 8:
                nearby_fires.append((obj.x, obj.y, obj.name))
            if obj.state.get("fishing_active") and "fishing_spot" in (obj.tags or []) and dist <= 4:
                nearby_fishing.append((obj.x, obj.y, obj.name))
            if "bench" in (obj.tags or []) and not obj.state.get("occupied") and dist <= 2:
                nearby_benches.append((obj.x, obj.y, obj.name))
            notice = obj.state.get("notice_text")
            if notice and dist <= 5:
                nearby_signs.append((obj.x, obj.y, obj.id, notice))
            if (obj.state.get("fruit_ripe") or obj.state.get("mushroom_present")) and dist <= 2:
                nearby_ripe.append((obj.x, obj.y, obj.name))

        return NaturalWorldContext(
            weather=self._weather.condition,
            storm_active=self._weather.is_dangerous(),
            nearby_fires=nearby_fires,
            nearby_fishing_spots=nearby_fishing,
            nearby_benches=nearby_benches,
            nearby_signs=nearby_signs,
            nearby_ripe_objects=nearby_ripe,
        )

    async def _on_agent_arrived(self, agent: EngineAgent) -> None:
        # 到达目标：若位于 Portal tile 则自动切换 scene
        portal = self._find_portal_at(agent.scene_id, agent.x, agent.y)
        if portal is not None and portal.interaction_type == "auto_enter":
            await self._teleport(agent, portal.to_scene_id, (portal.to_tile["x"], portal.to_tile["y"]))
            return
        agent.state = "INTERACTING"
        self._pending_events.append(
            _new_event(
                simulation_id=self._sim_id(),
                event_type="agent.action_finished",
                actor_entity_id=agent.id,
                scene_id=agent.scene_id,
                description=f"{agent.name} 到达目的地",
                importance=2,
                payload={},
                created_at=utcnow(),
            )
        )

    # ------------------------------------------------------------------
    # 玩家
    # ------------------------------------------------------------------

    def _current_player(self) -> EngineAgent | None:
        if self._player_id is None:
            return None
        return self._agents.get(self._player_id)

    def enqueue_input(self, evt: PlayerInputEvent) -> None:
        self._input_queue.append(evt)

    async def _handle_input(self, evt: PlayerInputEvent) -> None:
        player = self._current_player()
        if player is None:
            return
        if evt.kind == "move_path":
            path = evt.payload.get("path") or []
            player.path = [(p["x"], p["y"]) for p in path]
            player.state = "MOVING"
            player.dirty = True
        elif evt.kind == "teleport":
            scene_id = evt.payload["scene_id"]
            x = int(evt.payload["x"])
            y = int(evt.payload["y"])
            await self._teleport(player, scene_id, (x, y))
        elif evt.kind == "dialogue":
            # 注入对话广播；具体回复由 PlayerService 完成
            self._pending_events.append(
                _new_event(
                    simulation_id=self._sim_id(),
                    event_type="dialogue.message_created",
                    actor_entity_id=evt.payload.get("speaker_id"),
                    target_entity_id=evt.payload.get("target_id"),
                    scene_id=player.scene_id,
                    description=evt.payload.get("preview", ""),
                    importance=3,
                    payload=evt.payload,
                    created_at=utcnow(),
                )
            )

    async def _check_player_environment(self, player: EngineAgent) -> None:
        grid = get_scene_cache().get_grid(player.scene_id)
        if grid is None:
            return
        tile = grid.get(player.x, player.y)
        if tile is not None and tile.hazard_type == "deep_water":
            if "drowning" not in player.status_effects:
                player.status_effects.append("drowning")
                player.state = "DROWNING"
                player.dirty = True
                self._pending_events.append(
                    _new_event(
                        simulation_id=self._sim_id(),
                        event_type="world.hazard_triggered",
                        actor_entity_id=player.id,
                        scene_id=player.scene_id,
                        description=f"{player.name} 掉进了河里！",
                        importance=7,
                        payload={"hazard_type": tile.hazard_type, "new_status": "DROWNING"},
                        created_at=utcnow(),
                    )
                )
        else:
            if "drowning" in player.status_effects:
                player.status_effects.remove("drowning")
                player.state = "IDLE"
                player.dirty = True
        portal = self._find_portal_at(player.scene_id, player.x, player.y)
        if portal is not None and portal.interaction_type == "auto_enter":
            await self._teleport(
                player,
                portal.to_scene_id,
                (portal.to_tile["x"], portal.to_tile["y"]),
            )

    def _find_portal_at(self, scene_id: str, x: int, y: int) -> Portal | None:
        for p in self._portals_by_scene.get(scene_id, []):
            ft = p.from_tile
            if int(ft.get("x")) == x and int(ft.get("y")) == y:
                return p
        return None

    async def _teleport(
        self, agent: EngineAgent, to_scene_id: str, to_tile: tuple[int, int]
    ) -> None:
        from_scene = agent.scene_id
        agent.scene_id = to_scene_id
        agent.x, agent.y = to_tile
        agent.state = "IDLE"
        agent.path = []
        agent.dirty = True
        self._pending_events.append(
            _new_event(
                simulation_id=self._sim_id(),
                event_type="world.scene_changed",
                actor_entity_id=agent.id,
                scene_id=to_scene_id,
                description=f"{agent.name} 进入新场景",
                importance=3,
                payload={
                    "entity_id": agent.id,
                    "from_scene_id": from_scene,
                    "to_scene_id": to_scene_id,
                    "position": {"x": to_tile[0], "y": to_tile[1]},
                },
                created_at=utcnow(),
            )
        )

    # ------------------------------------------------------------------
    # 持久化 / 广播
    # ------------------------------------------------------------------

    async def _persist_tick(
        self,
        sim: Simulation,
        entity_updates: list[AgentRuntimeState],
        events: list[WorldEvent],
    ) -> None:
        async with self._session_factory() as session:
            db_sim = await session.get(Simulation, sim.id)
            if db_sim is not None:
                db_sim.current_step = self._step
                db_sim.world_time = self._world_time
            for upd in entity_updates:
                state = await session.get(AgentState, upd.agent_id)
                if state is not None:
                    state.scene_id = upd.scene_id
                    state.x = upd.position.x
                    state.y = upd.position.y
                    state.state = upd.state
                    state.emotion = upd.emotion
                    state.energy = upd.energy
                    state.hunger = upd.hunger
                    if upd.social_need is not None:
                        state.social_need = upd.social_need
                    if upd.fear is not None:
                        state.fear = upd.fear
                    state.status_effects = upd.status_effects
                    state.current_action_id = upd.current_action_id
                    state.current_goal = upd.current_goal
                    state.facing = upd.facing
                    state.busy_until = upd.busy_until
                    state.interruptible = upd.interruptible
                    state.current_priority = upd.current_priority
                    state.last_social_at = upd.last_social_at
                    agent_entry = self._agents.get(upd.agent_id)
                    state.path = (
                        [{"x": p[0], "y": p[1]} for p in agent_entry.path]
                        if agent_entry is not None
                        else []
                    )
                    state.last_decision_at = (
                        agent_entry.last_decision_at if agent_entry else None
                    )
            for evt in events:
                session.add(evt)
            # 写回被自然事件系统/玩家交互标记为 dirty 的 WorldObject
            despawned_ids: list[str] = []
            for eng_obj in list(self._objects.values()):
                if not eng_obj.dirty:
                    continue
                if eng_obj.pending_delete:
                    db_obj = await session.get(WorldObject, eng_obj.id)
                    if db_obj is not None:
                        await session.delete(db_obj)
                    despawned_ids.append(eng_obj.id)
                elif eng_obj.pending_create:
                    session.add(WorldObject(
                        id=eng_obj.id,
                        scene_id=eng_obj.scene_id,
                        name=eng_obj.name,
                        object_type=eng_obj.object_type,
                        position={"x": eng_obj.x, "y": eng_obj.y},
                        size={"width": eng_obj.width, "height": eng_obj.height},
                        blocks_movement=eng_obj.blocks_movement,
                        available_interactions=list(eng_obj.available_interactions),
                        state=dict(eng_obj.state),
                        tags=list(eng_obj.tags),
                    ))
                    eng_obj.pending_create = False
                else:
                    db_obj = await session.get(WorldObject, eng_obj.id)
                    if db_obj is not None:
                        db_obj.state = dict(eng_obj.state)
            await session.commit()
            # 持久化完成后，从内存缓存中真正移除已 despawn 的对象
            for oid in despawned_ids:
                self._objects.pop(oid, None)
        # 同步内存 ORM 对象，保证 snapshot() / get_simulation() 返回最新值
        if self._simulation is not None:
            self._simulation.current_step = self._step
            self._simulation.world_time = self._world_time

        # 事件统一出口：每条 WorldEvent 通过事件总线分发给 EventRouter，
        # 由 Router 触发 Observer 埋点 / 未来的 memory / task 分发。
        bus = get_event_bus()
        for evt in events:
            try:
                await bus.publish(
                    WORLD_EVENT_TOPIC,
                    {
                        "id": evt.id,
                        "simulation_id": evt.simulation_id,
                        "event_type": evt.event_type,
                        "source": evt.source,
                        "actor_entity_id": evt.actor_entity_id,
                        "target_entity_id": evt.target_entity_id,
                        "location_id": evt.location_id,
                        "scene_id": evt.scene_id,
                        "description": evt.description,
                        "importance": evt.importance,
                        "payload": evt.payload,
                        "created_at": iso(evt.created_at),
                    },
                )
            except Exception:
                logger.debug("world event publish failed", exc_info=True)

        # Redis 热写：同步 agent runtime + scene active_entities 供 Redis 直读。
        await self._sync_runtime_cache(entity_updates)

    async def _sync_runtime_cache(
        self, entity_updates: list[AgentRuntimeState]
    ) -> None:
        if not entity_updates:
            return
        redis = get_redis()
        # 按场景聚合
        scene_members: dict[str, list[str]] = {}
        for upd in entity_updates:
            try:
                await redis.set_json(
                    key_agent_runtime(upd.agent_id),
                    {
                        "agent_id": upd.agent_id,
                        "scene_id": upd.scene_id,
                        "x": upd.position.x,
                        "y": upd.position.y,
                        "state": upd.state,
                        "emotion": upd.emotion,
                        "facing": upd.facing,
                        "energy": upd.energy,
                        "hunger": upd.hunger,
                        "updated_at": iso(upd.updated_at),
                    },
                    ttl_seconds=300,
                )
            except Exception:
                continue
            scene_members.setdefault(upd.scene_id, []).append(upd.agent_id)
        for scene_id, members in scene_members.items():
            try:
                await redis.set_add(
                    key_scene_active_entities(scene_id),
                    *members,
                    ttl_seconds=300,
                )
            except Exception:
                continue

    async def _broadcast_delta(
        self, entity_updates: list[AgentRuntimeState], events: list[WorldEvent]
    ) -> None:
        # 每个 tick 都广播，保证前端世界时钟实时更新，即使当轮无实体变化也推送
        # 递增 delta 序号：前端可以检测乱序与丢失（§14.5）
        self._delta_seq = getattr(self, "_delta_seq", 0) + 1
        payload = SimulationDeltaPayload(
            step=self._step,
            world_time=self._world_time,
            entity_updates=entity_updates,
            events=[WorldEventSchema.model_validate(e) for e in events],
        )
        data = payload.model_dump(mode="json")
        data["seq"] = self._delta_seq
        await get_event_bus().publish(
            WS_BROADCAST_TOPIC,
            {
                "simulation_id": self._sim_id(),
                "type": "simulation.delta",
                "payload": data,
            },
        )

    # ------------------------------------------------------------------
    # 对外查询
    # ------------------------------------------------------------------

    def snapshot(self) -> tuple[Simulation, list[AgentRuntimeState]]:
        assert self._simulation is not None
        entities = [self._to_runtime_state(a) for a in self._agents.values()]
        return self._simulation, entities

    def get_simulation(self) -> Simulation | None:
        return self._simulation

    def set_status(self, status: str) -> None:
        if self._simulation is not None:
            self._simulation.status = status

    def set_speed(self, multiplier: float) -> None:
        if self._simulation is not None:
            self._simulation.speed_multiplier = multiplier

    def get_agent(self, agent_id: str) -> EngineAgent | None:
        return self._agents.get(agent_id)

    # ------------------------------------------------------------------
    # 物品生命周期 API（被 player_service / 自然事件 / 任务系统调用）
    # ------------------------------------------------------------------

    def get_object(self, object_id: str) -> EngineObject | None:
        """读取内存中的物品；若返回的实例被修改了 state，调用方必须设置 dirty=True。"""
        return self._objects.get(object_id)

    def apply_object_state_change(
        self,
        object_id: str,
        patch: dict[str, Any],
        *,
        actor: str | None = None,
        description: str = "",
        importance: int = 1,
        event_type: str = "world.object_state_changed",
    ) -> EngineObject | None:
        """
        合并 patch 到对象 state，标记 dirty 等待持久化，
        并发布一条 WorldEvent 让前端实时刷新该物品的视觉表现。

        约定：要"删除某 state key"，把对应 value 设为 None。
        """
        obj = self._objects.get(object_id)
        if obj is None:
            return None
        for key, val in patch.items():
            if val is None:
                obj.state.pop(key, None)
            else:
                obj.state[key] = val
        obj.dirty = True

        evt = WorldEvent(
            simulation_id=self._sim_id(),
            event_type=event_type,
            source="world",
            actor_entity_id=actor,
            target_entity_id=obj.id,
            scene_id=obj.scene_id,
            description=description or f"{obj.name} 状态发生变化",
            importance=importance,
            payload={
                "object_id": obj.id,
                "name": obj.name,
                "x": obj.x,
                "y": obj.y,
                "patch": {k: v for k, v in patch.items()},
                "state": dict(obj.state),
            },
            created_at=self._world_time,
        )
        self._pending_events.append(evt)
        return obj

    def spawn_object(
        self,
        *,
        scene_id: str,
        name: str,
        object_type: str,
        x: int,
        y: int,
        width: int = 1,
        height: int = 1,
        blocks_movement: bool = False,
        available_interactions: list[str] | None = None,
        state: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        actor: str | None = None,
        description: str = "",
        importance: int = 2,
    ) -> EngineObject:
        """运行时新增一个 WorldObject：写入内存缓存 + 标记待持久化 + 广播 spawn 事件。"""
        from uuid import uuid4

        obj_id = f"obj_{uuid4().hex[:8]}"
        obj = EngineObject(
            id=obj_id,
            scene_id=scene_id,
            name=name,
            object_type=object_type,
            x=x,
            y=y,
            width=width,
            height=height,
            blocks_movement=blocks_movement,
            available_interactions=list(available_interactions or []),
            state=dict(state or {}),
            tags=list(tags or []),
            dirty=True,
            pending_create=True,
        )
        self._objects[obj_id] = obj

        evt = WorldEvent(
            simulation_id=self._sim_id(),
            event_type="world.object_spawned",
            source="world",
            actor_entity_id=actor,
            target_entity_id=obj_id,
            scene_id=scene_id,
            description=description or f"世界中新出现了 {name}",
            importance=importance,
            payload={
                "object_id": obj_id,
                "scene_id": scene_id,
                "name": name,
                "object_type": object_type,
                "position": {"x": x, "y": y},
                "size": {"width": width, "height": height},
                "blocks_movement": blocks_movement,
                "available_interactions": list(available_interactions or []),
                "state": dict(state or {}),
                "tags": list(tags or []),
            },
            created_at=self._world_time,
        )
        self._pending_events.append(evt)
        return obj

    def despawn_object(
        self,
        object_id: str,
        *,
        actor: str | None = None,
        description: str = "",
        importance: int = 2,
    ) -> EngineObject | None:
        """运行时移除一个 WorldObject：标记 pending_delete + 广播 despawn 事件。"""
        obj = self._objects.get(object_id)
        if obj is None:
            return None
        obj.pending_delete = True
        obj.dirty = True

        evt = WorldEvent(
            simulation_id=self._sim_id(),
            event_type="world.object_despawned",
            source="world",
            actor_entity_id=actor,
            target_entity_id=object_id,
            scene_id=obj.scene_id,
            description=description or f"{obj.name} 从世界中消失",
            importance=importance,
            payload={"object_id": object_id, "scene_id": obj.scene_id, "name": obj.name},
            created_at=self._world_time,
        )
        self._pending_events.append(evt)
        return obj

    def broadcast_weather_state(self) -> None:
        """主动把当前天气作为一条 weather.condition_changed 事件广播一次（用于客户端首次连接同步）。"""
        evt = WorldEvent(
            simulation_id=self._sim_id(),
            event_type="weather.condition_changed",
            source="world",
            scene_id=None,
            description=f"当前天气：{self._weather.condition}，强度 {self._weather.intensity:.1f}",
            importance=1,
            payload={
                "prev": self._weather.condition,
                "condition": self._weather.condition,
                "intensity": self._weather.intensity,
            },
            created_at=self._world_time,
        )
        self._pending_events.append(evt)

    def start_chatting(self, agent_id: str) -> None:
        """令指定 Agent 进入 CHATTING 状态并记录开始时间，用于 player talk() 调用时冻结 NPC。"""
        import time as _time

        agent = self._agents.get(agent_id)
        if agent is not None:
            agent.state = "CHATTING"
            agent.path = []
            agent.dirty = True
        self._chatting_since_real[agent_id] = _time.time()

    def end_chatting(self, agent_id: str) -> None:
        """主动结束 CHATTING，让 NPC 恢复自主决策。"""
        agent = self._agents.get(agent_id)
        if agent is not None and agent.state == "CHATTING":
            agent.state = "IDLE"
            agent.last_decision_at = None
            agent.dirty = True
        self._chatting_since_real.pop(agent_id, None)

    def mark_busy_refusing(
        self, agent_id: str, *, line: str = "", duration_seconds: float = 3.0
    ) -> None:
        """阶段 19：软拒后让 NPC 短暂进入 BUSY_REFUSING 状态展示拒绝台词。

        - 仅 IDLE / WAITING 状态会被覆盖；忙碌状态保持不动。
        - 设置 ``current_goal`` 为拒绝台词，方便前端气泡读取。
        - ``duration_seconds`` 用 _chatting_since_real 共享通道做超时（与 CHATTING 同机制）。
        """
        import time as _time

        agent = self._agents.get(agent_id)
        if agent is None:
            return
        if agent.state in {"IDLE", "WAITING"}:
            agent.state = "BUSY_REFUSING"
            if line:
                agent.current_goal = line[:200]
            agent.dirty = True
            # 复用 _chatting_since_real 作为超时记录（_decide_all 不会决策这个状态）
            self._chatting_since_real[agent_id] = _time.time()

    def find_agent_by_name(self, name: str) -> EngineAgent | None:
        for a in self._agents.values():
            if a.name == name:
                return a
        return None

    async def reload_from_db(self) -> None:
        async with self._session_factory() as session:
            await self._load_state(session)

    # ------------------------------------------------------------------
    # 异步任务投递（阶段 12 §6）
    # ------------------------------------------------------------------

    async def _enqueue_decision_task(self, agent_id: str, now_dt: datetime) -> None:
        """把 agent_decision 投递到 TaskQueue。

        - 幂等 key：``agent_decision:{agent_id}:{simulation_step}``，同一步同一 agent 不会重复入库。
        - 主循环**不等待**任务执行结果；即使任务 failed，规则兜底早已给出即时行为。
        - 任务完成后 worker 的工具调用会改写 AgentState / AgentAction，
          下一轮 world tick 通过 ``reload_from_db`` 或状态同步感知。
        """
        from app.domain.tasks.queue import get_task_queue

        queue = get_task_queue()
        if queue is None:
            return
        agent = self._agents.get(agent_id)
        if agent is None or agent.is_player:
            return
        await queue.enqueue(
            task_type="agent_decision",
            payload={
                "agent_id": agent_id,
                "simulation_id": self._sim_id(),
                "world_time": now_dt.isoformat(),
            },
            entity_id=agent_id,
            simulation_id=self._sim_id(),
            simulation_step=self._step,
            priority=5,
            deadline_seconds=60.0,
        )
        agent.last_decision_at = now_dt
        agent.dirty = True

    # ------------------------------------------------------------------
    # 反思 / 日结
    # ------------------------------------------------------------------

    async def _enqueue_reflect_batch(self, now_dt: datetime) -> None:
        """
        把反思/日结投递到 daily_reflection 任务队列，不在 tick 内同步执行。

        替代原来的 _maybe_reflect_batch 内联 LLM 调用（会阻塞 tick 5-10s）。
        改为异步入队后，worker 独立执行，世界时钟不受影响。
        依然维护 _last_reflect_at / _last_summary_day 在内存中做频率控制，
        避免过度入队。
        """
        from app.domain.tasks.queue import get_task_queue

        queue = get_task_queue()
        if queue is None:
            return

        # 每 10 个游戏分钟触发一次反思入队
        candidates = [
            agent
            for agent in self._agents.values()
            if not agent.is_player
            and (
                agent.id not in self._last_reflect_at
                or (now_dt - self._last_reflect_at[agent.id]).total_seconds() >= 600
            )
        ]
        if not candidates:
            return

        self._rng.shuffle(candidates)
        target = candidates[0]

        try:
            await queue.enqueue(
                task_type="daily_reflection",
                payload={
                    "agent_id": target.id,
                    "world_time": now_dt.isoformat(),
                },
                entity_id=target.id,
                simulation_id=self._sim_id(),
                simulation_step=self._step,
                priority=8,  # low 队列，低于决策任务
                deadline_seconds=120.0,
            )
            # 入队成功后更新频率控制时间戳（防止同一 agent 在下一 tick 又被入队）
            self._last_reflect_at[target.id] = now_dt
        except Exception:
            logger.debug("enqueue daily_reflection failed for %s", target.id, exc_info=True)

        # 日结：一天一次，每个 agent 独立控制
        day_key = now_dt.strftime("%Y-%m-%d")
        for agent in self._agents.values():
            if agent.is_player:
                continue
            if self._last_summary_day.get(agent.id) == day_key:
                continue
            try:
                await queue.enqueue(
                    task_type="daily_reflection",
                    payload={
                        "agent_id": agent.id,
                        "world_time": now_dt.isoformat(),
                    },
                    entity_id=agent.id,
                    simulation_id=self._sim_id(),
                    # 日结用日期字符串作为幂等 extra，保证一天只执行一次
                    idempotency_extra=f"summary:{day_key}",
                    simulation_step=self._step,
                    priority=8,
                    deadline_seconds=120.0,
                )
                self._last_summary_day[agent.id] = day_key
            except Exception:
                logger.debug("enqueue daily_reflection(summary) failed for %s", agent.id, exc_info=True)


class _ORMStateView:
    """将 EngineAgent 伪装为 AgentState，满足 rule_agent 入参要求。"""

    def __init__(self, a: EngineAgent) -> None:
        self.agent_id = a.id
        self.scene_id = a.scene_id
        self.x = a.x
        self.y = a.y
        self.state = a.state
        self.energy = a.energy


def _face_towards(from_: tuple[int, int], to: tuple[int, int]) -> str:
    dx, dy = to[0] - from_[0], to[1] - from_[1]
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


def _new_event(
    *,
    simulation_id: str,
    event_type: str,
    description: str,
    created_at: datetime,
    source: str = "world",
    actor_entity_id: str | None = None,
    target_entity_id: str | None = None,
    location_id: str | None = None,
    scene_id: str | None = None,
    importance: int = 1,
    payload: dict[str, Any] | None = None,
) -> WorldEvent:
    return WorldEvent(
        simulation_id=simulation_id,
        event_type=event_type,
        source=source,
        actor_entity_id=actor_entity_id,
        target_entity_id=target_entity_id,
        location_id=location_id,
        scene_id=scene_id,
        description=description,
        importance=importance,
        payload=payload or {},
        created_at=created_at,
    )
