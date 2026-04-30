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

echo "[fetch] done."
echo "Next step: generate portraits with GenerateImage, then write manifest."
