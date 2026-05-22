# 部署任务复盘（Demo 全栈 · CI/CD · 测试机）

本文总结 **2026 年 5 月** 前后围绕 **virtual-town** 的 demo 部署、GitHub Actions、测试服务器联调中出现的问题、根因与结论，并回答：**手动在服务器执行 `docker compose` 是否说明 CI/CD 有缺陷**。

---

## 1. 目标与架构简述

- **目标**：在测试/生产环境用 **Docker Compose（`docker-compose.demo.yml`）** 跑通 Postgres、Redis、migrate、backend、worker、frontend（nginx 静态 + `/api` `/ws` 反代）。
- **CI/CD 设计**（仓库现状）：
  1. `push` 到 `main` 且 **backend pytest + frontend lint/build** 通过；
  2. Runner 上 **`write_deploy_env.py`** 生成 `.env`；
  3. **`rsync`** 将仓库同步到服务器 **`DEPLOY_PATH`**（默认不要求远端为 git 仓库）；
  4. **`scp`** 上传 `.env`；
  5. **`appleboy/ssh-action`**（`command_timeout: 30m`）在服务器 **`DEPLOY_PATH`** 执行 **`bash scripts/deploy_remote.sh`**（`DEPLOY_SKIP_GIT=1`），完成 **build → migrate → 分步 up 应用层**。

---

## 2. 问题时间线与现象

| 阶段 | 现象 | 根因（简述） |
|------|------|----------------|
| Git | `fatal: not a git repository` | `DEPLOY_PATH` 不是 git clone，而旧版脚本固定 `git fetch`。 |
| Rsync | `cannot delete non-empty directory`（miniconda 等） | `rsync --delete` + **`DEPLOY_PATH` 误为主目录**，试图删除家目录下与仓库无关的大目录。 |
| 测试 | pytest / lint 失败 | 世界生成尺寸与布局校验不一致；过时 import；前端缺 ESLint 依赖等（已在本仓库修复）。 |
| 容器 | 仅有 `vt_postgres`、`vt_redis`，无 frontend/backend | **（A）Compose 依赖语义**：`docker compose run --rm migrate` **不会**满足 `depends_on: migrate: condition: service_completed_successfully`，后续若只 `up backend/worker`，会卡在依赖图，应用层起不来；**（B）CD 执行半截**：前半段已 `up postgres/redis`，后半段 **build/migrate/up 失败** 或 **SSH/命令超时断开**；与「另一项目」共存时需区分目录与端口。 |
| 访问 | 本机 `curl 127.0.0.1:8080` 正常，外网超时或 502 | **安全组未放行 8080**；本机 **`http_proxy` 仍指向本地代理**导致 502；**误用 `https://IP:8080`**（demo 默认 HTTP）；根路径 **80/443** 是其它服务，不是本游戏。 |
| 路径 | 文档/口头曾出现 `paaawow_backend` 与 `~/virtual-town` 混用 | **同一台机多项目**，部署目录必须与 **Secret `DEPLOY_PATH`** 及实际 `docker compose` 工作目录一致。 |
| 仿真 | 重启容器后仍在跑、RQ 仍海量 pending | DB **`simulation.status`** 与 **Redis 卷内 RQ** 均持久化；旧逻辑未冷启动收敛 + 未清队列。见 §3、§9。 |

---

## 3. 已落地的修复（仓库侧）

- **`scripts/deploy_remote.sh`**：支持 `DEPLOY_SKIP_GIT=1`；无 `.git` 时给出明确错误而非盲目 `git fetch`。
- **`scripts/deploy_remote.sh`（2026-05 部署「只起库」专项）**：
  - 在 `run --rm migrate` 之后，对 **`backend`、`worker` 使用 `up … --no-deps`**，再单独 **`up … --no-deps frontend`**，避免与 **`service_completed_successfully`** 的图内 `migrate` 状态不对齐（参考 `docker-compose.demo.yml` 中 backend/worker 的 `depends_on`）。
  - 增加 **Redis 就绪等待**、`docker image prune -f`（容错）、以及 **`vt_backend` / `vt_worker` / `vt_frontend` running** 校验，失败即非零退出。
  - 全量输出 **`tee`** 到 **`/tmp/virtual-town-deploy.log`**，服务器上可 `tail` 复盘（SSH 日志不全或作业中断时尤其有用）。
- **`.github/workflows/ci-cd.yml`**：`rsync` 同步代码；**默认去掉 `--delete`**，可选 Secret **`DEPLOY_RSYNC_DELETE=1`** 在专用目录再启用镜像删除。
- **`.github/workflows/ci-cd.yml`（对齐参考项目 `paaawow_backend`）**：部署步改为 **`appleboy/ssh-action`**，**`command_timeout: 30m`**（该 Action **默认仅 10m**，长 **`docker compose build`** 易在「已有 postgres/redis、未起应用」处被掐断）；与 **`rsync` 共用** `~/.ssh/deploy_key`，复用 **`accept-new`** 写入的 `known_hosts`。在远端执行 **`bash scripts/deploy_remote.sh`**（以 rsync 后的协议本为准），不再依赖 **`bash -s` + stdin 灌脚本**。
- **`.github/DEPLOY_SECRETS.md`**：**`DEPLOY_PATH` 必须为专用目录**；说明 rsync 与 `DEPLOY_RSYNC_DELETE`。
- **冷启动仿真 + RQ 状态（2026-05-04）**：
  - `SIMULATION_AUTOSTART=false` 时，`SimulationEngine._load_state` **无论 DB 里是否已有行**，启动后都把 `simulation.status` 收敛为 **`paused`**（True 时收敛为 `running`），避免**仅重启容器**却继承上一进程的 `running`、世界在无人进 TownPage 时仍 tick。
  - **`SIMULATION_RESET_QUEUE_ON_START`**（默认 `true`）：`lifespan` 在 `runtime.start()` 前调用 **`reset_inflight_state_on_cold_start`**：对三条 RQ 队列 `empty()`，并把 PG **`tasks`** 表中 **`pending` 删除**、**`running` 标为 `cancelled`**，避免 `redis_data` 卷里残留 job 被新 worker 当「鬼任务」清空 LLM 配额、刷爆 **failed** 指标。
  - 前端 **`StartPage`**：`running` / **`paused`** 均显示「继续当前世界」（paused 时副文案提示进城后再恢复时间）。
- **测试与前端工具链**：世界 seed 尺寸、测试 import、ESLint 与锁文件等（见对应 commit）。

---

## 4. 手动执行的命令 vs CI/CD 是否等价

你在服务器上执行的核心步骤大致为：

```bash
docker compose -f docker-compose.demo.yml up -d postgres redis
# 等待 PostgreSQL / Redis 就绪后再继续（与脚本一致）
docker compose -f docker-compose.demo.yml build migrate backend worker frontend
docker compose -f docker-compose.demo.yml run --rm migrate
# 关键：run migrate 后须 --no-deps，否则 backend/worker 会一直等「图内」migrate 完成
docker compose -f docker-compose.demo.yml up -d --build --force-recreate --remove-orphans --no-deps backend worker
docker compose -f docker-compose.demo.yml up -d --build --force-recreate --remove-orphans --no-deps frontend
```

**`scripts/deploy_remote.sh` 中与上述等价的部分**（摘要）：

1. `up -d postgres redis` + **`pg_isready`** 与 **Redis `PING`** 等待；
2. `build migrate backend worker frontend`；
3. `run --rm migrate`；
4. **`up -d --build --force-recreate --remove-orphans --no-deps` 分两步**：先 `backend worker`，再 `frontend`。

因此：**从设计上看，CI/CD 成功跑完 deploy job 后，不应再依赖手工执行上述命令。** 手工通常出现在以下情况之一：

| 情况 | 说明 |
|------|------|
| **首次/调试** | 尚未配全 Secrets、或 `DEPLOY_PATH`/`rsync`/SSH 失败，deploy job 红，于是本地手搓验证。 |
| **路径或项目混淆** | 在错误目录（如其它项目根）执行 compose，或 Secret 指向非 `virtual-town` 目录。 |
| **执行到一半失败** | 例如 migrate 或 **build** 失败、或 **SSH 命令超时**，只留下了 postgres/redis；除 Actions 外还可看服务器 **`/tmp/virtual-town-deploy.log`**，排错后重跑。 |
| **未触发 deploy** | 例如只 merge 了 workflow 变更但 **deploy 条件为 push main**；或 **fork/Secret 未配** 导致 job skip。 |

**结论**：不是「CI/CD 没有实现完整部署逻辑」，而是 **要么部署流水线未成功跑通，要么环境（路径、安全组、Secret）未与文档一致**。脚本层面已与上述**含 `--no-deps` 与分步 up** 的手动步骤对齐；若仍只看到数据层容器，应**先查该次 deploy job 是否失败/超时**，再对照 **`depends_on` + `run migrate`** 与远端日志。

---

## 5. 仍可加强的建议（可选）

1. **Deploy job 末尾增加探测**（仅作软检查）：对 `http://127.0.0.1:8080` 或配置的 public URL 做 `curl -f`，失败则 job 失败（需在 runner 能访问的地址上做，公网检测需单独讨论）。
2. **文档中显式 checklist**：创建 `DEPLOY_PATH`、放行 **8080**（或 80/443 反代）、**`DEPLOY_PATH` 禁止为主目录**、`DEPLOY_RSYNC_DELETE` 慎用。
3. **多项目同机**：在文档中说明 **端口与安全组**；本 demo 默认 **8080 / 8000**。
4. **HTTPS 无域名**：Let's Encrypt 不支持裸 IP；无域名时以 **HTTP + 安全组** 为务实方案；有域名后再 443 + 证书。

---

## 6. 访问方式与安全组（本次踩坑小结）

- **游戏入口**：`http://<公网IP>:8080/`（默认 **FRONTEND_DEMO_PORT=8080**）。
- **API 文档（可选）**：`http://<公网IP>:8000/docs`。
- **云安全组**：若外网超时，首先检查 **TCP 8080**（及需要时的 **8000**）是否入站放行。
- **本机开发机**：终端若设置 **`http_proxy`**，对公网 IP 的 `curl`/浏览器可能经本地代理，出现 **502** 或异常；排障时用 **`curl --noproxy '*'`** 或 **`unset http_proxy`**。
- **与「其它项目正常」不矛盾**：其它项目常走 **80/443** 已放行，本 demo 走 **8080** 需单独放行。

---

## 7. 经验教训（一句话）

- **专用 `DEPLOY_PATH` + 慎用以 `--delete` 的 rsync + 安全组放行实际端口 + 区分 HTTP/HTTPS 与代理环境** = 本次问题的核心；CI/CD 脚本本身已覆盖与「手搓」相同的部署步骤，瓶颈多在 **环境配置与一次成功的流水线执行**。
- **Compose 补充**：**一次性 `run migrate` ≠ 依赖图里的 `service_completed_successfully`**，应用层要么 **`up` 全栈让 compose 顺序跑 migrate**，要么在 **`run migrate` 后对应用服务显式 `--no-deps`**。
- **CD 补充**：长时间远端部署宜用 **`appleboy/ssh-action` 等可配置 `command_timeout`** 的方式，避免默认 **10m** 或链路中断导致「半截部署」表象。

---

## 8. 复盘结论（2026-05：「只起库容器」已闭环）

本轮现象：**手动停容器后重跑 Action，`docker ps` 仍只见 `vt_postgres` / `vt_redis`，且不像刚部署刷新的实例**。

**根因归纳**：

1. **业务编排**：`run --rm migrate` 后若按常规 **`up backend`**（带对 `migrate` 的 **`service_completed_successfully`** 依赖），应用容器无法稳定拉起；修复为 **`--no-deps` + 先 worker/backend 再 frontend**（见 §3、`deploy_remote.sh`）。
2. **流水线执行**：参考 **`paaawow_backend`** 将部署 SSH 改为 **`appleboy/ssh-action`** 并设 **`command_timeout: 30m`**，避免长 **build** 阶段静默超时、远端脚本中止而基础设施容器仍存活。
3. **可观测性**：服务器侧 **`/tmp/virtual-town-deploy.log`** 便于区分「从未连上」「build 挂」「migrate 挂」与「up 挂」。

**当前状态**：上述修复合并后，**问题已解决**；后续同类排障可按 **GHA deploy 步骤日志 → 远端 deploy 日志 → `docker compose … logs`** 顺序收敛。

---

## 9. 附录：为何「只有 PG 持久」仍会看到 RQ 积压跨重启？

Docker Compose demo 里 **`postgres_data`** 与 **`redis_data`** 都是**命名卷**，挂到宿主（或云盘）。因此：

- **PostgreSQL**：库表（`simulation`、`tasks`、世界数据）跨容器重启不丢。
- **Redis**：默认 **RDB/AOF 持久化到 `/data`**，`vt_redis` 重启后 key 仍在；**RQ 把 job 元数据存在 Redis**（list / hash / sorted set 等），不是「纯内存进程队列」。所以**任务 backlog 会像 PG 一样跨一次「只重启容器卷不删」的部署**。
- **纯 RAM 的 Redis**（无卷、或 `docker compose down -v` 删卷）才会丢队列；当前 demo 设计是**可恢复的队列 + 可恢复的仿真状态**。

与之配套：§3 中 **冷启动清 RQ + 收敛 `simulation.status`**，让「重启 = 不丢档」与「不会默默接着跑 / 接着消费鬼任务」兼得。
