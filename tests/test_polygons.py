"""Юнит: сетка polygons — lookup и растеризация тайла."""

import numpy as np
from shapely.geometry import box

from app.grids.polygons import TMS, PolygonGrid
from app.grids.rectilinear import NO_CELL


def test_lookup() -> None:
    grid = PolygonGrid([box(0, 0, 1, 1), box(2, 2, 3, 3)])
    assert grid.lookup([0.5, 2.5, 10.0], [0.5, 2.5, 10.0]).tolist() == [0, 1, NO_CELL]
    assert len(grid) == 2


def test_index_tile() -> None:
    grid = PolygonGrid([box(0, 0, 1, 1)])
    t = TMS["3857"].tile(0.5, 0.5, 8)
    idx = grid.index_tile("3857", t.z, t.x, t.y)
    assert idx.shape == (256, 256)
    assert set(np.unique(idx)) <= {-1, 0}
    assert int((idx == 0).sum()) > 0  # полигон попал в тайл
    # Центр тайла (0.5, 0.5) внутри полигона.
    assert idx[128, 128] == 0


def test_empty_grid() -> None:
    grid = PolygonGrid([])
    assert len(grid) == 0
    t = TMS["3857"].tile(0.5, 0.5, 8)
    assert not (grid.index_tile("3857", t.z, t.x, t.y) >= 0).any()
