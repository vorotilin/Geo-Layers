"""Построение слоя из точек: биннинг → публикация → значение в точке."""

import json
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token
from app.config import Settings
from app.main import create_app

WRITE = {"Authorization": "Bearer write-t"}


@pytest.fixture
def client(db_url: str, db: psycopg.Connection, tmp_path: Path) -> Iterator[TestClient]:
    db.execute(
        "INSERT INTO token (hash, title, scopes, namespace_id) "
        "VALUES (%s, 'w', '{write}', 'shared')",
        (hash_token("write-t"),),
    )
    settings = Settings(
        database_url=db_url, data_dir=str(tmp_path / "data"), cache_dir=str(tmp_path / "cache")
    )
    with TestClient(create_app(settings)) as c:
        yield c


def _create_set(client: TestClient) -> str:
    csv = "lat,lng\n0.0,0.0\n0.0,0.001\n"
    resp = client.post(
        "/v1/point-sets",
        files={"file": ("p.csv", csv.encode(), "text/csv")},
        data={"title": "Точки"},
        headers=WRITE,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_build_bin_and_value(client: TestClient) -> None:
    set_id = _create_set(client)
    resp = client.post(
        "/v1/layers/build",
        json={
            "point_set_id": set_id,
            "method": "bin",
            "slug": "bins",
            "title": "Бины",
            "params": {"step": 1000, "agg": "count"},
        },
        headers=WRITE,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "draft" and body["value_kind"] == "extensive"
    layer_id, version_id = body["layer_id"], body["version_id"]

    pub = client.post(f"/v1/layers/{layer_id}/versions/{version_id}/publish", headers=WRITE)
    assert pub.status_code == 200 and pub.json()["status"] == "published"

    # Две точки в одной ячейке 1000 м → count = 2.
    val = client.get(
        "/v1/values", params={"lat": 0.0, "lng": 0.0, "layers": layer_id}, headers=WRITE
    ).json()
    assert val["values"][0]["value"] == 2


def test_build_kde(client: TestClient) -> None:
    set_id = _create_set(client)
    resp = client.post(
        "/v1/layers/build",
        json={
            "point_set_id": set_id,
            "method": "kde",
            "slug": "heat",
            "title": "Тепловая",
            "params": {"bandwidth_m": 200, "step": 50},
        },
        headers=WRITE,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["value_kind"] == "intensive"
    layer_id, version_id = body["layer_id"], body["version_id"]

    pub = client.post(f"/v1/layers/{layer_id}/versions/{version_id}/publish", headers=WRITE)
    assert pub.status_code == 200

    val = client.get(
        "/v1/values", params={"lat": 0.0, "lng": 0.0, "layers": layer_id}, headers=WRITE
    ).json()
    assert val["values"][0]["value"] > 0  # плотность в точке положительна


def test_build_choropleth(client: TestClient) -> None:
    doc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"zone_id": 1, "val": 0},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"zone_id": 2, "val": 0},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[2, 2], [2, 3], [3, 3], [3, 2], [2, 2]]],
                },
            },
        ],
    }
    pr = client.post(
        "/v1/polygons",
        files={"file": ("p.geojson", json.dumps(doc).encode(), "application/json")},
        data={"title": "Районы", "provider": "test", "period": "2024Q1", "slug": "districts"},
        headers=WRITE,
    )
    assert pr.status_code == 201, pr.text
    grid_id = pr.json()["grid_id"]

    csv = "lat,lng\n0.5,0.5\n0.6,0.6\n2.5,2.5\n"
    ps = client.post(
        "/v1/point-sets",
        files={"file": ("pts.csv", csv.encode(), "text/csv")},
        data={"title": "Точки"},
        headers=WRITE,
    )
    set_id = ps.json()["id"]

    resp = client.post(
        "/v1/layers/build",
        json={
            "point_set_id": set_id,
            "method": "choropleth",
            "grid_id": grid_id,
            "slug": "cafes",
            "title": "Кафе по районам",
            "params": {"agg": "count", "normalize": "none"},
        },
        headers=WRITE,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["value_kind"] == "extensive"
    layer_id, version_id = body["layer_id"], body["version_id"]

    pub = client.post(f"/v1/layers/{layer_id}/versions/{version_id}/publish", headers=WRITE)
    assert pub.status_code == 200

    for lng, lat, expected in [(0.5, 0.5, 2), (2.5, 2.5, 1)]:
        val = client.get(
            "/v1/values", params={"lat": lat, "lng": lng, "layers": layer_id}, headers=WRITE
        ).json()
        assert val["values"][0]["value"] == expected


def test_build_h3(client: TestClient) -> None:
    csv = "lat,lng\n0.0,0.0\n0.0,0.0001\n"
    ps = client.post(
        "/v1/point-sets",
        files={"file": ("p.csv", csv.encode(), "text/csv")},
        data={"title": "Точки"},
        headers=WRITE,
    )
    set_id = ps.json()["id"]
    resp = client.post(
        "/v1/layers/build",
        json={
            "point_set_id": set_id,
            "method": "h3",
            "slug": "h3",
            "title": "H3",
            "params": {"resolution": 9, "agg": "count"},
        },
        headers=WRITE,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["cells"] >= 1
    layer_id, version_id = body["layer_id"], body["version_id"]

    pub = client.post(f"/v1/layers/{layer_id}/versions/{version_id}/publish", headers=WRITE)
    assert pub.status_code == 200
    val = client.get(
        "/v1/values", params={"lat": 0.0, "lng": 0.0, "layers": layer_id}, headers=WRITE
    ).json()
    assert val["values"][0]["value"] == 2
