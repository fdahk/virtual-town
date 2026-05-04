"""
应用配置。

- 所有配置从环境变量 / .env 读取。
- `.env` 文件仅用于本地开发，生产环境必须由部署系统注入环境变量。
- 密钥类字段不在日志中输出。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局应用配置。字段命名保持与 `.env.example` 一致以便一一对照。"""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用通用
    app_env: Literal["dev", "test", "staging", "prod"] = Field(default="dev")
    app_name: str = Field(default="virtual-town")
    app_log_level: str = Field(default="INFO")
    app_debug: bool = Field(default=True)

    # 后端服务
    backend_host: str = Field(default="0.0.0.0")
    backend_port: int = Field(default=8000)
    backend_public_url: AnyHttpUrl = Field(default="http://localhost:8000")
    backend_cors_origins: str = Field(default="http://localhost:5173")

    # 数据库
    database_url: str = Field(
        default="postgresql+asyncpg://virtual_town:virtual_town_dev@localhost:5432/virtual_town"
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg://virtual_town:virtual_town_dev@localhost:5432/virtual_town"
    )

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0")

    # LLM
    llm_provider: str = Field(default="dashscope")
    llm_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    llm_api_key: str = Field(default="")
    llm_chat_model: str = Field(default="qwen-plus")
    llm_reasoning_model: str = Field(default="qwen-plus")
    llm_embedding_model: str = Field(default="text-embedding-v3")
    llm_embedding_dim: int = Field(default=1024)
    llm_chat_timeout_seconds: float = Field(default=30.0)
    llm_max_retries: int = Field(default=2)
    llm_enabled: bool = Field(default=False)
    # 阶段 17：按任务角色可选不同模型（留空则回退到 chat / reasoning 默认）
    # 支持的角色：chat / reasoning / dialogue / query_rewrite / intent / planning / embedding
    llm_dialogue_model: str = Field(default="")
    llm_planning_model: str = Field(default="")
    llm_query_rewrite_model: str = Field(default="")

    # 仿真
    simulation_world_tick_hz: float = Field(default=5.0)
    # 阶段 19+++ 调参：5 → 3，让 NPC 决策更频繁、社交反应更灵
    simulation_ai_tick_minutes: int = Field(default=3)
    simulation_speed_default: float = Field(default=1.0)
    # 新游戏 / seed 默认室外地图尺寸（格）。前端向导初始值由 GET /games/world-defaults 透出。
    #
    # 最小值由 ``app/domain/world_gen/outdoor.py`` 里硬编码的 BUILDING_LAYOUT /
    # HOME_LAYOUT 决定（最远建筑：xiaoke 家门 x=114、xiaodi 家门 y=82、woodshop
    # 门 (92,69) ；OUTDOOR_AREAS 的 forest_grove (32,87) / farmland (53,86)）。
    # 因此 ``width≥120`` / ``height≥90`` 才能保证默认 22 NPC 模板下所有建筑、
    # 住宅、户外区域都落在地图内、portal/entry 不越界。
    #
    # 历史遗留：曾默认 90×60，导致 8 户家庭 + 农场屋 + 木工坊的入口落在地图外、
    # NPC 永远 "暂时不可达"，并触发 22 NPC 决策刷屏的现象（详见
    # ``docs/开发手册/debug/20260502-world-bounds-mismatch.md``）。
    # 通过 ``backend/tests/test_world_gen_reachability.py`` 在 CI 守住。
    world_gen_outdoor_default_width: int = Field(default=120, ge=120, le=200)
    world_gen_outdoor_default_height: int = Field(default=90, ge=90, le=200)
    # 默认 False：用户必须从前端「开始页」手动点「新游戏」或「读档」启动仿真，
    # 避免后端起步时悄悄运行一个旧世界。需要 CI 自动启动仿真时显式置 True。
    #
    # 历史 bug：仅在「DB 中没有 simulation 行」的首次 seed 时生效；后续重启
    # 容器，``_load_state`` 直接复用 DB 里残留的 ``status='running'``，让本配置
    # 等价于被绕过。修复后（2026-05），lifespan 启动时**所有分支**都会按本字段
    # 收敛 status：False → ``paused``，True → ``running``。详见
    # ``docs/开发手册/debug/deployment-retrospective-2026-05.md`` §8。
    simulation_autostart: bool = Field(default=False)
    # 冷启动时是否清空 RQ 队列里残留的「待跑/进行中」任务。
    #
    # ``redis_data`` 是命名卷持久化，``vt_redis`` 容器重启后 RQ ``vt:default``
    # 队列里上一进程入队的 jobs 仍然可见；新 worker 起来会**继续消费**这些
    # 引用了上一进程内存（trace_id / simulation_step / agent 状态）的过期任务，
    # 既浪费 LLM 配额，也制造 ``failed`` 指标雪崩。冷启动一次性清队列 +
    # 把 PG ``tasks`` 表里 ``pending`` / ``running`` 收敛到 ``cancelled``，
    # 保留 ``succeeded`` / ``failed`` 历史指标，可观测性更稳定。
    #
    # 默认 True；只在你**确实**希望「重启后接着跑老任务」时（极少见）设为 False。
    simulation_reset_queue_on_start: bool = Field(default=True)
    # 所有「游戏内」WebSocket（/ws/simulations/{id}，前端 Town 场景）断开后，
    # 是否在等待若干秒仍无人重连时自动暂停仿真。
    #
    # 观测台 /ws/observability 不计入：避免研发开着 Dashboard 仍被当成「无人在线」。
    # 暂停后引擎不再 _tick()，也就不会继续往 RQ 入队新决策；队列里已有 backlog
    # 仍可能由 worker 短暂消费完，属正常现象。
    simulation_auto_pause_when_no_game_ws_clients: bool = Field(default=True)
    # 最后一个游戏 WS 断开到真正执行 pause 的延迟（秒）。给 Tab 刷新 / 短断网留缓冲，
    # 避免一断就连暂停。
    simulation_auto_pause_after_idle_seconds: float = Field(default=45.0, ge=0.0, le=3600.0)
    # 异步任务队列（阶段 12 — Redis + RQ）
    # - async: 投递到 RQ 队列，由独立 worker 进程消费（默认；见 docker-compose `worker` service）
    # - sync:  保留原先 inline 的 LLM 决策路径，便于 e2e 与回归
    task_queue_mode: Literal["async", "sync"] = Field(default="async")
    # 后端实现：MVP 选型为 rq（规划文档 §12、事件系统与任务调度方案 §2）。
    # 后续如需切 Celery，只需实现 ``app.domain.tasks.queue`` 的 enqueue/runner 对接，
    # 保持 TaskRegistry/TaskHandler 不动。
    task_queue_backend: Literal["rq"] = Field(default="rq")
    # RQ 队列名（按优先级切成多个队列，worker 可同时监听）
    task_queue_default: str = Field(default="vt:default")
    task_queue_high: str = Field(default="vt:high")
    task_queue_low: str = Field(default="vt:low")
    # worker 进程数量（容器副本数，扩展时调 ``docker compose up --scale worker=N``）。
    # 单容器内的并发槽位见 ``task_queue_worker_concurrency``。
    task_queue_worker_count: int = Field(default=1)
    # 单 worker 容器内的并发槽位数（阶段 21+：自定义 async 并发模型）。
    # 22 NPC 的 ``agent_decision`` 以 LLM I/O 为主；并发槽≈吞吐上限（≈ slots / p95_latency）。
    #
    # 调参约束：单进程 ``pool_size + max_overflow``（见 ``app/db/session.py``）须 ≥
    # ``本字段 × (1–2 条并发 DB session / 任务)``。**多副本 worker**（``docker compose
    # --scale worker=N``）时，每个副本各有独立连接池 —— 总峰值连接数近似
    # ``N × 池上限 + backend 进程``，勿超过 Postgres ``max_connections``；不足时
    # 应**先减本字段或单池上限**，再扩副本。
    task_queue_worker_concurrency: int = Field(default=18, ge=1, le=64)
    # 兼容旧字段名（.env 中可能还写着 TASK_QUEUE_CONCURRENCY）
    task_queue_concurrency: int = Field(default=2)
    # ``agent_decision`` 任务在队列中等待的 deadline（秒）。
    # 略宽裕可减少队列淤积时的无谓超时丢弃；过大则会浪费 LLM 在已过仿真步的任务上。
    agent_decision_deadline_seconds: float = Field(default=105.0, ge=10.0, le=600.0)
    # 观测批量落库：略增大间隔/批量，降低高 tick 负载下观测写库频率（Dashboard 延迟 +~0.5s 可接受）。
    observability_flush_interval: float = Field(default=2.0, ge=0.2, le=30.0)
    observability_batch_size: int = Field(default=280, ge=20, le=5000)
    # 观测内存缓冲上限（条数_bucket），四项 deque 共用丢弃策略见 ``Observer._Buffer.drop_overflow``。
    observability_buffer_max_size: int = Field(default=4000, ge=500, le=50000)

    # 阶段 18：安全 / 内容安全
    # 每个玩家在 security_rate_window_seconds 秒内最多发送
    # security_player_rate_limit 条 talk / interact
    security_player_rate_limit: int = Field(default=1)
    security_rate_window_seconds: float = Field(default=1.0)
    # 公共 API 每 IP 的软限流（每秒）
    security_ip_rate_limit: int = Field(default=20)
    security_ip_rate_window_seconds: float = Field(default=1.0)
    # 玩家单条消息最长字符数（超出服务端直接截断并拒绝）
    security_max_message_length: int = Field(default=500)
    # 工具越权告警阈值（Redis 滑窗内累计 deny 次数）
    security_tool_violation_threshold: int = Field(default=5)
    security_tool_violation_window_seconds: float = Field(default=300.0)

    # 阶段 19：社会化升级（请求-同意-拒绝 + NPC-NPC 自主社交）
    # 当 social_need ≥ 该阈值（0..100 标度，对应 AgentState.social_need * 100）时，
    # NPC 决策会优先尝试发起社交请求。
    # 阶段 19+++ 调参：55 → 40，让 NPC 更早觉得"该社交了"。
    social_need_trigger_threshold: int = Field(default=40)
    # NPC-NPC 对话最多轮次（每轮包含 A 说 + B 回，每方最多 10 次发言）
    npc_dialog_max_turns: int = Field(default=10)
    # 请求 pending 超时（秒）：超过该时长 target 没响应 → 自动 expired
    interaction_request_timeout_seconds: float = Field(default=30.0)
    # 被同一对方连续硬拒绝 N 次后，对该对方触发冷却（仿真分钟）
    social_cooldown_after_refusal_minutes: int = Field(default=30)
    # 被陌生人请求时的硬拒绝熟悉度阈值（0..1 标度，对应 Relationship.familiarity）
    interaction_stranger_familiarity_threshold: float = Field(default=0.2)
    # 高优先级任务的紧迫度阈值（≥ 此值时硬拒绝其他请求）
    interaction_high_priority_threshold: int = Field(default=8)

    # 阶段 19++：世界事件 → 记忆投影
    # 设为 False 可彻底关闭事件→记忆投影（如压测时减负）
    memory_projection_enabled: bool = Field(default=True)
    # 不在白名单内的事件，importance ≥ 此阈值才投影成记忆
    # 阶段 20：默认 4 → 2，让所有有明确归属的事件都至少进 working scope（30 分钟）。
    # 想完全不投影的事件请加进 _BLOCK_EVENT_TYPES 黑名单。
    memory_projection_default_min_importance: int = Field(default=2)
    # 同一 (actor, event_type, target) 组合的默认去重窗口（仿真分钟）
    memory_projection_default_debounce_minutes: int = Field(default=30)

    # 阶段 20：记忆系统重构
    # consolidation worker 是否启用：每仿真日按主题合并低 importance archived 记忆
    memory_consolidation_enabled: bool = Field(default=True)
    # 单个主题桶 ≥ 此条数才触发合并（避免太碎的桶产出无价值 summary）
    memory_consolidation_min_bucket_size: int = Field(default=3)
    # 每次合并扫描的"过去 N 仿真天"窗口
    memory_consolidation_lookback_days: int = Field(default=7)
    # rumination worker 是否启用：每个 NPC 24 仿真小时回顾重要记忆
    memory_rumination_enabled: bool = Field(default=True)
    # 触发沉思的最小重要度阈值（≥ 此值的长期记忆才会被回顾）
    memory_rumination_importance_threshold: int = Field(default=7)
    # 单次沉思最多抽取多少条候选记忆
    memory_rumination_sample_size: int = Field(default=5)
    # 对话中每条消息是否都为参与者写一条 chat 记忆（关掉则只在 wrap_up 写一条）
    memory_dialogue_per_message: bool = Field(default=True)

    # 阶段 19+：基础需求自然演化（每仿真分钟的增量；0 表示不演化）
    # 阶段 19+++ 调参：0.0010 → 0.0030（3x），新阈值 0.40 ≈ 130 仿真分钟
    # 即从早 8 点不社交到下午 10 点会强烈想找人，与现实节奏接近。
    needs_social_growth_per_minute: float = Field(default=0.0030)
    # hunger / energy 也跟随时间演化，让 have_meal / rest_at 也有自然驱动力
    # 阶段 19+++ 调参：0.0008 → 0.0020，让 NPC 一天大约 3-4 次有明显饥饿感
    needs_hunger_growth_per_minute: float = Field(default=0.0020)
    # 阶段 19+++ 调参：0.0005 → 0.0015，让 NPC 一天会想休息几次
    needs_energy_decay_per_minute: float = Field(default=0.0015)
    # 单次社交完成后社交需求衰减比例（last_social_at 更新时一次性扣）
    needs_social_decay_after_chat: float = Field(default=0.55)
    # 引擎层"自主社交邂逅"扫描间隔（仿真分钟）：两个空闲 NPC 走得近 + 一方
    # social_need 高时，自动触发 request_interaction
    # 阶段 19+++ 调参：5 → 2，让自然邂逅出现得更频繁
    social_encounter_scan_minutes: int = Field(default=2)
    # 邂逅触发的最大曼哈顿距离
    # 阶段 19+++ 调参：3 → 5，覆盖更大的"擦肩而过"范围
    social_encounter_distance: int = Field(default=5)
    # 邂逅触发后该 NPC 在多少仿真分钟内不再主动邂逅别人
    # 阶段 19+++ 调参：20 → 10，避免冷却期过长冷场
    social_encounter_cooldown_minutes: int = Field(default=10)

    @field_validator("backend_cors_origins")
    @classmethod
    def _strip_cors(cls, value: str) -> str:
        return value.strip()

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS 白名单列表。"""
        raw = self.backend_cors_origins
        if not raw:
            return []
        if raw.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def llm_is_configured(self) -> bool:
        """第三方 API 是否已配置可用（llm_enabled=true 且有 API Key）。"""
        return self.llm_enabled and bool(self.llm_api_key)

    @property
    def ollama_is_configured(self) -> bool:
        """Ollama 本地模型是否已配置可用。"""
        return getattr(self, "ollama_enabled", False) and bool(
            getattr(self, "ollama_base_url", "")
        )

    @property
    def any_llm_configured(self) -> bool:
        """是否有任意 LLM Provider 可用（第三方 API 或 Ollama 任一即可）。"""
        return self.llm_is_configured or self.ollama_is_configured

    def model_for_role(self, role: str) -> str:
        """按任务角色选择模型。未配置则回退到 chat / reasoning 默认。

        角色定义（阶段 17 §5）：
        - ``chat`` / ``dialogue`` / ``query_rewrite`` / ``intent``：日常对话与短任务 → chat
        - ``reasoning`` / ``reflection`` / ``planning`` / ``daily_plan``：长链推理 → reasoning
        - ``embedding``：文本向量 → embedding
        """
        role = (role or "chat").lower()
        if role == "embedding":
            return self.llm_embedding_model
        if role in {"dialogue", "chat"}:
            return self.llm_dialogue_model or self.llm_chat_model
        if role in {"query_rewrite", "intent"}:
            return self.llm_query_rewrite_model or self.llm_chat_model
        if role in {"planning", "daily_plan", "hourly_schedule", "task_decomposition"}:
            return self.llm_planning_model or self.llm_reasoning_model
        if role in {"reasoning", "reflection", "daily_summary"}:
            return self.llm_reasoning_model
        return self.llm_chat_model

    @property
    def is_prod(self) -> bool:
        """是否生产环境（决定错误响应是否脱敏）。"""
        return self.app_env == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回单例配置，避免重复解析 `.env`。"""
    return Settings()
