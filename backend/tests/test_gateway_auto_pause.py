"""游戏 WebSocket 全断线后自动暂停仿真的回归测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.websocket import gateway as gw


@pytest.fixture(autouse=True)
def _reset_gateway_tasks():
    gw._pending_auto_pause.clear()
    gw._manager = None
    yield
    for t in list(gw._pending_auto_pause.values()):
        if not t.done():
            t.cancel()
    gw._pending_auto_pause.clear()
    gw._manager = None


@pytest.mark.asyncio
async def test_auto_pause_schedules_when_last_client_disconnects():
    calls: list[str] = []

    async def fake_pause(sid: str) -> SimpleNamespace:
        calls.append(f"pause:{sid}")
        return SimpleNamespace(id=sid, status="paused")

    mock_rt = MagicMock()
    mock_rt.engine.get_simulation.return_value = SimpleNamespace(
        id="sim-a", status="running"
    )
    mock_rt.pause_simulation = AsyncMock(side_effect=fake_pause)

    settings = SimpleNamespace(
        simulation_auto_pause_when_no_game_ws_clients=True,
        simulation_auto_pause_after_idle_seconds=0.0,
    )

    with (
        patch.object(gw, "get_settings", return_value=settings),
        patch(
            "app.services.simulation_runtime.get_simulation_runtime",
            return_value=mock_rt,
        ),
    ):
        mgr = gw.get_connection_manager()
        ws1, ws2 = object(), object()
        async with mgr._lock:
            mgr._connections["sim-a"].update({ws1, ws2})

        await mgr.disconnect("sim-a", ws1)
        assert "sim-a" not in gw._pending_auto_pause
        await mgr.disconnect("sim-a", ws2)
        task = gw._pending_auto_pause.get("sim-a")
        assert task is not None
        await asyncio.wait_for(task, timeout=2.0)

    assert calls == ["pause:sim-a"]


@pytest.mark.asyncio
async def test_auto_pause_cancelled_when_reconnect_before_idle():
    mock_rt = MagicMock()
    mock_rt.pause_simulation = AsyncMock()

    settings = SimpleNamespace(
        simulation_auto_pause_when_no_game_ws_clients=True,
        simulation_auto_pause_after_idle_seconds=3600.0,
    )

    with (
        patch.object(gw, "get_settings", return_value=settings),
        patch(
            "app.services.simulation_runtime.get_simulation_runtime",
            return_value=mock_rt,
        ),
    ):
        mgr = gw.get_connection_manager()
        ws = object()
        async with mgr._lock:
            mgr._connections["sim-b"].add(ws)
        await mgr.disconnect("sim-b", ws)
        task = gw._pending_auto_pause["sim-b"]
        gw._cancel_scheduled_auto_pause("sim-b")
        await asyncio.sleep(0)

    assert task.cancelled()
    mock_rt.pause_simulation.assert_not_called()


@pytest.mark.asyncio
async def test_auto_pause_feature_disabled_no_schedule():
    settings = SimpleNamespace(
        simulation_auto_pause_when_no_game_ws_clients=False,
        simulation_auto_pause_after_idle_seconds=0.0,
    )

    with patch.object(gw, "get_settings", return_value=settings):
        mgr = gw.get_connection_manager()
        ws = object()
        async with mgr._lock:
            mgr._connections["sim-c"].add(ws)
        await mgr.disconnect("sim-c", ws)

    assert "sim-c" not in gw._pending_auto_pause
