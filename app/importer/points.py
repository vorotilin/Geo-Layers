"""Парсер наборов точек: CSV, GeoJSON, NDJSON (brief §3, design §6).

Координаты обязательны, вес и категория — по желанию (вес по умолчанию 1).
Читаем в numpy-массивы: миллион точек — десятки МБ, в памяти приемлемо.
"""

import csv
import io
import json
from typing import Any

import numpy as np
import numpy.typing as npt

from app.storage import LocalStorage

F64 = npt.NDArray[np.float64]
F32 = npt.NDArray[np.float32]

_LAT_NAMES = ("lat", "latitude", "широта")
_LON_NAMES = ("lng", "lon", "long", "longitude", "долгота")


class PointsError(ValueError):
    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


def _number(raw: Any) -> float:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return np.nan
    if isinstance(raw, bool):
        raise PointsError("bad_value", "Значение — логическое")
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        raise PointsError("bad_value", f"Не число: {raw}") from None


def _sniff(text: str) -> str:
    first = text.lstrip("\ufeff \t\r\n")
    if first.startswith("{"):
        lines = [ln for ln in first.splitlines() if ln.strip()]
        if len(lines) > 1 and all(ln.lstrip().startswith("{") for ln in lines):
            return "ndjson"
        return "geojson"
    return "csv"


def _delimiter(sample: str) -> str:
    line = sample.splitlines()[0] if sample.splitlines() else ""
    for d in (";", ",", "\t"):
        if d in line:
            return d
    return ","


def _col_index(header: list[str], name: str | None, fallback: tuple[str, ...]) -> int | None:
    if name:
        for i, h in enumerate(header):
            if h.strip().lower() == name.lower():
                return i
    for i, h in enumerate(header):
        if h.strip().lower() in fallback:
            return i
    return None


class _Acc:
    def __init__(self) -> None:
        self.lon: list[float] = []
        self.lat: list[float] = []
        self.weight: list[float] = []
        self.category: list[str] = []
        self.errors = 0

    def add(self, lon: float, lat: float, weight: float, category: str) -> None:
        self.lon.append(lon)
        self.lat.append(lat)
        self.weight.append(weight)
        self.category.append(category)


def _feature_point(
    f: dict[str, Any], acc: _Acc, weight_col: str | None, category_col: str | None
) -> None:
    g = f.get("geometry") or {}
    coords = g.get("coordinates")
    if g.get("type") != "Point" or not isinstance(coords, list) or len(coords) < 2:
        raise PointsError("bad_geometry", "Ожидался Point")
    lon, lat = _number(coords[0]), _number(coords[1])
    if not (np.isfinite(lon) and np.isfinite(lat)):
        raise PointsError("bad_coords", "Нет координат")
    props = f.get("properties") or {}
    weight = 1.0
    if weight_col is not None:
        weight = _number(props.get(weight_col))
        if not np.isfinite(weight):
            weight = 1.0
    category = ""
    if category_col is not None:
        category = str(props.get(category_col) or "")
    acc.add(lon, lat, weight, category)


def parse_points(
    data: bytes | str,
    *,
    lon: str = "lng",
    lat: str = "lat",
    weight: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Разбор набора точек (CSV / GeoJSON / NDJSON) → numpy-массивы.

    Формат определяется сам; битые строки пропускаются (счётчик `errors`).
    """
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    fmt = _sniff(text)
    acc = _Acc()

    if fmt == "csv":
        reader = csv.reader(io.StringIO(text), delimiter=_delimiter(text[:4096]))
        try:
            header = next(reader)
        except StopIteration:
            raise PointsError("no_points", "Файл пуст") from None
        lon_i = _col_index(header, lon, _LON_NAMES)
        lat_i = _col_index(header, lat, _LAT_NAMES)
        w_i = _col_index(header, weight, ()) if weight else None
        c_i = _col_index(header, category, ()) if category else None
        if lon_i is None or lat_i is None:
            raise PointsError("bad_columns", "Не найдены колонки координат")
        for row in reader:
            if len(row) <= max(lon_i, lat_i):
                acc.errors += 1
                continue
            try:
                lo, la = _number(row[lon_i]), _number(row[lat_i])
                if not (np.isfinite(lo) and np.isfinite(la)):
                    raise PointsError("bad_coords", "Нет координат")
                w = 1.0
                if w_i is not None and w_i < len(row):
                    w = _number(row[w_i])
                    if not np.isfinite(w):
                        w = 1.0
                c = ""
                if c_i is not None and c_i < len(row):
                    c = str(row[c_i]).strip()
                acc.add(lo, la, w, c)
            except PointsError:
                acc.errors += 1
    else:
        if fmt == "geojson":
            doc = json.loads(text)
            features: Any = doc.get("features") if isinstance(doc, dict) else doc
            if not isinstance(features, list):
                raise PointsError("no_points", "Нет точек")
            items = features
        else:  # ndjson
            items = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    acc.errors += 1
        for f in items:
            try:
                _feature_point(f, acc, weight, category)
            except (PointsError, TypeError):
                acc.errors += 1

    if not acc.lon:
        raise PointsError("no_points", "В файле нет точек")
    return {
        "lon": np.asarray(acc.lon, dtype=np.float64),
        "lat": np.asarray(acc.lat, dtype=np.float64),
        "weight": np.asarray(acc.weight, dtype=np.float32),
        "category": np.asarray(acc.category, dtype=np.str_),
        "count": len(acc.lon),
        "errors": acc.errors,
    }


def snapshot_key(version_id: str) -> str:
    return f"points/{version_id}.npz"


def save_snapshot(storage: LocalStorage, version_id: str, parsed: dict[str, Any]) -> str:
    """Снимок версии набора точек (.npz) для воркеров; возвращает ключ."""
    key = snapshot_key(version_id)
    with storage.writer(key) as fh:
        np.savez(
            fh,
            lon=parsed["lon"],
            lat=parsed["lat"],
            weight=parsed["weight"],
            category=parsed["category"],
        )
    return key


def load_snapshot(storage: LocalStorage, key: str) -> dict[str, Any]:
    with np.load(storage.path(key)) as d:
        return {
            "lon": d["lon"],
            "lat": d["lat"],
            "weight": d["weight"],
            "category": d["category"],
        }
