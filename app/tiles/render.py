"""Рендер тайла: тайл индексов сетки → значения → классы → PNG (design §5, ADR-0005/0006).

Тайловые схемы берём из morecantile: 3857 = WebMercatorQuad, 3395 =
WorldMercatorWGS84Quad. Тайл индексов зависит только от сетки и схемы и кэшируется.
M1: фиксированная палитра и квантильные классы; обзоры на мелких зумах — M2.
"""

import io
from typing import Any

import morecantile
import numpy as np
import numpy.typing as npt
from PIL import Image

from app.grids.crs import GridCRS, transformer
from app.grids.rectilinear import NO_CELL, Layout
from app.grids.store import _LRU, F32, take
from app.styles import Style, compute_breaks, hex_to_rgb
from app.tiles.overview import overview_min_zoom, overview_tile

TMS: dict[str, morecantile.TileMatrixSet] = {
    "3857": morecantile.tms.get("WebMercatorQuad"),
    "3395": morecantile.tms.get("WorldMercatorWGS84Quad"),
}
MAX_ZOOM = 22

# 7 классов, последовательная палитра (жёлтый → красный); индекс 0 — «нет данных».
PALETTE = [
    (255, 255, 178),
    (254, 217, 118),
    (254, 178, 76),
    (253, 141, 60),
    (252, 78, 42),
    (227, 26, 28),
    (177, 0, 38),
]
CLASSES = len(PALETTE)
ALPHA = 200

# 256×256 int32 = 256 КБ на тайл: 512 тайлов ≈ 128 МБ на процесс api (их API_WORKERS).
_index_cache = _LRU(512)
# Обзорные float32-тайлы (256×256 = 256 КБ) — общие для всех стилей.
_overview_cache = _LRU(256)


def pixel_centers(
    tms_id: str, z: int, x: int, y: int, size: int
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Центры пикселей тайла в метрах схемы: (xs[size], ys[size]) — по столбцам и строкам."""
    b = TMS[tms_id].xy_bounds(morecantile.Tile(x, y, z))
    step_x = (b.right - b.left) / size
    step_y = (b.top - b.bottom) / size
    xs = b.left + (np.arange(size, dtype=np.float64) + 0.5) * step_x
    ys = b.top - (np.arange(size, dtype=np.float64) + 0.5) * step_y
    return xs.astype(np.float64), ys.astype(np.float64)


def index_tile(
    layout: Layout, layout_key: str, crs: GridCRS, tms_id: str, z: int, x: int, y: int, size: int
) -> npt.NDArray[np.int32]:
    """idx ячейки под центром каждого пикселя ([size, size], −1 — пусто)."""
    cache_key = f"{layout_key}|{tms_id}|{z}/{x}/{y}|{size}"
    cached = _index_cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    xs, ys = pixel_centers(tms_id, z, x, y, size)
    to_grid = transformer(f"EPSG:{tms_id}", crs.name)
    if crs.is_wgs84:
        # Обе схемы — Меркатор: долгота зависит только от x, широта — только от y.
        lon, _ = to_grid.transform(xs, np.full(size, ys[0]))
        _, lat = to_grid.transform(np.full(size, xs[0]), ys)
        col = np.searchsorted(layout.x_edges, lon, side="right") - 1
        row = np.searchsorted(layout.y_edges, lat, side="right") - 1
        rows, cols = layout.shape
        ok_c = (col >= 0) & (col < cols)
        ok_r = (row >= 0) & (row < rows)
        out = np.full((size, size), NO_CELL, dtype=np.int32)
        if ok_c.any() and ok_r.any():
            sub = layout.index[np.ix_(row[ok_r], col[ok_c])]
            out[np.ix_(ok_r, ok_c)] = sub
    else:
        gx, gy = to_grid.transform(*np.meshgrid(xs, ys))
        out = layout.lookup(gx, gy)
    _index_cache.put(cache_key, out)
    return out


def quantile_breaks(stats: dict[str, Any] | None) -> npt.NDArray[np.float64] | None:
    """Внутренние границы CLASSES квантильных классов из p1…p99 статистики."""
    if not stats or not stats.get("percentiles"):
        return None
    p = np.asarray(stats["percentiles"], dtype=np.float64)  # p[k-1] = k-й перцентиль
    ks = [round(100 * i / CLASSES) for i in range(1, CLASSES)]
    return np.array([p[k - 1] for k in ks])


def classify(values: F32, breaks: npt.NDArray[np.float64] | None) -> npt.NDArray[np.uint8]:
    """0 — нет данных, 1…CLASSES — класс."""
    out = np.zeros(values.shape, dtype=np.uint8)
    ok = np.isfinite(values)
    if breaks is None or not ok.any():
        return out
    out[ok] = (np.digitize(values[ok], breaks, right=False) + 1).astype(np.uint8)
    return out


def _png_palette() -> tuple[list[int], bytes]:
    flat = [0, 0, 0]
    for rgb in PALETTE:
        flat.extend(rgb)
    alpha = bytes([0] + [ALPHA] * CLASSES)
    return flat, alpha


_FLAT, _ALPHA = _png_palette()


def encode_png(
    classes: npt.NDArray[np.uint8],
    flat: list[int] | None = None,
    alpha: bytes | None = None,
) -> bytes:
    img = Image.fromarray(classes, mode="P")
    img.putpalette(_FLAT if flat is None else flat)
    buf = io.BytesIO()
    img.save(buf, format="PNG", transparency=_ALPHA if alpha is None else alpha, compress_level=3)
    return buf.getvalue()


def empty_png(size: int) -> bytes:
    return encode_png(np.zeros((size, size), dtype=np.uint8))


def palette_flat(
    colors: list[tuple[int, int, int]], no_data_rgb: tuple[int, int, int] = (0, 0, 0)
) -> list[int]:
    """Плоская палитра PNG: индекс 0 — «нет данных», дальше — классы."""
    flat = list(no_data_rgb)
    for rgb in colors:
        flat.extend(rgb)
    return flat


def palette_alpha(
    colors: list[tuple[int, int, int]], opacity: int, no_data_opaque: bool = False
) -> bytes:
    first = 255 if no_data_opaque else 0
    return bytes([first] + [opacity] * len(colors))


def stylize(tile_vals: F32, stats: dict[str, Any] | None, style: Style) -> bytes:
    """Раскраска уже выбранных значений тайла: breaks → классы → палитра → PNG."""
    breaks = compute_breaks(style, stats)
    classes = classify(tile_vals, breaks)
    no_data_rgb: tuple[int, int, int] = (0, 0, 0)
    no_data_opaque = False
    if style.no_data != "transparent":
        no_data_rgb = hex_to_rgb(style.no_data)
        no_data_opaque = True
    return encode_png(
        classes,
        flat=palette_flat(style.colors(), no_data_rgb),
        alpha=palette_alpha(style.colors(), style.opacity, no_data_opaque),
    )


def render_styled(
    values: F32,
    idx: npt.NDArray[np.int32],
    stats: dict[str, Any] | None,
    style: Style,
) -> bytes:
    """Раскраска выборки `values[idx]` по стилю (для тестов и совместимости)."""
    return stylize(take(values, idx), stats, style)


def _tile_values(
    values: F32,
    layout: Layout,
    layout_key: str,
    crs: GridCRS,
    value_kind: str,
    tms_id: str,
    z: int,
    x: int,
    y: int,
    size: int,
    values_key: str | None,
) -> F32:
    """Значения тайла: на мелких зумах — обзорный агрегат, иначе — по ячейкам."""
    if crs.is_wgs84 and z < overview_min_zoom(layout):
        cache_key = (
            f"{values_key or id(values)}|{layout_key}|{value_kind}|{tms_id}|{z}/{x}/{y}|{size}"
        )
        cached = _overview_cache.get(cache_key)
        if cached is None:
            cached = overview_tile(values, layout, value_kind, tms_id, z, x, y, size)
            _overview_cache.put(cache_key, cached)
        return cached  # type: ignore[no-any-return]
    idx = index_tile(layout, layout_key, crs, tms_id, z, x, y, size)
    return take(values, idx)


def render(
    values: F32,
    layout: Layout,
    layout_key: str,
    crs: GridCRS,
    stats: dict[str, Any] | None,
    tms_id: str,
    z: int,
    x: int,
    y: int,
    size: int = 256,
    style: Style | None = None,
    value_kind: str = "intensive",
    values_key: str | None = None,
) -> bytes:
    tile_vals = _tile_values(
        values, layout, layout_key, crs, value_kind, tms_id, z, x, y, size, values_key
    )
    if style is not None:
        return stylize(tile_vals, stats, style)
    return encode_png(classify(tile_vals, quantile_breaks(stats)))


def render_polygon_tile(
    values: F32,
    polygons: list[Any],
    stats: dict[str, Any] | None,
    tms_id: str,
    z: int,
    x: int,
    y: int,
    size: int = 256,
    style: Style | None = None,
) -> bytes:
    """Тайл для сетки `polygons`: растеризация полигонов → idx → значения → PNG."""
    from app.grids.polygons import PolygonGrid

    idx = PolygonGrid(polygons).index_tile(tms_id, z, x, y, size)
    tile_vals = take(values, idx)
    if style is not None:
        return stylize(tile_vals, stats, style)
    return encode_png(classify(tile_vals, quantile_breaks(stats)))


def tile_in_range(tms_id: str, z: int, x: int, y: int) -> bool:
    if not 0 <= z <= MAX_ZOOM:
        return False
    m = TMS[tms_id].matrix(z)
    return 0 <= x < m.matrixWidth and 0 <= y < m.matrixHeight
