#!/usr/bin/env bash
# 下载并安装外部视觉资源。
# 所有 URL 固化在此脚本，若上游挪动需要更新并同步 CREDITS.md / manifest。
#
# 来源与授权：
#   - Kenney · Tiny Town (CC0, 公共领域)
#       https://opengameart.org/content/tiny-town
#   - [LPC] Cats and Dogs by bluecarrot16 (CC-BY 3.0 / GPL 3.0 / GPL 2.0 / OGA-BY 3.0)
#       https://opengameart.org/content/lpc-cats-and-dogs
#   - Universal LPC Spritesheet (CC-BY-SA 3.0 / GPL 3.0)
#       https://github.com/sanderfrenken/Universal-LPC-Spritesheet-Character-Generator
#
# 用法：./scripts/fetch_assets.sh [--force]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSETS_DIR="$ROOT_DIR/frontend/public/assets"
CACHE_DIR="$ROOT_DIR/.cache/assets"
FORCE=false
for arg in "$@"; do
  [ "$arg" = "--force" ] && FORCE=true
done

mkdir -p "$CACHE_DIR" \
         "$ASSETS_DIR/tilesets/kenney_tiny_town" \
         "$ASSETS_DIR/sprites/humans" \
         "$ASSETS_DIR/sprites/animals" \
         "$ASSETS_DIR/sprites/player" \
         "$ASSETS_DIR/portraits/humans" \
         "$ASSETS_DIR/portraits/animals" \
         "$ASSETS_DIR/maps" \
         "$ASSETS_DIR/manifest"

skip_if_present() {
  local target="$1"
  if [ -f "$target" ] && [ "$FORCE" != true ]; then
    return 0
  fi
  return 1
}

echo "[fetch] Kenney Tiny Town (CC0)"
ZIP="$CACHE_DIR/kenney_tiny-town.zip"
if ! skip_if_present "$ZIP"; then
  curl -sLfo "$ZIP" "https://opengameart.org/sites/default/files/kenney_tiny-town.zip"
fi
unzip -oq "$ZIP" -d "$CACHE_DIR/tiny_town"
cp "$CACHE_DIR/tiny_town/Tilemap/tilemap_packed.png" "$ASSETS_DIR/tilesets/kenney_tiny_town/tilemap_packed.png"
cp "$CACHE_DIR/tiny_town/License.txt" "$ASSETS_DIR/tilesets/kenney_tiny_town/LICENSE.txt" 2>/dev/null || true
cp "$CACHE_DIR/tiny_town/Tilesheet.txt" "$ASSETS_DIR/tilesets/kenney_tiny_town/README.txt" 2>/dev/null || true

echo "[fetch] Kenney Tiny Dungeon (CC0) – furniture & interior tiles"
mkdir -p "$ASSETS_DIR/tilesets/kenney_tiny_dungeon"
DZIP="$CACHE_DIR/kenney_tinydungeon.zip"
DDIR="$CACHE_DIR/tiny_dungeon"
if ! skip_if_present "$DZIP"; then
  curl -sLfo "$DZIP" "https://opengameart.org/sites/default/files/kenney_tinydungeon.zip"
fi
if ! skip_if_present "$ASSETS_DIR/tilesets/kenney_tiny_dungeon/tilemap_packed.png"; then
  unzip -oq "$DZIP" -d "$DDIR"
  # 将单个 tile_*.png 合成为 12×11 sprite sheet（与 Tiny Town 格式一致）
  TILES_DIR="$DDIR/Tiles"
  OUT_SHEET="$ASSETS_DIR/tilesets/kenney_tiny_dungeon/tilemap_packed.png"
  if [ -x "$ROOT_DIR/backend/.venv/bin/python" ]; then
    PY_BIN="$ROOT_DIR/backend/.venv/bin/python"
  else
    PY_BIN="python3"
  fi
  "$PY_BIN" - "$TILES_DIR" "$OUT_SHEET" << 'PYEOF'
import sys, glob
from PIL import Image
TILES_DIR, OUT = sys.argv[1], sys.argv[2]
tiles = sorted(glob.glob(f"{TILES_DIR}/tile_*.png"))
if not tiles:
    print("[warn] Tiny Dungeon tiles not found, skipping"); exit(0)
COLS, TW, TH = 12, 16, 16
ROWS = (len(tiles) + COLS - 1) // COLS
sheet = Image.new("RGBA", (TW * COLS, TH * ROWS), (0, 0, 0, 0))
for i, path in enumerate(tiles):
    t = Image.open(path).convert("RGBA")
    if t.size != (TW, TH): t = t.resize((TW, TH), Image.NEAREST)
    sheet.paste(t, ((i % COLS) * TW, (i // COLS) * TH))
sheet.save(OUT)
print(f"[fetch] Tiny Dungeon: composed {len(tiles)} tiles into sheet")
PYEOF
  cp "$DDIR/License.txt" "$ASSETS_DIR/tilesets/kenney_tiny_dungeon/LICENSE.txt" 2>/dev/null || true
fi

echo "[fetch] LPC Cats and Dogs (CC-BY 3.0)"
CAT="$CACHE_DIR/lpc_cats.png"
DOG="$CACHE_DIR/lpc_dogs.png"
if ! skip_if_present "$CAT"; then
  curl -sLfo "$CAT" "https://opengameart.org/sites/default/files/cat_0.png"
fi
if ! skip_if_present "$DOG"; then
  curl -sLfo "$DOG" "https://opengameart.org/sites/default/files/dog_2.png"
fi
cp "$CAT" "$ASSETS_DIR/sprites/animals/lpc_cats.png"
cp "$DOG" "$ASSETS_DIR/sprites/animals/lpc_dogs.png"

echo "[fetch] LPC Universal Spritesheet (CC-BY-SA 3.0) - selected layers"
LPC_BASE="https://raw.githubusercontent.com/sanderfrenken/Universal-LPC-Spritesheet-Character-Generator/master/spritesheets"
LPC_CACHE="$CACHE_DIR/lpc"
mkdir -p "$LPC_CACHE"

# 每一项格式： <local_name> <source_path>
LPC_LAYERS=(
  "body_male_light|body/bodies/male/light.png"
  "body_female_light|body/bodies/female/light.png"
  "body_male_taupe|body/bodies/male/taupe.png"
  "head_female_light|head/heads/human/female/light.png"
  "head_male_light|head/heads/human/male/light.png"
  "head_male_taupe|head/heads/human/male/taupe.png"
  "hair_long_female_black|hair/long/female/black.png"
  "hair_ponytail_female_raven|hair/ponytail/female/raven.png"
  "hair_long_female_lavender|hair/long/female/lavender.png"
  "hair_short_male_brown|hair/bedhead/male/dark_brown.png"
  "hair_short_male_black|hair/bedhead/male/black.png"
  "hair_short_male_gray|hair/bedhead/male/gray.png"
  "shirt_longsleeve_female_bluegray|torso/clothes/longsleeve/longsleeve/female/bluegray.png"
  "apron_female_white|torso/aprons/apron/female/white.png"
  "shirt_longsleeve_male_blue|torso/clothes/longsleeve/longsleeve/male/blue.png"
  "shirt_longsleeve_male_gray|torso/clothes/longsleeve/longsleeve/male/gray.png"
  "shirt_longsleeve_male_charcoal|torso/clothes/longsleeve/longsleeve/male/charcoal.png"
  "shirt_longsleeve_female_lavender|torso/clothes/longsleeve/longsleeve/female/lavender.png"
  "pants_male_black|legs/pants/male/black.png"
  "pants_male_brown|legs/pants/male/brown.png"
  "pants_male_charcoal|legs/pants/male/charcoal.png"
  "pants_female_black|legs/pants/female/black.png"
  "pants_female_brown|legs/pants/female/brown.png"
  "shoes_female_brown|feet/shoes/female/brown.png"
  "shoes_male_brown|feet/shoes/male/brown.png"
)

for entry in "${LPC_LAYERS[@]}"; do
  local_name="${entry%%|*}"
  src_path="${entry#*|}"
  target="$LPC_CACHE/${local_name}.png"
  if skip_if_present "$target"; then
    continue
  fi
  if ! curl -sLfo "$target" "$LPC_BASE/$src_path"; then
    echo "[warn] LPC layer missing at upstream: $src_path — using body base as placeholder"
    cp "$LPC_CACHE/body_female_light.png" "$target" 2>/dev/null || \
      cp "$LPC_CACHE/body_male_light.png" "$target" 2>/dev/null || true
  fi
done

echo "[fetch] compose LPC layered sprites for NPCs"
# 优先使用项目内的 backend venv，确保 PIL 可用
if [ -x "$ROOT_DIR/backend/.venv/bin/python" ]; then
  PY="$ROOT_DIR/backend/.venv/bin/python"
else
  PY="$(command -v python3 || command -v python)"
  if ! "$PY" -c "import PIL" >/dev/null 2>&1; then
    echo "[warn] Pillow not installed in $PY; install with: $PY -m pip install pillow"
    exit 2
  fi
fi
"$PY" "$ROOT_DIR/scripts/compose_lpc.py" \
  --in "$LPC_CACHE" \
  --out "$ASSETS_DIR/sprites/humans"

echo "[fetch] natural-event effect assets (procedural SVG fallbacks – no external dependency)"
EFFECTS_DIR="$ASSETS_DIR/effects"
mkdir -p "$EFFECTS_DIR"

# 生成程序化 SVG 粒子帧，无外部依赖，授权为 CC0（自制）
# 前端直接用 Phaser Graphics API 绘制动效；此处生成 1×1 透明 PNG 作为占位符，
# 真实美术资源可在此替换同名文件而无需改代码。
generate_placeholder_png() {
  local target="$1"
  if skip_if_present "$target"; then return 0; fi
  # 生成 1x1 透明 PNG（base64 解码）
  printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82' > "$target"
}

generate_placeholder_png "$EFFECTS_DIR/fire_placeholder.png"
generate_placeholder_png "$EFFECTS_DIR/raindrop_placeholder.png"
generate_placeholder_png "$EFFECTS_DIR/firefly_placeholder.png"
generate_placeholder_png "$EFFECTS_DIR/splash_placeholder.png"

echo "[fetch] done."
echo "Next step: generate portraits with GenerateImage, then write manifest."
