"""N×N 双向关系矩阵生成。

输入：所有 NPC（含玩家）模板列表。
输出：``relationships`` 表的行（仅人类间 + 动物-主人；玩家关系单独处理）。

策略：
1. 把 ``DEFAULT_RELATIONSHIP_PAIRS`` 中显式定义的强关系对称写入 (a→b) (b→a)
2. 对默认 22 NPC 中未覆盖的对，按"同区"/"同职业"做弱默认关系
3. 自定义 NPC（非默认 ID）与所有人为"陌生"弱关系
4. 动物→主人 / 主人→动物 走 ``DEFAULT_OWNER_PAIRS`` 双向写入
"""

from __future__ import annotations

import uuid
from typing import Any

from app.db.templates.npc_defaults import AgentTemplate
from app.db.templates.relationship_seeds import (
    DEFAULT_OWNER_PAIRS,
    DEFAULT_RELATIONSHIP_PAIRS,
)


# 默认弱关系基线
WEAK_BASELINE = {"familiarity": 0.05, "trust": 0.05, "affection": 0.0,
                 "fear": 0.0, "summary": ""}
NEIGHBOR_BASELINE = {"familiarity": 0.25, "trust": 0.2, "affection": 0.05,
                     "fear": 0.0, "summary": "邻居"}
COLLEAGUE_BASELINE = {"familiarity": 0.4, "trust": 0.3, "affection": 0.1,
                      "fear": 0.0, "summary": "同行"}
PET_OWNER_BASELINE = {"familiarity": 0.95, "trust": 0.95, "affection": 0.95,
                      "fear": 0.0, "summary": "主人"}
OWNER_PET_BASELINE = {"familiarity": 0.9, "trust": 0.9, "affection": 0.9,
                      "fear": 0.0, "summary": "我的宠物"}


def _new_id() -> str:
    return f"rel_{uuid.uuid4().hex[:8]}"


def build_matrix(
    humans: list[AgentTemplate],
    animals: list[AgentTemplate],
    *,
    player_id: str | None = None,
) -> list[dict[str, Any]]:
    """构造完整双向关系矩阵。"""

    all_npcs = humans + animals
    npc_by_id: dict[str, AgentTemplate] = {a.id: a for a in all_npcs}

    rels: dict[tuple[str, str], dict[str, Any]] = {}

    def upsert(a: str, b: str, payload: dict[str, Any]) -> None:
        """写入一条 a→b；同 key 已存在时取较强者（familiarity 大优先）。"""
        prev = rels.get((a, b))
        if prev and prev.get("familiarity", 0.0) >= payload.get("familiarity", 0.0):
            return
        rels[(a, b)] = payload

    # 1. 显式强关系（对称）
    for from_id, to_id, fam, trust, aff, fear, summary in DEFAULT_RELATIONSHIP_PAIRS:
        if from_id not in npc_by_id or to_id not in npc_by_id:
            continue
        payload = {"familiarity": fam, "trust": trust, "affection": aff,
                   "fear": fear, "summary": summary}
        upsert(from_id, to_id, dict(payload))
        upsert(to_id, from_id, dict(payload))

    # 2. 弱默认关系（同区 / 同职业）
    human_ids = [h.id for h in humans]
    for i, a in enumerate(humans):
        for b in humans[i + 1:]:
            key_ab = (a.id, b.id)
            key_ba = (b.id, a.id)
            if key_ab in rels and key_ba in rels:
                continue

            base = WEAK_BASELINE
            if a.preferred_district == b.preferred_district:
                base = NEIGHBOR_BASELINE
            if a.occupation and a.occupation == b.occupation:
                base = COLLEAGUE_BASELINE

            payload = dict(base)
            upsert(a.id, b.id, dict(payload))
            upsert(b.id, a.id, dict(payload))

    # 3. 主人 ↔ 宠物
    for animal_id, owner_id, _ in DEFAULT_OWNER_PAIRS:
        if animal_id not in npc_by_id or owner_id not in npc_by_id:
            continue
        upsert(animal_id, owner_id, dict(PET_OWNER_BASELINE))
        upsert(owner_id, animal_id, dict(OWNER_PET_BASELINE))

    # 4. 自定义动物（不在 DEFAULT_OWNER_PAIRS 中但有 owner_id）
    for animal in animals:
        if animal.owner_id and animal.owner_id in npc_by_id:
            key_ab = (animal.id, animal.owner_id)
            key_ba = (animal.owner_id, animal.id)
            if key_ab not in rels:
                upsert(animal.id, animal.owner_id, dict(PET_OWNER_BASELINE))
            if key_ba not in rels:
                upsert(animal.owner_id, animal.id, dict(OWNER_PET_BASELINE))

    # 5. 玩家：与所有 NPC 为初始陌生（familiarity=0），首次见面后 LLM 更新
    if player_id:
        for npc_id in npc_by_id:
            stranger = {"familiarity": 0.0, "trust": 0.0, "affection": 0.0,
                        "fear": 0.0, "summary": "刚到小镇的陌生人"}
            rels[(player_id, npc_id)] = dict(stranger)
            rels[(npc_id, player_id)] = dict(stranger)

    # 序列化为 ORM 字典
    rows: list[dict[str, Any]] = []
    for (from_id, to_id), payload in rels.items():
        rows.append({
            "id": _new_id(),
            "from_agent_id": from_id,
            "to_entity_id": to_id,
            **payload,
        })
    return rows
