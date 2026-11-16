"""Ручки /v1/styles: CRUD, ревизии, резолвинг (design §7, brief §5)."""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api.styles import resolve_style
from app.auth import hash_token
from app.config import Settings
from app.main import create_app

READ = {"Authorization": "Bearer read-t"}
WRITE = {"Authorization": "Bearer write-t"}


@pytest.fixture
def client(db_url: str, db: psycopg.Connection) -> Iterator[TestClient]:
    for token, scope in [("read-t", "read"), ("write-t", "write")]:
        db.execute(
            "INSERT INTO token (hash, title, scopes, namespace_id) VALUES (%s, %s, %s, 'shared')",
            (hash_token(token), token, [scope]),
        )
    with TestClient(create_app(Settings(database_url=db_url))) as c:
        yield c


def test_create_list_update_style(client: TestClient) -> None:
    spec = {"palette": "blues", "classes": 3, "method": "quantile"}
    created = client.post("/v1/styles", json={"name": "blue3", "spec": spec}, headers=WRITE)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "blue3" and body["revision"] == 1 and body["spec"]["classes"] == 3

    items = client.get("/v1/styles", headers=READ).json()["items"]
    assert [i["name"] for i in items] == ["blue3"]

    updated = client.put(
        f"/v1/styles/{body['id']}",
        json={"spec": {"palette": "greens", "classes": 5}},
        headers=WRITE,
    )
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    items = client.get("/v1/styles", headers=READ).json()["items"]
    assert items[0]["revision"] == 2 and items[0]["spec"]["palette"] == "greens"


def test_style_validation(client: TestClient) -> None:
    assert (
        client.post("/v1/styles", json={"name": "Bad Name", "spec": {}}, headers=WRITE).status_code
        == 422
    )
    resp = client.post(
        "/v1/styles", json={"name": "ok", "spec": {"palette": "nope"}}, headers=WRITE
    )
    assert resp.status_code == 422 and resp.json()["error"] == "style_invalid"


def test_resolve_style(db: psycopg.Connection) -> None:
    db.execute(
        "INSERT INTO style (namespace_id, name, spec, revision) VALUES ('shared', 'a', '{}', 1)"
    )
    style_obj, ref = resolve_style(db, "shared", "default")
    assert ref == "default" and style_obj is not None  # встроенный
    style_obj, ref = resolve_style(db, "shared", "a", latest=True)
    assert ref == "a@1" and style_obj is not None
    style_obj, ref = resolve_style(db, "shared", "a@1")
    assert ref == "a@1"
    style_obj, ref = resolve_style(db, "shared", "a")  # строго: без ревизии — не резолвим
    assert style_obj is None and ref is None
    style_obj, ref = resolve_style(db, "shared", "missing", latest=True)
    assert style_obj is None and ref is None


def test_legend_layer_not_found(client: TestClient) -> None:
    resp = client.get("/v1/layers/00000000-0000-0000-0000-000000000000/legend", headers=READ)
    assert resp.status_code == 404 and resp.json()["error"] == "layer_not_found"
