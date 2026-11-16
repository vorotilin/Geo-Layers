"""Данные слоёв: пакет точек, агрегат по bbox, рейтинг."""

import json
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token
from app.config import Settings
from app.jobs.handlers import HANDLERS
from app.jobs.runner import Worker
from app.main import create_app
from tests.synth import SynthGrid, crs84_squares

WRITE = {"Authorization": "Bearer write-t"}


@pytest.fixture
def env(
    db_url: str, db: psycopg.Connection, tmp_path: Path
) -> Iterator[tuple[TestClient, SynthGrid, str]]:
    db.execute(
        "INSERT INTO token (hash, title, scopes, namespace_id) "
        "VALUES (%s, 'w', '{write}', 'shared')",
        (hash_token("write-t"),),
    )
    settings = Settings(
        database_url=db_url, data_dir=str(tmp_path / "data"), cache_dir=str(tmp_path / "cache")
    )
    with TestClient(create_app(settings)) as client:
        grid = crs84_squares(metrics=1)
        up = client.post(
            "/v1/files",
            files={"file": ("g.geojson", json.dumps(grid.geojson()).encode())},
            headers=WRITE,
        )
        imp = client.post(
            "/v1/imports",
            json={"file_id": up.json()["id"], "manifest": grid.manifest()},
            headers=WRITE,
        )
        worker = Worker(settings, HANDLERS)
        with psycopg.connect(db_url, autocommit=True) as conn:
            while worker.run_once(conn) is not None:
                pass
        job = client.get(f"/v1/jobs/{imp.json()['job_id']}", headers=WRITE).json()
        version = job["result"]["versions"][0]
        pub = client.post(
            f"/v1/layers/{version['layer_id']}/versions/{version['version_id']}/publish",
            headers=WRITE,
        )
        assert pub.status_code == 200
        yield client, grid, version["layer_id"]


def test_batch_aggregate_rating(env: tuple[TestClient, SynthGrid, str]) -> None:
    client, grid, layer_id = env
    pts = grid.random_points(3, seed=5)
    body = {
        "points": [{"id": str(i), "lat": lat, "lng": lon} for i, (lon, lat) in enumerate(pts)],
        "layers": layer_id,
    }
    resp = client.post("/v1/values/batch", json=body, headers=WRITE)
    assert resp.status_code == 200, resp.text
    items = resp.json()["values"][0]["items"]
    for p, item in zip(pts, items, strict=True):
        assert item["value"] == grid.expected(p[0], p[1], 0)

    lon0, lat0, lon1, lat1 = grid.bounds_wgs84()
    resp = client.post(
        "/v1/aggregate", json={"layers": layer_id, "bbox": [lon0, lat0, lon1, lat1]}, headers=WRITE
    )
    assert resp.status_code == 200, resp.text
    agg = resp.json()["aggregates"][0]
    assert agg["cells"] > 0 and agg["mean"] is not None and agg["min"] <= agg["max"]
    assert 0.0 <= agg["coverage"] <= 1.0

    resp = client.get(f"/v1/layers/{layer_id}/rating", params={"limit": 5}, headers=WRITE)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 5
    vals = [i["value"] for i in items]
    assert vals == sorted(vals, reverse=True)
