"""重要度五因素评分单元测试（阶段 15.5）。"""

from __future__ import annotations

from app.domain.planning import ImportanceContext, compute_importance


def test_first_person_vs_bystander() -> None:
    """亲历者 vs 旁观者对同一事件写入的 importance 不同。

    使用普通事件，避免 raw 先一步触顶 10 让 perspective 贡献不可见。
    """
    base_kwargs = dict(
        memory_type="event",
        event_type="world.object_interacted",
        similar_memory_count=3,  # 不给 novelty 加成
    )
    r_first = compute_importance(
        ImportanceContext(is_first_person=True, **base_kwargs)
    )
    r_bystander = compute_importance(
        ImportanceContext(is_first_person=False, **base_kwargs)
    )
    assert r_first.importance > r_bystander.importance
    assert r_first.detail["perspective"] == 1
    assert r_bystander.detail["perspective"] == 0


def test_importance_breakdown_present() -> None:
    ctx = ImportanceContext(
        memory_type="chat",
        emotion="happy",
        involves_player=True,
        relationship_familiarity=0.9,
    )
    res = compute_importance(ctx)
    detail = res.detail
    assert set(detail.keys()) >= {
        "base",
        "emotion",
        "relationship",
        "novelty",
        "danger",
        "perspective",
        "raw",
        "final",
    }
    # 有关系加成（熟人 + 玩家）
    assert detail["relationship"] >= 2
    # 关系、情绪都非零
    assert detail["emotion"] >= 1


def test_importance_clamped_to_1_10() -> None:
    # 极端参数：高情绪 + 危险 + 熟人 + 首见
    ctx = ImportanceContext(
        memory_type="summary",
        event_type="world.hazard_triggered",
        emotion="panic",
        is_first_person=True,
        involves_player=True,
        relationship_familiarity=1.0,
        relationship_fear=1.0,
        similar_memory_count=0,
        hazard_involved=True,
        hazard_level=5,
    )
    res = compute_importance(ctx)
    assert 1 <= res.importance <= 10
    assert res.detail["raw"] >= res.importance  # raw 可能大于 final


def test_importance_floor_one() -> None:
    ctx = ImportanceContext(memory_type="event")
    res = compute_importance(ctx)
    assert res.importance >= 1
