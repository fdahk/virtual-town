#!/usr/bin/env bash
# 初始化演示世界：室外+室内地图、6 NPC、2 狗、2 猫、默认玩家、初始关系
# 用法：
#   ./scripts/seed.sh             # 总是执行
#   ./scripts/seed.sh --if-empty  # 仅当数据库为空时执行
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/backend"

if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

ARGS=()
if [ "${1:-}" = "--if-empty" ]; then
  ARGS+=("--if-empty")
fi

DATABASE_URL_SYNC="${DATABASE_URL_LOCAL_SYNC:-${DATABASE_URL_SYNC}}" \
  python -m app.db.seed "${ARGS[@]}"
