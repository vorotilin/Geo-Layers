"""Сквозной сценарий M1 для CI и приёмки (common-requirements §9.2, план M1 шаг 9).

Загрузка → импорт → опрос задания → публикация → значение в точке (сверка с
формулой) → подпись → тайл PNG в 3857 и 3395. Только стандартная библиотека Python.

    python scripts/e2e.py                                  # синтетика (ответ известен)
    python scripts/e2e.py --sample data/layers_sample_2023Q1.zip

Переменные: APC_URL (по умолчанию http://localhost:8000), APC_TOKEN (dev-admin-token).
"""

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from typing import Any

BASE = os.environ.get("APC_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("APC_TOKEN", "dev-admin-token")


def call(
    method: str,
    path: str,
    body: Any = None,
    *,
    raw: bytes | None = None,
    content_type: str | None = None,
    auth: bool = True,
) -> tuple[int, Any, dict[str, str]]:
    url = path if path.startswith("http") else BASE + path
    data = raw
    headers = {"Authorization": f"Bearer {TOKEN}"} if auth else {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
            payload = resp.read()
            status, msg = resp.status, resp.headers
    except urllib.error.HTTPError as err:
        payload, status, msg = err.read(), err.code, err.headers
    hdrs = {k.lower(): v for k, v in msg.items()}
    ctype = hdrs.get("content-type", "")
    return status, (json.loads(payload) if "json" in ctype else payload), hdrs


def check(cond: bool, message: str) -> None:
    if not cond:
        print(f"ОШИБКА: {message}")
        sys.exit(1)


def upload(name: str, data: bytes) -> str:
    boundary = uuid.uuid4().hex
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    status, resp, _ = call(
        "POST", "/v1/files", raw=body, content_type=f"multipart/form-data; boundary={boundary}"
    )
    check(status in (200, 201), f"загрузка {name}: {status} {resp}")
    return str(resp["id"])


def import_file(name: str, data: bytes, manifest: dict[str, Any], timeout: float) -> dict[str, Any]:
    file_id = upload(name, data)
    status, resp, _ = call("POST", "/v1/imports", {"file_id": file_id, "manifest": manifest})
    check(status in (200, 202), f"импорт {name}: {status} {resp}")
    deadline = time.monotonic() + timeout
    while True:
        _, job, _ = call("GET", resp["status_url"])
        if job["status"] in ("succeeded", "failed", "cancelled"):
            break
        check(time.monotonic() < deadline, f"импорт {name} не завершился за {timeout} с")
        time.sleep(0.5)
    check(job["status"] == "succeeded", f"импорт {name}: {job['status']} {job['error']}")
    return dict(job["result"])


def publish(version: dict[str, Any]) -> None:
    status, resp, _ = call(
        "POST", f"/v1/layers/{version['layer_id']}/versions/{version['version_id']}/publish"
    )
    check(status == 200 and resp["status"] == "published", f"публикация: {status} {resp}")


def value(layer_id: str, lon: float, lat: float) -> Any:
    status, resp, _ = call("GET", f"/v1/values?lat={lat}&lng={lon}&layers={layer_id}")
    check(status == 200, f"значение: {status} {resp}")
    return resp["values"][0]["value"]


def tile_xy(lon: float, lat: float, z: int) -> tuple[int, int]:
    """Номер тайла 3857 (для 3395 по широте может отличаться на 1 — берём тот же для проверки)."""
    n = 2**z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def check_tiles(layer_id: str, lon: float, lat: float) -> None:
    for tms in ("3857", "3395"):
        status, signed, _ = call("POST", "/v1/tiles/sign", {"layer": layer_id, "tms": tms})
        check(status == 200, f"подпись {tms}: {status} {signed}")
        x, y = tile_xy(lon, lat, 12)
        url = signed["url_template"].format(z=12, x=x, y=y)
        started = time.perf_counter()
        status, png, hdrs = call("GET", url, auth=False)
        cold_ms = (time.perf_counter() - started) * 1000
        check(status == 200 and png[:8] == b"\x89PNG\r\n\x1a\n", f"тайл {tms}: {status}")
        started = time.perf_counter()
        status, _, _ = call("GET", url, auth=False)
        warm_ms = (time.perf_counter() - started) * 1000
        status_nosig, _, _ = call("GET", url.split("?")[0], auth=False)
        check(status_nosig == 403, f"тайл без подписи должен быть 403, а не {status_nosig}")
        print(
            f"  тайл {tms} z12: {len(png)} байт, холодный {cold_ms:.0f} мс, "
            f"из кэша {warm_ms:.0f} мс, ETag {hdrs.get('etag')}"
        )


# --- синтетика: значение ячейки = row·1000 + col ---


def synthetic(nx: int = 60, ny: int = 40) -> tuple[bytes, list[tuple[float, float, float]]]:
    lon0, lat0, dlon, dlat = 37.30, 55.50, 0.0045, 0.0027
    features, probes = [], []
    for row in range(ny):
        for col in range(nx):
            if (row * 31 + col * 17) % 13 == 0:
                continue  # дыра: «неизвестно»
            x0, x1 = round(lon0 + col * dlon, 6), round(lon0 + (col + 1) * dlon, 6)
            y0, y1 = round(lat0 + row * dlat, 6), round(lat0 + (row + 1) * dlat, 6)
            v = row * 1000 + col
            features.append(
                {
                    "type": "Feature",
                    "properties": {"zone_id": row * 10_000 + col, "val": v},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[x0, y1], [x0, y0], [x1, y0], [x1, y1], [x0, y1]]],
                    },
                }
            )
            if (row + col) % 37 == 0:
                probes.append(((x0 + x1) / 2, (y0 + y1) / 2, float(v)))
    doc = {"type": "FeatureCollection", "features": features}
    return json.dumps(doc).encode(), probes


def manifest(slug: str, title: str, kind: str, provider: str, period: str) -> dict[str, Any]:
    return {
        "kind": "grid",
        "dataset": {"provider": provider, "title": title},
        "period": period,
        "geometry": {"type": "geojson"},
        "key": "zone_id",
        "metrics": [{"column": "val", "slug": slug, "title": title, "value_kind": kind}],
    }


def run_synthetic(timeout: float) -> None:
    print("Синтетика: квадраты CRS84, значение = row·1000 + col")
    data, probes = synthetic()
    period = f"e2e{int(time.time())}"
    result = import_file(
        "synth.geojson",
        data,
        manifest("synth_value", "E2E синтетика", "intensive", "e2e", period),
        timeout,
    )
    version = result["versions"][0]
    publish(version)
    for lon, lat, expected in probes:
        got = value(version["layer_id"], lon, lat)
        check(got == expected, f"значение в ({lon}, {lat}): {got}, ожидалось {expected}")
    check(value(version["layer_id"], 10.0, 10.0) is None, "вне сетки должно быть null")
    print(f"  значений сверено: {len(probes)}")
    check_tiles(version["layer_id"], probes[0][0], probes[0][1])


def run_sample(path: str, timeout: float) -> None:
    kinds = {
        "mean_income_home": ("Средний доход", "intensive"),
        "cnt_job": ("Рабочие места", "extensive"),
        "realty_commercial_real_estate": ("Доля коммерческой недвижимости", "intensive"),
    }
    started = time.monotonic()
    grids = set()
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        files = {n: z.read(n) for n in names}
    first_feature = None
    income = None
    for name, data in files.items():
        slug = name.removesuffix("_2023Q1.geojson")
        title, kind = kinds[slug]
        t0 = time.monotonic()
        result = import_file(name, data, manifest(slug, title, kind, "sample", "2023Q1"), timeout)
        grids.add(result["grid"]["grid_id"])
        print(
            f"  {name}: {result['rows_ok']} ячеек, ошибок {result['rows_failed']}, "
            f"{time.monotonic() - t0:.1f} с, сетка {result['grid']['cells_total']}"
        )
        if slug == "mean_income_home":
            income = result["versions"][0]
            first_feature = json.loads(data)["features"][0]
    total = time.monotonic() - started
    check(len(grids) == 1, f"три файла должны лечь на одну сетку, а их {len(grids)}")
    print(f"Образец целиком: {total:.1f} с (требование ≤ 120 с)")
    check(total <= 120, "загрузка образца дольше 2 минут")
    if income is None or first_feature is None:
        check(False, "в архиве нет mean_income_home_2023Q1.geojson")
        return
    publish(income)
    ring = first_feature["geometry"]["coordinates"][0]
    lon = (min(p[0] for p in ring) + max(p[0] for p in ring)) / 2
    lat = (min(p[1] for p in ring) + max(p[1] for p in ring)) / 2
    got = value(income["layer_id"], lon, lat)
    check(
        got == first_feature["properties"]["val"],
        f"значение первой ячейки: {got}, ожидалось {first_feature['properties']['val']}",
    )
    check_tiles(income["layer_id"], lon, lat)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sample", help="путь к layers_sample_2023Q1.zip")
    parser.add_argument("--timeout", type=float, default=300, help="ожидание задания, с")
    args = parser.parse_args()
    status, health, _ = call("GET", "/ready", auth=False)
    check(status == 200, f"/ready: {status} {health}")
    run_synthetic(args.timeout)
    if args.sample:
        run_sample(args.sample, args.timeout)
    print("OK")


if __name__ == "__main__":
    main()
