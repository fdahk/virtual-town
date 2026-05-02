# 本地开发：热重载与进程重启 SOP

**适用场景**：你在本机用 `bash scripts/dev.sh` 一键拉起 backend / RQ
worker / frontend 三件套，开发过程中遇到「改了代码但好像没生效」「日志
还在报上一秒就修过的 bug」「不知道哪个进程要不要手动重启」时，按本文
排查。

---

## 一、三件套各自的代码加载机制

| 进程 | 启动命令 | 是否自动 reload | 监听目录 |
|---|---|---|---|
| **backend (uvicorn)** | `uvicorn app.main:app --reload` | ✅ 自带，依赖 `watchfiles` | 整个 `backend/` |
| **RQ worker** | `watchfiles --filter python "python -m app.domain.tasks.worker" app` | ✅ **dev 模式**（dev.sh 已加） | `backend/app/` |
| **frontend (vite)** | `npm run dev` | ✅ 自带 HMR | `frontend/src/` |
| **alembic 迁移** | `alembic upgrade head` | ❌ 一次性 | — |
| **DB 数据** | — | ❌ 持久化 | — |

> dev 默认 `APP_ENV=dev`，worker 自动包 `watchfiles`；CI / 生产请显式
> 设 `APP_ENV=prod` 跳过包装，避免无故重启。

---

## 二、最容易踩坑的"改代码不生效"组合

### 1. 改了 ORM / Pydantic Schema → 必须 alembic 迁移 + 重启 backend

仅改 `app/db/models.py` 的列定义不会自动同步到 PostgreSQL。`uvicorn
--reload` 只重启 Python 进程，不会运行 DDL。

```bash
# 1. 生成迁移
cd backend && alembic revision -m "add xxx column" --autogenerate
# 2. 检查迁移脚本（alembic 自动生成可能有歧义）
# 3. 执行
DATABASE_URL_SYNC="${DATABASE_URL_LOCAL_SYNC}" alembic upgrade head
# 4. backend 通过 --reload 已自动重启；worker 由 watchfiles 已自动重启
```

如果忘了 alembic upgrade，常见报错：
- `TypeError: 'xxx' is an invalid keyword argument for SomeModel`
- `column "xxx" of relation "yyy" does not exist`

### 2. 改了前端 manifest / 资源文件 → 浏览器要硬刷新

vite HMR 只热更模块，不热更 `public/` 下的静态文件。改完
`sprites.manifest.json`、`tilesets.manifest.json`、`portraits.manifest.json`
后必须 **Cmd+Shift+R** 硬刷新（绕过 disk cache），否则 Phaser 仍按旧
manifest 找资源。

### 3. 改了 manifest 注册表 → 必须跑产物生成脚本

新增 NPC sprite 时，仅改 `compose_lpc.py::NPC_LAYERS` +
`sprites.manifest.json` 不会生成 PNG。**强制跑**：

```bash
bash scripts/fetch_assets.sh
# 它会：(a) 下载缺失 LPC 分层 → (b) 调 compose_lpc.py 合成 sprite
#      (c) 调 compose_portraits.py 自动从 sprite 头部裁兜底立绘
```

否则前端报 `Failed to process file: spritesheet "human_npc_xxx"`。

### 4. 改了 docker-compose 资源 → 重启容器

`postgres` / `redis` 由 docker compose 管，dev.sh 不会自动 docker
restart。需要：

```bash
docker compose restart postgres
# 或
docker compose down && docker compose up -d postgres redis
```

---

## 三、判断「这次重启需要重哪几样」决策表

| 改动文件 | 需要重启 | 原因 |
|---|---|---|
| `backend/app/**.py`（业务代码） | ❌ 自动 | uvicorn --reload + worker watchfiles |
| `backend/app/db/models.py`（加/改列） | ✅ alembic upgrade | DDL 不会自动同步 |
| `backend/app/schemas/**.py`（Pydantic Literal 收敛） | 🤔 视情况 | 自动 reload，但旧数据不合规会 500 → 检查 SQL |
| `backend/alembic/versions/*.py` | ✅ alembic upgrade | 否则 schema 不变 |
| `backend/app/core/config.py`（默认值） | ✅ 自动 | uvicorn 重启会重读 |
| `.env` | ✅ **手动重启** dev.sh | uvicorn / worker 启动时一次性读 |
| `frontend/src/**.tsx` | ❌ 自动 | vite HMR |
| `frontend/public/assets/manifest/*.json` | ✅ 浏览器硬刷新 | 静态资源 |
| `frontend/public/assets/sprites/**.png` | ✅ 浏览器硬刷新 | 同上 |
| `scripts/dev.sh` 本身 | ✅ 全停 + 重跑 | dev.sh 在 wait 循环里，不会 re-source 自己 |
| `docker-compose.yml` | ✅ `docker compose up -d` | compose 不监视 yml |

---

## 四、确认进程到底加载了哪份代码

经验法则：**怀疑代码不同步时，先看 PID 启动时间**。

```bash
ps -eo pid,etime,command | grep -E "watchfiles|app\.domain\.tasks\.worker|app\.main:app" | grep -v grep
```

输出示例：

```
54714  00:25  python uvicorn app.main:app --reload
54715  00:25  python watchfiles --filter python ... app
55014  00:03  python -m app.domain.tasks.worker     ← worker 进程刚才被 watchfiles 重启过
```

PID 启动时间晚于你最后一次 save 的，说明已重新加载；早于，说明它仍是旧
代码。

要强制重启某一个：

```bash
# A) 让 watchfiles / uvicorn 自然触发：模拟一次 touch
touch backend/app/domain/tasks/runner.py        # worker 重启
touch backend/app/main.py                       # backend 重启

# B) 手动 kill 子进程，watchfiles / uvicorn 会立刻 respawn
kill -TERM <worker_child_pid>     # 不要 kill watchfiles 父，否则进程没了
```

要重启全部三件套，最稳的做法：

```bash
pkill -TERM -f "scripts/dev.sh"          # dev.sh 的 EXIT trap 会清光所有子进程
sleep 3
bash scripts/dev.sh                       # 重新拉起
```

---

## 五、`dev.sh` 自身机制摘要（避免再写错）

```
zsh / 用户终端
└── bash scripts/dev.sh                              ← 主控（trap EXIT/INT/TERM 做 cleanup）
    ├── (uvicorn ... --reload)            BACKEND_PID
    │   └── reloader spawn 真正 server 子进程，文件改动后 spawn 新 server
    ├── (watchfiles "python -m app.domain.tasks.worker" app)  WORKER_PID
    │   └── spawn worker 子进程，文件改动后 SIGTERM 后 respawn
    └── (npm run dev)                     FRONTEND_PID
        └── vite 自带 HMR
```

- `dev.sh` 不监视脚本自己的修改；改了 `dev.sh` 必须 `pkill -f dev.sh` 重新跑。
- `cleanup()` 用进程组 kill（`kill -TERM -- "-$pgid"`），子孙进程都干净。
- `wait` 循环检测任一直接子进程退出即整体清理；意味着只 kill `WORKER_PID`
  也会拖整个栈下线（设计如此，避免裸进程游荡）。

---

## 六、验证 worker 热重载是否真的生效

新克隆 / 新机器第一次跑 dev.sh 时，可以通过这段脚本确认包装层就位：

```bash
# 1) 看启动日志有这一行
grep "watchfiles 自动重载" /tmp/vt_dev.log
# 期望：[dev] worker 已启用 watchfiles 自动重载（监视 backend/app/*.py）

# 2) 看 ps 里有 watchfiles 父 + python 子
ps -eo pid,command | grep -E "watchfiles.*app\.domain\.tasks|app\.domain\.tasks\.worker$" | grep -v grep
# 期望两行：watchfiles ... + python -m app.domain.tasks.worker

# 3) touch 触发 reload
old=$(pgrep -f "python -m app\.domain\.tasks\.worker" | tail -1)
touch backend/app/domain/tasks/runner.py
sleep 5
new=$(pgrep -f "python -m app\.domain\.tasks\.worker" | tail -1)
[ "$old" != "$new" ] && echo "OK: worker PID $old → $new" || echo "FAIL: PID 没变"
```

---

## 七、常见误判清单（"我明明改了，怎么还在报老错？"）

| 你看到的 | 真正原因 |
|---|---|
| 行号一致 → 代码没改？ | 你的修改可能只是改了缩进 / 空格，行号不变；先 `git diff` 验真，再确认 PID 是否新进程 |
| 后端日志正常但 worker 还报 | RQ worker 没装 watchfiles 包装（旧 dev.sh）或 `APP_ENV != dev` |
| frontend 行为不一致 | manifest 是静态文件，必须硬刷新 |
| ORM 改了但报 invalid keyword | 没跑 alembic upgrade |
| `.env` 改了不生效 | uvicorn / worker 启动时一次性读，必须重启 dev.sh |
| docker postgres 数据丢了 | `docker compose down -v` 会删 volume；普通 `docker compose down` 保留 |
| 修了 schema 但旧行还在报 | 数据库里仍是旧值，需要 `UPDATE` 或重跑 `POST /api/games/new` 重建世界 |

---

## 八、复盘案例索引

- `20260502-stage21-newgame-hotfix.md`：典型「改了 runner.py 缩进但
  worker 没重启」造成的"虚假未修复"误判，触发了本 SOP 的诞生。
- `20260501-trace-boundary-and-deadline-bugs.md`：deadline 字段写了但
  runner 不查的姊妹 bug，可对比阅读。
