"""Миграции, bootstrap-токен, проверка Bearer-токенов."""

from pathlib import Path

import psycopg
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.auth import (
    AdminPrincipal,
    Principal,
    ReadPrincipal,
    ensure_bootstrap_token,
    find_principal,
    hash_token,
)
from app.config import Settings
from app.db import sqlalchemy_url
from app.main import create_app
from app.migrate import alembic_config, migrate, upgrade_database

TABLES = {
    "namespace",
    "token",
    "file",
    "job",
    "import",
    "dataset",
    "grid",
    "grid_cell",
    "layer",
    "layer_version",
}


def test_sqlalchemy_url() -> None:
    assert sqlalchemy_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert sqlalchemy_url("postgres://u@h/db") == "postgresql+psycopg://u@h/db"


def test_single_head() -> None:
    from alembic.script import ScriptDirectory

    assert len(ScriptDirectory.from_config(alembic_config()).get_heads()) == 1


def test_scopes_imply() -> None:
    admin = Principal("t", "shared", frozenset({"admin"}))
    read = Principal("t", "shared", frozenset({"read"}))
    assert admin.can("write") and admin.can("read")
    assert read.can("read") and not read.can("write")


def _tables(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
    return {r[0] for r in rows}


def test_upgrade_is_idempotent_and_downgrade_clean(empty_db: str) -> None:
    from alembic import command
    from sqlalchemy import create_engine, pool

    upgrade_database(empty_db)
    upgrade_database(empty_db)  # повторный запуск — без ошибок
    with psycopg.connect(empty_db) as conn:
        assert _tables(conn) >= TABLES

    cfg = alembic_config()
    engine = create_engine(sqlalchemy_url(empty_db), poolclass=pool.NullPool)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
    with psycopg.connect(empty_db) as conn:
        assert not (TABLES & _tables(conn))
    upgrade_database(empty_db)


def test_bootstrap_only_when_empty(db: psycopg.Connection) -> None:
    assert ensure_bootstrap_token(db, None) is False
    assert ensure_bootstrap_token(db, "first") is True
    assert ensure_bootstrap_token(db, "second") is False
    principal = find_principal(db, "first")
    assert principal is not None
    assert principal.namespace_id == "shared"
    assert principal.scopes == {"admin"}
    assert find_principal(db, "second") is None


def test_migrate_command(empty_db: str, tmp_path: Path) -> None:
    settings = Settings(
        database_url=empty_db,
        data_dir=str(tmp_path / "data"),
        cache_dir=str(tmp_path / "cache"),
        bootstrap_admin_token="boot",
    )
    migrate(settings)
    migrate(settings)
    with psycopg.connect(empty_db) as conn:
        assert conn.execute("SELECT count(*) FROM token").fetchone() == (1,)


def test_constraints(db: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO job (type, status, namespace_id) VALUES ('x', 'weird', 'shared')")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO token (hash, title, scopes, namespace_id) VALUES (%s, 't', %s, 'shared')",
            (hash_token("x"), ["root"]),
        )


def test_grid_cell_geometry(db: psycopg.Connection) -> None:
    grid_id = db.execute(
        "INSERT INTO grid (namespace_id, provider, kind, crs) "
        "VALUES ('shared', 'acme', 'rectilinear', 'OGC:CRS84') RETURNING id"
    ).fetchone()[0]  # type: ignore[index]
    db.execute(
        "INSERT INTO grid_cell (grid_id, idx, key, geom) "
        "VALUES (%s, 0, 'z0', ST_MakeEnvelope(37.0, 55.0, 37.01, 55.01, 4326))",
        (grid_id,),
    )
    hit = db.execute(
        "SELECT idx FROM grid_cell WHERE grid_id = %s "
        "AND ST_Intersects(geom, ST_SetSRID(ST_Point(37.005, 55.005), 4326))",
        (grid_id,),
    ).fetchall()
    assert hit == [(0,)]


# --- Bearer-токены в HTTP ---

probe = APIRouter()


@probe.get("/v1/_probe/read")
def _read(p: ReadPrincipal) -> dict[str, str]:
    return {"namespace": p.namespace_id}


@probe.get("/v1/_probe/admin")
def _admin(p: AdminPrincipal) -> dict[str, str]:
    return {"token": p.token_id}


@pytest.fixture
def http(db_url: str, db: psycopg.Connection) -> TestClient:
    ensure_bootstrap_token(db, "admin-secret")
    db.execute(
        "INSERT INTO token (hash, title, scopes, namespace_id) VALUES (%s, 'r', %s, 'shared')",
        (hash_token("read-secret"), ["read"]),
    )
    db.execute(
        "INSERT INTO token (hash, title, scopes, namespace_id, revoked_at) "
        "VALUES (%s, 'old', %s, 'shared', now())",
        (hash_token("revoked-secret"), ["admin"]),
    )
    app = create_app(Settings(database_url=db_url))
    app.include_router(probe)
    return TestClient(app)


@pytest.mark.parametrize(
    ("header", "path", "status"),
    [
        (None, "/v1/_probe/read", 401),
        ("Basic abc", "/v1/_probe/read", 401),
        ("Bearer wrong", "/v1/_probe/read", 401),
        ("Bearer revoked-secret", "/v1/_probe/read", 401),
        ("Bearer read-secret", "/v1/_probe/read", 200),
        ("Bearer read-secret", "/v1/_probe/admin", 403),
        ("Bearer admin-secret", "/v1/_probe/read", 200),
        ("bearer admin-secret", "/v1/_probe/admin", 200),
    ],
)
def test_bearer(http: TestClient, header: str | None, path: str, status: int) -> None:
    headers = {"Authorization": header} if header else {}
    with http as client:
        resp = client.get(path, headers=headers)
    assert resp.status_code == status, resp.text
    if status == 401:
        assert resp.json()["error"] == "unauthorized"
        assert resp.headers["WWW-Authenticate"] == "Bearer"
    if status == 403:
        assert resp.json()["error"] == "forbidden"
