"""Ручки /v1/tokens: выпуск, список, отзыв, доступ по scope и namespace."""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token
from app.config import Settings
from app.main import create_app

ADMIN = {"Authorization": "Bearer admin-t"}
WRITE = {"Authorization": "Bearer write-t"}


@pytest.fixture
def client(db_url: str, db: psycopg.Connection) -> Iterator[TestClient]:
    db.execute("INSERT INTO namespace (id, title) VALUES ('other', 'Чужой')")
    for token, scope, ns in [
        ("admin-t", "admin", "shared"),
        ("write-t", "write", "shared"),
    ]:
        db.execute(
            "INSERT INTO token (hash, title, scopes, namespace_id) VALUES (%s, %s, %s, %s)",
            (hash_token(token), token, [scope], ns),
        )
    with TestClient(create_app(Settings(database_url=db_url))) as c:
        yield c


def test_create_list_revoke(client: TestClient) -> None:
    created = client.post(
        "/v1/tokens", json={"title": "read-only", "scopes": ["read"]}, headers=ADMIN
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["scopes"] == ["read"] and body["revoked_at"] is None and "token" in body

    items = client.get("/v1/tokens", headers=ADMIN).json()["items"]
    assert [i["title"] for i in items] == ["admin-t", "write-t", "read-only"]
    assert all("token" not in i and "hash" not in i for i in items)  # секреты не отдаём

    # Выпущенный токен работает.
    assert (
        client.get("/v1/layers", headers={"Authorization": f"Bearer {body['token']}"}).status_code
        == 200
    )

    revoked = client.delete(f"/v1/tokens/{body['id']}", headers=ADMIN)
    assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None
    # Отозванный токен больше не действует.
    assert (
        client.get("/v1/layers", headers={"Authorization": f"Bearer {body['token']}"}).status_code
        == 401
    )


def test_invalid_scopes_and_access(client: TestClient) -> None:
    resp = client.post("/v1/tokens", json={"title": "x", "scopes": ["root"]}, headers=ADMIN)
    assert resp.status_code == 422 and resp.json()["error"] == "token_invalid"
    # write-токену выпуск токенов запрещён.
    assert (
        client.post(
            "/v1/tokens", json={"title": "x", "scopes": ["read"]}, headers=WRITE
        ).status_code
        == 403
    )


def test_revoke_missing_and_foreign(client: TestClient, db: psycopg.Connection) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.delete(f"/v1/tokens/{missing}", headers=ADMIN).status_code == 404
    # Токен чужого namespace не виден и не отзывается.
    db.execute(
        "INSERT INTO token (hash, title, scopes, namespace_id) "
        "VALUES (%s, 'foreign', '{read}', 'other')",
        (hash_token("foreign-t"),),
    )
    row = db.execute("SELECT id FROM token WHERE title = 'foreign'").fetchone()
    assert row is not None
    foreign = row[0]
    assert client.delete(f"/v1/tokens/{foreign}", headers=ADMIN).status_code == 404
