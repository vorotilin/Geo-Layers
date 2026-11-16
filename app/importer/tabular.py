"""Парсер табличных сеток: CSV и XLSX (WKT или «центр + размер») — brief §1.

Выход совместим с GeoJSON-парсером: прямоугольники ячеек + значения + ключи,
подаётся в общий поток `attach_to_grid` (rectilinear).
"""

import csv
import io
from typing import Any

import numpy as np
from openpyxl import load_workbook
from shapely import wkt


class TabularError(ValueError):
    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


def _number(raw: Any) -> float:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return np.nan
    if isinstance(raw, bool):
        raise TabularError("bad_value", "Значение — логическое")
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        raise TabularError("bad_value", f"Не число: {raw}") from None


def _bbox(
    geom: dict[str, Any], row: list[str], header: dict[str, int]
) -> tuple[float, float, float, float]:
    gtype = geom.get("type")
    if gtype == "wkt":
        text = row[header[geom["column"]]]
        g = wkt.loads(text)
        if g.geom_type != "Polygon" or g.has_z:
            raise TabularError("not_rectangle", "Ожидался Polygon без Z")
        xs = [c[0] for c in g.exterior.coords]
        ys = [c[1] for c in g.exterior.coords]
        return min(xs), max(xs), min(ys), max(ys)
    if gtype == "center_size":
        c0, c1, c2, c3 = (header[c] for c in geom["columns"])
        x, y, w, h = _number(row[c0]), _number(row[c1]), _number(row[c2]), _number(row[c3])
        if not all(np.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
            raise TabularError("bad_geometry", "Некорректный «центр+размер»")
        return x - w / 2, x + w / 2, y - h / 2, y + h / 2
    raise TabularError("bad_geometry", f"Неизвестный тип геометрии: {gtype}")


def parse_tabular(
    data: bytes,
    *,
    fmt: str,
    key_col: str,
    metric_cols: list[str],
    geom: dict[str, Any],
) -> dict[str, Any]:
    """Читает CSV/XLSX и возвращает прямоугольники + значения + ключи + счётчик ошибок."""
    if fmt == "csv":
        text = data.decode("utf-8")
        sample = text[:4096]
        delim = ";"
        for d in (";", ",", "\t"):
            if d in (sample.splitlines() or [""])[0]:
                delim = d
                break
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        if not rows:
            raise TabularError("no_features", "Файл пуст")
        header_row = [h.strip() for h in rows[0]]
        data_rows = rows[1:]
    elif fmt == "xlsx":
        wb = load_workbook(io.BytesIO(data), read_only=True)
        ws = wb.worksheets[0]
        header_row = [
            str(c.value).strip() if c.value is not None else "" for c in next(ws.iter_rows())
        ]
        data_rows = [[("" if c.value is None else str(c.value)) for c in r] for r in ws.iter_rows()]
    else:
        raise TabularError("bad_format", f"Неизвестный формат: {fmt}")

    header = {h: i for i, h in enumerate(header_row)}
    for name in [key_col, *metric_cols]:
        if name not in header:
            raise TabularError("bad_columns", f"Нет колонки «{name}»")

    keys: list[str] = []
    bboxes: list[tuple[float, float, float, float]] = []
    values: list[list[float]] = []
    errors = 0
    for r in data_rows:
        if len(r) <= max(header.values()):
            errors += 1
            continue
        try:
            k = str(r[header[key_col]]).strip()
            if not k:
                raise TabularError("missing_key", "Нет ключа")
            bbox = _bbox(geom, r, header)
            vals = [_number(r[header[c]]) for c in metric_cols]
        except (TabularError, IndexError):
            errors += 1
            continue
        keys.append(k)
        bboxes.append(bbox)
        values.append(vals)

    if not keys:
        raise TabularError("no_features", "Нет валидных строк")
    bb = np.asarray(bboxes, dtype=np.float64)
    return {
        "keys": keys,
        "xmin": bb[:, 0],
        "xmax": bb[:, 1],
        "ymin": bb[:, 2],
        "ymax": bb[:, 3],
        "values": np.asarray(values, dtype=np.float32),
        "errors": errors,
    }
