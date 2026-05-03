#!/usr/bin/env bash
# ============================================================
# AI 小镇 - 用户体验一键启动脚本
# 前提：已安装 Docker Desktop（含 Docker Compose V2）
# 用法：bash scripts/start.sh [--no-open] [--rebuild]
# ============================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="docker-compose.demo.yml"

# 解析参数
NO_OPEN=false
FORCE_REBUILD=false
for arg in "$@"; do
  case "$arg" in
    --no-open)    NO_OPEN=true ;;
    --rebuild)    FORCE_REBUILD=true ;;
  esac
done

# ------------------------------------------------------------
# 颜色
# ------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[start]${NC} $*"; }
success() { echo -e "${GREEN}[start]${NC} $*"; }
warn()    { echo -e "${YELLOW}[start]${NC} $*"; }
error()   { echo -e "${RED}[start] 错误：${NC}$*" >&2; }
step()    { echo -e "\n${BOLD}── $* ${NC}"; }

# ------------------------------------------------------------
# Banner
# ------------------------------------------------------------
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║         🏘  AI 小镇 · 一键启动           ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""

# ------------------------------------------------------------
# 辅助：检查端口是否被占用，返回占用 PID（逗号分隔）或空
# ------------------------------------------------------------
port_pids() {
  local port="$1"
  if command -v lsof &>/dev/null; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | tr '\n' ',' | sed 's/,$//' || true
  fi
}

# 尝试释放端口（只杀非 Docker 进程，Docker 容器的端口由 compose 管理）
release_port() {
  local port="$1"
  local pids
  pids="$(port_pids "$port")"
  [ -z "$pids" ] && return 0
  warn "端口 $port 被占用（PID: $pids），尝试释放..."
  echo "$pids" | tr ',' '\n' | while read -r pid; do
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
  done
  sleep 1
  pids="$(port_pids "$port")"
  if [ -n "$pids" ]; then
    echo "$pids" | tr ',' '\n' | while read -r pid; do
      [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
    done
    sleep 0.5
  fi
}

# ------------------------------------------------------------
# 1. 检查 Docker
# ------------------------------------------------------------
step "检查环境"

if ! command -v docker &>/dev/null; then
  error "未找到 docker 命令。请先安装 Docker Desktop："
  error "  https://www.docker.com/products/docker-desktop/"
  exit 1
fi

if ! docker info &>/dev/null; then
  error "Docker 守护进程未运行，请先启动 Docker Desktop 再重试。"
  exit 1
fi

if ! docker compose version &>/dev/null; then
  error "未找到 'docker compose'（V2）插件。请升级 Docker Desktop 至最新版。"
  exit 1
fi

success "Docker 已就绪（$(docker --version | head -1)）"

# ------------------------------------------------------------
# 2. 准备 .env
# ------------------------------------------------------------
step "配置文件"

if [ ! -f .env ]; then
  info ".env 不存在，从模板创建..."
  cp .env.example .env
fi

# 导入现有 .env 到当前 shell（用于读取变量）
set -a
# shellcheck disable=SC1091
source .env 2>/dev/null || true
set +a

FRONTEND_PORT="${FRONTEND_DEMO_PORT:-8080}"
BACKEND_PORT_VAL="${BACKEND_PORT:-8000}"

# ------------------------------------------------------------
# 3. 配置 LLM API Key
# ------------------------------------------------------------
step "LLM 配置"

CURRENT_KEY="${LLM_API_KEY:-}"

if [ -n "$CURRENT_KEY" ]; then
  info "检测到已配置 LLM_API_KEY（末尾：…${CURRENT_KEY: -6}）"
  echo -e "  按 ${BOLD}Enter${NC} 保留，或输入新 Key 覆盖："
  read -r -p "  > " NEW_KEY
  [ -n "$NEW_KEY" ] && CURRENT_KEY="$NEW_KEY"
else
  echo ""
  echo -e "  ${BOLD}NPC 智能决策需要 LLM API Key（通义千问 / 兼容 OpenAI 协议均可）。${NC}"
  echo -e "  申请地址：https://dashscope.console.aliyun.com/"
  echo -e "  ${YELLOW}留空则 NPC 使用规则行为（无需 Key，但对话不智能）。${NC}"
  echo ""
  read -r -p "  请输入 LLM_API_KEY（可留空）> " NEW_KEY
  CURRENT_KEY="${NEW_KEY:-}"
fi

# 写回 .env（sed -i 跨平台）
update_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env 2>/dev/null; then
    sed -i.bak "s|^${key}=.*|${key}=${val}|" .env && rm -f .env.bak
  else
    echo "${key}=${val}" >> .env
  fi
}

update_env "LLM_API_KEY"      "$CURRENT_KEY"
update_env "FRONTEND_DEMO_PORT" "$FRONTEND_PORT"

if [ -z "$CURRENT_KEY" ]; then
  update_env "LLM_ENABLED" "false"
  warn "LLM 已禁用，NPC 将使用规则行为。"
else
  update_env "LLM_ENABLED" "true"
fi

# ------------------------------------------------------------
# 4. 检测已运行状态
# ------------------------------------------------------------
step "检查容器状态"

# 判断是否存在上次失败的 migrate 容器（exit code != 0）
MIGRATE_FAILED=false
if docker inspect vt_migrate &>/dev/null; then
  MIGRATE_EXIT=$(docker inspect vt_migrate --format='{{.State.ExitCode}}' 2>/dev/null || echo "")
  MIGRATE_RUNNING=$(docker inspect vt_migrate --format='{{.State.Running}}' 2>/dev/null || echo "false")
  if [ "$MIGRATE_RUNNING" = "false" ] && [ "$MIGRATE_EXIT" != "" ] && [ "$MIGRATE_EXIT" != "0" ]; then
    warn "检测到上次 migrate 失败（exit $MIGRATE_EXIT），将重新执行..."
    docker rm -f vt_migrate 2>/dev/null || true
    MIGRATE_FAILED=true
  fi
fi

# 判断是否全部正常运行（仅检查核心服务）
ALREADY_HEALTHY=false
if [ "$MIGRATE_FAILED" = "false" ] && [ "$FORCE_REBUILD" = "false" ]; then
  if curl -sf "http://localhost:${BACKEND_PORT_VAL}/healthz" -o /dev/null 2>/dev/null && \
     curl -sf "http://localhost:${FRONTEND_PORT}"            -o /dev/null 2>/dev/null; then
    ALREADY_HEALTHY=true
  fi
fi

if [ "$ALREADY_HEALTHY" = "true" ]; then
  success "服务已在运行中，直接打开浏览器..."
  echo ""
  echo -e "  ${BOLD}前端：${NC}     http://localhost:${FRONTEND_PORT}"
  echo -e "  ${BOLD}观测台：${NC}   http://localhost:${FRONTEND_PORT}/observability"
  echo -e "  ${BOLD}API：${NC}      http://localhost:${BACKEND_PORT_VAL}/docs"
  echo ""
  if [ "$NO_OPEN" = "false" ]; then
    URL="http://localhost:${FRONTEND_PORT}"
    command -v open    &>/dev/null && open "$URL"
    command -v xdg-open &>/dev/null && xdg-open "$URL" &
    command -v start   &>/dev/null && start "$URL"
  fi
  exit 0
fi

# ------------------------------------------------------------
# 5. 检测端口占用（非 Docker 进程）
# ------------------------------------------------------------
step "端口检查（前端 :${FRONTEND_PORT} / 后端 :${BACKEND_PORT_VAL}）"

PORTS_OK=true
for port in "$FRONTEND_PORT" "$BACKEND_PORT_VAL"; do
  pids="$(port_pids "$port")"
  if [ -n "$pids" ]; then
    # 检查是否是 Docker 的端口（进程名含 docker/com.docker）
    non_docker_pids=""
    for pid in $(echo "$pids" | tr ',' ' '); do
      proc_name="$(ps -p "$pid" -o comm= 2>/dev/null || true)"
      if [[ "$proc_name" != *docker* ]] && [[ "$proc_name" != *com.docker* ]]; then
        non_docker_pids="$non_docker_pids $pid"
      fi
    done
    non_docker_pids="${non_docker_pids## }"
    if [ -n "$non_docker_pids" ]; then
      warn "端口 $port 被非 Docker 进程占用（PID: $non_docker_pids）"
      PORTS_OK=false
    fi
  fi
done

if [ "$PORTS_OK" = "false" ]; then
  echo ""
  echo -e "  自动尝试释放..."
  release_port "$FRONTEND_PORT"
  release_port "$BACKEND_PORT_VAL"
  sleep 0.5
  # 二次检查
  for port in "$FRONTEND_PORT" "$BACKEND_PORT_VAL"; do
    if [ -n "$(port_pids "$port")" ]; then
      error "端口 $port 仍被占用，请手动处理后重试："
      error "  lsof -tiTCP:$port | xargs kill"
      exit 1
    fi
  done
  success "端口已释放"
fi

# ------------------------------------------------------------
# 6. 启动容器
# ------------------------------------------------------------
step "构建并启动容器（首次约 3–5 分钟，后续秒级）"

BUILD_FLAG="--build"
[ "$FORCE_REBUILD" = "false" ] && {
  # 若镜像已存在且代码未改变则跳过 rebuild（用户第二次启动加速）
  if docker image inspect virtual-town-backend &>/dev/null && \
     docker image inspect virtual-town-frontend &>/dev/null; then
    BUILD_FLAG=""
    info "检测到已有镜像，跳过重新构建（使用 --rebuild 强制重建）"
  fi
}

if ! docker compose -f "$COMPOSE_FILE" up $BUILD_FLAG -d 2>&1; then
  error "容器启动命令失败，查看日志："
  docker compose -f "$COMPOSE_FILE" logs --tail=30 2>/dev/null || true
  exit 1
fi

# ------------------------------------------------------------
# 7. 等待 migrate 完成
# ------------------------------------------------------------
echo ""
info "等待数据库迁移完成..."

MIGRATE_TIMEOUT=180
elapsed=0
while true; do
  if ! docker inspect vt_migrate &>/dev/null; then
    # 容器还未创建，稍等
    sleep 2; elapsed=$((elapsed + 2)); continue
  fi

  running="$(docker inspect vt_migrate --format='{{.State.Running}}' 2>/dev/null || echo "false")"
  exit_code="$(docker inspect vt_migrate --format='{{.State.ExitCode}}' 2>/dev/null || echo "")"

  if [ "$running" = "false" ] && [ -n "$exit_code" ]; then
    if [ "$exit_code" = "0" ]; then
      success "数据库迁移完成 ✓"
      break
    else
      error "数据库迁移失败（exit $exit_code），详细日志："
      echo ""
      docker compose -f "$COMPOSE_FILE" logs migrate 2>/dev/null | tail -40
      echo ""
      error "请检查日志后重试。常见原因：数据库连接失败（等待 postgres 启动）。"
      error "可运行以下命令查看完整日志："
      error "  docker compose -f $COMPOSE_FILE logs migrate"
      exit 1
    fi
  fi

  if [ "$elapsed" -ge "$MIGRATE_TIMEOUT" ]; then
    error "迁移超时（${MIGRATE_TIMEOUT}s），查看日志："
    docker compose -f "$COMPOSE_FILE" logs --tail=30 migrate 2>/dev/null || true
    exit 1
  fi

  printf "."
  sleep 3
  elapsed=$((elapsed + 3))
done

# ------------------------------------------------------------
# 8. 等待后端健康
# ------------------------------------------------------------
info "等待后端 API 就绪..."

BACKEND_TIMEOUT=60
elapsed=0
until curl -sf "http://localhost:${BACKEND_PORT_VAL}/healthz" -o /dev/null 2>/dev/null; do
  if [ "$elapsed" -ge "$BACKEND_TIMEOUT" ]; then
    error "后端启动超时（${BACKEND_TIMEOUT}s），查看日志："
    docker compose -f "$COMPOSE_FILE" logs --tail=30 backend 2>/dev/null || true
    exit 1
  fi
  printf "."
  sleep 2
  elapsed=$((elapsed + 2))
done
success "后端 API 已就绪 ✓"

# ------------------------------------------------------------
# 9. 等待前端就绪
# ------------------------------------------------------------
info "等待前端 nginx 就绪..."

FRONTEND_TIMEOUT=30
elapsed=0
until curl -sf "http://localhost:${FRONTEND_PORT}" -o /dev/null 2>/dev/null; do
  if [ "$elapsed" -ge "$FRONTEND_TIMEOUT" ]; then
    warn "前端服务未响应，请手动打开：http://localhost:${FRONTEND_PORT}"
    break
  fi
  printf "."
  sleep 2
  elapsed=$((elapsed + 2))
done
echo ""
success "前端已就绪 ✓"

# ------------------------------------------------------------
# 10. 启动完成摘要
# ------------------------------------------------------------
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║         AI 小镇启动成功！                ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}前端（游戏入口）：${NC}    http://localhost:${FRONTEND_PORT}"
echo -e "  ${BOLD}观测台（研发）：${NC}      http://localhost:${FRONTEND_PORT}/observability"
echo -e "  ${BOLD}API 文档（Swagger）：${NC} http://localhost:${BACKEND_PORT_VAL}/docs"
echo ""
echo -e "  ${CYAN}停止：${NC}  docker compose -f $COMPOSE_FILE down"
echo -e "  ${CYAN}日志：${NC}  docker compose -f $COMPOSE_FILE logs -f"
echo -e "  ${CYAN}重建：${NC}  bash scripts/start.sh --rebuild"
echo ""

# ------------------------------------------------------------
# 11. 打开浏览器
# ------------------------------------------------------------
if [ "$NO_OPEN" = "false" ]; then
  URL="http://localhost:${FRONTEND_PORT}"
  command -v open     &>/dev/null && open "$URL"       && exit 0
  command -v xdg-open &>/dev/null && xdg-open "$URL" & exit 0
  # Windows Git Bash
  command -v start    &>/dev/null && start "$URL"
fi
