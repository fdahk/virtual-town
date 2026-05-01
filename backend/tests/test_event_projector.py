"""
阶段 19++：世界事件 → 记忆投影器测试。

覆盖：
- 白名单事件（``world.sign_noticed`` 等）写入指定类型 / 重要度的记忆。
- 黑名单事件（``agent.action_started`` 等高频事件）不投影。
- 没有 ``actor_entity_id`` 的纯环境事件不投影。
- 同一 (actor, event_type, target) 在去重窗口内只写入一次。
- ``memory_projection_enabled=False`` 时直接跳过。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.memory import event_projector
from app.domain.memory.event_projector import project_world_event_to_memory


@pytest.fixture(autouse=True)
def _reset_inproc_debounce():
    event_projector._INPROC_DEBOUNCE.clear()
    yield
    event_projector._INPROC_DEBOUNCE.clear()


@pytest.fixture
def memory_writes(monkeypatch):
    """拦截 _write_memory，捕获每次投影调用的参数。"""
    captured: list[dict[str, Any]] = []

    async def _fake_write(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(event_projector, "_write_memory", _fake_write)
    # Redis 不可用，强制走进程内 fallback debounce
    monkeypatch.setattr(
        event_projector, "_claim_debounce", _passthrough_debounce
    )
    return captured


_calls: dict[str, int] = {}


async def _passthrough_debounce(key: str, *, minutes: int) -> bool:  # noqa: ARG001
    """测试用 debounce：第一次返回 True，之后同 key 返回 False。"""
    _calls[key] = _calls.get(key, 0) + 1
    return _calls[key] == 1


@pytest.fixture(autouse=True)
def _reset_calls():
    _calls.clear()
    yield
    _calls.clear()


@pytest.mark.asyncio
async def test_sign_noticed_projected_to_event_memory(memory_writes):
    payload = {
        "event_type": "world.sign_noticed",
        "actor_entity_id": "npc1",
        "target_entity_id": "obj_sign",
        "description": "小明 注意到告示牌：社区文艺演出将于本周末在广场举行",
        "importance": 4,
        "payload": {"notice_text": "社区文艺演出将于本周末在广场举行"},
    }
    await project_world_event_to_memory(payload)
    assert len(memory_writes) == 1
    args = memory_writes[0]
    assert args["actor_id"] == "npc1"
    assert args["memory_type"] == "event"
    assert args["importance"] >= 4
    assert "社区文艺演出" in args["description"]


@pytest.mark.asyncio
async def test_action_started_blocked(memory_writes):
    payload = {
        "event_type": "agent.action_started",
        "actor_entity_id": "npc1",
        "description": "小明：home",
        "importance": 2,
    }
    await project_world_event_to_memory(payload)
    assert memory_writes == []


@pytest.mark.asyncio
async def test_no_actor_skipped(memory_writes):
    payload = {
        "event_type": "world.sign_noticed",
        "description": "环境事件",
        "importance": 4,
    }
    await project_world_event_to_memory(payload)
    assert memory_writes == []


@pytest.mark.asyncio
async def test_default_threshold_filters_low_importance(memory_writes):
    # nature.fruit_rotted 不在白名单 → 走默认阈值 4
    payload = {
        "event_type": "nature.fruit_rotted",
        "actor_entity_id": "npc1",
        "description": "果实变质了",
        "importance": 2,
    }
    await project_world_event_to_memory(payload)
    assert memory_writes == []

    # importance ≥ 4 时投影成 thought
    payload["importance"] = 5
    await project_world_event_to_memory(payload)
    assert len(memory_writes) == 1
    assert memory_writes[0]["memory_type"] == "thought"


@pytest.mark.asyncio
async def test_debounce_skips_repeated_event(memory_writes):
    payload = {
        "event_type": "world.sign_noticed",
        "actor_entity_id": "npc1",
        "target_entity_id": "obj_sign",
        "description": "重复刷屏",
        "importance": 4,
    }
    # 第一次写入；第二次同 key 被 debounce 拦截
    await project_world_event_to_memory(payload)
    await project_world_event_to_memory(payload)
    assert len(memory_writes) == 1


@pytest.mark.asyncio
async def test_disabled_via_settings(memory_writes, monkeypatch):
    fake_settings = MagicMock()
    fake_settings.memory_projection_enabled = False
    monkeypatch.setattr(
        "app.core.config.get_settings", lambda: fake_settings
    )
    payload = {
        "event_type": "world.sign_noticed",
        "actor_entity_id": "npc1",
        "description": "公告",
        "importance": 4,
    }
    await project_world_event_to_memory(payload)
    assert memory_writes == []
