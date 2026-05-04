# GitHub Actions · CI/CD Secrets

路径：**Settings → Secrets and variables → Actions → New repository secret**

---

## 部署 SSH（必配）

| Secret | 说明 |
|--------|------|
| **DEPLOY_SSH_PRIVATE_KEY** | 部署专用 SSH **私钥**全文（PEM）。服务器 `authorized_keys` 放对应公钥。 |
| **DEPLOY_HOST** | 服务器 IP 或域名；CI 默认 `CORS` / `BACKEND_PUBLIC_URL` 会基于它推导。 |
| **DEPLOY_USER** | SSH 用户名（如 `ubuntu`）。 |
| **DEPLOY_PATH** | 仓库在服务器上的**绝对路径**（**不要求**事先 `git clone`）。GitHub Actions 会用 `rsync` 把当前 `main` 同步到该目录，再用 `DEPLOY_SKIP_GIT=1` 跑 `deploy_remote.sh`。也可改为手动维护 git 仓库并不设 `DEPLOY_SKIP_GIT`（见 `scripts/deploy_remote.sh`）。 |

---

## 应用与数据库（由 CI 生成 `.env`）

Demo 栈已在 **`frontend/nginx.demo.conf`** 里用 **nginx 反代** `/api`、`/ws` 到后端；浏览器通常只访问 **`:8080`**，前端走**相对路径**，多数请求与页面**同源**，浏览器不启 CORS。  
但 FastAPI 仍挂了 **`CORSMiddleware`**：你用 **`:8000/docs`** 或其它端口/域名调 API 时，`Origin` 必须在白名单里。CI 默认写入 **`BACKEND_CORS_ORIGINS=http://{DEPLOY_HOST}:{FRONTEND_DEMO_PORT}`**（与 nginx 入口一致）。

| Secret                                                                          | 说明                                                                                                                                 |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| **DEPLOY_POSTGRES_PASSWORD**                                                    | 生产 Postgres 密码 → `POSTGRES_PASSWORD`、`DATABASE_URL*`。                                                                          |
| **DEPLOY_CORS_ORIGIN**                                                          | （**可选**）显式设置 `BACKEND_CORS_ORIGINS`（如 **`https://game.example.com`**）。不配则用 **`http://{DEPLOY_HOST}:8080`** 推导（纯 HTTP + 默认端口）。 |
| **DEPLOY_LLM_API_KEY**                                                          | （可选）不配则关闭 LLM。                                                                                                              |
| **DEPLOY_BACKEND_PUBLIC_URL**                                                   | （可选）不配则 `http://{DEPLOY_HOST}:8000`。                                                                                           |

---

## Compose（可选）

| Secret | 说明 |
|--------|------|
| **DEPLOY_COMPOSE_FILE** | compose 文件名，默认 `docker-compose.demo.yml`。 |

---

## CI 跑测试

**不需要**配置任何 Secret；后端 job 已强制 `LLM_API_KEY=""`，避免仓库级 `LLM_API_KEY` secret 在 PR 上误启 LLM 导致意图测试不稳定。

---

## 服务器准备

- 安装 Docker + Compose V2，用户具备 `docker` 权限。
- 创建一个空目录（或已有旧代码），路径与 **DEPLOY_PATH** 一致即可；**无需**预先 `git clone`，CI 会 `rsync` 同步仓库内容（排除 `.git`、`node_modules` 等）。
- 若你希望**仅**在服务器上 `git pull` 更新而不让 CI 覆盖工作区，可自行调整 workflow（去掉 rsync 步骤、SSH 不传 `DEPLOY_SKIP_GIT=1`），并保证 **DEPLOY_PATH** 为可 `git fetch origin main` 的 clone。
- 防火墙放行 **8080**（前端）与 **8000**（API，若需外网调 OpenAPI）。

---

## 部署后容器仍是旧的？

CI 成功但 `docker ps` 里 **vt_backend** 等 **CREATED** 时间不变时，优先排查：

1. **DEPLOY_PATH** 是否就是你在服务器上执行 `docker compose` 的目录（`pwd` / `readlink -f .` 一致）。
2. **本机是否还有另一套**手工 `docker compose` 起的栈（别的目录 / 别的 compose 文件），你看的是那一套旧容器。
3. **构建缓存**：在服务器上 `export DEPLOY_BUILD_NO_CACHE=1` 后再跑一次 `deploy_remote.sh`；或在 GitHub SSH 一步里给远端加上该 export。
4. 需要连 **postgres/redis** 也换新容器外壳（数据仍在卷内）：`export DEPLOY_RECREATE_DATA_CONTAINERS=1` 后重跑脚本。
