"""
Redis 客户端封装（阶段 13）。

- 单例异步客户端，连接在应用启动时延迟建立。
- Redis 不可用时**不**抛异常：所有调用方需视为 best-effort 缓存，
  缺失直接回落 Postgres。通过 ``health_check`` 可主动探测。

约定的 key 命名空间（与 ``数据库与存储模块实施方案.md §5`` 一致）：

- ``agent:{agent_id}:short_memory``  — Agent 短期记忆列表，TTL 10min~24h
- ``agent:{agent_id}:runtime``       — Agent 运行态缓存（AgentState 映射）
- ``dialogue:{conv_id}:recent_messages`` — 多轮对话窗口
- ``world:{scene_id}:active_entities``   — 场景内活跃实体 id 集合
- ``sim:{sim_id}:state``            — 仿真级缓存
- ``task:{task_id}:status``         — 任务状态摘要（供观测页面轮询）
"""

from __future__ import annotations

import asyncio
from typing import Any

import orjson

try:  # redis.asyncio is shipped in redis>=5
    from redis import asyncio as redis_asyncio  # type: ignore
    from redis.asyncio import Redis as AsyncRedis  # type: ignore
    from redis.exceptions import RedisError  # type: ignore
except Exception:  # pragma: no cover
    redis_asyncio = None  # type: ignore
    AsyncRedis = Any  # type: ignore

    class RedisError(Exception):  # type: ignore
        pass

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class RedisService:
    """最小 Redis 封装 + 常用高阶操作。"""

    def __init__(self) -> None:
        self._client: AsyncRedis | None = None
        self._available: bool = True
        self._lock = asyncio.Lock()

    async def _ensure_client(self) -> AsyncRedis | None:
        if redis_asyncio is None:
            return None
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is not None:
                return self._client
            settings = get_settings()
            try:
                self._client = redis_asyncio.from_url(
                    settings.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_timeout=2.0,
                    socket_connect_timeout=2.0,
                )
                await self._client.ping()
                self._available = True
            except Exception as exc:
                logger.warning("redis init failed: %s", exc)
                self._client = None
                self._available = False
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    async def health_check(self) -> dict[str, Any]:
        """探测 Redis 可用性（供 ``/health/redis``）。"""
        client = await self._ensure_client()
        if client is None:
            return {"ok": False, "reason": "client_unavailable"}
        try:
            pong = await client.ping()
            self._available = bool(pong)
            return {"ok": True}
        except Exception as exc:
            self._available = False
            return {"ok": False, "reason": str(exc)}

    # ------------------------------------------------------------------
    # 通用 KV
    # ------------------------------------------------------------------

    async def set_json(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> bool:
        client = await self._ensure_client()
        if client is None:
            return False
        try:
            data = orjson.dumps(value).decode()
            if ttl_seconds:
                await client.set(key, data, ex=ttl_seconds)
            else:
                await client.set(key, data)
            return True
        except Exception as exc:
            logger.debug("redis set_json failed %s: %s", key, exc)
            return False

    async def get_json(self, key: str) -> Any | None:
        client = await self._ensure_client()
        if client is None:
            return None
        try:
            raw = await client.get(key)
            if raw is None:
                return None
            return orjson.loads(raw)
        except Exception as exc:
            logger.debug("redis get_json failed %s: %s", key, exc)
            return None

    async def delete(self, *keys: str) -> int:
        client = await self._ensure_client()
        if client is None or not keys:
            return 0
        try:
            return int(await client.delete(*keys))
        except Exception:
            return 0

    async def set_nx(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        """SETNX with TTL：原子设置 key 并返回是否首次设置成功。

        Redis 不可用时返回 ``True``（best-effort），避免因为缓存层不可用而把
        业务流量直接掐断（与本类其它方法的 best-effort 语义一致）。

        典型用法：分布式锁。``ttl_seconds`` 既保证锁不死，也避免 Redis 单点故障
        时锁永驻。
        """
        client = await self._ensure_client()
        if client is None:
            return True
        try:
            ok = await client.set(key, value, ex=ttl_seconds, nx=True)
            return bool(ok)
        except Exception:
            return True

    async def expire(self, key: str, seconds: int) -> bool:
        client = await self._ensure_client()
        if client is None:
            return False
        try:
            return bool(await client.expire(key, seconds))
        except Exception:
            return False

    async def exists(self, key: str) -> bool:
        client = await self._ensure_client()
        if client is None:
            return False
        try:
            return bool(await client.exists(key))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 列表（短期记忆 / 对话窗口）
    # ------------------------------------------------------------------

    async def list_push(
        self,
        key: str,
        item: Any,
        *,
        max_len: int = 100,
        ttl_seconds: int | None = None,
    ) -> bool:
        client = await self._ensure_client()
        if client is None:
            return False
        try:
            data = orjson.dumps(item).decode()
            pipe = client.pipeline()
            pipe.lpush(key, data)
            pipe.ltrim(key, 0, max_len - 1)
            if ttl_seconds:
                pipe.expire(key, ttl_seconds)
            await pipe.execute()
            return True
        except Exception as exc:
            logger.debug("redis list_push failed %s: %s", key, exc)
            return False

    async def list_range(self, key: str, *, limit: int = 30) -> list[Any]:
        client = await self._ensure_client()
        if client is None:
            return []
        try:
            items = await client.lrange(key, 0, limit - 1)
            out: list[Any] = []
            for raw in items:
                try:
                    out.append(orjson.loads(raw))
                except Exception:
                    continue
            return out
        except Exception:
            return []

    # ------------------------------------------------------------------
    # 集合（场景内活跃实体）
    # ------------------------------------------------------------------

    async def set_add(self, key: str, *members: str, ttl_seconds: int | None = None) -> bool:
        client = await self._ensure_client()
        if client is None or not members:
            return False
        try:
            pipe = client.pipeline()
            pipe.sadd(key, *members)
            if ttl_seconds:
                pipe.expire(key, ttl_seconds)
            await pipe.execute()
            return True
        except Exception:
            return False

    async def set_remove(self, key: str, *members: str) -> bool:
        client = await self._ensure_client()
        if client is None or not members:
            return False
        try:
            await client.srem(key, *members)
            return True
        except Exception:
            return False

    async def set_members(self, key: str) -> list[str]:
        client = await self._ensure_client()
        if client is None:
            return []
        try:
            return list(await client.smembers(key))
        except Exception:
            return []

    # ------------------------------------------------------------------
    # 键空间扫描（观测 / TTL worker）
    # ------------------------------------------------------------------

    async def scan_keys(self, pattern: str, *, count: int = 200) -> list[str]:
        client = await self._ensure_client()
        if client is None:
            return []
        try:
            cursor = 0
            keys: list[str] = []
            while True:
                cursor, batch = await client.scan(cursor=cursor, match=pattern, count=count)
                keys.extend(batch)
                if cursor == 0:
                    break
            return keys
        except Exception:
            return []


_svc: RedisService | None = None


def get_redis() -> RedisService:
    global _svc
    if _svc is None:
        _svc = RedisService()
    return _svc


# -----------------------------------------------------------------------------
# key 构造器：集中管理命名空间，防止散落
# -----------------------------------------------------------------------------


def key_short_memory(agent_id: str) -> str:
    return f"agent:{agent_id}:short_memory"


def key_agent_runtime(agent_id: str) -> str:
    return f"agent:{agent_id}:runtime"


def key_dialogue_recent(conversation_id: str) -> str:
    return f"dialogue:{conversation_id}:recent_messages"


def key_scene_active_entities(scene_id: str) -> str:
    return f"world:{scene_id}:active_entities"


def key_sim_state(simulation_id: str) -> str:
    return f"sim:{simulation_id}:state"


def key_task_status(task_id: str) -> str:
    return f"task:{task_id}:status"


def key_task_idempotency(idempotency_key: str) -> str:
    return f"task:idempotency:{idempotency_key}"


def key_agent_unreachable(agent_id: str) -> str:
    """不可达目标黑名单：agent:{id}:unreachable。
    存储 location_id/entity_id 集合，TTL 5 分钟。
    tool 失败后写入；决策前读取并过滤，避免 LLM 反复尝试不可达目标。
    """
    return f"agent:{agent_id}:unreachable"


def key_agent_decision_lock(agent_id: str) -> str:
    """决策"进行中"锁：agent:{id}:decision_lock。

    阶段 21+：22 NPC 每 ``ai_tick_minutes`` 仿真分钟（≈ 0.6 真实秒）就会触发一次
    enqueue。SimpleWorker 时代单线程 LLM 调用 5-15s/次，导致同一 NPC 的 N 份
    决策任务在队列里堆积，超过 ``deadline_seconds`` 后批量被丢弃，玩家观感即
    "只有少数 NPC 在动"。

    新增决策锁：入队前 SETNX，TTL = handler 默认超时 + 余量；锁持有期间引擎
    跳过该 NPC 的 enqueue，且**不更新** ``last_decision_at`` —— 等锁到期或被
    handler 主动释放后，下个 tick 立刻重试。

    锁释放时机：
    - handler 成功完成 → ``runner._mark_succeeded`` 释放；
    - handler 失败 / 超时 / deadline 过期 → ``runner._mark_failed`` 释放；
    - 进程崩溃 → 由 TTL 自动过期（默认 60s）。
    """
    return f"agent:{agent_id}:decision_lock"


def key_embedding_cache(text_hash: str) -> str:
    """Embedding 向量缓存：embed:{text_hash}。
    相同文本的 embedding 结果缓存 1 小时，减少重复 API 调用。
    """
    return f"embed:{text_hash}"


__all__ = [
    "RedisService",
    "get_redis",
    "key_agent_decision_lock",
    "key_agent_runtime",
    "key_agent_unreachable",
    "key_dialogue_recent",
    "key_embedding_cache",
    "key_scene_active_entities",
    "key_short_memory",
    "key_sim_state",
    "key_task_idempotency",
    "key_task_status",
]
