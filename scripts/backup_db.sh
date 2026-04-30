#!/usr/bin/env bash
# scripts/backup_db.sh
# 基础版数据库备份（阶段 13 §5；部署阶段 20 会补全 restore / 定时策略）。
#
# 输出：./backups/virtual_town_<timestamp>.sql.gz + asset manifest 快照
#
# 用法：
#   ./scripts/backup_db.sh                 # 使用 .env 中的本地 postgres
#   BACKUP_DIR=/mnt/backups ./scripts/backup_db.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
mkdir -p "$BACKUP_DIR"

TS="$(date -u +"%Y%m%dT%H%M%SZ")"
DUMP_FILE="$BACKUP_DIR/virtual_town_${TS}.sql.gz"
MANIFEST_FILE="$BACKUP_DIR/assets_manifest_${TS}.tar.gz"

DB_USER="${POSTGRES_USER:-virtual_town}"
DB_NAME="${POSTGRES_DB:-virtual_town}"
DB_HOST="${POSTGRES_HOST_LOCAL:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"

echo "[backup] 数据库 -> $DUMP_FILE"

# 优先直连本地 postgres；若不可达则回退 docker compose exec
if PGPASSWORD="${POSTGRES_PASSWORD:-virtual_town_dev}" pg_dump \
      -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" \
      --no-owner --no-privileges --clean --if-exists \
      "$DB_NAME" 2>/dev/null | gzip -9 > "$DUMP_FILE"; then
  echo "[backup] direct pg_dump 成功"
else
  echo "[backup] direct pg_dump 失败，改用 docker compose exec"
  docker compose exec -T postgres \
    pg_dump -U "$DB_USER" --no-owner --no-privileges --clean --if-exists "$DB_NAME" \
    | gzip -9 > "$DUMP_FILE"
fi

echo "[backup] asset manifest -> $MANIFEST_FILE"
if [ -d frontend/public/assets/manifest ]; then
  tar -czf "$MANIFEST_FILE" -C frontend/public/assets manifest 2>/dev/null || true
fi

ls -lh "$BACKUP_DIR" | tail -10
echo "[backup] done."
