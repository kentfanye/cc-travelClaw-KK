"""
统一环境配置 — Pydantic BaseSettings。
从环境变量和.env文件读取。
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """TravelClaw全局配置"""

    # API密钥
    api_key: str = ""
    api_base: str = "https://api.z.ai/api/coding/paas/v4"
    model_name: str = "glm-5.1"

    # 数据库
    database_url: str = "sqlite+aiosqlite:///travelclaw.db"

    # 日志
    log_level: str = "INFO"
    log_format: str = "text"  # text / json

    # 调度
    max_concurrent_agents: int = 1  # GLM-5.1免费API限流严格，默认串行
    retry_max_attempts: int = 5
    retry_base_delay: float = 2.0

    # API服务
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "*"

    # 团队配置
    config_path: str = "config.yaml"

    model_config = {"env_prefix": "TRAVELCLAW_", "env_file": ".env", "extra": "ignore"}


# 全局单例
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings():
    """重置（用于测试）"""
    global _settings
    _settings = None
