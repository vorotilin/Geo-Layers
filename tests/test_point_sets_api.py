"""Ручки /v1/point-sets: создание, пополнение, список, тепловая карта."""

import io
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.auth import hash_token
from app.config import Settings
from app.main import create_app
from app.tiles.heatmap import TMS

WRITE = {"Authorization": "Bearer write-t"}
READ = {"Authorization": "Bearer read-t"}


@pytest.fixture
def client(db_url: str, db: psycopg.Connection, tmp_path: Path) -> Iterator[TestClient]:
    for token, scope in [("write-t", "write"), ("read-t", "read")]:
        db.execute(
            "INSERT INTO token (hash, title, scopes, namespace_id) VALUES (%s, %s, %s, 'shared')",
            (hash_token(token), token, [scope]),
        )
    settings = Settings(database_url=db_url, data_dir=str(tmp_path / "data"))
    with TestClient(create_app(settings)) as c:
        yield c


def test_create_list_append(client: TestClient) -> None:
    csv = "id;name;category;lat;lng;seats\n1;A;cafe;55.75;37.62;24\n2;B;shop;55.76;37.63;10\n"
    resp = client.post(
        "/v1/point-sets",
        files={"file": ("p.csv", csv.encode(), "text/csv")},
        data={
            "title": "Кафе",
            "lon": "lng",
            "lat": "lat",
            "weight": "seats",
            "category": "category",
        },
        headers=WRITE,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["count"] == 2 and body["errors"] == 0 and body["version_no"] == 1
    set_id = body["id"]

    items = client.get("/v1/point-sets", headers=READ).json()["items"]
    assert len(items) == 1 and items[0]["title"] == "Кафе" and items[0]["versions"] == 1

    resp2 = client.post(
        f"/v1/point-sets/{set_id}/versions",
        files={"file": ("p2.csv", b"lat,lng\n55.77,37.64\n", "text/csv")},
        headers=WRITE,
    )
    assert resp2.status_code == 201 and resp2.json()["version_no"] == 2

    items = client.get("/v1/point-sets", headers=READ).json()["items"]
    assert items[0]["versions"] == 2 and items[0]["latest_version_no"] == 2


def test_bad_file_and_auth(client: TestClient) -> None:
    resp = client.post(
        "/v1/point-sets",
        files={"file": ("e.csv", b"lat,lng\n", "text/csv")},
        data={"title": "x"},
        headers=WRITE,
    )
    assert resp.status_code == 422 and resp.json()["error"] == "no_points"

    resp = client.post(
        "/v1/point-sets",
        files={"file": ("p.csv", b"lat,lng\n55.0,37.0\n", "text/csv")},
        data={"title": "x"},
        headers=READ,
    )
    assert resp.status_code == 403


def test_heatmap(client: TestClient) -> None:
    csv = "lat,lng\n0.0,0.0\n0.0,0.001\n"
    ps = client.post(
        "/v1/point-sets",
        files={"file": ("p.csv", csv.encode(), "text/csv")},
        data={"title": "Т"},
        headers=WRITE,
    )
    set_id = ps.json()["id"]
    t = TMS["3857"].tile(0.0, 0.0, 8)
    resp = client.get(
        f"/v1/point-sets/{set_id}/heatmap/3857/{t.z}/{t.x}/{t.y}.png?radius=20", headers=READ
    )
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
    img = np.asarray(Image.open(io.BytesIO(resp.content)))
    assert img.shape[:2] == (256, 256) and img.max() > 0  # есть непрозрачные пиксели
