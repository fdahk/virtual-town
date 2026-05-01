#!/usr/bin/env bash
# 一键启动本地完整开发环境
# 行为：
#   1. 确保 .env 存在
#   2. 启动 Docker Compose 依赖（postgres、redis）
#   3. 等待依赖就绪
#   4. 执行数据库迁移
#   5. 若数据库为空则执行种子数据脚本
#   6. 分别启动后端 / RQ worker / 前端，并在退出时一并清理
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ---------------------------------------------------------------------------
# 0. 进程清理工具：在脚本启动前 + 任何信号退出时使用
#    - 显式用 ${VAR:-} 避免 set -u 触发 unbound 错误
#    - 用 PID 数组 + 进程组 kill，避免遗漏 uvicorn 重载子进程
# ---------------------------------------------------------------------------

BACKEND_PID=""
WORKER_PID=""
FRONTEND_PID=""

cleanup() {
  set +e
  trap - EXIT INT TERM
  echo
  echo "[dev] 清理子进程..."
  for pid in "${BACKEND_PID:-}" "${WORKER_PID:-}" "${FRONTEND_PID:-}"; do
    [ -n "$pid" ] || continue
    # 终止整个进程组（uvicorn --reload 会派生子进程；npm run dev 同理）
    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
    if [ -n "$pgid" ]; then
      kill -TERM -- "-$pgid" 2>/dev/null || true
    fi
    kill -TERM "$pid" 2>/dev/null || true
  done
  # 兜底：等 0.5s 仍存活则强杀
  sleep 0.5
  for pid in "${BACKEND_PID:-}" "${WORKER_PID:-}" "${FRONTEND_PID:-}"; do
    [ -n "$pid" ] || continue
    kill -KILL "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# 1. 端口占用检测：上次未干净退出会留下监听 8000 / 5173 的旧进程，
#    这里直接告诉用户哪个 PID 在占着，避免 uvicorn 启动后才 errno 48。
# ---------------------------------------------------------------------------

check_port_free() {
  local port="$1" label="$2"
  # 优先用 lsof（macOS 默认有），fallback 到 ss / netstat
  local owners=""
  if command -v lsof >/dev/null 2>&1; then
    owners="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' || true)"
  elif command -v ss >/dev/null 2>&1; then
    owners="$(ss -ltnp "sport = :$port" 2>/dev/null | awk 'NR>1 {print $NF}' | grep -oE 'pid=[0-9]+' | cut -d= -f2 | tr '\n' ' ' || true)"
  fi
  if [ -n "$owners" ]; then
    echo "[dev] 端口 $port ($label) 被占用，PID: $owners"
    return 1
  fi
  return 0
}

# 结束指定端口上的 LISTEN 进程（开发脚本场景下多为上次未退干净的 uvicorn/vite）
kill_listeners_on_port() {
  local port="$1"
  [ -n "$port" ] || return 0
  local pid
  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi
  while read -r pid; do
    [ -n "$pid" ] || continue
    # ${port}：避免 $port 紧邻全角「：」时被 bash 并成变量名（set -u 报 unbound）
    echo "[dev] 释放端口 ${port}：结束 PID ${pid}"
    kill -TERM "$pid" 2>/dev/null || true
  done <<EOF
$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
EOF
  sleep 0.6
  while read -r pid; do
    [ -n "$pid" ] || continue
    kill -KILL "$pid" 2>/dev/null || true
  done <<EOF
$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
EOF
}

if [ ! -f .env ]; then
  echo "[dev] .env 不存在，复制 .env.example -> .env"
  cp .env.example .env
fi

# 导出 .env 中所有变量到当前 shell
set -a
# shellcheck disable=SC1091
source .env
set +a

BACKEND_PORT_VAL="${BACKEND_PORT:-8000}"
FRONTEND_PORT_VAL="${VITE_DEV_PORT:-5173}"

PORTS_OK=1
for attempt in 1 2; do
  PORTS_OK=1
  check_port_free "$BACKEND_PORT_VAL" "backend"  || PORTS_OK=0
  check_port_free "$FRONTEND_PORT_VAL" "frontend" || PORTS_OK=0
  if [ "$PORTS_OK" -eq 1 ]; then
    break
  fi
  if [ "$attempt" -eq 1 ]; then
    echo "[dev] 尝试自动释放占用端口（多为上次 dev 未停干净）..."
    kill_listeners_on_port "$BACKEND_PORT_VAL"
    kill_listeners_on_port "$FRONTEND_PORT_VAL"
    sleep 0.2
  else
    echo "[dev] 端口仍被占用，请手动：lsof -tiTCP:${BACKEND_PORT_VAL} -sTCP:LISTEN | xargs kill"
    echo "[dev] 启动中止。"
    exit 1
  fi
done

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

echo "[dev] 启动后端 API 服务器 -> http://localhost:${BACKEND_PORT_VAL}"
(
  cd backend
  if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
  DATABASE_URL="${DATABASE_URL_LOCAL:-${DATABASE_URL}}" \
  DATABASE_URL_SYNC="${DATABASE_URL_LOCAL_SYNC:-${DATABASE_URL_SYNC}}" \
  REDIS_URL="${REDIS_URL_LOCAL:-${REDIS_URL}}" \
    exec uvicorn app.main:app --reload --host 0.0.0.0 --port "${BACKEND_PORT_VAL}"
) &
BACKEND_PID=$!

echo "[dev] 启动 RQ worker（消费 agent_decision / embedding / reflection 等异步任务）"
(
  cd backend
  if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
  DATABASE_URL="${DATABASE_URL_LOCAL:-${DATABASE_URL}}" \
  DATABASE_URL_SYNC="${DATABASE_URL_LOCAL_SYNC:-${DATABASE_URL_SYNC}}" \
  REDIS_URL="${REDIS_URL_LOCAL:-${REDIS_URL}}" \
    exec python -m app.domain.tasks.worker
) &
WORKER_PID=$!

echo "[dev] 启动前端开发服务器 -> http://localhost:${FRONTEND_PORT_VAL}"
(
  cd frontend
  exec npm run dev -- --host 0.0.0.0
) &
FRONTEND_PID=$!

# 注意：${WORKER_PID} 必须用花括号定界，
# 否则后面的全角标点会被 bash 当成变量名的一部分（"WORKER_PID�: unbound variable"）。
cat <<EOF

============================================================
AI 小镇本地开发环境启动完成

  前端:    http://localhost:${FRONTEND_PORT_VAL}      (PID ${FRONTEND_PID})
  后端:    http://localhost:${BACKEND_PORT_VAL}       (PID ${BACKEND_PID})
  OpenAPI: http://localhost:${BACKEND_PORT_VAL}/docs
  研发观测: http://localhost:${FRONTEND_PORT_VAL}/observability

  RQ worker PID: ${WORKER_PID}  (消费 Redis 队列 vt:high / vt:default / vt:low)

按 Ctrl+C 停止全部子进程。
============================================================

EOF

# macOS 自带 bash 3.2 不支持 `wait -n`（需 bash≥4.3）。
# 用 kill -0 轮询：任一子进程先退出即跳出，由 EXIT trap 统一 cleanup。
while kill -0 "${BACKEND_PID}" 2>/dev/null &&
      kill -0 "${WORKER_PID}" 2>/dev/null &&
      kill -0 "${FRONTEND_PID}" 2>/dev/null; do
  sleep 0.5
done
echo "[dev] 一个子进程已退出，开始清理..."
wait "${BACKEND_PID}" 2>/dev/null || true
wait "${WORKER_PID}" 2>/dev/null || true
wait "${FRONTEND_PID}" 2>/dev/null || true
