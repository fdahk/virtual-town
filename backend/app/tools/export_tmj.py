"""
把数据库中的 map_tiles / locations / portals / world_objects 导出为 Tiled `.tmj`。

用法：
    python -m app.tools.export_tmj --out frontend/public/assets/maps

每个 scene 生成一份 .tmj。瓦片索引由前端的 `tilesets.manifest.json` 里的 terrain_map 决定，
为保持后端独立，此处重复声明一次静态映射（两端同步更新即可）。

Tiled .tmj 格式最小字段：
{
  "type": "map",
  "orientation": "orthogonal",
  "renderorder": "right-down",
  "tilewidth": 32, "tileheight": 32,
  "width": W, "height": H,
  "tilesets": [
    {"firstgid": 1, "name": "kenney_tiny_town", "tilewidth": 16, "tileheight": 16,
     "tilecount": 132, "columns": 12, "image": "...", "imagewidth": 192, "imageheight": 176}
  ],
  "layers": [
    {"type": "tilelayer", "name": "ground", "width": W, "height": H,
     "data": [gid, gid, ...]}
  ],
  "nextlayerid": 2, "nextobjectid": 1, "version": "1.10", "tiledversion": "1.10"
}
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import MapScene, MapTile


TERRAIN_TO_TILE_INDEX: dict[str, int] = {
    "grass": 0,
    "grass_flower": 24,
    "road": 40,
    "bridge": 43,
    "dirt": 37,
    "river": 43,  # 桥索引用作水的占位（前端会在 hazard 上覆盖蓝色 overlay）
    "wall": 77,
    "floor": 37,
    "cafe_ext": 60,
    "school_ext": 52,
    "grocery_ext": 60,
    "flower_ext": 62,
    "cafe_door": 77,
    "school_door": 74,
    "grocery_door": 78,
    "flower_door": 74,
}

DEFAULT_TILE_INDEX = 0


def _gid(terrain: str) -> int:
    return TERRAIN_TO_TILE_INDEX.get(terrain, DEFAULT_TILE_INDEX) + 1  # Tiled GID 从 1 起


def export_scene(session: Session, scene: MapScene, out_dir: Path) -> Path:
    w, h = scene.width, scene.height
    # 初始化为全 0（Tiled 中 0 代表无瓦片）
    data = [0] * (w * h)
    rows = (
        session.execute(select(MapTile).where(MapTile.scene_id == scene.id))
    ).scalars().all()
    for t in rows:
        if 0 <= t.x < w and 0 <= t.y < h:
            data[t.y * w + t.x] = _gid(t.terrain)

    doc: dict[str, Any] = {
        "type": "map",
        "version": "1.10",
        "tiledversion": "1.10",
        "orientation": "orthogonal",
        "renderorder": "right-down",
        "tilewidth": 32,
        "tileheight": 32,
        "width": w,
        "height": h,
        "infinite": False,
        "nextlayerid": 2,
        "nextobjectid": 1,
        "tilesets": [
            {
                "firstgid": 1,
                "name": "kenney_tiny_town",
                "tilewidth": 16,
                "tileheight": 16,
                "tilecount": 132,
                "columns": 12,
                "image": "../tilesets/kenney_tiny_town/tilemap_packed.png",
                "imagewidth": 192,
                "imageheight": 176,
                "spacing": 0,
                "margin": 0,
            }
        ],
        "layers": [
            {
                "id": 1,
                "type": "tilelayer",
                "name": "ground",
                "visible": True,
                "opacity": 1,
                "width": w,
                "height": h,
                "x": 0,
                "y": 0,
                "data": data,
            }
        ],
    }
    target = out_dir / f"{scene.id}.tmj"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[tmj] {scene.id}: {w}×{h} → {target}")
    return target


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="frontend/public/assets/maps", type=Path)
    args = ap.parse_args()

    settings = get_settings()
    url = os.getenv("DATABASE_URL_SYNC", settings.database_url_sync)
    engine = create_engine(url, pool_pre_ping=True)
    with Session(engine) as session:
        scenes = session.execute(select(MapScene)).scalars().all()
        for scene in scenes:
            export_scene(session, scene, args.out)


if __name__ == "__main__":
    main()
