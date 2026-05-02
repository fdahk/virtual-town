"""种子数据脚本。

阶段 21：从硬编码 22 NPC + 单地图迁移到 ``world_gen`` 参数化生成器。
- 默认走 ``GenerationConfig.default()``：22 NPC + 默认室外尺寸（``WORLD_GEN_OUTDOOR_*``）
- ``--if-empty``：DB 没 agent 时才执行，避免反复刷数据
- ``--seed N``：固定 RNG 种子，用于复现/测试

详细模板与生成器：
- ``app.db.templates``：22 默认 NPC + 4 动物 + LPC 美术目录
- ``app.domain.world_gen``：把模板 + 参数转成 WorldPlan 并写入 DB
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Agent, Simulation
from app.db.templates import DEFAULT_HUMAN_TEMPLATES
from app.domain.world_gen import GenerationConfig, apply_plan, generate_world


def seed(session: Session, *, force: bool = True, seed_value: int = 42) -> None:
    existing = session.execute(select(Agent).limit(1)).scalar_one_or_none()
    if existing is not None and not force:
        print("[seed] agents already exist, skip")
        return

    print("[seed] running world_gen with default templates...")
    config = GenerationConfig.default(seed=seed_value)
    plan = generate_world(config)
    apply_plan(session, plan)

    # 创建/更新 simulation 行（autostart 由配置决定）
    sim = session.execute(select(Simulation).limit(1)).scalar_one_or_none()
    if sim is None:
        from datetime import datetime, timezone

        sim = Simulation(
            status="running" if get_settings().simulation_autostart else "idle",
            world_time=datetime(2026, 4, 30, 7, 30, tzinfo=timezone.utc),
            world_tick_hz=get_settings().simulation_world_tick_hz,
            ai_tick_minutes=get_settings().simulation_ai_tick_minutes,
            speed_multiplier=get_settings().simulation_speed_default,
            current_step=0,
        )
        session.add(sim)
        session.commit()

    print(
        f"[seed] done. {len(plan.scenes)} scenes, {len(plan.agents)} agents "
        f"({len(DEFAULT_HUMAN_TEMPLATES)} 默认人类), "
        f"{sum(len(t) for t in plan.tiles_by_scene.values())} tiles, "
        f"{len(plan.world_objects)} objects, "
        f"{len(plan.relationships)} relationships."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed AI 小镇默认世界")
    parser.add_argument("--if-empty", action="store_true",
                        help="仅在数据库无 agent 时执行")
    parser.add_argument("--seed", type=int, default=42,
                        help="RNG 种子（影响装饰物随机摆放）")
    args = parser.parse_args()

    settings = get_settings()
    db_url = (
        os.environ.get("DATABASE_URL_LOCAL_SYNC")
        or os.environ.get("DATABASE_URL_SYNC")
        or settings.database_url_sync
    )
    if not db_url:
        print("DATABASE_URL_SYNC 未配置", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(db_url)
    with Session(engine) as session:
        seed(session, force=not args.if_empty, seed_value=args.seed)


if __name__ == "__main__":
    main()
