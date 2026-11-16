"""Бининг точек в гексагоны H3 (brief §3, design §6).

Результат — гексагоны как полигоны + значения (count/sum/mean): ложится на общий
механизм сетки `polygons` (растеризация, значение в точке).
"""

import h3
import numpy as np
import numpy.typing as npt
from shapely.geometry import Polygon

from app.derive.binning import BinningError
from app.stats import compute

F64 = npt.NDArray[np.float64]


def bin_h3(
    lon: npt.ArrayLike,
    lat: npt.ArrayLike,
    *,
    resolution: int,
    agg: str = "count",
    weight: npt.ArrayLike | None = None,
    value: npt.ArrayLike | None = None,
) -> dict:
    """Точки → гексагоны H3 (полигоны) + значения. Пустой гексагон при mean — NaN."""
    if not 0 <= int(resolution) <= 15:
        raise BinningError("resolution должен быть 0..15")
    if agg not in ("count", "sum", "mean"):
        raise BinningError(f"неизвестная агрегация: {agg}")
    lon_a = np.asarray(lon, dtype=np.float64)
    lat_a = np.asarray(lat, dtype=np.float64)
    if lon_a.size == 0 or lon_a.shape != lat_a.shape:
        raise BinningError("нет точек или разные длины")
    w = np.ones(lon_a.size) if weight is None else np.asarray(weight, dtype=np.float64)
    v = np.ones(lon_a.size) if value is None else np.asarray(value, dtype=np.float64)

    cell_of = [h3.latlng_to_cell(lat_a[i], lon_a[i], int(resolution)) for i in range(lon_a.size)]
    cells: list[str] = []
    idx: dict[str, int] = {}
    for c in cell_of:
        if c not in idx:
            idx[c] = len(cells)
            cells.append(c)

    n = len(cells)
    counts = np.zeros(n, dtype=np.float64)
    wsum = np.zeros(n, dtype=np.float64)
    vsum = np.zeros(n, dtype=np.float64)
    for i, c in enumerate(cell_of):
        j = idx[c]
        counts[j] += 1
        wsum[j] += w[i]
        vsum[j] += v[i]

    if agg == "count":
        values = counts
    elif agg == "sum":
        values = wsum
    else:  # mean
        values = np.divide(vsum, counts, out=np.full(n, np.nan), where=counts > 0)

    polygons = []
    for c in cells:
        boundary = h3.cell_to_boundary(c)  # [(lat, lng), ...]
        polygons.append(Polygon([(lng, lat) for lat, lng in boundary]))

    values32 = values.astype(np.float32)
    return {
        "crs": "OGC:CRS84",
        "polygons": polygons,
        "keys": cells,
        "values": values32,
        "stats": compute(values32, n),
        "cells": n,
    }
