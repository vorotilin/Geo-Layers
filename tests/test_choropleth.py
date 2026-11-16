"""Юнит: картограмма — соединение точек с полигонами и нормировки."""

import numpy as np
import pytest
from shapely.geometry import box

from app.derive.binning import BinningError
from app.derive.choropleth import count_in_polygons, normalize, polygon_areas_m2


def test_count_in_polygons() -> None:
    # Два квадрата: [0,1]×[0,1] и [2,3]×[2,3].
    polys = [box(0, 0, 1, 1), box(2, 2, 3, 3)]
    lon = np.array([0.5, 0.6, 2.5, 2.6, 2.7])
    lat = np.array([0.5, 0.5, 2.5, 2.5, 2.5])
    counts = count_in_polygons(lon, lat, polys)
    assert counts.tolist() == [2.0, 3.0]

    # С весами: сумма.
    w = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert count_in_polygons(lon, lat, polys, weight=w).tolist() == [3.0, 12.0]


def test_normalize() -> None:
    counts = np.array([2.0, 3.0])
    areas = np.array([1e6, 2e6])  # 1 км² и 2 км²
    assert normalize(counts, areas, "none").tolist() == [2.0, 3.0]
    assert normalize(counts, areas, "per_km2").tolist() == [2.0, 1.5]
    assert normalize(counts, areas, "share").tolist() == pytest.approx([0.4, 0.6])
    with pytest.raises(BinningError):
        normalize(counts, areas, "median")


def test_polygon_areas_m2() -> None:
    # Квадрат 1°×1° на экваторе ≈ 1.236e10 м².
    a = polygon_areas_m2([box(0, 0, 1, 1)])[0]
    assert a == pytest.approx(1.2365e10, rel=1e-2)


def test_errors() -> None:
    with pytest.raises(BinningError):
        count_in_polygons(np.array([]), np.array([]), [box(0, 0, 1, 1)])
    with pytest.raises(BinningError):
        count_in_polygons(np.array([0.5]), np.array([0.5]), [])
