"""Импорт полигонов → публикация → значение в точке и тайл."""

import io
import json
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.auth import hash_token
from app.config import Settings
from app.main import create_app
from app.tiles import render

WRITE = {"Authorization": "Bearer write-t"}


def _doc() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"zone_id": 1, "val": 100},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"zone_id": 2, "val": 200},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[2, 2], [2, 3], [3, 3], [3, 2], [2, 2]]],
                },
            },
        ],
    }


@pytest.fixture
def client(db_url: str, db: psycopg.Connection, tmp_path: Path) -> Iterator[TestClient]:
    db.execute(
        "INSERT INTO token (hash, title, scopes, namespace_id) "
        "VALUES (%s, 'w', '{write}', 'shared')",
        (hash_token("write-t"),),
    )
    settings = Settings(
        database_url=db_url,
        data_dir=str(tmp_path / "data"),
        cache_dir=str(tmp_path / "cache"),
        url_signing_secret="s",
    )
    with TestClient(create_app(settings)) as c:
        yield c


def test_import_publish_value_tile(client: TestClient) -> None:
    data = {
        "title": "Районы",
        "provider": "test",
        "period": "2024Q1",
        "slug": "districts",
        "key": "zone_id",
        "column": "val",
    }
    resp = client.post(
        "/v1/polygons",
        files={"file": ("p.geojson", json.dumps(_doc()).encode(), "application/json")},
        data=data,
        headers=WRITE,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["cells"] == 2 and body["status"] == "draft"
    layer_id, version_id = body["layer_id"], body["version_id"]

    pub = client.post(f"/v1/layers/{layer_id}/versions/{version_id}/publish", headers=WRITE)
    assert pub.status_code == 200

    for lng, lat, expected in [(0.5, 0.5, 100), (2.5, 2.5, 200), (10.0, 10.0, None)]:
        val = client.get(
            "/v1/values", params={"lat": lat, "lng": lng, "layers": layer_id}, headers=WRITE
        ).json()
        assert val["values"][0]["value"] == expected

    signed = client.post(
        "/v1/tiles/sign", json={"layer": layer_id, "tms": "3857"}, headers=WRITE
    ).json()
    t = render.TMS["3857"].tile(0.5, 0.5, 8)
    url = urlsplit(signed["url_template"].format(z=t.z, x=t.x, y=t.y))
    resp = client.get(f"{url.path}?{url.query}")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
    img = np.asarray(Image.open(io.BytesIO(resp.content)))
    assert img.max() > 0  # есть данные
