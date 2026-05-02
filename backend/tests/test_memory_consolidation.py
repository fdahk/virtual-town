"""
阶段 20：记忆合并 / 沉思 单测（规则兜底版本，不依赖数据库 / LLM）。

覆盖：
- ``_bucket_by_theme`` 按 keywords[0] / subject 分桶。
- ``_rule_summary`` 规则兜底输出包含主题词 + 引用前几条。
- ``_rule_rumination`` 规则兜底输出非空 + 引用 top importance。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.domain.memory.consolidation import _bucket_by_theme, _rule_summary
from app.domain.memory.rumination import _rule_rumination


def _mem(
    mid: str,
    desc: str,
    importance: int,
    *,
    keywords: list[str] | None = None,
    subject: str | None = None,
    memory_type: str = "event",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=mid,
        description=desc,
        importance=importance,
        memory_type=memory_type,
        keywords=list(keywords or []),
        subject=subject,
        created_at=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )


def test_bucket_by_theme_uses_first_keyword():
    rows = [
        _mem("a", "和小王聊咖啡", 2, keywords=["咖啡", "chat"]),
        _mem("b", "和小王聊点心", 2, keywords=["咖啡"]),
        _mem("c", "看到流星", 3, keywords=["天文"]),
    ]
    buckets = _bucket_by_theme(rows)
    assert "咖啡" in buckets and len(buckets["咖啡"]) == 2
    assert "天文" in buckets and len(buckets["天文"]) == 1


def test_bucket_falls_back_to_subject_then_type():
    rows = [
        _mem("a", "想到母亲", 3, subject="母亲"),
        _mem("b", "想到母亲", 3, subject="母亲"),
        _mem("c", "无主题", 1),
    ]
    buckets = _bucket_by_theme(rows)
    assert "母亲" in buckets
    assert "event" in buckets


def test_rule_summary_mentions_theme_and_top_descriptions():
    rows = [
        _mem("a", "和小王在便利店遇到", 3),
        _mem("b", "和小王下棋", 5),
        _mem("c", "和小王闲聊", 2),
    ]
    out = _rule_summary("小王", rows)
    assert "小王" in out.summary
    assert out.importance <= 5
    # 高 importance 的 b 应该被提到
    assert "下棋" in out.summary or "便利店" in out.summary


def test_rule_rumination_uses_top_importance_evidence():
    agent = SimpleNamespace(
        name="小芳", entity_type="human", occupation=None, personality=[], background=None
    )
    rows = [
        _mem("h1", "第一次见到小王心跳加速", 9),
        _mem("h2", "和小王共度生日", 8),
        _mem("low", "下雨了", 3),
    ]
    out = _rule_rumination(agent, rows)
    assert "h1" in out.evidence_memory_ids
    assert out.thought.strip()
    assert 4 <= out.importance <= 6
