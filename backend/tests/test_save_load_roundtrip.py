"""阶段 21：JSON 存档导入/导出 roundtrip 测试。

覆盖：
- ``export_save`` → ``import_save`` 双向往返不丢字段
- 主版本号不兼容时抛 ``SaveValidationError``（HTTP 400）
- 必需字段（agents 等）缺失时抛 ``SaveValidationError``
- 导入后 simulation 行存在且 status / current_step 与 payload 一致
- memories 的 ``embedding`` 列被故意置 NULL（由后续 backfill）

与 ``test_tasks_and_observer.py`` 一样依赖 ``TEST_DATABASE_URL`` 指向一个
干净的 Postgres 实例（pgvector + JSONB 是必需的，sqlite 跑不起来）。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import Agent, Memory, Simulation
from app.db.templates import (
    DEFAULT_ANIMAL_TEMPLATES,
    DEFAULT_HUMAN_TEMPLATES,
    DEFAULT_PLAYER_TEMPLATE,
)
from app.domain.world_gen import GenerationConfig, generate_world
from app.domain.world_gen.apply import apply_plan_async
from app.services.game_service import (
    SAVE_FORMAT_VERSION,
    SaveValidationError,
    _validate_save,
    export_save,
    import_save,
)


# ---------------------------------------------------------------------------
# 纯校验测试（不需要 DB）
# ---------------------------------------------------------------------------


def test_validate_save_rejects_non_dict() -> None:
    with pytest.raises(SaveValidationError, match="JSON 对象"):
        _validate_save([])  # type: ignore[arg-type]


def test_validate_save_rejects_missing_version() -> None:
    with pytest.raises(SaveValidationError, match="version"):
        _validate_save({"scenes": []})


def test_validate_save_rejects_incompatible_major() -> None:
    with pytest.raises(SaveValidationError, match="主版本号"):
        _validate_save({
            "version": "2.0",
            "scenes": [], "tiles": [], "locations": [], "agents": [{}],
        })


def test_validate_save_rejects_empty_agents() -> None:
    with pytest.raises(SaveValidationError, match="agents"):
        _validate_save({
            "version": SAVE_FORMAT_VERSION,
            "scenes": [], "tiles": [], "locations": [], "agents": [],
        })


def test_validate_save_accepts_minor_version_diff() -> None:
    """次版本号差异（如 1.0 vs 1.1）应允许，新字段缺失靠默认值兜底。"""
    _validate_save({
        "version": "1.0",
        "scenes": [], "tiles": [], "locations": [], "agents": [{"id": "a"}],
    })


# ---------------------------------------------------------------------------
# 集成测试（需要 TEST_DATABASE_URL）
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory() -> async_sessionmaker:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set; skip save/load roundtrip "
            "(requires an isolated postgres with pgvector)"
        )
    engine = create_async_engine(url, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception:
        pytest.skip("postgres unavailable; skip save/load roundtrip")
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest.fixture
def mock_runtime(monkeypatch):
    """import_save 内部会调 ``simulation_runtime.reload()``：测试里短路掉。"""
    fake_runtime = MagicMock()
    fake_runtime.reload = AsyncMock()
    fake_runtime.engine = MagicMock()

    from app.services import game_service

    monkeypatch.setattr(game_service, "get_simulation_runtime", lambda: fake_runtime)
    return fake_runtime


async def _seed_minimal_world(session: AsyncSession, *, seed: int = 7) -> None:
    """生成一个最小可用的世界（室外图符合布局下界，仅用更少模板）。

    GenerationConfig 默认 22 NPC + 大地图，对单元测试太重；这里用更小的 seed
    + 截断模板列表加速。室外尺寸需 ≥120×90，否则会触发世界布局校验失败。
    """
    config = GenerationConfig(
        seed=seed,
        outdoor_width=120,
        outdoor_height=90,
        humans=list(DEFAULT_HUMAN_TEMPLATES[:2]),
        animals=list(DEFAULT_ANIMAL_TEMPLATES[:1]),
        player=DEFAULT_PLAYER_TEMPLATE,
    )
    plan = generate_world(config)
    await apply_plan_async(session, plan)
    # 加一个 simulation 行，让 export_save 的 simulation 字段非空
    session.add(Simulation(
        id="sim_test_save",
        status="running",
        world_time=datetime.now(timezone.utc),
        current_step=42,
        speed_multiplier=2.0,
    ))
    # 加一条记忆，用于验证 memories 也参与 roundtrip
    agent = (await session.execute(select(Agent).limit(1))).scalar_one()
    session.add(Memory(
        id="mem_test_001", agent_id=agent.id,
        memory_type="observation", scope="short_term",
        description="测试记忆：玩家进入小镇",
        importance=4, importance_detail={"base": 4.0},
        emotional_valence=0.1,
        keywords=["enter", "town"],
        evidence_memory_ids=[],
        embedding=None,
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()


@pytest.mark.asyncio
async def test_export_then_import_roundtrip_preserves_counts(
    session_factory, mock_runtime,
) -> None:
    """export → import → export 后两次 export 的关键 counts 应一致。"""
    async with session_factory() as session:
        await _seed_minimal_world(session)
        snapshot_a = await export_save(session)
        counts_a = snapshot_a["counts"]

    async with session_factory() as session:
        result = await import_save(session, snapshot_a)
        assert result["simulation_id"] == "sim_test_save"
        assert result["current_step"] == 42

    async with session_factory() as session:
        snapshot_b = await export_save(session)
        counts_b = snapshot_b["counts"]

    # 关键表行数完全一致（容忍 simulation 这种单例字段）
    for key in ("scenes", "tiles", "locations", "portals", "world_objects",
                "agents", "relationships", "memories"):
        assert counts_a[key] == counts_b[key], (
            f"{key}: before={counts_a[key]} after={counts_b[key]}"
        )

    # mock_runtime.reload 必须被调用（否则引擎拿不到新世界）
    mock_runtime.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_imported_memories_have_null_embedding(
    session_factory, mock_runtime,
) -> None:
    """导入后 memory.embedding 必须是 NULL，由后台任务重建。"""
    async with session_factory() as session:
        await _seed_minimal_world(session)
        snapshot = await export_save(session)

    async with session_factory() as session:
        await import_save(session, snapshot)

    async with session_factory() as session:
        memories = (await session.execute(select(Memory))).scalars().all()
        assert memories, "记忆应该被恢复"
        for m in memories:
            assert m.embedding is None, f"memory.id={m.id} embedding 应为 NULL"
