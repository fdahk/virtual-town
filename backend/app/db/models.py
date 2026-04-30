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
    """运行态。固定为 1:1 关系，避免重复查询 agents 主表。"""

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
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="short_term")
    subject: Mapped[str | None] = mapped_column(String(128), nullable=True)
    predicate: Mapped[str | None] = mapped_column(String(128), nullable=True)
    object: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    emotional_valence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    keywords: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_memory_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ttl_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# 仿真
# ---------------------------------------------------------------------------


class Simulation(Base, TimestampMixin):
    __tablename__ = "simulations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    world_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    world_tick_hz: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
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


__all__ = [
    "Agent",
    "AgentAction",
    "AgentState",
    "DialogueMessage",
    "Location",
    "MapScene",
    "MapTile",
    "Memory",
    "Portal",
    "Relationship",
    "Simulation",
    "WorldEvent",
    "WorldObject",
]
