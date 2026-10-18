"""Общие фикстуры. Тесты с БД нужен PostGIS: TEST_DATABASE_URL, иначе пропускаются.

Внимание: схема `public` тестовой базы пересоздаётся — не указывайте рабочую базу.
"""

import os
from collections.abc import Iterator

import psycopg
import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def reset_schema(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")


@pytest.fixture
def empty_db() -> str:
    """Пустая база (без таблиц)."""
    if not TEST_DATABASE_URL:
        pytest.skip("нужен TEST_DATABASE_URL с PostGIS")
    reset_schema(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture
def db_url(empty_db: str) -> str:
    """База со схемой до head."""
    from app.migrate import upgrade_database

    upgrade_database(empty_db)
    return empty_db


@pytest.fixture
def db(db_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(db_url, autocommit=True) as conn:
        yield conn
