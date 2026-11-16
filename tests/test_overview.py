"""Юнит: обзоры на мелких зумах — площади ячеек, агрегат, даунсэмплинг."""

import io

import numpy as np
import pytest
from PIL import Image

from app.grids.crs import normalize_crs
from app.grids.rectilinear import Layout, build
from app.tiles import render
from app.tiles.overview import (
    TMS,
    cell_areas_m2,
    downsample2x,
    overview_min_zoom,
    overview_tile,
)
from tests.synth import SynthGrid, crs84_squares


def _layout_values(fill: float) -> tuple[SynthGrid, Layout, np.ndarray]:
    grid = crs84_squares(nx=8, ny=8, holes=False)
    c = grid.cells
    arr = lambda name: np.array([getattr(x, name) for x in c])  # noqa: E731
    layout = build(arr("xmin"), arr("xmax"), arr("ymin"), arr("ymax"), np.arange(len(c))).layout
    return grid, layout, np.full(len(c), fill, dtype=np.float32)


def _tile_at(grid: SynthGrid, z: int) -> tuple[int, int]:
    lon0, lat0, lon1, lat1 = grid.bounds_wgs84()
    t = TMS["3857"].tile((lon0 + lon1) / 2, (lat0 + lat1) / 2, z)
    return t.x, t.y


def test_cell_areas_equator_square() -> None:
    a = cell_areas_m2(np.array([0.0, 1.0]), np.array([0.0, 1.0]))
    # 1°×1° на экваторе ≈ 111.2 × 111.2 км.
    assert a[0, 0] == pytest.approx(1.2365e10, rel=1e-3)


def test_overview_intensive_uniform() -> None:
    grid, layout, values = _layout_values(5.0)
    x, y = _tile_at(grid, 7)  # на z7 ячейка (~300 м) меньше пикселя (~700 м)
    out = overview_tile(values, layout, "intensive", "3857", 7, x, y)
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    assert np.allclose(finite, 5.0, atol=1e-4)


def test_overview_extensive_is_density_per_km2() -> None:
    grid, layout, values = _layout_values(100.0)
    area = cell_areas_m2(layout.x_edges, layout.y_edges)
    mean_area = float(area[layout.index >= 0].mean())
    expected = 100.0 / mean_area * 1e6  # на км²
    x, y = _tile_at(grid, 7)
    out = overview_tile(values, layout, "extensive", "3857", 7, x, y)
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    assert np.allclose(finite, expected, rtol=1e-3)


def test_overview_no_data_is_nan() -> None:
    grid, layout, _ = _layout_values(5.0)
    values = np.full(layout.index.max() + 1, np.nan, dtype=np.float32)
    x, y = _tile_at(grid, 7)
    out = overview_tile(values, layout, "intensive", "3857", 7, x, y)
    assert not np.isfinite(out).any()


def test_downsample2x() -> None:
    tile = np.arange(1, 17, dtype=np.float32).reshape(4, 4)
    out = downsample2x(tile)
    assert out.shape == (2, 2)
    assert out[0, 0] == pytest.approx((1 + 2 + 5 + 6) / 4)
    assert out[1, 1] == pytest.approx((11 + 12 + 15 + 16) / 4)


def test_downsample2x_skips_nan() -> None:
    tile = np.array([[1.0, np.nan], [3.0, 4.0]], dtype=np.float32)
    out = downsample2x(tile)
    assert out.shape == (1, 1)
    assert out[0, 0] == pytest.approx((1 + 3 + 4) / 3)
    assert np.isnan(downsample2x(np.full((2, 2), np.nan, dtype=np.float32))[0, 0])


def test_overview_min_zoom_reasonable() -> None:
    _, layout, _ = _layout_values(5.0)
    z = overview_min_zoom(layout)
    assert 8 <= z <= 11  # ячейка ~300 м → обзор на зумах ниже ~10


def test_render_low_zoom_uses_overview() -> None:
    grid, layout, values = _layout_values(5.0)
    stats = {"count": len(values), "min": 5.0, "max": 5.0, "percentiles": [5.0] * 99}
    x, y = _tile_at(grid, 5)
    png = render.render(values, layout, "k", normalize_crs("OGC:CRS84"), stats, "3857", 5, x, y)
    img = np.asarray(Image.open(io.BytesIO(png)))
    assert img.shape == (256, 256) and img.max() > 0
