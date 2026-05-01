"""
自然事件系统 —— 可扩展的事件驱动架构。

设计原则
--------
1. 每种事件封装为独立的 ``NaturalEventHandler`` 子类，单一职责。
2. 通过 ``NaturalEventContext`` 传递所有世界状态，handler 之间无直接依赖。
3. 通过 ``NATURAL_EVENT_HANDLERS`` 列表统一调度；新增事件类型只需在末尾追加实例。
4. Handler 只修改内存中 ``EngineObject.state`` 并置 ``dirty=True``；
   脏对象的 DB 写回由引擎的 ``_persist_tick`` 统一处理。
5. 产生的 ``WorldEvent`` 由 handler 返回，引擎统一持久化并广播前端；
   前端可监听事件类型来渲染动效（火焰、水坑、萤火虫……）。

已实现的事件类型（12 种）
------------------------
- WeatherEventHandler       天气系统（晴/多云/雨/暴风/雾）Markov 链转换
- FireEventHandler          自燃、蔓延、熄灭
- MushroomEventHandler      雨后蘑菇萌发与枯萎
- FlowerEventHandler        花朵晨开暮合
- FishingEventHandler       河边钓鱼点激活与鱼获互动
- BenchRestEventHandler     NPC 在长椅旁自发休息回复能量
- NoticeEventHandler        NPC 接近告示牌产生感知事件
- StormShelterEventHandler  暴风雨时向室外 NPC 发出避难警告
- PuddleEventHandler        雨天水坑形成与干涸
- FireflyEventHandler       夜晚萤火虫出现
- BirdEventHandler          白天鸟类在公园出没，被NPC惊走
- FruitRipenEventHandler    雨后果树结果，NPC 可采摘补充能量

扩展方法
--------
继承 ``NaturalEventHandler``，实现 ``event_category`` 和 ``tick()``，
然后将实例追加到本文件末尾的 ``NATURAL_EVENT_HANDLERS`` 列表即可。
"""

from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 天气状态（引擎全局持有，WeatherEventHandler 更新，其余 handler 只读）
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class WeatherState:
    """全局天气状态。引擎在 __init__ 时创建，贯穿整个仿真生命周期。"""

    condition: str = "sunny"   # sunny | cloudy | rainy | stormy | foggy
    intensity: float = 0.0     # 0-1，雨量/风力/雾浓度
    duration_ticks: int = 0    # 当前天气已持续的 tick 数
    temperature: float = 22.0  # 摄氏度（影响火灾概率）
    wind_speed: float = 0.0    # m/s

    def is_wet(self) -> bool:
        return self.condition in ("rainy", "stormy")

    def is_dangerous(self) -> bool:
        return self.condition == "stormy"

    def fire_risk_multiplier(self) -> float:
        """晴热增大火灾风险，潮湿则大幅降低。"""
        if self.condition == "stormy":
            return 0.0
        if self.condition == "rainy":
            return 0.1
        if self.condition == "sunny" and self.temperature > 30:
            return 3.0
        if self.condition == "sunny":
            return 1.5
        return 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 事件上下文（每 tick 由引擎构建后传入所有 handler）
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class NaturalEventContext:
    """每次 tick 传入各 handler 的读写上下文。"""

    objects: dict[str, Any]   # id → EngineObject（可修改 .state / .dirty）
    agents: dict[str, Any]    # id → EngineAgent（可修改 .energy / .hunger / .dirty）
    scene_id: str             # 当前处理的室外主场景
    world_time: datetime
    sim_id: str
    rng: random.Random
    weather: WeatherState     # 引用；WeatherEventHandler 直接修改其字段
    _debounce: set[str] = field(default_factory=set)

    def already_seen(self, *parts: str) -> bool:
        """防止同一 tick 同一对象对重复触发（如 NPC-Sign 同帧双重感知）。"""
        key = "|".join(parts)
        if key in self._debounce:
            return True
        self._debounce.add(key)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 基类
# ─────────────────────────────────────────────────────────────────────────────


class NaturalEventHandler(ABC):
    """
    单一职责：处理一类自然事件。
    - 不直接写 DB；返回的 WorldEvent 列表由引擎统一持久化。
    - 修改 EngineObject.state 后设置 dirty=True，引擎会在 _persist_tick 写回。
    """

    @property
    @abstractmethod
    def event_category(self) -> str: ...

    @abstractmethod
    def tick(self, ctx: NaturalEventContext) -> list[Any]: ...  # list[WorldEvent]

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    def _evt(
        self,
        ctx: NaturalEventContext,
        *,
        event_type: str,
        description: str,
        actor: str | None = None,
        target: str | None = None,
        location_id: str | None = None,
        importance: int = 1,
        payload: dict[str, Any] | None = None,
        obj: Any | None = None,
    ) -> Any:
        """
        构造一条 WorldEvent；若传入 ``obj``，自动把对象当前 state 注入 payload，
        前端可以据此实时更新物品视觉表现，无需额外往返查询。
        """
        from app.db.models import WorldEvent

        merged_payload = dict(payload or {})
        if obj is not None:
            merged_payload.setdefault("object_id", obj.id)
            merged_payload.setdefault("x", obj.x)
            merged_payload.setdefault("y", obj.y)
            merged_payload.setdefault("name", obj.name)
            merged_payload.setdefault("scene_id", obj.scene_id)
            # state 总是覆盖，保证最新
            merged_payload["state"] = dict(obj.state)

        return WorldEvent(
            simulation_id=ctx.sim_id,
            event_type=event_type,
            source="world",
            actor_entity_id=actor,
            target_entity_id=target,
            scene_id=ctx.scene_id,
            location_id=location_id,
            description=description,
            importance=importance,
            payload=merged_payload,
            created_at=ctx.world_time,
        )

    def _scene_objects(self, ctx: NaturalEventContext) -> list[Any]:
        return [o for o in ctx.objects.values() if o.scene_id == ctx.scene_id]

    def _scene_agents(self, ctx: NaturalEventContext) -> list[Any]:
        return [
            a for a in ctx.agents.values()
            if a.scene_id == ctx.scene_id and not a.is_player
        ]

    def _has_tag(self, obj: Any, tag: str) -> bool:
        return tag in (obj.tags or [])

    def _near_agents(
        self, obj: Any, ctx: NaturalEventContext, radius: int = 5
    ) -> list[Any]:
        return [
            a for a in ctx.agents.values()
            if a.scene_id == ctx.scene_id
            and abs(a.x - obj.x) + abs(a.y - obj.y) <= radius
        ]

    def _neighbor_objects(
        self, obj: Any, ctx: NaturalEventContext, radius: int = 1
    ) -> list[Any]:
        return [
            o for o in self._scene_objects(ctx)
            if o.id != obj.id
            and abs(o.x - obj.x) <= radius
            and abs(o.y - obj.y) <= radius
        ]


# ─────────────────────────────────────────────────────────────────────────────
# 1. 天气系统（Markov 链）
# ─────────────────────────────────────────────────────────────────────────────

# 每 tick 的转移概率（每游戏分钟 ≈ 一个 tick，120 tick ≈ 2 游戏小时）
_WEATHER_TRANSITIONS: dict[str, dict[str, float]] = {
    "sunny":  {"cloudy": 0.002},
    "cloudy": {"sunny": 0.003, "rainy": 0.004, "foggy": 0.001},
    "rainy":  {"cloudy": 0.005, "stormy": 0.001},
    "stormy": {"rainy": 0.008},
    "foggy":  {"sunny": 0.01, "cloudy": 0.005},
}

# 游戏小时段 → 倾向出现的天气（概率翻倍）
_HOUR_BIAS: dict[str, tuple[int, int]] = {
    "foggy": (5, 9),    # 清晨易起雾
    "sunny": (10, 16),  # 午间多晴天
}

_COND_TARGET_TEMP: dict[str, float] = {
    "sunny": 28.0, "cloudy": 20.0, "rainy": 16.0, "stormy": 12.0, "foggy": 14.0,
}


class WeatherEventHandler(NaturalEventHandler):
    """Markov 链天气转换，同时维护全局温度与雷声。"""

    @property
    def event_category(self) -> str:
        return "weather"

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        w = ctx.weather
        w.duration_ticks += 1
        events: list[Any] = []
        hour = ctx.world_time.hour

        # 小时偏置：特定时段增大某天气的转移概率
        bias_cond = next(
            (c for c, (h0, h1) in _HOUR_BIAS.items() if h0 <= hour < h1), None
        )

        for target_cond, base_prob in _WEATHER_TRANSITIONS.get(w.condition, {}).items():
            prob = base_prob
            if target_cond == bias_cond:
                prob *= 2.5
            if w.duration_ticks > 120:  # 持续 2 游戏小时后，更容易转换
                prob *= 1.5
            if ctx.rng.random() < prob:
                prev = w.condition
                w.condition = target_cond
                w.duration_ticks = 0
                w.intensity = ctx.rng.uniform(0.3, 1.0)
                events.append(self._evt(
                    ctx,
                    event_type="weather.condition_changed",
                    description=f"天气由{prev}转为{target_cond}，强度{w.intensity:.1f}",
                    importance=3,
                    payload={"prev": prev, "condition": target_cond, "intensity": w.intensity},
                ))
                break  # 每 tick 最多转换一次

        # 温度缓慢向目标靠拢（热力学缓冲）
        target_temp = _COND_TARGET_TEMP.get(w.condition, 20.0)
        w.temperature += (target_temp - w.temperature) * 0.005

        # 暴风雨随机雷声
        if w.condition == "stormy" and ctx.rng.random() < 0.03:
            events.append(self._evt(
                ctx,
                event_type="weather.thunder",
                description="一声炸雷滚过小镇上空",
                importance=4,
                payload={"condition": w.condition},
            ))

        return events


# ─────────────────────────────────────────────────────────────────────────────
# 2. 火灾系统
# ─────────────────────────────────────────────────────────────────────────────

# 基础概率（晴热天气乘以 fire_risk_multiplier）
FIRE_START_PROB: float = 0.00008
FIRE_SPREAD_PROB: float = 0.03
FIRE_EXTINGUISH_PROB: float = 0.04  # 自然熄灭（每 tick）


class FireEventHandler(NaturalEventHandler):
    """随机自燃 → 向邻格蔓延 → 超时/随机熄灭。"""

    @property
    def event_category(self) -> str:
        return "fire"

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        events: list[Any] = []
        risk_mult = ctx.weather.fire_risk_multiplier()
        scene_objs = self._scene_objects(ctx)
        burning = [o for o in scene_objs if o.state.get("on_fire")]
        flammable = [
            o for o in scene_objs
            if o.state.get("flammable") and not o.state.get("on_fire")
        ]

        # ── 自燃 ──────────────────────────────────────────────────────────────
        for obj in flammable:
            if ctx.rng.random() < FIRE_START_PROB * risk_mult:
                obj.state["on_fire"] = True
                obj.state["burn_ticks"] = 0
                obj.dirty = True
                events.append(self._evt(
                    ctx,
                    event_type="world.fire_started",
                    description=f"{obj.name} 突然起火了！",
                    target=obj.id,
                    importance=7,
                    obj=obj,
                ))

        # ── 燃烧推进：蔓延 / 熄灭 ─────────────────────────────────────────────
        for obj in burning:
            ticks = obj.state.get("burn_ticks", 0) + 1
            obj.state["burn_ticks"] = ticks
            obj.dirty = True
            burn_max = obj.state.get("burn_max_ticks", 10)

            if ticks >= burn_max or ctx.rng.random() < FIRE_EXTINGUISH_PROB:
                obj.state["on_fire"] = False
                obj.state["burn_ticks"] = 0
                obj.dirty = True
                events.append(self._evt(
                    ctx,
                    event_type="world.fire_extinguished",
                    description=f"{obj.name} 的火焰熄灭了",
                    target=obj.id,
                    importance=4,
                    obj=obj,
                ))
                continue

            # 向邻近可燃对象蔓延
            if ctx.rng.random() < FIRE_SPREAD_PROB:
                neighbors = self._neighbor_objects(obj, ctx, radius=2)
                candidates = [
                    n for n in neighbors
                    if n.state.get("flammable") and not n.state.get("on_fire")
                ]
                if candidates:
                    tgt = ctx.rng.choice(candidates)
                    tgt.state["on_fire"] = True
                    tgt.state["burn_ticks"] = 0
                    tgt.dirty = True
                    events.append(self._evt(
                        ctx,
                        event_type="world.fire_spread",
                        description=f"火从 {obj.name} 蔓延到 {tgt.name}！",
                        actor=obj.id,
                        target=tgt.id,
                        importance=6,
                        payload={
                            "from_id": obj.id, "to_id": tgt.id,
                            "x": tgt.x, "y": tgt.y,
                        },
                    ))

        return events


# ─────────────────────────────────────────────────────────────────────────────
# 3. 蘑菇萌发（雨后）
# ─────────────────────────────────────────────────────────────────────────────

MUSHROOM_GROW_PROB: float = 0.0015
MUSHROOM_MAX_TICKS: int = 200
MUSHROOM_DRY_PROB: float = 0.002  # 晴天自然枯萎概率


class MushroomEventHandler(NaturalEventHandler):
    """雨后蘑菇在标记区域萌发；晴天缓慢枯萎；可被 NPC 采摘。"""

    @property
    def event_category(self) -> str:
        return "mushroom"

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        events: list[Any] = []
        is_wet = ctx.weather.is_wet()
        for obj in self._scene_objects(ctx):
            if not self._has_tag(obj, "mushroom_spawn"):
                continue
            if obj.state.get("mushroom_present"):
                ticks = obj.state.get("mushroom_ticks", 0) + 1
                obj.state["mushroom_ticks"] = ticks
                obj.dirty = True
                if ticks >= MUSHROOM_MAX_TICKS or (not is_wet and ctx.rng.random() < MUSHROOM_DRY_PROB):
                    obj.state["mushroom_present"] = False
                    obj.state.pop("mushroom_ticks", None)
                    obj.dirty = True
                    events.append(self._evt(
                        ctx, event_type="nature.mushroom_withered",
                        description=f"{obj.name} 处的蘑菇枯萎了",
                        target=obj.id, importance=1, obj=obj,
                    ))
            elif is_wet and ctx.rng.random() < MUSHROOM_GROW_PROB:
                obj.state["mushroom_present"] = True
                obj.state["mushroom_ticks"] = 0
                obj.dirty = True
                events.append(self._evt(
                    ctx, event_type="nature.mushroom_appeared",
                    description=f"{obj.name} 周围冒出了新鲜蘑菇",
                    target=obj.id, importance=2, obj=obj,
                ))
        return events


# ─────────────────────────────────────────────────────────────────────────────
# 4. 花朵晨开暮合
# ─────────────────────────────────────────────────────────────────────────────


class FlowerEventHandler(NaturalEventHandler):
    """花园花朵按游戏时间开放/闭合，雨天不开放。"""

    @property
    def event_category(self) -> str:
        return "flower"

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        hour = ctx.world_time.hour
        should_bloom = (6 <= hour < 19) and not ctx.weather.is_wet()
        events: list[Any] = []
        for obj in self._scene_objects(ctx):
            if not self._has_tag(obj, "flower"):
                continue
            currently_bloomed = obj.state.get("bloomed", False)
            if should_bloom and not currently_bloomed:
                obj.state["bloomed"] = True
                obj.dirty = True
                events.append(self._evt(
                    ctx, event_type="nature.flower_bloomed",
                    description=f"{obj.name} 在清晨绽放",
                    target=obj.id, importance=1, obj=obj,
                ))
            elif not should_bloom and currently_bloomed:
                obj.state["bloomed"] = False
                obj.dirty = True
                events.append(self._evt(
                    ctx, event_type="nature.flower_withered",
                    description=f"{obj.name} 在傍晚合拢花瓣",
                    target=obj.id, importance=1, obj=obj,
                ))
        return events


# ─────────────────────────────────────────────────────────────────────────────
# 5. 河边钓鱼
# ─────────────────────────────────────────────────────────────────────────────

FISHING_ACTIVATE_PROB: float = 0.003   # 每 tick 钓鱼点激活概率
FISHING_DEACTIVATE_PROB: float = 0.005  # 每 tick 钓鱼点消散概率
FISH_CATCH_PROB: float = 0.25          # NPC 每次钓鱼成功概率（雨天上调）


class FishingEventHandler(NaturalEventHandler):
    """河边钓鱼点随机激活；NPC 在 IDLE/INTERACTING 状态且位于旁边时触发鱼获互动。"""

    @property
    def event_category(self) -> str:
        return "fishing"

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        events: list[Any] = []
        for obj in self._scene_objects(ctx):
            if not self._has_tag(obj, "fishing_spot"):
                continue
            active = obj.state.get("fishing_active", False)

            if not active:
                prob = FISHING_ACTIVATE_PROB * (1.8 if ctx.weather.is_wet() else 1.0)
                if ctx.rng.random() < prob:
                    obj.state["fishing_active"] = True
                    obj.dirty = True
                    events.append(self._evt(
                        ctx, event_type="nature.fishing_spot_appeared",
                        description=f"{obj.name} 附近有鱼儿浮出水面",
                        target=obj.id, importance=2, obj=obj,
                    ))
            else:
                if ctx.rng.random() < FISHING_DEACTIVATE_PROB:
                    obj.state["fishing_active"] = False
                    obj.dirty = True
                    continue

                # NPC 接近 → 触发钓鱼结果
                catch_prob = FISH_CATCH_PROB * (1.4 if ctx.weather.is_wet() else 1.0)
                for agent in self._near_agents(obj, ctx, radius=2):
                    if agent.state not in ("IDLE", "INTERACTING", "WAITING"):
                        continue
                    key = f"fishing|{agent.id}|{obj.id}"
                    if ctx.already_seen(key):
                        continue
                    caught = ctx.rng.random() < catch_prob
                    if caught:
                        agent.hunger = max(0.0, agent.hunger - 0.1)
                        agent.dirty = True
                    events.append(self._evt(
                        ctx,
                        event_type="nature.fish_caught" if caught else "nature.fish_escaped",
                        description=(
                            f"{agent.name} 在 {obj.name} 钓到了一条鱼！"
                            if caught else
                            f"{agent.name} 在 {obj.name} 钓鱼，鱼儿跑了"
                        ),
                        actor=agent.id, target=obj.id,
                        importance=3 if caught else 2,
                        payload={"agent_id": agent.id, "object_id": obj.id, "caught": caught},
                    ))
        return events


# ─────────────────────────────────────────────────────────────────────────────
# 6. 长椅休息
# ─────────────────────────────────────────────────────────────────────────────


class BenchRestEventHandler(NaturalEventHandler):
    """低能量 NPC 在室外长椅旁自发休息，恢复精力。"""

    @property
    def event_category(self) -> str:
        return "bench"

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        events: list[Any] = []
        if ctx.weather.is_dangerous():
            return events

        for obj in self._scene_objects(ctx):
            if not self._has_tag(obj, "bench"):
                continue

            occupied = obj.state.get("occupied", False)
            if occupied:
                # 5% 概率自然离开
                if ctx.rng.random() < 0.05:
                    obj.state["occupied"] = False
                    obj.state.pop("occupied_by", None)
                    obj.dirty = True
                continue

            for agent in self._near_agents(obj, ctx, radius=1):
                if agent.is_player or agent.energy > 0.6:
                    continue
                if agent.state not in ("IDLE", "WAITING"):
                    continue
                key = f"bench|{agent.id}|{obj.id}"
                if ctx.already_seen(key):
                    continue

                obj.state["occupied"] = True
                obj.state["occupied_by"] = agent.id
                obj.dirty = True
                agent.energy = min(1.0, agent.energy + 0.15)
                agent.dirty = True
                events.append(self._evt(
                    ctx, event_type="nature.bench_rest",
                    description=f"{agent.name} 坐在 {obj.name} 上休息片刻",
                    actor=agent.id, target=obj.id, importance=1,
                    payload={"agent_id": agent.id, "object_id": obj.id},
                ))
                break  # 一张椅子每次只接受一个 NPC

        return events


# ─────────────────────────────────────────────────────────────────────────────
# 7. 告示牌感知
# ─────────────────────────────────────────────────────────────────────────────

SIGN_NOTICE_DIST: int = 4  # 格子单位


class NoticeEventHandler(NaturalEventHandler):
    """NPC 经过告示牌附近时生成感知事件，内容供 LLM 感知模块使用。"""

    @property
    def event_category(self) -> str:
        return "notice"

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        events: list[Any] = []
        for obj in self._scene_objects(ctx):
            notice_text = obj.state.get("notice_text")
            if not notice_text:
                continue
            for agent in self._near_agents(obj, ctx, radius=SIGN_NOTICE_DIST):
                if agent.is_player:
                    continue
                key = f"sign|{agent.id}|{obj.id}|{ctx.world_time.hour}"
                if ctx.already_seen(key):
                    continue
                events.append(self._evt(
                    ctx, event_type="world.sign_noticed",
                    description=f"{agent.name} 注意到告示牌：{notice_text[:40]}",
                    actor=agent.id, target=obj.id, importance=2,
                    payload={
                        "agent_id": agent.id,
                        "sign_id": obj.id,
                        "notice_text": notice_text,
                        "x": obj.x, "y": obj.y,
                    },
                ))
        return events


# ─────────────────────────────────────────────────────────────────────────────
# 8. 暴风雨避难警告
# ─────────────────────────────────────────────────────────────────────────────


class StormShelterEventHandler(NaturalEventHandler):
    """暴风雨时向室外 NPC 发出避难事件；引擎/规则层可据此覆盖 NPC 决策。"""

    @property
    def event_category(self) -> str:
        return "storm_shelter"

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        if not ctx.weather.is_dangerous():
            return []
        events: list[Any] = []
        for agent in self._scene_agents(ctx):
            if agent.state == "SLEEPING":
                continue
            key = f"storm_shelter|{agent.id}|{ctx.world_time.hour}"
            if ctx.already_seen(key):
                continue
            events.append(self._evt(
                ctx, event_type="world.storm_warning",
                description=f"暴风雨！{agent.name} 应当寻找避风场所",
                actor=agent.id, importance=6,
                payload={"agent_id": agent.id, "condition": ctx.weather.condition},
            ))
        return events


# ─────────────────────────────────────────────────────────────────────────────
# 9. 雨天水坑
# ─────────────────────────────────────────────────────────────────────────────

PUDDLE_FORM_PROB: float = 0.0005   # 雨中每 tick 每水坑区域成型概率（乘以 intensity）
PUDDLE_DRY_PROB: float = 0.002     # 晴天每 tick 干涸概率


class PuddleEventHandler(NaturalEventHandler):
    """雨天水坑在标记区域形成，晴天逐渐消散。"""

    @property
    def event_category(self) -> str:
        return "puddle"

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        events: list[Any] = []
        is_wet = ctx.weather.is_wet()
        for obj in self._scene_objects(ctx):
            if not self._has_tag(obj, "puddle_area"):
                continue
            has_puddle = obj.state.get("has_puddle", False)
            if is_wet and not has_puddle:
                if ctx.rng.random() < PUDDLE_FORM_PROB * ctx.weather.intensity:
                    obj.state["has_puddle"] = True
                    obj.dirty = True
                    events.append(self._evt(
                        ctx, event_type="nature.puddle_formed",
                        description=f"{obj.name} 处积出了水坑",
                        target=obj.id, importance=1, obj=obj,
                    ))
            elif not is_wet and has_puddle:
                if ctx.rng.random() < PUDDLE_DRY_PROB:
                    obj.state["has_puddle"] = False
                    obj.dirty = True
                    events.append(self._evt(
                        ctx, event_type="nature.puddle_dried",
                        description=f"{obj.name} 处的水坑干涸了",
                        target=obj.id, importance=1, obj=obj,
                    ))
        return events


# ─────────────────────────────────────────────────────────────────────────────
# 10. 夜晚萤火虫
# ─────────────────────────────────────────────────────────────────────────────


class FireflyEventHandler(NaturalEventHandler):
    """游戏时间 19:00–4:00，公园区域萤火虫出现（纯视觉广播事件）。"""

    @property
    def event_category(self) -> str:
        return "firefly"

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        hour = ctx.world_time.hour
        is_night = hour >= 19 or hour < 4
        events: list[Any] = []
        for obj in self._scene_objects(ctx):
            if not self._has_tag(obj, "firefly_area"):
                continue
            active = obj.state.get("firefly_active", False)
            if active and not is_night:
                obj.state["firefly_active"] = False
                obj.dirty = True
            elif not active and is_night and ctx.rng.random() < 0.002:
                obj.state["firefly_active"] = True
                obj.dirty = True
                events.append(self._evt(
                    ctx, event_type="nature.firefly_appeared",
                    description=f"{obj.name} 周围点点萤光在夜里轻舞",
                    target=obj.id, importance=1, obj=obj,
                ))
        return events


# ─────────────────────────────────────────────────────────────────────────────
# 11. 白天鸟类活动
# ─────────────────────────────────────────────────────────────────────────────

BIRD_APPEAR_PROB: float = 0.001
BIRD_LEAVE_PROB: float = 0.005


class BirdEventHandler(NaturalEventHandler):
    """白天鸟类在树丛/公园出没；NPC 靠近则惊飞。"""

    @property
    def event_category(self) -> str:
        return "bird"

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        hour = ctx.world_time.hour
        if not (6 <= hour < 20) or ctx.weather.is_dangerous():
            return []
        events: list[Any] = []
        for obj in self._scene_objects(ctx):
            if not self._has_tag(obj, "bird_area"):
                continue
            has_bird = obj.state.get("bird_present", False)
            nearby = self._near_agents(obj, ctx, radius=3)
            if has_bird:
                if nearby or ctx.rng.random() < BIRD_LEAVE_PROB:
                    obj.state["bird_present"] = False
                    obj.dirty = True
                    events.append(self._evt(
                        ctx, event_type="nature.bird_flew_away",
                        description=f"一群鸟从 {obj.name} 惊飞而起",
                        target=obj.id, importance=1, obj=obj,
                    ))
            elif not nearby and ctx.rng.random() < BIRD_APPEAR_PROB:
                obj.state["bird_present"] = True
                obj.dirty = True
                events.append(self._evt(
                    ctx, event_type="nature.bird_appeared",
                    description=f"一群小鸟落在 {obj.name} 附近嬉戏",
                    target=obj.id, importance=1, obj=obj,
                ))
        return events


# ─────────────────────────────────────────────────────────────────────────────
# 12. 果树结果与采摘
# ─────────────────────────────────────────────────────────────────────────────

FRUIT_RIPEN_PROB: float = 0.001
FRUIT_ROT_PROB: float = 0.0005


class FruitRipenEventHandler(NaturalEventHandler):
    """雨后果树结果，饥饿的 NPC 可采摘补充能量；无人采摘则腐烂。"""

    @property
    def event_category(self) -> str:
        return "fruit"

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        events: list[Any] = []
        for obj in self._scene_objects(ctx):
            if not self._has_tag(obj, "fruit_tree"):
                continue
            has_fruit = obj.state.get("fruit_ripe", False)
            if not has_fruit:
                prob = FRUIT_RIPEN_PROB * (2.0 if ctx.weather.is_wet() else 0.3)
                if ctx.rng.random() < prob:
                    obj.state["fruit_ripe"] = True
                    obj.state["fruit_ticks"] = 0
                    obj.dirty = True
                    events.append(self._evt(
                        ctx, event_type="nature.fruit_ripened",
                        description=f"{obj.name} 的果实成熟了，散发着香气",
                        target=obj.id, importance=2, obj=obj,
                    ))
            else:
                obj.state["fruit_ticks"] = obj.state.get("fruit_ticks", 0) + 1
                obj.dirty = True
                # 饥饿 NPC 采摘
                for agent in self._near_agents(obj, ctx, radius=1):
                    if agent.is_player or agent.hunger < 0.5:
                        continue
                    key = f"fruit|{agent.id}|{obj.id}"
                    if ctx.already_seen(key):
                        continue
                    obj.state["fruit_ripe"] = False
                    obj.dirty = True
                    agent.hunger = max(0.0, agent.hunger - 0.25)
                    agent.energy = min(1.0, agent.energy + 0.1)
                    agent.dirty = True
                    events.append(self._evt(
                        ctx, event_type="nature.fruit_picked",
                        description=f"{agent.name} 摘了 {obj.name} 上的果实",
                        actor=agent.id, target=obj.id, importance=2,
                        obj=obj, payload={"agent_id": agent.id},
                    ))
                    break
                # 自然腐烂
                if obj.state.get("fruit_ripe") and ctx.rng.random() < FRUIT_ROT_PROB:
                    obj.state["fruit_ripe"] = False
                    obj.dirty = True
                    events.append(self._evt(
                        ctx, event_type="nature.fruit_rotted",
                        description=f"{obj.name} 的果实因无人采摘而腐烂",
                        target=obj.id, importance=1, obj=obj,
                    ))
        return events


# ─────────────────────────────────────────────────────────────────────────────
# 注册表 —— 新增事件类型只需在此追加实例
# ─────────────────────────────────────────────────────────────────────────────

NATURAL_EVENT_HANDLERS: list[NaturalEventHandler] = [
    WeatherEventHandler(),
    FireEventHandler(),
    MushroomEventHandler(),
    FlowerEventHandler(),
    FishingEventHandler(),
    BenchRestEventHandler(),
    NoticeEventHandler(),
    StormShelterEventHandler(),
    PuddleEventHandler(),
    FireflyEventHandler(),
    BirdEventHandler(),
    FruitRipenEventHandler(),
]


def tick_all(ctx: NaturalEventContext) -> list[Any]:
    """
    运行所有已注册的自然事件处理器，收集产生的 WorldEvent 列表。
    单个 handler 异常被吞掉并记录日志，不中断仿真循环。
    """
    events: list[Any] = []
    for handler in NATURAL_EVENT_HANDLERS:
        try:
            events.extend(handler.tick(ctx))
        except Exception:
            logger.exception("natural event handler %s crashed", handler.event_category)
    return events
