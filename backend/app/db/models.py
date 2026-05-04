"""
数据库 ORM 模型。

字段命名与 `数据模型与接口契约V1.md` 保持一致。
所有业务主键使用 UUID（字符串形式）。

关键表：
- map_scenes       场景（室外/室内）
- map_tiles        每一格子（后端权威碰撞与危险）
- locations        语义地点
- portals          场景之间的传送门
- world_objects    家具/设施/障碍物
- agents           NPC / 动物 / 玩家（统一实体）
- agent_states     运行态（位置、当前状态等）
- relationships    实体间关系
- agent_actions    行动计划
- memories         记忆（含 pgvector embedding）
- world_events     世界事件流
- simulations      仿真实例
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base, TimestampMixin

EMBEDDING_DIM = get_settings().llm_embedding_dim


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# 世界：场景、格子、地点、传送门、物体
# ---------------------------------------------------------------------------


class MapScene(Base, TimestampMixin):
    """一个可运行的 2D 场景，例如室外小镇、咖啡店内部。"""

    __tablename__ = "map_scenes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    scene_type: Mapped[str] = mapped_column(String(16), nullable=False)  # outdoor / indoor
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    tile_size: Mapped[int] = mapped_column(Integer, nullable=False, default=32)
    tiled_map_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    tiles: Mapped[list["MapTile"]] = relationship(back_populates="scene", cascade="all, delete-orphan")
    locations: Mapped[list["Location"]] = relationship(back_populates="scene")
    objects: Mapped[list["WorldObject"]] = relationship(back_populates="scene")


class MapTile(Base):
    """权威地图格子。Phaser 渲染不参与真相判定。"""

    __tablename__ = "map_tiles"
    __table_args__ = (
        UniqueConstraint("scene_id", "x", "y", name="uq_map_tiles_scene_xy"),
        Index("ix_map_tiles_scene", "scene_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("map_scenes.id", ondelete="CASCADE"), nullable=False)
    x: Mapped[int] = mapped_column(Integer, nullable=False)
    y: Mapped[int] = mapped_column(Integer, nullable=False)
    terrain: Mapped[str] = mapped_column(String(32), nullable=False, default="grass")
    walkable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    blocks_movement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blocks_vision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hazard_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hazard_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    scene: Mapped[MapScene] = relationship(back_populates="tiles")


class Location(Base, TimestampMixin):
    __tablename__ = "locations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    scene_id: Mapped[str] = mapped_column(ForeignKey("map_scenes.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    location_type: Mapped[str] = mapped_column(String(32), nullable=False)
    bounds: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    entry_tiles: Mapped[list[dict[str, int]]] = mapped_column(JSONB, nullable=False, default=list)
    open_hours: Mapped[dict[str, str] | None] = mapped_column(JSONB, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    scene: Mapped[MapScene] = relationship(back_populates="locations")


class Portal(Base, TimestampMixin):
    __tablename__ = "portals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    from_scene_id: Mapped[str] = mapped_column(ForeignKey("map_scenes.id", ondelete="CASCADE"), nullable=False)
    from_tile: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    to_scene_id: Mapped[str] = mapped_column(ForeignKey("map_scenes.id", ondelete="CASCADE"), nullable=False)
    to_tile: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    interaction_type: Mapped[str] = mapped_column(String(16), nullable=False, default="auto_enter")
    requires_permission: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)


class WorldObject(Base, TimestampMixin):
    __tablename__ = "world_objects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    scene_id: Mapped[str] = mapped_column(ForeignKey("map_scenes.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    size: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=lambda: {"width": 1, "height": 1})
    blocks_movement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    available_interactions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    scene: Mapped[MapScene] = relationship(back_populates="objects")


# ---------------------------------------------------------------------------
# Agent：NPC / 动物 / 玩家
# ---------------------------------------------------------------------------


class Agent(Base, TimestampMixin):
    """
    所有可被感知的实体都是 Agent。
    - entity_type: human / animal / player
    - species:     human / dog / cat（动物需填）
    玩家角色也存在 agents 表中，方便 NPC 记忆和感知统一处理。
    """

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    species: Mapped[str | None] = mapped_column(String(16), nullable=True)
    occupation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    personality: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    background: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lifestyle: Mapped[str | None] = mapped_column(Text, nullable=True)
    appearance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    long_term_goals: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    home_location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schedule_template: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    state: Mapped["AgentState"] = relationship(back_populates="agent", uselist=False, cascade="all, delete-orphan")


class AgentState(Base, TimestampMixin):
    """运行态。固定为 1:1 关系，避免重复查询 agents 主表。

    阶段 19（社会化升级）新增字段：

    - ``busy_until``：当前活动占用结束时间，决策器与请求评估器据此判断"忙碌"。
    - ``interruptible``：是否允许被打断（睡眠/紧急任务为 False）。
    - ``current_priority``：当前任务优先级 0-10，请求评估器对比 incoming 请求紧迫度。
    - ``last_social_at``：上一次完成社交（对话/被请求接受）的游戏时间，
      用于社交动机评估（社交需求满足度）。
    """

    __tablename__ = "agent_states"

    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    scene_id: Mapped[str] = mapped_column(ForeignKey("map_scenes.id", ondelete="RESTRICT"), nullable=False)
    x: Mapped[int] = mapped_column(Integer, nullable=False)
    y: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="IDLE")
    emotion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    energy: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    hunger: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    social_need: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)
    fear: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status_effects: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    current_action_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_goal: Mapped[str | None] = mapped_column(String(256), nullable=True)
    path: Mapped[list[dict[str, int]]] = mapped_column(JSONB, nullable=False, default=list)
    facing: Mapped[str] = mapped_column(String(8), nullable=False, default="down")
    last_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 阶段 19：社会化升级
    busy_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interruptible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    current_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_social_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped[Agent] = relationship(back_populates="state")


class Relationship(Base, TimestampMixin):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("from_agent_id", "to_entity_id", name="uq_relationships_from_to"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    from_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    to_entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    familiarity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trust: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    affection: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fear: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class InteractionRequest(Base):
    """阶段 19：交互请求 / 同意 / 拒绝 协议持久化记录。

    生命周期：``pending → accepted | declined | expired | cancelled``。
    decline_kind 区分软拒（短台词后释放）和硬拒（直接拒绝，需要等关系/状态变化）。
    """

    __tablename__ = "interaction_requests"
    __table_args__ = (
        Index("ix_interaction_requests_target_status", "target_id", "status"),
        Index("ix_interaction_requests_requester_created", "requester_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    simulation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requester_id: Mapped[str] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    target_id: Mapped[str] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="chat")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decline_kind: Mapped[str | None] = mapped_column(String(8), nullable=True)
    npc_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    requester_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    target_priority_at_request: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# 行动 / 事件 / 记忆
# ---------------------------------------------------------------------------


class AgentAction(Base, TimestampMixin):
    __tablename__ = "agent_actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    action_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class WorldEvent(Base):
    __tablename__ = "world_events"
    __table_args__ = (
        Index("ix_world_events_sim_time", "simulation_id", "created_at"),
        Index("ix_world_events_actor", "actor_entity_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    simulation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="world")
    actor_entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scene_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class Memory(Base):
    """
    Agent 记忆。embedding 为可空：Embedding 服务失败时记忆先写入，
    稍后由后台任务补算。
    """

    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_agent_created", "agent_id", "created_at"),
        Index("ix_memories_agent_scope", "agent_id", "scope"),
        Index("ix_memories_summarized_into", "summarized_into_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # 阶段 20：scope 取值集合扩展为
    #   working / short_term / long_term / archived / consolidated
    # 其中 consolidated 表示该条已被某条 summary（long_term）合并，仅作为证据存在；
    # 默认检索只覆盖前三类。
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="short_term")
    subject: Mapped[str | None] = mapped_column(String(128), nullable=True)
    predicate: Mapped[str | None] = mapped_column(String(128), nullable=True)
    object: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # 阶段 15.5：五因素明细（base / emotion / relationship / novelty / danger）
    importance_detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    emotional_valence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    keywords: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_memory_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ttl_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 阶段 20：被合并到的 summary memory.id；非 NULL 即代表"已被合并"。
    # consolidation worker 每天把 archived 同主题 ≥3 条合并成 summary 后回填。
    summarized_into_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


# ---------------------------------------------------------------------------
# 仿真
# ---------------------------------------------------------------------------


class Simulation(Base, TimestampMixin):
    __tablename__ = "simulations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    world_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    world_tick_hz: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)
    ai_tick_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    speed_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ---------------------------------------------------------------------------
# 对话
# ---------------------------------------------------------------------------


class DialogueMessage(Base):
    """对话消息。每一条对话都存档，方便回放与记忆溯源。"""

    __tablename__ = "dialogue_messages"
    __table_args__ = (
        Index("ix_dialogue_conv_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    speaker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    emotion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    animation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# 异步任务队列（阶段 12）
# ---------------------------------------------------------------------------


class Task(Base, TimestampMixin):
    """异步任务记录（agent_decision / daily_plan / embedding / reflection / ...）。

    - ``idempotency_key`` 规范：``{task_type}:{entity_id}:{simulation_step}``，
      保证同一仿真步 + 同一实体的任务不会被重复投递。
    - ``payload`` 中存业务参数 + ``trace`` 快照，供 worker 恢复 trace 上下文。
    - 完整生命周期：``pending → running → succeeded / failed / timeout / cancelled``。
    """

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
        Index("ix_tasks_status_enqueued_at", "status", "enqueued_at"),
        Index("ix_tasks_type_status", "task_type", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    task_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    simulation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    simulation_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class TaskStatusLog(Base):
    """任务状态变迁审计日志（14.2）。"""

    __tablename__ = "task_status_log"
    __table_args__ = (
        Index("ix_task_status_log_task_created", "task_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# ---------------------------------------------------------------------------
# 可观测性（阶段 14.2）
# ---------------------------------------------------------------------------


class ObservabilityEvent(Base):
    """统一观测事件流：跨模块 span / log / error / audit。

    业务代码不直接写这张表，而是通过 ``app.services.observer.Observer`` 埋点。
    """

    __tablename__ = "observability_events"
    __table_args__ = (
        Index("ix_obs_events_created", "created_at"),
        Index("ix_obs_events_trace", "trace_id"),
        Index("ix_obs_events_sim_created", "simulation_id", "created_at"),
        Index("ix_obs_events_category", "category", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    simulation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    span_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False, default="INFO")
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    player_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    world_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    world_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LLMCallRecord(Base):
    """LLM 调用审计（14.2）。敏感信息（prompt 全文、api key）默认不记录。"""

    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_created", "created_at"),
        Index("ix_llm_calls_trace", "trace_id"),
        Index("ix_llm_calls_model", "model", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    span_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    simulation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    caller_module: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    schema_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ToolCallRecord(Base):
    """Tool calling 审计（14.2）。"""

    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_created", "created_at"),
        Index("ix_tool_calls_trace", "trace_id"),
        Index("ix_tool_calls_tool", "tool", "created_at"),
        Index("ix_tool_calls_agent", "caller_agent_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    span_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    simulation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    caller_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    schema_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    permission_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    world_state_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 阶段 18.2：权限审计结果（allow / deny_permission / deny_schema / deny_world / ...）
    policy_outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# 阶段 15：层次化规划
# ---------------------------------------------------------------------------


class AgentPlan(Base, TimestampMixin):
    """Agent 三层计划（daily_plan / hourly_schedule / task_decomposition）。

    - daily_plan 每游戏日一条，content 描述当天的高层目标与阶段安排。
    - hourly_schedule 把 daily_plan 拆到小时粒度；parent_plan_id 指向 daily_plan。
    - task_decomposition 把当前小时拆为 5/10/30 分钟子任务；parent 指向 hourly。

    设计决策：
    - 计划不直接改世界，工具调用仍是改世界的唯一通道。
    - ``status=active`` 的计划可能被外部事件（§15.3）触发 ``status=superseded`` 后
      重新生成新一条，保留旧计划作为审计。
    """

    __tablename__ = "agent_plans"
    __table_args__ = (
        Index("ix_agent_plans_agent_day", "agent_id", "day_key"),
        Index("ix_agent_plans_agent_type_status", "agent_id", "plan_type", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    plan_type: Mapped[str] = mapped_column(String(32), nullable=False)
    day_key: Mapped[str] = mapped_column(String(16), nullable=False)
    hour_key: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_plans.id", ondelete="SET NULL"), nullable=True
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="rule")


__all__ = [
    "AgentPlan",
    "Agent",
    "AgentAction",
    "AgentState",
    "DialogueMessage",
    "InteractionRequest",
    "LLMCallRecord",
    "Location",
    "MapScene",
    "MapTile",
    "Memory",
    "ObservabilityEvent",
    "Portal",
    "Relationship",
    "Simulation",
    "Task",
    "TaskStatusLog",
    "ToolCallRecord",
    "WorldEvent",
    "WorldObject",
]
