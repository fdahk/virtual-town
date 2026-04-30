#!/usr/bin/env bash
# 销毁并重建数据库，重新执行迁移与种子数据
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[reset-db] 销毁 postgres 数据卷..."
docker compose down -v postgres || true
docker volume rm virtual-town_postgres_data 2>/dev/null || true

echo "[reset-db] 重新启动 postgres..."
docker compose up -d postgres
sleep 5

echo "[reset-db] 执行迁移与种子..."
(
  cd backend
  if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
  DATABASE_URL_SYNC="${DATABASE_URL_LOCAL_SYNC:-${DATABASE_URL_SYNC}}" alembic upgrade head
)
"$ROOT_DIR/scripts/seed.sh"
echo "[reset-db] 完成。"
