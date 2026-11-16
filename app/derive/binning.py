"""Биннинг точек в квадратную сетку (brief §3, design §6).

Точки проецируются в локальную метрическую проекцию (UTM по центру набора) — квадрат
шага `step` метров это честные метры, а не градусы. Результат — обычная прямоугольная
сетка (`rectilinear`, CRS EPSG:326xx/327xx), которую дальше рендерит и запрашивает
общий механизм: тайлы, значение в точке, агрегаты, рейтинг.

H3-бининг — отдельно (нужна библиотека h3, см. known-issues).
"""

import numpy as np
import numpy.typing as npt
from pyproj import CRS, Transformer

from app.grids.rectilinear import Layout
from app.stats import compute

F64 = npt.NDArray[np.float64]


class BinningError(ValueError):
    pass


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG-код зоны UTM для точки: 326xx северное полушарие, 327xx южное."""
    zone = min(max(int(np.floor((lon + 180.0) / 6.0)) + 1, 1), 60)
    return 32600 + zone if lat >= 0 else 32700 + zone


def bin_squares(
    lon: npt.ArrayLike,
    lat: npt.ArrayLike,
    *,
    step: float,
    agg: str = "count",
    weight: npt.ArrayLike | None = None,
    value: npt.ArrayLike | None = None,
) -> dict:
    """Точки → квадратная сетка шага `step` метров (UTM).

    `agg`: count (число точек) | sum (сумма весов) | mean (среднее поля `value`).
    Пустые ячейки: count/sum → 0, mean → NaN («нет данных»).
    """
    if step <= 0:
        raise BinningError("step должен быть положительным")
    if agg not in ("count", "sum", "mean"):
        raise BinningError(f"неизвестная агрегация: {agg}")
    lon_a = np.asarray(lon, dtype=np.float64)
    lat_a = np.asarray(lat, dtype=np.float64)
    if lon_a.size == 0:
        raise BinningError("нет точек")
    if lon_a.shape != lat_a.shape:
        raise BinningError("lon и lat разной длины")

    epsg = utm_epsg(float(lon_a.mean()), float(lat_a.mean()))
    to_utm = Transformer.from_crs("OGC:CRS84", CRS.from_epsg(epsg), always_xy=True)
    x, y = to_utm.transform(lon_a, lat_a)

    x0 = np.floor(x.min() / step) * step
    y0 = np.floor(y.min() / step) * step
    cols = ((x - x0) / step).astype(np.int64)
    rows = ((y - y0) / step).astype(np.int64)
    ncols = int(cols.max()) + 1
    nrows = int(rows.max()) + 1
    flat = rows * ncols + cols
    n = nrows * ncols

    counts = np.bincount(flat, minlength=n)
    if agg == "count":
        values = counts.astype(np.float64)
    elif agg == "sum":
        w = np.ones(lon_a.size) if weight is None else np.asarray(weight, dtype=np.float64)
        values = np.bincount(flat, weights=w, minlength=n)
    else:  # mean
        v = np.ones(lon_a.size) if value is None else np.asarray(value, dtype=np.float64)
        sums = np.bincount(flat, weights=v, minlength=n)
        values = np.divide(sums, counts, out=np.full(n, np.nan), where=counts > 0)

    x_edges = x0 + np.arange(ncols + 1) * step
    y_edges = y0 + np.arange(nrows + 1) * step
    index = np.arange(n, dtype=np.int32).reshape(nrows, ncols)
    values32 = values.astype(np.float32)
    return {
        "crs": f"EPSG:{epsg}",
        "layout": Layout(x_edges, y_edges, index),
        "values": values32,
        "stats": compute(values32, n),
        "cells": n,
    }
