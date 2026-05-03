#!/usr/bin/env bash
# ============================================================
# 在**目标服务器上**执行（由 GitHub Actions 经 SSH 调用）。
# 依赖：Docker / Compose V2；仓库根目录 .env 由 GitHub Actions（write_deploy_env.py）先行 SCP 到本机。
# 环境变量（可选）：
#   DEPLOY_PATH       — 仓库根目录绝对路径
#   COMPOSE_FILE      — 默认 docker-compose.demo.yml
#   POSTGRES_USER     — pg_isready 用，默认 virtual_town
#   DEPLOY_SKIP_GIT=1 — 不执行 git pull（代码已由 CI rsync 等方式同步到 DEPLOY_PATH）
# ============================================================
set -euo pipefail

ROOT="${DEPLOY_PATH:?请在服务器上设置 DEPLOY_PATH 指向本仓库根目录}"
cd "$ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.demo.yml}"
POSTGRES_USER="${POSTGRES_USER:-virtual_town}"

echo "[deploy] repo: $ROOT | compose: $COMPOSE_FILE"

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
docker compose -f "$COMPOSE_FILE" up -d postgres redis

echo "[deploy] 等待 PostgreSQL 就绪…"
for i in $(seq 1 45); do
  if docker compose -f "$COMPOSE_FILE" exec -T postgres \
      pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; then
    break
  fi
  sleep 2
  if [ "$i" -eq 45 ]; then
    echo "[deploy] postgres 健康检查超时" >&2
    exit 1
  fi
done

echo "[deploy] 构建 migrate / backend / worker / frontend…"
docker compose -f "$COMPOSE_FILE" build migrate backend worker frontend

echo "[deploy] 执行数据库迁移与种子（--if-empty）…"
docker compose -f "$COMPOSE_FILE" run --rm migrate

echo "[deploy] 滚动更新全部服务…"
docker compose -f "$COMPOSE_FILE" up -d --build

echo "[deploy] 完成。"
