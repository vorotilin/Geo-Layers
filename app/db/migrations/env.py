"""Окружение Alembic. Миграции пишем вручную (SQL), автогенерации нет."""

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings
from app.db import sqlalchemy_url

config = context.config


def run_migrations_online() -> None:
    # app/migrate.py передаёт своё соединение (под advisory lock); CLI — создаёт сам.
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(sqlalchemy_url(get_settings().database_url), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline-режим не поддерживается")
run_migrations_online()
