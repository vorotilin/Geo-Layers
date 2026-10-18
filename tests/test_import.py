"""Импорт → публикация → значение и тайл (план M1, шаги 7–8) на синтетике и образце."""

import io
import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.auth import hash_token
from app.config import Settings
from app.jobs.handlers import HANDLERS
from app.jobs.runner import Worker
from app.main import create_app
from app.tiles import render
from tests.synth import SynthGrid, broken_features, cell_value, crs84_squares, utm_squares

AUTH = {"Authorization": "Bearer t-write"}
SAMPLE = Path(__file__).resolve().parent.parent / "data" / "layers_sample_2023Q1.zip"


@dataclass
class Env:
    client: TestClient
    settings: Settings
    tmp: Path

    def upload(self, body: bytes | dict[str, Any], name: str = "f.geojson") -> str:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        resp = self.client.post("/v1/files", files={"file": (name, data)}, headers=AUTH)
        assert resp.status_code in (200, 201), resp.text
        return str(resp.json()["id"])

    def run_jobs(self) -> None:
        worker = Worker(self.settings, HANDLERS)
        with psycopg.connect(self.settings.database_url, autocommit=True) as conn:
            while worker.run_once(conn) is not None:
                pass

    def import_(self, body: bytes | dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
        resp = self.client.post(
            "/v1/imports", json={"file_id": self.upload(body), "manifest": manifest}, headers=AUTH
        )
        assert resp.status_code == 202, resp.text
        self.run_jobs()
        job = self.client.get(f"/v1/jobs/{resp.json()['job_id']}", headers=AUTH).json()
        return job

    def publish(self, version: dict[str, Any]) -> None:
        resp = self.client.post(
            f"/v1/layers/{version['layer_id']}/versions/{version['version_id']}/publish",
            headers=AUTH,
        )
        assert resp.status_code == 200 and resp.json()["status"] == "published"

    def value(self, layer_id: str, lon: float, lat: float) -> Any:
        resp = self.client.get(
            "/v1/values", params={"lat": lat, "lng": lon, "layers": layer_id}, headers=AUTH
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["values"][0]["value"]


@pytest.fixture
def env(db_url: str, db: psycopg.Connection, tmp_path: Path) -> Iterator[Env]:
    db.execute(
        "INSERT INTO token (hash, title, scopes, namespace_id) "
        "VALUES (%s, 't', '{write}', 'shared')",
        (hash_token("t-write"),),
    )
    settings = Settings(
        database_url=db_url,
        data_dir=str(tmp_path / "data"),
        cache_dir=str(tmp_path / "cache"),
        url_signing_secret="test-secret",
        public_base_url="http://testserver",
    )
    with TestClient(create_app(settings)) as client:
        yield Env(client, settings, tmp_path)


def check_values(env: Env, grid: SynthGrid, layer_id: str, metric: int = 0, n: int = 200) -> None:
    for lon, lat in grid.random_points(n):
        assert env.value(layer_id, lon, lat) == grid.expected(lon, lat, metric)


def check_tile(
    env: Env, grid: SynthGrid, url_template: str, tms: str, z: int, metric: int = 0
) -> int:
    """Проверка тайла по построению: цвет пикселя ↔ ячейка, найденная shapely (design §12)."""
    lon0, lat0, lon1, lat1 = grid.bounds_wgs84()
    tile = render.TMS[tms].tile((lon0 + lon1) / 2, (lat0 + lat1) / 2, z)
    parts = urlsplit(url_template.format(z=tile.z, x=tile.x, y=tile.y))
    resp = env.client.get(f"{parts.path}?{parts.query}")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
    assert "immutable" in resp.headers["cache-control"]
    img = np.asarray(Image.open(io.BytesIO(resp.content)))
    xs, ys = render.pixel_centers(tms, tile.z, tile.x, tile.y, 256)
    from pyproj import Transformer

    to_wgs = Transformer.from_crs(f"EPSG:{tms}", "OGC:CRS84", always_xy=True)
    rng = np.random.default_rng(7)
    checked = 0
    for px, py in rng.integers(0, 256, (400, 2)):
        lon, lat = to_wgs.transform(xs[px], ys[py])
        expected = grid.expected(lon, lat, metric)
        if expected is None:
            assert img[py, px] == 0, (px, py)
        else:
            assert img[py, px] != 0, (px, py)
            checked += 1
    assert checked > 50
    return checked


@pytest.mark.parametrize("make", [crs84_squares, utm_squares], ids=["crs84", "utm"])
def test_synthetic_end_to_end(env: Env, make: Any) -> None:
    grid = make(metrics=2)
    job = env.import_(grid.geojson(), grid.manifest())
    assert job["status"] == "succeeded", job
    result = job["result"]
    assert result["grid"]["cells_total"] == len(grid.cells)
    assert result["grid"]["crs"] == grid.crs
    assert result["rows_failed"] == 0
    versions = {v["slug"]: v for v in result["versions"]}
    assert set(versions) == {"metric_0", "metric_1"}

    v0 = versions["metric_0"]
    # Черновик в значениях не участвует — нет опубликованной версии.
    resp = env.client.get(
        "/v1/values", params={"lat": 55.6, "lng": 37.4, "layers": v0["layer_id"]}, headers=AUTH
    ).json()
    assert resp["values"][0]["reason"] == "no_published_version"

    env.publish(v0)
    check_values(env, grid, v0["layer_id"])
    # Конкретная версия черновика второй метрики — по id@version.
    v1 = versions["metric_1"]
    lon, lat = grid.random_points(1, seed=3)[0]
    resp = env.client.get(
        "/v1/values",
        params={"lat": lat, "lng": lon, "layers": f"{v1['layer_id']}@{v1['version_id']}"},
        headers=AUTH,
    ).json()
    assert resp["values"][0]["value"] == grid.expected(lon, lat, 1)
    assert env.value(v0["layer_id"], 10.0, 10.0) is None  # вне сетки

    stats = env.client.get(
        f"/v1/layers/{v0['layer_id']}/versions/{v0['version_id']}", headers=AUTH
    ).json()["stats"]
    expected_vals = [cell_value(0, c.row, c.col) for c in grid.cells]
    assert stats["count"] == len(grid.cells)
    assert stats["min"] == min(expected_vals) and stats["max"] == max(expected_vals)

    for tms in ("3857", "3395"):
        signed = env.client.post(
            "/v1/tiles/sign", json={"layer": v0["layer_id"], "tms": tms}, headers=AUTH
        ).json()
        assert signed["version_id"] == v0["version_id"]
        check_tile(env, grid, signed["url_template"], tms, z=13)


def test_tile_signature_and_cache(env: Env) -> None:
    grid = crs84_squares()
    v = env.import_(grid.geojson(), grid.manifest())["result"]["versions"][0]
    env.publish(v)
    signed = env.client.post("/v1/tiles/sign", json={"layer": v["layer_id"]}, headers=AUTH).json()
    lon0, lat0, lon1, lat1 = grid.bounds_wgs84()
    t = render.TMS["3857"].tile((lon0 + lon1) / 2, (lat0 + lat1) / 2, 12)
    url = urlsplit(signed["url_template"].format(z=t.z, x=t.x, y=t.y))
    good = f"{url.path}?{url.query}"

    first = env.client.get(good)
    assert first.status_code == 200
    again = env.client.get(good)  # из дискового кэша
    assert again.content == first.content and again.headers["etag"] == first.headers["etag"]
    assert env.client.get(good, headers={"If-None-Match": first.headers["etag"]}).status_code == 304

    assert env.client.get(url.path).status_code == 403  # без подписи
    assert env.client.get(f"{url.path}?{url.query.replace('sig=', 'sig=0')}").status_code == 403
    other = url.path.replace(v["version_id"], "00000000-0000-0000-0000-000000000000")
    assert env.client.get(f"{other}?{url.query}").status_code == 403  # подпись не от этой версии
    assert env.client.get(url.path, headers=AUTH).status_code == 200  # токен тоже подходит

    retina = env.client.get(good.replace(".png", "@2x.png"))
    assert Image.open(io.BytesIO(retina.content)).size == (512, 512)
    empty = env.client.get(url.path.replace(f"/{t.x}/", f"/{t.x + 40}/"), headers=AUTH)
    assert empty.status_code == 200
    assert not np.asarray(Image.open(io.BytesIO(empty.content))).any()  # прозрачный


def test_errors_reported_and_threshold(env: Env) -> None:
    grid = crs84_squares()
    doc = grid.geojson(extra=broken_features())
    dup = json.loads(json.dumps(doc["features"][0]))
    doc["features"].append(dup)  # повтор ключа
    doc["features"][1]["properties"]["m0"] = "не число"

    job = env.import_(doc, grid.manifest(options={"max_error_rate": 0.5}))
    assert job["status"] == "succeeded", job
    imp = env.client.get(f"/v1/imports/{job['result']['import_id']}", headers=AUTH).json()
    report = imp["report"]
    assert report["errors_by_type"] == {
        "missing_geometry": 1,
        "not_rectangle": 1,
        "missing_key": 1,
        "unsupported_geometry": 1,
        "duplicate_key": 1,
        "bad_value": 1,
    }
    assert report["rows_failed"] == 5  # bad_value не роняет строку: значение = NaN
    assert {e["feature"] for e in report["errors"]} >= {len(grid.cells) + 1}

    strict = env.import_(doc, grid.manifest(period="2024Q2", options={"max_error_rate": 0.001}))
    assert strict["status"] == "failed"
    assert strict["error"]["error"] == "too_many_errors"


def test_bad_files(env: Env) -> None:
    grid = crs84_squares()
    job = env.import_(b"{not json", grid.manifest())
    assert job["status"] == "failed" and job["error"]["error"] == "invalid_json"
    job = env.import_({"type": "FeatureCollection", "features": []}, grid.manifest(period="p2"))
    assert job["error"]["error"] == "no_features"


def test_manifest_validation_and_dedup(env: Env) -> None:
    grid = crs84_squares()
    file_id = env.upload(grid.geojson())
    assert env.upload(grid.geojson()) == file_id  # тот же файл — та же запись

    bad = grid.manifest(geometry={"type": "h3"})
    resp = env.client.post("/v1/imports", json={"file_id": file_id, "manifest": bad}, headers=AUTH)
    assert resp.status_code == 422 and resp.json()["error"] == "manifest_invalid"

    body = {"file_id": file_id, "manifest": grid.manifest(unknown_field=1)}
    first = env.client.post("/v1/imports", json=body, headers=AUTH)
    second = env.client.post("/v1/imports", json=body, headers=AUTH)
    assert first.status_code == 202 and second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]

    key = {"Idempotency-Key": "k1"} | AUTH
    other: dict[str, Any] = {"file_id": file_id, "manifest": grid.manifest(period="2030Q1")}
    assert env.client.post("/v1/imports", json=other, headers=key).status_code == 202
    other["manifest"]["period"] = "2031Q1"
    conflict = env.client.post("/v1/imports", json=other, headers=key)
    assert conflict.status_code == 409 and conflict.json()["error"] == "idempotency_conflict"


def test_files_share_grid_and_new_cells_append(env: Env) -> None:
    """Два файла поставщика по одной сетке: геометрия хранится один раз, idx стабильны."""
    full = crs84_squares()
    part = SynthGrid(full.crs, full.cells[: len(full.cells) // 2])
    rest = SynthGrid(full.crs, full.cells[len(full.cells) // 3 :])

    a = env.import_(part.geojson(), part.manifest())["result"]
    b = env.import_(rest.geojson(), rest.manifest(period="2024Q2"))["result"]
    assert a["grid"]["grid_id"] == b["grid"]["grid_id"] and b["grid"]["reused"]
    assert b["grid"]["cells_total"] == len(full.cells)
    assert b["grid"]["cells_added"] == len(full.cells) - len(part.cells)

    va, vb = a["versions"][0], b["versions"][0]
    assert va["layer_id"] == vb["layer_id"]  # тот же слой, другой период
    env.publish(va)
    env.publish(vb)
    # Текущая — последнего периода: значения есть по всей «rest».
    check_values(env, rest, vb["layer_id"], n=100)
    # Старая версия короче сетки: хвост читается как «неизвестно».
    lon, lat = rest.random_points(1, seed=11)[0]
    in_part = part.expected(lon, lat)
    resp = env.client.get(
        "/v1/values",
        params={"lat": lat, "lng": lon, "layers": f"{va['layer_id']}@{va['version_id']}"},
        headers=AUTH,
    ).json()
    assert resp["values"][0]["value"] == in_part


def test_same_keys_other_geometry_new_grid(env: Env) -> None:
    a = crs84_squares()
    b = crs84_squares(lon0=40.0)  # те же ключи, другое место
    ga = env.import_(a.geojson(), a.manifest())["result"]["grid"]
    job = env.import_(b.geojson(), b.manifest(period="2024Q2"))
    assert job["result"]["grid"]["grid_id"] != ga["grid_id"]
    imp = env.client.get(f"/v1/imports/{job['result']['import_id']}", headers=AUTH).json()
    assert imp["report"]["warnings"][0]["warning"] == "grid_geometry_mismatch"


@pytest.mark.skipif(not SAMPLE.exists(), reason="нет data/layers_sample_2023Q1.zip")
def test_sample_three_files_one_grid(env: Env) -> None:
    """Образец — один из прогонов: три файла, одна сетка, 128 132 ячейки."""
    with zipfile.ZipFile(SAMPLE) as z:
        files = {n.removesuffix("_2023Q1.geojson"): z.read(n) for n in z.namelist()}
    kinds = {
        "mean_income_home": ("Средний доход", "руб.", "intensive"),
        "cnt_job": ("Рабочие места", "шт.", "extensive"),
        "realty_commercial_real_estate": ("Доля коммерческой недвижимости", None, "intensive"),
    }
    grids = set()
    results = {}
    for slug, data in files.items():
        title, unit, kind = kinds[slug]
        manifest = {
            "kind": "grid",
            "dataset": {"provider": "sample", "title": "Москва и область 500 м"},
            "period": "2023Q1",
            "geometry": {"type": "geojson"},
            "key": "zone_id",
            "metrics": [
                {"column": "val", "slug": slug, "title": title, "unit": unit, "value_kind": kind}
            ],
        }
        job = env.import_(data, manifest)
        assert job["status"] == "succeeded", job
        assert job["result"]["rows_failed"] == 0
        grids.add(job["result"]["grid"]["grid_id"])
        results[slug] = job["result"]
    assert len(grids) == 1
    assert max(r["grid"]["cells_total"] for r in results.values()) == 128_132

    version = results["mean_income_home"]["versions"][0]
    env.publish(version)
    for feature in json.loads(files["mean_income_home"])["features"][:50]:
        ring = feature["geometry"]["coordinates"][0]
        lon = (min(p[0] for p in ring) + max(p[0] for p in ring)) / 2
        lat = (min(p[1] for p in ring) + max(p[1] for p in ring)) / 2
        expected = feature["properties"]["val"]
        assert env.value(version["layer_id"], lon, lat) == pytest.approx(expected, rel=1e-6)
