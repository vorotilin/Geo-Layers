"""Потоковый парсер GeoJSON-сетки. Запускается подпроцессом с лимитами (design §4).

    python -m app.importer.parser <файл> <каталог-вывода> <spec.json>

spec: {"key": "zone_id", "columns": ["val", ...]}. Память не зависит от размера файла:
объекты читаются по одному (ijson), результат пишется чанками:

- chunk_NNNNN.npz — xmin, xmax, ymin, ymax (f8), values (f4, [n, колонки]), feature (i8)
- chunk_NNNNN.keys — ключи ячеек, по одному в строке
- errors.jsonl — первые MAX_ERRORS ошибок; summary.json — счётчики и CRS файла

В stdout — строки `progress <0..1>` для прогресса задания.
Коды выхода: 0 — разобрали (возможно, с ошибками в строках), 2 — файл не читается.
"""

import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, BinaryIO

import ijson
import numpy as np

CHUNK = 100_000
MAX_ERRORS = 1000
PROGRESS_EVERY = 20_000


class FatalError(Exception):
    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


class RowError(Exception):
    def __init__(self, error: str, message: str, column: str | None = None) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.column = column


def read_crs(fh: BinaryIO) -> str | None:
    """CRS из члена `crs` верхнего уровня, если он стоит до `features` (так почти всегда)."""
    builder: ijson.ObjectBuilder | None = None
    for prefix, event, value in ijson.parse(fh):
        if prefix == "features" and event == "start_array":
            return None
        if prefix == "crs" and event == "start_map" and builder is None:
            builder = ijson.ObjectBuilder()
        if builder is not None:
            builder.event(event, value)
            if prefix == "crs" and event == "end_map":
                crs = builder.value
                name = (crs.get("properties") or {}).get("name") if isinstance(crs, dict) else None
                return str(name) if name else None
    return None


def rectangle(geometry: Any) -> tuple[float, float, float, float]:
    """Прямоугольник, выровненный по осям: (xmin, xmax, ymin, ymax) или RowError."""
    if not isinstance(geometry, dict):
        raise RowError("missing_geometry", "Нет геометрии")
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "MultiPolygon" and isinstance(coords, list) and len(coords) == 1:
        gtype, coords = "Polygon", coords[0]
    if gtype != "Polygon":
        raise RowError("unsupported_geometry", f"Ожидался Polygon, получен {gtype}")
    if not isinstance(coords, list) or len(coords) != 1:
        raise RowError("not_rectangle", "Полигон с дырами или без контура")
    try:
        ring = [(float(p[0]), float(p[1])) for p in coords[0]]
    except (TypeError, ValueError, IndexError):
        raise RowError("bad_geometry", "Некорректные координаты") from None
    if len(ring) >= 2 and ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) != 4:
        raise RowError("not_rectangle", "Ячейка не прямоугольник")
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    w, h = xmax - xmin, ymax - ymin
    if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0:
        raise RowError("not_rectangle", "Ячейка нулевого размера")
    tx, ty = w * 1e-3, h * 1e-3
    corners = set()
    for x, y in ring:
        cx = 0 if abs(x - xmin) <= tx else 1 if abs(x - xmax) <= tx else None
        cy = 0 if abs(y - ymin) <= ty else 1 if abs(y - ymax) <= ty else None
        if cx is None or cy is None:
            raise RowError("not_rectangle", "Ячейка не выровнена по осям")
        corners.add((cx, cy))
    if len(corners) != 4:
        raise RowError("not_rectangle", "Ячейка не прямоугольник")
    return xmin, xmax, ymin, ymax


def cell_key(props: dict[str, Any], key: str) -> str:
    raw = props.get(key)
    if raw is None or raw == "":
        raise RowError("missing_key", f"Нет ключа ячейки «{key}»", key)
    if isinstance(raw, bool):
        raise RowError("bad_key", "Ключ ячейки — логическое значение", key)
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    if isinstance(raw, int | float):
        return str(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if text and "\n" not in text and "\r" not in text and len(text) <= 200:
            return text
    raise RowError("bad_key", "Некорректный ключ ячейки", key)


def number(raw: Any, column: str) -> float:
    """Число или NaN (= неизвестно). Нечисловое — RowError."""
    if raw is None:
        return math.nan
    if isinstance(raw, bool):
        raise RowError("bad_value", "Значение — логическое", column)
    if isinstance(raw, int | float):
        return float(raw)
    if isinstance(raw, str):
        text = raw.strip().replace(",", ".")
        if text == "":
            return math.nan
        try:
            return float(text)
        except ValueError:
            pass
    raise RowError("bad_value", "Значение не число", column)


class ChunkWriter:
    def __init__(self, out: Path, columns: int) -> None:
        self.out = out
        self.columns = columns
        self.n_chunks = 0
        self._reset()

    def _reset(self) -> None:
        self.bbox: list[tuple[float, float, float, float]] = []
        self.values: list[list[float]] = []
        self.features: list[int] = []
        self.keys: list[str] = []

    def add(
        self,
        feature_no: int,
        key: str,
        bbox: tuple[float, float, float, float],
        values: list[float],
    ) -> None:
        self.bbox.append(bbox)
        self.values.append(values)
        self.features.append(feature_no)
        self.keys.append(key)
        if len(self.keys) >= CHUNK:
            self.flush()

    def flush(self) -> None:
        if not self.keys:
            return
        name = f"chunk_{self.n_chunks:05d}"
        b = np.asarray(self.bbox, dtype=np.float64)
        np.savez(
            self.out / f"{name}.npz",
            xmin=b[:, 0],
            xmax=b[:, 1],
            ymin=b[:, 2],
            ymax=b[:, 3],
            values=np.asarray(self.values, dtype=np.float32).reshape(-1, self.columns),
            feature=np.asarray(self.features, dtype=np.int64),
        )
        (self.out / f"{name}.keys").write_text("\n".join(self.keys) + "\n", encoding="utf-8")
        self.n_chunks += 1
        self._reset()


def parse(src: Path, out: Path, key: str, columns: list[str]) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    size = max(src.stat().st_size, 1)
    counts: Counter[str] = Counter()
    writer = ChunkWriter(out, len(columns))
    total = ok = 0
    with (out / "errors.jsonl").open("w", encoding="utf-8") as errors:

        def report(feature_no: int, err: RowError) -> None:
            counts[err.error] += 1
            if sum(counts.values()) <= MAX_ERRORS:
                item = {"feature": feature_no, "error": err.error, "message": err.message}
                if err.column:
                    item["column"] = err.column
                errors.write(json.dumps(item, ensure_ascii=False) + "\n")

        with src.open("rb") as fh:
            try:
                crs = read_crs(fh)
                fh.seek(0)
                for feature in ijson.items(fh, "features.item", use_float=True):
                    total += 1
                    try:
                        if not isinstance(feature, dict):
                            raise RowError("bad_feature", "Объект не Feature")
                        props = feature.get("properties") or {}
                        if not isinstance(props, dict):
                            raise RowError("bad_feature", "properties не объект")
                        bbox = rectangle(feature.get("geometry"))
                        k = cell_key(props, key)
                        values = []
                        for col in columns:
                            try:
                                values.append(number(props.get(col), col))
                            except RowError as exc:
                                report(total, exc)  # битое значение = NaN, строка жива
                                values.append(math.nan)
                    except RowError as exc:
                        report(total, exc)
                        continue
                    writer.add(total, k, bbox, values)
                    ok += 1
                    if total % PROGRESS_EVERY == 0:
                        print(f"progress {min(fh.tell() / size, 1):.3f}", flush=True)
            except ijson.JSONError as exc:
                raise FatalError("invalid_json", f"Файл не читается как JSON: {exc}") from None
    writer.flush()
    if total == 0:
        raise FatalError("no_features", "В файле нет объектов features")
    return {
        "features": total,
        "rows_ok": ok,
        "errors_by_type": dict(counts),
        "crs": crs,
        "chunks": writer.n_chunks,
    }


def main(argv: list[str]) -> int:
    src, out, spec_path = Path(argv[0]), Path(argv[1]), Path(argv[2])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    try:
        summary = parse(src, out, spec["key"], spec["columns"])
    except FatalError as exc:
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(
            json.dumps({"fatal": {"error": exc.error, "message": exc.message}}, ensure_ascii=False),
            encoding="utf-8",
        )
        return 2
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    print("progress 1", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
