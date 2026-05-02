"""
从已合成的 LPC sprite sheet 裁剪每个 NPC 的对话立绘（fallback portrait）。

业界常见做法是为对话 UI 准备高质量插画立绘（项目里旧 NPC 用 GenerateImage 产
出 1536×1024 油画风），但当资源缺失或要为新增 NPC 快速兜底时，从 sprite 头部
裁一张放大版同样可用——既保留角色辨识度，也避免对话面板出现空占位。

输入：``frontend/public/assets/sprites/humans/<id>.png`` （compose_lpc.py 的产物）
输出：``frontend/public/assets/portraits/humans/<id>.png``

LPC sprite sheet 排布（compose_lpc.py 已约定）：
    9 列 × 4 行，每帧 64×64，行序为 up / left / down / right。
    我们取「down 行」（row 2）「frame 0」作为面向玩家的 idle 姿态，
    再在 64×64 帧内向头部聚焦：上沿留 0px、向下截 40px，宽度居中 56px。
    然后用最近邻把这块 56×40 放大到 312×376（≈ 5.5x），保证像素艺术风格。

只生成「目标目录里还不存在」的肖像；旧 NPC 的高质量立绘原样保留。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def crop_portrait(sheet_path: Path) -> Image.Image:
    sheet = Image.open(sheet_path).convert("RGBA")
    if sheet.size != (576, 256):
        raise ValueError(
            f"unexpected sprite sheet size for {sheet_path.name}: {sheet.size}, "
            "expected 576x256 (9x4 frames of 64x64)"
        )
    frame_w, frame_h = 64, 64
    down_row, idle_col = 2, 0
    fx = idle_col * frame_w
    fy = down_row * frame_h
    head_w = 48
    head_h = 44
    cx = fx + (frame_w - head_w) // 2
    cy = fy + 8
    head = sheet.crop((cx, cy, cx + head_w, cy + head_h))
    portrait = head.resize((head_w * 6, head_h * 6), Image.NEAREST)
    return portrait


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sprites",
        type=Path,
        required=True,
        help="目录：compose_lpc.py 输出的 humans sprite sheets",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="目录：肖像输出位置（frontend/public/assets/portraits/humans）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="即使目标已存在也重新生成（默认仅补全缺失项）",
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    sheets = sorted(args.sprites.glob("*.png"))
    if not sheets:
        print(f"[portraits] no sheets found under {args.sprites}")
        return 1

    skipped, generated = 0, 0
    for sheet_path in sheets:
        target = args.out / sheet_path.name
        if target.exists() and not args.overwrite:
            skipped += 1
            continue
        try:
            portrait = crop_portrait(sheet_path)
        except Exception as exc:
            print(f"[portraits] skip {sheet_path.name}: {exc}")
            continue
        portrait.save(target, optimize=True)
        print(f"[portraits] {sheet_path.name} -> {target.name} ({portrait.size[0]}x{portrait.size[1]})")
        generated += 1

    print(f"[portraits] done. generated={generated}, skipped_existing={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
