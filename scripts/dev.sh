#!/usr/bin/env bash
# 一键启动本地完整开发环境
# 行为：
#   1. 确保 .env 存在
#   2. 启动 Docker Compose 依赖（postgres、redis）
#   3. 等待依赖就绪
#   4. 执行数据库迁移
#   5. 若数据库为空则执行种子数据脚本
#   6. 分别启动后端和前端开发服务器
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -f .env ]; then
  echo "[dev] .env 不存在，复制 .env.example -> .env"
  cp .env.example .env
fi

# 导出 .env 中所有变量到当前 shell
set -a
# shellcheck disable=SC1091
source .env
set +a

echo "[dev] 启动依赖服务 (postgres + redis)..."
docker compose up -d postgres redis

echo "[dev] 等待 postgres 健康..."
for i in {1..30}; do
  if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-virtual_town}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "[dev] 执行数据库迁移..."
if [ -d backend/.venv ]; then
  # shellcheck disable=SC1091
  source backend/.venv/bin/activate
fi
(
  cd backend
  if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
  # 通过本地 Python 直接访问已暴露的 postgres 端口
  DATABASE_URL_SYNC="${DATABASE_URL_LOCAL_SYNC:-${DATABASE_URL_SYNC}}" \
    alembic upgrade head
)

echo "[dev] 如无数据则执行种子数据..."
"$ROOT_DIR/scripts/seed.sh" --if-empty || true

echo "[dev] 启动后端开发服务器 -> http://localhost:8000"
(
  cd backend
  if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
  DATABASE_URL="${DATABASE_URL_LOCAL:-${DATABASE_URL}}" \
  DATABASE_URL_SYNC="${DATABASE_URL_LOCAL_SYNC:-${DATABASE_URL_SYNC}}" \
  REDIS_URL="${REDIS_URL_LOCAL:-${REDIS_URL}}" \
    uvicorn app.main:app --reload --host 0.0.0.0 --port "${BACKEND_PORT:-8000}"
) &
BACKEND_PID=$!

echo "[dev] 启动前端开发服务器 -> http://localhost:5173"
(
  cd frontend
  npm run dev -- --host 0.0.0.0
) &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true" EXIT

cat <<EOF

============================================================
AI 小镇本地开发环境启动完成

  前端:    http://localhost:5173
  后端:    http://localhost:8000
  OpenAPI: http://localhost:8000/docs

按 Ctrl+C 停止。
============================================================

EOF

wait
