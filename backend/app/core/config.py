"""
应用配置。

- 所有配置从环境变量 / .env 读取。
- `.env` 文件仅用于本地开发，生产环境必须由部署系统注入环境变量。
- 密钥类字段不在日志中输出。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn, field_validator
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

    # 仿真
    simulation_world_tick_hz: float = Field(default=5.0)
    simulation_ai_tick_minutes: int = Field(default=5)
    simulation_speed_default: float = Field(default=1.0)
    simulation_autostart: bool = Field(default=True)

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
        """是否已经具备调用外部 LLM 的最低条件。"""
        return self.llm_enabled and bool(self.llm_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回单例配置，避免重复解析 `.env`。"""
    return Settings()
