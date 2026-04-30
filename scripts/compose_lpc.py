"""
把下载好的 LPC 分层 PNG 合成为每个 NPC 最终 sprite sheet。

LPC 标准 sheet 尺寸：832 × 1344 像素，64×64 每帧，21 行 × 13 列；
扩展 sheet：832 × 2944（46 行），walk 动画仍位于第 8-11 行。
本项目只使用 walk 四向（8-11 行，每行 9 帧），最终产出 576 × 256（9 列 × 4 行）。

层 z-order（下层先画）：
    body → head → legs → feet → torso → hair

重要：body/bodies/*/light.png 只含躯干（无头部），
      head/heads/human/*/light.png 才是面部皮肤层，必须单独加载。
层不存在时跳过；失败不会阻塞主流程。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


# NPC → 各层文件名（和 fetch_assets.sh 中一一对应）
# head 层是面部皮肤，必须紧跟 body 层合成，否则角色只有头发没有脸。
NPC_LAYERS: dict[str, dict[str, str]] = {
    "xiaofang": {  # 咖啡店店员，女，长黑发，围裙
        "body": "body_female_light.png",
        "head": "head_female_light.png",
        "hair": "hair_ponytail_female_raven.png",
        "torso": "apron_female_white.png",
        "legs": "pants_female_black.png",
        "feet": "shoes_female_brown.png",
    },
    "xiaoming": {  # 高中生，男，黑短发
        "body": "body_male_light.png",
        "head": "head_male_light.png",
        "hair": "hair_short_male_black.png",
        "torso": "shirt_longsleeve_male_blue.png",
        "legs": "pants_male_black.png",
        "feet": "shoes_male_brown.png",
    },
    "xiaowang": {  # 程序员，男，棕短发
        "body": "body_male_light.png",
        "head": "head_male_light.png",
        "hair": "hair_short_male_brown.png",
        "torso": "shirt_longsleeve_male_charcoal.png",
        "legs": "pants_male_charcoal.png",
        "feet": "shoes_male_brown.png",
    },
    "linna": {  # 医生，女，长黑发
        "body": "body_female_light.png",
        "head": "head_female_light.png",
        "hair": "hair_long_female_black.png",
        "torso": "shirt_longsleeve_female_bluegray.png",
        "legs": "pants_female_black.png",
        "feet": "shoes_female_brown.png",
    },
    "chenbo": {  # 老板，男，灰发
        "body": "body_male_taupe.png",
        "head": "head_male_taupe.png",
        "hair": "hair_short_male_gray.png",
        "torso": "shirt_longsleeve_male_gray.png",
        "legs": "pants_male_brown.png",
        "feet": "shoes_male_brown.png",
    },
    "ayan": {  # 花店女主，长薰衣草发
        "body": "body_female_light.png",
        "head": "head_female_light.png",
        "hair": "hair_long_female_lavender.png",
        "torso": "shirt_longsleeve_female_lavender.png",
        "legs": "pants_female_brown.png",
        "feet": "shoes_female_brown.png",
    },
    "player_default": {  # 玩家默认
        "body": "body_male_light.png",
        "head": "head_male_light.png",
        "hair": "hair_short_male_brown.png",
        "torso": "shirt_longsleeve_male_blue.png",
        "legs": "pants_male_brown.png",
        "feet": "shoes_male_brown.png",
    },
}

# 最终输出的 walk sheet
WALK_ROW_START = 8  # 0-index：LPC 的 walk 从第 8 行开始（up/left/down/right）
WALK_ROW_COUNT = 4
FRAME = 64
OUT_COLS = 9  # walk 每向 9 帧（idle + 8 步态）
OUT_W = OUT_COLS * FRAME  # 576
OUT_H = WALK_ROW_COUNT * FRAME  # 256


def _extract_walk(layer_path: Path) -> Image.Image | None:
    """
    从单层 PNG 中仅取出 walk 四向的 576×256 区域。

    LPC 既有 832×1344（经典 21 行）也有 832×2944（扩展 46 行）格式。
    walk 四向始终位于第 8-11 行（像素 y=512-768），横向 0-9 列（像素 x=0-576）。
    """
    if not layer_path.exists():
        return None
    img = Image.open(layer_path).convert("RGBA")
    w, h = img.size
    if w < OUT_W or h < (WALK_ROW_START + WALK_ROW_COUNT) * FRAME:
        # 某些 layer（hair 样式、某些物件）可能仅覆盖 head 区域或帧数较少
        # 我们宽容处理：用 walk 区域内存在的部分，其余用透明填充
        box = (
            0,
            WALK_ROW_START * FRAME,
            min(OUT_W, w),
            min((WALK_ROW_START + WALK_ROW_COUNT) * FRAME, h),
        )
        partial = img.crop(box)
        canvas = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
        canvas.paste(partial, (0, 0))
        return canvas
    return img.crop((0, WALK_ROW_START * FRAME, OUT_W, (WALK_ROW_START + WALK_ROW_COUNT) * FRAME))


def _compose_one(layers: list[Path]) -> Image.Image | None:
    """按顺序将 layers 叠加为 walk 四向 576×256。"""
    base = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    any_layer = False
    for layer_path in layers:
        crop = _extract_walk(layer_path)
        if crop is None:
            continue
        base = Image.alpha_composite(base, crop)
        any_layer = True
    return base if any_layer else None


def compose_npc(name: str, layer_map: dict[str, str], in_dir: Path, out_dir: Path) -> Path | None:
    # head 必须紧跟 body 合成，使面部皮肤出现在服装和头发之下
    order = ("body", "head", "legs", "feet", "torso", "hair")
    layer_paths = [in_dir / layer_map[k] for k in order if k in layer_map]
    sheet = _compose_one(layer_paths)
    if sheet is None:
        print(f"[compose] {name}: no usable layers")
        return None
    target = out_dir / f"{name}.png"
    sheet.save(target)
    print(f"[compose] {name} -> {target.name} ({sheet.size[0]}×{sheet.size[1]})")
    return target


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_dir", required=True, type=Path)
    ap.add_argument("--out", dest="out_dir", required=True, type=Path)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, layers in NPC_LAYERS.items():
        try:
            compose_npc(name, layers, args.in_dir, args.out_dir)
        except Exception as exc:
            print(f"[compose] FAILED {name}: {exc}")


if __name__ == "__main__":
    main()
