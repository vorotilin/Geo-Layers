"""Доступ к PostgreSQL: строки подключения и пул соединений api."""

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import Settings

# Фиксированный namespace на M1 (docs/plan-m1.md, шаг 4); scopes и свои namespace — M2.
SHARED_NAMESPACE = "shared"


def sqlalchemy_url(database_url: str) -> str:
    """DATABASE_URL в формате libpq → URL SQLAlchemy с драйвером psycopg 3 (для Alembic)."""
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    return database_url


def create_pool(settings: Settings) -> ConnectionPool:
    """Пул для api. Не ждёт БД при старте: недоступность видна в /ready, а не падением."""
    pool = ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=settings.db_pool_size,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    pool.open(wait=False)
    return pool
