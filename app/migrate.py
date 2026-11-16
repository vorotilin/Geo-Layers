"""Подготовка окружения перед стартом api и воркера (`python -m app migrate`).

Каталоги хранилища, миграции Alembic до head, bootstrap admin-токен.
Безопасно запускать повторно и параллельно: всё под advisory lock.
"""

import logging
import time
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, pool, text

from app.auth import ensure_bootstrap_token
from app.config import Settings
from app.db import sqlalchemy_url
from app.logs import setup_logging
from app.storage import ensure_s3_bucket

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "db" / "migrations"
# Произвольная константа: ключ advisory lock на время миграций.
MIGRATE_LOCK_ID = 0x61706331  # "apc1"
DB_WAIT_SECONDS = 60


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def upgrade_database(database_url: str) -> None:
    cfg = alembic_config()
    engine = create_engine(sqlalchemy_url(database_url), poolclass=pool.NullPool)
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATE_LOCK_ID})
        conn.commit()
        try:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            conn.commit()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATE_LOCK_ID})
            conn.commit()
    engine.dispose()


def wait_for_database(database_url: str, timeout: float = DB_WAIT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with psycopg.connect(database_url, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            log.info("waiting for database")
            time.sleep(2)


def migrate(settings: Settings) -> None:
    setup_logging(settings.log_level)
    for path in (settings.data_dir, settings.cache_dir):
        Path(path).mkdir(parents=True, exist_ok=True)

    wait_for_database(settings.database_url)
    upgrade_database(settings.database_url)
    with psycopg.connect(settings.database_url) as conn:
        ensure_bootstrap_token(conn, settings.bootstrap_admin_token)
    ensure_s3_bucket(settings)
    log.info("migrate done")
