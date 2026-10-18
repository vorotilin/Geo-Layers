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
    # Соединений в пуле на один процесс api.
    db_pool_size: int = 5

    # Admin-токен, создаётся при пустой таблице токенов (app/migrate.py).
    bootstrap_admin_token: str | None = None

    # Постоянное хранилище файлов и массивов значений. На M1 — локальный диск
    # (общий volume api и worker); S3 — с M2 (docs/plan-m1.md).
    data_dir: str = "/var/lib/apc-layers"
    cache_dir: str = "/var/cache/apc-layers"

    # Очередь заданий (ADR-0003).
    # Как часто воркер продлевает аренду задания и проверяет отмену, секунды.
    job_heartbeat_seconds: float = 10
    # Задание без heartbeat дольше этого — воркер считается упавшим, задание повторяется.
    job_stale_seconds: float = 60
    # Задержка повтора после ошибки: base, 4·base, 16·base … не больше max.
    job_retry_base_seconds: float = 10
    job_retry_max_seconds: float = 3600
    # Как часто воркер опрашивает очередь, если не пришёл NOTIFY, секунды.
    worker_poll_seconds: float = 5

    # Лимиты (common-requirements §4).
    max_upload_bytes: int = 4 * 1024**3
    max_job_duration_seconds: int = 3600
    parser_memory_limit_mb: int = 1024

    # Подписанные ссылки на тайлы (ADR-0008).
    url_signing_secret: str | None = None
    url_signing_secret_previous: str | None = None
    signed_url_ttl_seconds: int = 86400
    sign_exp_rounding_seconds: int = 3600

    # Собранная статика веб-интерфейса (ui/dist), раздаётся по /ui/. Нет каталога — нет /ui.
    ui_dir: str = "/srv/ui"

    # Сколько секунд ждать каждую зависимость в /ready.
    ready_timeout_seconds: float = 2.0

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
