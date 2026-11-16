"""Кэш тайлов: прогрев и сброс (brief §11)."""

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

ADMIN = {"Authorization": "Bearer admin-t"}


@pytest.fixture
def env(
    db_url: str, db: psycopg.Connection, tmp_path: Path
) -> Iterator[tuple[TestClient, Settings, SynthGrid, str]]:
    db.execute(
        "INSERT INTO token (hash, title, scopes, namespace_id) "
        "VALUES (%s, 'a', '{admin}', 'shared')",
        (hash_token("admin-t"),),
    )
    settings = Settings(
        database_url=db_url, data_dir=str(tmp_path / "data"), cache_dir=str(tmp_path / "cache")
    )
    with TestClient(create_app(settings)) as client:
        grid = crs84_squares(metrics=1)
        up = client.post(
            "/v1/files",
            files={"file": ("g.geojson", json.dumps(grid.geojson()).encode())},
            headers=ADMIN,
        )
        imp = client.post(
            "/v1/imports",
            json={"file_id": up.json()["id"], "manifest": grid.manifest()},
            headers=ADMIN,
        )
        worker = Worker(settings, HANDLERS)
        with psycopg.connect(db_url, autocommit=True) as conn:
            while worker.run_once(conn) is not None:
                pass
        job = client.get(f"/v1/jobs/{imp.json()['job_id']}", headers=ADMIN).json()
        version = job["result"]["versions"][0]
        client.post(
            f"/v1/layers/{version['layer_id']}/versions/{version['version_id']}/publish",
            headers=ADMIN,
        )
        yield client, settings, grid, version["layer_id"]


def test_purge(env: tuple[TestClient, Settings, SynthGrid, str]) -> None:
    client, settings, _, _ = env
    cache_dir = Path(settings.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "dummy.png").write_bytes(b"x")
    resp = client.post("/v1/cache/purge", headers=ADMIN)
    assert resp.status_code == 200 and resp.json()["purged"]
    assert not (cache_dir / "dummy.png").exists()


def test_warm(env: tuple[TestClient, Settings, SynthGrid, str]) -> None:
    client, settings, grid, layer_id = env
    lon0, lat0, lon1, lat1 = grid.bounds_wgs84()
    resp = client.post(
        "/v1/cache/warm",
        json={"layer_id": layer_id, "bbox": [lon0, lat0, lon1, lat1], "min_zoom": 8, "max_zoom": 8},
        headers=ADMIN,
    )
    assert resp.status_code == 202, resp.text
    worker = Worker(settings, HANDLERS)
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        while worker.run_once(conn) is not None:
            pass
    job = client.get(f"/v1/jobs/{resp.json()['job_id']}", headers=ADMIN).json()
    assert job["status"] == "succeeded", job
    assert job["result"]["tiles"] > 0
    assert len(list(Path(settings.cache_dir).rglob("*.png"))) > 0
