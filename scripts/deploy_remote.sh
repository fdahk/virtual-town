#!/usr/bin/env bash
# ============================================================
# 在**目标服务器上**执行（由 GitHub Actions 经 SSH 调用）。
# 依赖：Docker / Compose V2；仓库根目录 .env 由 GitHub Actions（write_deploy_env.py）先行 SCP 到本机。
# 环境变量（可选）：
#   DEPLOY_PATH       — 仓库根目录绝对路径
#   COMPOSE_FILE      — 默认 docker-compose.demo.yml
#   POSTGRES_USER     — pg_isready 用，默认 virtual_town
#   DEPLOY_SKIP_GIT=1 — 不执行 git pull（代码已由 CI rsync 等方式同步到 DEPLOY_PATH）
#   DEPLOY_BUILD_NO_CACHE=1 — docker compose build 加 --no-cache（排除构建缓存未失效）
#   DEPLOY_RECREATE_DATA_CONTAINERS=1 — 重建 postgres/redis 容器外壳（数据卷保留）
#   DEPLOY_VERBOSE=1  — set -x 打印命令
#
# 注意：「run --rm migrate」与「depends_on: migrate: service_completed_successfully」
# 在 Compose v2 里**不共享同一条依赖状态**。若先 run migrate 再只 up backend，
# backend 会永远等不到 migrate「在工程图里」完成，只剩下先拉起的 postgres/redis。
# 正确做法：run migrate 后对 backend/worker/frontend 使用 --no-deps；或不用 run、
# 直接一次 up 全栈。本脚本采用：显式 run migrate + --no-deps 拉起应用层。
# ============================================================
set -euo pipefail

if [ "${DEPLOY_VERBOSE:-0}" = 1 ]; then
  set -x
fi

ROOT="${DEPLOY_PATH:?请在服务器上设置 DEPLOY_PATH 指向本仓库根目录}"
cd "$ROOT"

# 全量日志落盘，SSH 超时或 GHA 日志不全时可在服务器查看：tail -f /tmp/virtual-town-deploy.log
DEPLOY_LOG="${DEPLOY_LOG:-/tmp/virtual-town-deploy.log}"
exec > >(tee -a "$DEPLOY_LOG") 2>&1
echo "[deploy] 日志文件: $DEPLOY_LOG"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.demo.yml}"
POSTGRES_USER="${POSTGRES_USER:-virtual_town}"

DC=(docker compose -f "$COMPOSE_FILE")

echo "[deploy] repo: $ROOT | compose: $COMPOSE_FILE"
docker compose version || true
docker version --format '{{json .Server}}' 2>/dev/null | head -c 400 || true
echo

if [ ! -f backend/pyproject.toml ]; then
  echo "[deploy] 错误: 当前目录下没有 backend/pyproject.toml。" >&2
  echo "        请确认 GitHub Secret DEPLOY_PATH 与 rsync 目标一致，且 CI 已成功同步代码。" >&2
  exit 1
fi
echo "[deploy] 宿主机 backend/app/main.py: $(ls -la backend/app/main.py | awk '{print $5,$6,$7,$8}')"

if [ "${DEPLOY_SKIP_GIT:-0}" = 1 ]; then
  echo "[deploy] 跳过 git（DEPLOY_SKIP_GIT=1，假定目录内容已由 CI 同步）"
elif [ -d "$ROOT/.git" ]; then
  git fetch origin main
  git reset --hard origin/main
else
  echo "[deploy] 错误: $ROOT 不是 git 仓库，无法 git pull。" >&2
  echo "  解决方式任选其一：" >&2
  echo "  - 在服务器上对该路径执行 git clone，并保证能 fetch origin main；或" >&2
  echo "  - 使用 GitHub Actions 部署时开启仓库 rsync，并在 ssh 中 export DEPLOY_SKIP_GIT=1。" >&2
  exit 128
fi

echo "[deploy] 启动 postgres / redis…"
docker image prune -f >/dev/null 2>&1 || true
"${DC[@]}" up -d postgres redis

echo "[deploy] 等待 PostgreSQL 就绪…"
for i in $(seq 1 45); do
  if "${DC[@]}" exec -T postgres pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; then
    break
  fi
  sleep 2
  if [ "$i" -eq 45 ]; then
    echo "[deploy] postgres 健康检查超时" >&2
    exit 1
  fi
done

echo "[deploy] 等待 Redis 就绪…"
for i in $(seq 1 30); do
  if "${DC[@]}" exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
    break
  fi
  sleep 1
  if [ "$i" -eq 30 ]; then
    echo "[deploy] redis ping 超时" >&2
    exit 1
  fi
done

echo "[deploy] 构建 migrate / backend / worker / frontend…"
BUILD_OPTS=()
if [ X"${DEPLOY_BUILD_NO_CACHE:-}" = X1 ] || [ X"${DEPLOY_BUILD_NO_CACHE:-}" = Xtrue ]; then
  BUILD_OPTS=(--no-cache)
  echo "[deploy] DEPLOY_BUILD_NO_CACHE=1 → 不使用构建缓存"
fi
"${DC[@]}" build "${BUILD_OPTS[@]}" migrate backend worker frontend

echo "[deploy] 执行数据库迁移与种子（一次性容器，与 compose 工程内 migrate 状态无关）…"
"${DC[@]}" run --rm migrate

echo "[deploy] 拉起 backend / worker / frontend（--no-deps：勿再等待 service_completed_successfully 的 migrate 图节点）…"
"${DC[@]}" up -d --build --force-recreate --remove-orphans --no-deps backend worker
"${DC[@]}" up -d --build --force-recreate --remove-orphans --no-deps frontend

if [ X"${DEPLOY_RECREATE_DATA_CONTAINERS:-}" = X1 ] || [ X"${DEPLOY_RECREATE_DATA_CONTAINERS:-}" = Xtrue ]; then
  echo "[deploy] DEPLOY_RECREATE_DATA_CONTAINERS=1 → 重建 postgres/redis 容器（数据仍在卷内）…"
  "${DC[@]}" up -d --force-recreate postgres redis
fi

echo "[deploy] 当前服务状态:"
"${DC[@]}" ps -a || true

_fail_container() {
  local name="$1"
  local st
  st="$(docker inspect "$name" --format '{{.State.Status}}' 2>/dev/null || echo missing)"
  if [ "$st" != "running" ]; then
    echo "[deploy] 错误: 容器 $name 状态为 $st（期望 running）" >&2
    docker logs --tail 120 "$name" 2>/dev/null || true
    exit 1
  fi
}

echo "[deploy] 校验关键容器…"
_fail_container vt_backend
_fail_container vt_worker
_fail_container vt_frontend

for c in vt_postgres vt_redis vt_backend vt_worker vt_frontend; do
  if docker inspect "$c" >/dev/null 2>&1; then
    docker inspect "$c" --format "{{.Name}} container_created={{.Created}}" 2>/dev/null || true
  fi
done

echo "[deploy] 完成。"
echo "[deploy] 本次部署已通过 vt_backend / vt_worker / vt_frontend running 检查。"
echo "        若仍异常: docker compose -f \"$COMPOSE_FILE\" logs backend --tail 100"
