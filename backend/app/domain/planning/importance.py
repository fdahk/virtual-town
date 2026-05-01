"""
重要度评分（阶段 15.5：五分项细化）。

公式（与 ``智能NPC与记忆模块实施方案.md §5.2`` 对齐，补上规则化实现）：

    importance =
      base_event_score +
      emotion_weight +
      relationship_weight +
      novelty_weight +
      danger_weight

最终值 clamp 到 ``[1, 10]``，写入 Memory 时 ``importance_detail`` 字段记录每一项原始贡献。

动机：让"亲历 vs 旁观"、"撞见熟人 vs 路人"、"走进 deep_water 区"等场景之间
产生**可解释的 importance 差异**；观测平台的 Memory Inspector 可直接读这份明细。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 每个 memory_type 的基线分数
_BASE_BY_TYPE: dict[str, int] = {
    "event": 2,
    "chat": 2,
    "thought": 3,
    "summary": 5,
}

# 特定事件类型的额外基线
_BASE_BY_EVENT_TYPE: dict[str, int] = {
    "world.hazard_triggered": 6,
    "animal.reacted": 2,
    "world.object_interacted": 1,
    "agent.action_finished": 0,
    "dialogue.message_created": 1,
    "world.scene_changed": 1,
    "memory.created": 0,
}

# emotion 强度映射
_EMOTION_WEIGHT: dict[str, int] = {
    "scared": 3,
    "fear": 3,
    "angry": 3,
    "panic": 3,
    "sad": 2,
    "happy": 2,
    "excited": 2,
    "focused": 1,
    "friendly": 1,
    "grateful": 1,
    "curious": 1,
    "neutral": 0,
    "calm": 0,
}


@dataclass
class ImportanceContext:
    """计算 importance 时可以传入的上下文。

    所有字段都是可选；缺省对应 0 贡献。
    """

    memory_type: str = "event"
    event_type: str | None = None
    emotion: str | None = None
    actor_entity_id: str | None = None
    subject_entity_id: str | None = None
    # 主体 (actor) 视角下，对 subject 的关系分值（0-1 归一化，越高越熟）
    relationship_familiarity: float | None = None
    relationship_affection: float | None = None
    relationship_fear: float | None = None
    # 是否亲历（actor == 当前 agent）
    is_first_person: bool = False
    # 已知相似记忆计数（用于 novelty，越大越不新鲜）
    similar_memory_count: int = 0
    # 是否涉及危险（deep_water / hazard）
    hazard_involved: bool = False
    hazard_level: int | None = None
    # 玩家参与（通常让事件更值得记住）
    involves_player: bool = False
    # 由调用方传入的额外提示（不影响分值，但写入明细便于审计）
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportanceResult:
    importance: int  # 1..10
    detail: dict[str, Any]


def compute_importance(ctx: ImportanceContext) -> ImportanceResult:
    """按五分项计算 importance。返回整数值 + 明细字典。"""

    base = _BASE_BY_TYPE.get(ctx.memory_type, 2)
    if ctx.event_type and ctx.event_type in _BASE_BY_EVENT_TYPE:
        base += _BASE_BY_EVENT_TYPE[ctx.event_type]

    emotion_w = _EMOTION_WEIGHT.get((ctx.emotion or "").lower(), 0)

    relationship_w = 0
    rf = ctx.relationship_familiarity or 0.0
    ra = ctx.relationship_affection or 0.0
    rfear = ctx.relationship_fear or 0.0
    # 熟人 +1~+2；关系亲密 +1；关系恐惧 +2；陌生人 +0
    if rf >= 0.5:
        relationship_w += 1
    if rf >= 0.8:
        relationship_w += 1
    if ra >= 0.5:
        relationship_w += 1
    if rfear >= 0.5:
        relationship_w += 2
    if ctx.involves_player:
        relationship_w += 1

    novelty_w = 0
    # similar_memory_count=0 → +2；1 → +1；>=3 → 0
    if ctx.similar_memory_count <= 0:
        novelty_w = 2
    elif ctx.similar_memory_count == 1:
        novelty_w = 1
    elif ctx.similar_memory_count == 2:
        novelty_w = 1

    danger_w = 0
    if ctx.hazard_involved:
        danger_w = 3
        if ctx.hazard_level and ctx.hazard_level >= 2:
            danger_w += 2

    # 亲历加成：事件亲历者比旁观者记得更清楚
    perspective_bonus = 1 if ctx.is_first_person else 0

    raw = base + emotion_w + relationship_w + novelty_w + danger_w + perspective_bonus
    importance = max(1, min(raw, 10))

    detail: dict[str, Any] = {
        "base": base,
        "emotion": emotion_w,
        "relationship": relationship_w,
        "novelty": novelty_w,
        "danger": danger_w,
        "perspective": perspective_bonus,
        "raw": raw,
        "final": importance,
    }
    if ctx.extras:
        detail["extras"] = ctx.extras
    return ImportanceResult(importance=importance, detail=detail)


__all__ = [
    "ImportanceContext",
    "ImportanceResult",
    "compute_importance",
]
