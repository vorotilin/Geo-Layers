"""Конфигурация — только из переменных окружения (common-requirements §4).

Все переменные описаны в `.env.example`. Пустое значение = значение по умолчанию.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore")

    app_env: Literal["dev", "prod"] = "prod"
    app_version: str = "dev"
    log_level: Literal["debug", "info", "warn", "error"] = "info"
    http_port: int = 8000
    api_workers: int = 2
    public_base_url: str = "http://localhost:8000"
    cors_origins: str = ""

    database_url: str = "postgresql://apc:apc@localhost:5432/apc"

    # Постоянное хранилище файлов и массивов значений. На M1 — локальный диск
    # (общий volume api и worker); S3 — с M2 (docs/plan-m1.md).
    data_dir: str = "/var/lib/apc-layers"
    cache_dir: str = "/var/cache/apc-layers"

    # Сколько секунд ждать каждую зависимость в /ready.
    ready_timeout_seconds: float = 2.0

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
