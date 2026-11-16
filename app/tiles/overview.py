"""Обзоры на мелких зумах (design §5.3, brief §6): где ячейка меньше пикселя,
рисуем агрегат, а не отдельную ячейку.

- `intensive` (среднее, доля) — среднее, взвешенное по площади ячейки;
- `extensive` (счётчик) — плотность: сумма значений / сумма площади → значение на км².

M2: только WGS84-прямоугольные сетки (образец). Ячейки в проекционной CRS и
произвольные полигоны на обзорах пока не агрегируются (рисуются по центру пикселя) —
см. known-issues.
"""

import math

import morecantile
import numpy as np
import numpy.typing as npt

from app.grids.crs import WGS84, transformer
from app.grids.rectilinear import Layout
from app.grids.store import F32

F64 = npt.NDArray[np.float64]

TMS: dict[str, morecantile.TileMatrixSet] = {
    "3857": morecantile.tms.get("WebMercatorQuad"),
    "3395": morecantile.tms.get("WorldMercatorWGS84Quad"),
}

# Радиус сферы равной площади (WGS84), м: для площадей ячеек точность ~0.1 %.
_AUTHALIC_R = 6371007.1809


def cell_areas_m2(x_edges: F64, y_edges: F64) -> F64:
    """Площадь каждой ячейки прямоугольной WGS84-сетки (м²), форма [rows, cols]."""
    dlon = np.radians(np.diff(x_edges))  # [cols]
    dsin = np.diff(np.sin(np.radians(y_edges)))  # [rows] = sin(lat2) − sin(lat1)
    return _AUTHALIC_R**2 * dsin[:, None] * dlon[None, :]


def overview_min_zoom(layout: Layout) -> int:
    """Минимальный зум, с которого ячейка ≥ ~2 px (ниже — рисуем обзор).

    Оценка по медианной высоте ячейки и разрешению Web Mercator на средней широте
    сетки. Для 3395 разрешение по широте близко — порог тот же.
    """
    if layout.y_edges.size < 2:
        return 0
    cell_m = float(np.median(np.diff(layout.y_edges))) * 111_320.0
    if cell_m <= 0:
        return 0
    lat = float(np.median(layout.y_edges))
    res0 = 156543.0339 * math.cos(math.radians(lat))
    z = int(math.ceil(math.log2(2 * res0 / cell_m)))
    return max(0, min(22, z))


def _lonlat_bounds(tms_id: str, z: int, x: int, y: int) -> tuple[float, float, float, float]:
    b = TMS[tms_id].xy_bounds(morecantile.Tile(x, y, z))
    to_ll = transformer(f"EPSG:{tms_id}", WGS84)
    cx = np.array([b.left, b.right, b.left, b.right])
    cy = np.array([b.bottom, b.bottom, b.top, b.top])
    lon, lat = to_ll.transform(cx, cy)
    return float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())


def overview_tile(
    values: F32,
    layout: Layout,
    value_kind: str,
    tms_id: str,
    z: int,
    x: int,
    y: int,
    size: int = 256,
) -> F32:
    """Агрегированный тайл `float32[size,size]` (NaN — «нет данных»).

    Ячейка целиком относится к пикселю, в котором лежит её центр: при ячейке,
    заметно меньшей пикселя, это и есть нужный агрегат.
    """
    lon_min, lat_min, lon_max, lat_max = _lonlat_bounds(tms_id, z, x, y)
    xc = (layout.x_edges[:-1] + layout.x_edges[1:]) / 2
    yc = (layout.y_edges[:-1] + layout.y_edges[1:]) / 2
    col_lo = int(np.searchsorted(xc, lon_min, side="left"))
    col_hi = int(np.searchsorted(xc, lon_max, side="right"))
    row_lo = int(np.searchsorted(yc, lat_min, side="left"))
    row_hi = int(np.searchsorted(yc, lat_max, side="right"))
    if col_hi <= col_lo or row_hi <= row_lo:
        return np.full((size, size), np.nan, dtype=np.float32)

    idx = layout.index[row_lo:row_hi, col_lo:col_hi]
    dense = np.full(idx.shape, np.nan, dtype=np.float32)
    ok_idx = (idx >= 0) & (idx < values.size)
    if ok_idx.any():
        dense[ok_idx] = values[idx[ok_idx]]
    area = cell_areas_m2(layout.x_edges[col_lo : col_hi + 1], layout.y_edges[row_lo : row_hi + 1])

    xs = (layout.x_edges[col_lo:col_hi] + layout.x_edges[col_lo + 1 : col_hi + 1]) / 2
    ys = (layout.y_edges[row_lo:row_hi] + layout.y_edges[row_lo + 1 : row_hi + 1]) / 2
    to_m = transformer(WGS84, f"EPSG:{tms_id}")
    lon_g, lat_g = np.meshgrid(xs, ys)
    x_m, y_m = to_m.transform(lon_g, lat_g)
    b = TMS[tms_id].xy_bounds(morecantile.Tile(x, y, z))
    px = ((x_m - b.left) / (b.right - b.left) * size).astype(np.int64)
    py = ((b.top - y_m) / (b.top - b.bottom) * size).astype(np.int64)

    valid = (px >= 0) & (px < size) & (py >= 0) & (py < size) & np.isfinite(dense)
    acc_val = np.zeros((size, size), dtype=np.float64)
    acc_va = np.zeros((size, size), dtype=np.float64)
    acc_area = np.zeros((size, size), dtype=np.float64)
    if valid.any():
        v = dense[valid].astype(np.float64)
        a = area[valid]
        np.add.at(acc_val, (py[valid], px[valid]), v)
        np.add.at(acc_va, (py[valid], px[valid]), v * a)
        np.add.at(acc_area, (py[valid], px[valid]), a)

    out = np.full((size, size), np.nan, dtype=np.float32)
    ok = acc_area > 0
    if value_kind == "intensive":
        out[ok] = (acc_va[ok] / acc_area[ok]).astype(np.float32)
    else:
        # Плотность: сумма значений / сумма площади → на км².
        out[ok] = (acc_val[ok] / acc_area[ok] * 1e6).astype(np.float32)
    return out


def downsample2x(tile: F32) -> F32:
    """2×2 среднее с пропуском NaN (NaN, если все четыре пикселя — «нет данных»)."""
    h, w = tile.shape
    blocks = tile[: h // 2 * 2, : w // 2 * 2].reshape(h // 2, 2, w // 2, 2)
    sums = np.nansum(blocks, axis=(1, 3))
    counts = np.isfinite(blocks).sum(axis=(1, 3))
    out = np.full((h // 2, w // 2), np.nan, dtype=np.float32)
    ok = counts > 0
    out[ok] = (sums[ok] / counts[ok]).astype(np.float32)
    return out
