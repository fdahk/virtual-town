"""反思规则兜底单元测试（不依赖数据库 / LLM）。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.domain.memory.reflection import _rule_daily_summary, _rule_reflect


def _mem(mid: str, desc: str, importance: int, mtype: str = "event") -> SimpleNamespace:
    return SimpleNamespace(
        id=mid,
        description=desc,
        importance=importance,
        memory_type=mtype,
        created_at=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )


def test_rule_reflect_references_top_memories():
    agent = SimpleNamespace(name="小芳", personality=["热情"])
    mems = [
        _mem("m1", "小王点了美式咖啡", 8),
        _mem("m2", "看到小明在做作业", 4),
        _mem("m3", "天气很好", 2),
    ]
    out = _rule_reflect(agent, mems)
    assert len(out) == 1
    assert "m1" in out[0].evidence_memory_ids
    assert out[0].importance >= 5


def test_rule_reflect_empty():
    agent = SimpleNamespace(name="小芳", personality=[])
    assert _rule_reflect(agent, []) == []


def test_rule_daily_summary_mentions_highlight():
    agent = SimpleNamespace(name="小王", personality=["理性"])
    mems = [
        _mem("m1", "点了一杯美式咖啡", 6, "event"),
        _mem("m2", "聊了几句", 3, "chat"),
    ]
    summary = _rule_daily_summary(agent, mems)
    assert "美式" in summary.summary
    assert summary.highlights
