"""Agent 层 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.world import TilePosition


class AgentAppearance(BaseModel):
    sprite_sheet: str | None = None
    hair: str | None = None
    clothes: str | None = None
    color: str | None = None
    scale: float = 1.0


class AgentProfile(BaseModel):
    id: str
    entity_type: Literal["human", "animal", "player"]
    name: str
    age: int | None = None
    gender: str | None = None
    species: str | None = None
    appearance: AgentAppearance = Field(default_factory=AgentAppearance)
    occupation: str | None = None
    personality: list[str] = Field(default_factory=list)
    background: str = ""
    lifestyle: str | None = None
    long_term_goals: list[str] = Field(default_factory=list)
    home_location_id: str | None = None

    class Config:
        from_attributes = True


class AgentProfilePatch(BaseModel):
    name: str | None = None
    age: int | None = None
    gender: str | None = None
    appearance: AgentAppearance | None = None
    personality: list[str] | None = None
    background: str | None = None
    lifestyle: str | None = None
    long_term_goals: list[str] | None = None


class AgentRuntimeState(BaseModel):
    agent_id: str
    scene_id: str
    position: TilePosition
    state: str
    emotion: str | None = None
    energy: float
    hunger: float
    social_need: float | None = None
    fear: float | None = None
    status_effects: list[str] = Field(default_factory=list)
    current_action_id: str | None = None
    current_goal: str | None = None
    facing: str = "down"
    updated_at: datetime
    # 阶段 19（社会化升级）：是否忙碌、是否可被打断、当前优先级
    busy_until: datetime | None = None
    interruptible: bool = True
    current_priority: int = 0
    last_social_at: datetime | None = None


class Relationship(BaseModel):
    id: str
    from_agent_id: str
    to_entity_id: str
    familiarity: float
    trust: float
    affection: float
    fear: float
    summary: str | None = None
    updated_at: datetime

    class Config:
        from_attributes = True


# ----- 玩家相关 -----


class CreatePlayerRequest(BaseModel):
    name: str
    personality: list[str] = Field(default_factory=list)
    appearance_description: str | None = None
    background: str | None = None
    sprite_key: str | None = None


class PlayerMoveRequest(BaseModel):
    """
    玩家移动意图。
    - 当 `target` 提供时按路径寻路移动到该 tile；
    - 当仅 `direction` 提供时按方向单步移动一次；
    - 二选一。
    """

    target: TilePosition | None = None
    direction: Literal["up", "down", "left", "right"] | None = None


class PlayerMoveResponse(BaseModel):
    accepted: bool
    path: list[TilePosition] = Field(default_factory=list)
    reason: str | None = None


class PlayerInteractRequest(BaseModel):
    object_id: str | None = None
    entity_id: str | None = None
    interaction_type: str
    extra: dict[str, Any] = Field(default_factory=dict)


class PlayerInteractResponse(BaseModel):
    success: bool
    events: list[str] = Field(default_factory=list)
    reaction: dict[str, Any] | None = None
    message: str | None = None


class PlayerTalkRequest(BaseModel):
    target_entity_id: str
    text: str = Field(..., min_length=1, max_length=500)


class PlayerTalkResponse(BaseModel):
    conversation_id: str
    reply: str
    emotion: str | None = None
    memory_ids: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)


class ApproachNpcRequest(BaseModel):
    """玩家请求靠近某个 NPC，服务端计算路径并加入引擎队列。"""

    npc_id: str


class EndChatRequest(BaseModel):
    """玩家主动关闭对话，释放 NPC 的 CHATTING 状态。"""

    npc_id: str


# ----- 阶段 19：交互请求 / 同意 / 拒绝协议 -----


class InteractionRequestRecord(BaseModel):
    """InteractionRequest 持久化记录的 Pydantic 视图。"""

    id: str
    requester_id: str
    target_id: str
    kind: Literal["chat", "help", "trade"]
    status: Literal["pending", "accepted", "declined", "expired", "cancelled"]
    reason: str | None = None
    decline_kind: Literal["soft", "hard"] | None = None
    npc_line: str | None = None
    requester_priority: int = 3
    target_priority_at_request: int = 0
    created_at: datetime
    resolved_at: datetime | None = None
    expires_at: datetime | None = None

    class Config:
        from_attributes = True


class PlayerInteractionRequestCreate(BaseModel):
    """玩家发起 NPC 交互请求。"""

    target_entity_id: str
    kind: Literal["chat", "help", "trade"] = "chat"
    reason: str | None = None


class PlayerInteractionRequestResponse(BaseModel):
    """请求发起的同步结果：要么直接接受可立即对话，要么返回拒绝原因。"""

    request_id: str
    status: Literal["accepted", "declined"]
    decline_kind: Literal["soft", "hard"] | None = None
    npc_line: str | None = None
    reason: str | None = None
    target_state: str | None = None
