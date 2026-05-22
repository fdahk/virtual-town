# 复盘：RQ Worker 进程内 asyncio 循环与 AsyncEngine 冲突

日期：2026-05-01  
关联：`backend/app/domain/tasks/runner.py`、`worker.py`、`queue.py`

## 现象

- Smoke 场景：FastAPI/API 进程 `enqueue` 成功，`tasks.status` 长期停在 `pending`；或 Redis 显示 RQ Job `failed`，trace 类似：
  - `RuntimeError: Task ... got Future ... attached to a different loop`
  - `asyncpg.exceptions._base.InternalClientError: got result for unknown protocol state 3`（次生）
- 首次实现在 `runner.run_task_job` 里对每个 RQ job 调用 **`asyncio.run(_execute_task(...))`**，即每 job **新建再销毁一个 event loop**。

## 根因

1. SQLAlchemy 2 async + asyncpg：**AsyncEngine / 连接池与首次建立连接时所用的 event loop 绑定**。  
   在同一进程内对不同 loop `asyncio.run` 复用全局单例 Engine，checkout 时出现 Future 归属于「旧 loop」，当前协程运行在「新 loop」，触发跨 loop Future 报错。
2. **TTL worker**若另起线程 + **独立** `asyncio` loop，会与 **runner** 的 loop 争抢同一全局 Engine，问题复现路径相同——必须统一到**同一常驻 loop**。

## 修复

- Worker 进程内启动**一个常驻** `_LOOP`（独立线程 `run_forever`）。
- RQ job 入口 `run_task_job`：**不再** `asyncio.run`，改为 `asyncio.run_coroutine_threadsafe(_execute_task(id), loop).result(...)`。
- `_ensure_worker_bootstrap` 里对 Observer：`run_coroutine_threadsafe(start, loop)` 在同一 loop 上启动。
- `worker.py`：TTL worker 在同一 `_ensure_loop()` 上 `ttl.start()`，删除「单独线程新开 loop」的 TTL。

## 附加：双 Worker 占位

调试时若只 kill 启动 shell（父 PID）而未结束子进程 `python -m ...worker`，会出现**旧代码 Worker**与**新 Worker**同在 Redis `rq:workers` 集合中，job 可能被旧 Worker 捞起并异常失败。处理方式：确认无残留 Worker 进程，必要时清理 Redis 中已无心跳的 stale `rq:worker:*` key。

## 经验教训

- 在「同步框架消费异步业务」（RQ/Celery + SQLAlchemy asyncio）的场景下，要么 **Engine 完全按进程/loop 维度隔离**，要么 **全进程共用单一常驻 asyncio loop**，不要对每个任务 `asyncio.run` 建新 loop。
- 本地 smoke 后以 `ps`、`docker exec vt_redis redis-cli smembers rq:workers` 核对是否只有一个活跃的 worker 实例。
