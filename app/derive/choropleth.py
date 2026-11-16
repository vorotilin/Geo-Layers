"""Картограмма: точки × полигоны → число на полигон и нормировки (brief §3, design §6).

Пространственное соединение — shapely STRtree. Нормировки: none | per_km2 (геодезическая
площадь) | share (доля от суммы). Тайлы полигонов требуют rasterio (не подключён) —
здесь считаются данные и рейтинг, рендер полигонов отложен (known-issues).
"""

import numpy as np
import numpy.typing as npt
from pyproj import Geod
from shapely import STRtree
from shapely.geometry import Point, Polygon

from app.derive.binning import BinningError

F64 = npt.NDArray[np.float64]

_GEOD = Geod(ellps="WGS84")


def count_in_polygons(
    lon: npt.ArrayLike,
    lat: npt.ArrayLike,
    polygons: list[Polygon],
    weight: npt.ArrayLike | None = None,
) -> F64:
    """Число точек (или сумма весов) внутри каждого полигона."""
    lon_a = np.asarray(lon, dtype=np.float64)
    lat_a = np.asarray(lat, dtype=np.float64)
    if lon_a.size == 0 or lon_a.shape != lat_a.shape:
        raise BinningError("нет точек или разные длины")
    if not polygons:
        raise BinningError("нет полигонов")
    w = np.ones(lon_a.size) if weight is None else np.asarray(weight, dtype=np.float64)
    tree = STRtree(polygons)
    counts = np.zeros(len(polygons), dtype=np.float64)
    for i in range(lon_a.size):
        hits = tree.query(Point(lon_a[i], lat_a[i]), predicate="within")
        for h in hits:
            counts[int(h)] += w[i]
    return counts


def polygon_areas_m2(polygons: list[Polygon]) -> F64:
    """Геодезические площади полигонов (м²), координаты — долгота/широта."""
    return np.array([abs(_GEOD.geometry_area_perimeter(p)[0]) for p in polygons], dtype=np.float64)


def normalize(counts: F64, areas_m2: F64, method: str) -> F64:
    """Нормировки: none | per_km2 | share (доля от суммы)."""
    out = counts.astype(np.float64)
    if method == "none":
        return out
    if method == "per_km2":
        return out / (areas_m2 / 1e6)
    if method == "share":
        total = out.sum()
        return out / total if total else out
    raise BinningError(f"неизвестная нормировка: {method}")
