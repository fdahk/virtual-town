"""initial schema

创建完整的基础表：
- map_scenes / map_tiles / locations / portals / world_objects
- agents / agent_states / relationships
- agent_actions / memories / world_events / dialogue_messages
- simulations

Revision ID: 20260430_0001
Revises:
Create Date: 2026-04-30 11:30:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision: str = "20260430_0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "map_scenes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("scene_type", sa.String(16), nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("tile_size", sa.Integer, nullable=False),
        sa.Column("tiled_map_url", sa.String(256), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "map_tiles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("scene_id", sa.String(64), sa.ForeignKey("map_scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("x", sa.Integer, nullable=False),
        sa.Column("y", sa.Integer, nullable=False),
        sa.Column("terrain", sa.String(32), nullable=False),
        sa.Column("walkable", sa.Boolean, nullable=False),
        sa.Column("blocks_movement", sa.Boolean, nullable=False),
        sa.Column("blocks_vision", sa.Boolean, nullable=False),
        sa.Column("hazard_type", sa.String(32), nullable=True),
        sa.Column("hazard_level", sa.Integer, nullable=True),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.UniqueConstraint("scene_id", "x", "y", name="uq_map_tiles_scene_xy"),
    )
    op.create_index("ix_map_tiles_scene", "map_tiles", ["scene_id"])

    op.create_table(
        "locations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scene_id", sa.String(64), sa.ForeignKey("map_scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("location_type", sa.String(32), nullable=False),
        sa.Column("bounds", postgresql.JSONB, nullable=False),
        sa.Column("entry_tiles", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("open_hours", postgresql.JSONB, nullable=True),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "portals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("from_scene_id", sa.String(64), sa.ForeignKey("map_scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_tile", postgresql.JSONB, nullable=False),
        sa.Column("to_scene_id", sa.String(64), sa.ForeignKey("map_scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_tile", postgresql.JSONB, nullable=False),
        sa.Column("interaction_type", sa.String(16), nullable=False),
        sa.Column("requires_permission", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "world_objects",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scene_id", sa.String(64), sa.ForeignKey("map_scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("position", postgresql.JSONB, nullable=False),
        sa.Column("size", postgresql.JSONB, nullable=False),
        sa.Column("blocks_movement", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("available_interactions", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("state", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("age", sa.Integer, nullable=True),
        sa.Column("gender", sa.String(16), nullable=True),
        sa.Column("species", sa.String(16), nullable=True),
        sa.Column("occupation", sa.String(64), nullable=True),
        sa.Column("personality", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("background", sa.Text, nullable=False, server_default=""),
        sa.Column("lifestyle", sa.Text, nullable=True),
        sa.Column("appearance", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("long_term_goals", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("home_location_id", sa.String(64), nullable=True),
        sa.Column("schedule_template", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "agent_states",
        sa.Column("agent_id", sa.String(64), sa.ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scene_id", sa.String(64), sa.ForeignKey("map_scenes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("x", sa.Integer, nullable=False),
        sa.Column("y", sa.Integer, nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("emotion", sa.String(32), nullable=True),
        sa.Column("energy", sa.Float, nullable=False, server_default=sa.text("1.0")),
        sa.Column("hunger", sa.Float, nullable=False, server_default=sa.text("0.2")),
        sa.Column("social_need", sa.Float, nullable=False, server_default=sa.text("0.3")),
        sa.Column("fear", sa.Float, nullable=False, server_default=sa.text("0.0")),
        sa.Column("status_effects", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("current_action_id", sa.String(64), nullable=True),
        sa.Column("current_goal", sa.String(256), nullable=True),
        sa.Column("path", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("facing", sa.String(8), nullable=False, server_default="down"),
        sa.Column("last_decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "relationships",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("from_agent_id", sa.String(64), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_entity_id", sa.String(64), nullable=False),
        sa.Column("familiarity", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("trust", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("affection", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("fear", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("from_agent_id", "to_entity_id", name="uq_relationships_from_to"),
    )

    op.create_table(
        "agent_actions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("agent_id", sa.String(64), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("target_location_id", sa.String(64), nullable=True),
        sa.Column("target_entity_id", sa.String(64), nullable=True),
        sa.Column("target_object_id", sa.String(64), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("action_metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    embedding_dim = get_settings().llm_embedding_dim
    op.create_table(
        "memories",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("agent_id", sa.String(64), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_type", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(128), nullable=True),
        sa.Column("predicate", sa.String(128), nullable=True),
        sa.Column("object", sa.String(128), nullable=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("importance", sa.Integer, nullable=False, server_default=sa.text("3")),
        sa.Column("emotional_valence", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("keywords", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_memory_ids", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("embedding", Vector(embedding_dim), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ttl_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_memories_created_at", "memories", ["created_at"])
    op.create_index("ix_memories_agent_created", "memories", ["agent_id", "created_at"])
    op.create_index("ix_memories_agent_scope", "memories", ["agent_id", "scope"])

    op.create_table(
        "world_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("simulation_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("actor_entity_id", sa.String(64), nullable=True),
        sa.Column("target_entity_id", sa.String(64), nullable=True),
        sa.Column("location_id", sa.String(64), nullable=True),
        sa.Column("scene_id", sa.String(64), nullable=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("importance", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_world_events_created_at", "world_events", ["created_at"])
    op.create_index("ix_world_events_sim_time", "world_events", ["simulation_id", "created_at"])
    op.create_index("ix_world_events_actor", "world_events", ["actor_entity_id"])

    op.create_table(
        "dialogue_messages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("speaker_id", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("emotion", sa.String(32), nullable=True),
        sa.Column("animation", sa.String(32), nullable=True),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dialogue_conv_created", "dialogue_messages", ["conversation_id", "created_at"])

    op.create_table(
        "simulations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("world_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("world_tick_hz", sa.Float, nullable=False, server_default=sa.text("5")),
        sa.Column("ai_tick_minutes", sa.Integer, nullable=False, server_default=sa.text("5")),
        sa.Column("speed_multiplier", sa.Float, nullable=False, server_default=sa.text("1")),
        sa.Column("current_step", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("simulations")
    op.drop_index("ix_dialogue_conv_created", table_name="dialogue_messages")
    op.drop_table("dialogue_messages")
    op.drop_index("ix_world_events_actor", table_name="world_events")
    op.drop_index("ix_world_events_sim_time", table_name="world_events")
    op.drop_index("ix_world_events_created_at", table_name="world_events")
    op.drop_table("world_events")
    op.drop_index("ix_memories_agent_scope", table_name="memories")
    op.drop_index("ix_memories_agent_created", table_name="memories")
    op.drop_index("ix_memories_created_at", table_name="memories")
    op.drop_table("memories")
    op.drop_table("agent_actions")
    op.drop_table("relationships")
    op.drop_table("agent_states")
    op.drop_table("agents")
    op.drop_table("world_objects")
    op.drop_table("portals")
    op.drop_table("locations")
    op.drop_index("ix_map_tiles_scene", table_name="map_tiles")
    op.drop_table("map_tiles")
    op.drop_table("map_scenes")
