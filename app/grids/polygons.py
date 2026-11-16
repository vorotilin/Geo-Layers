"""Сетка `polygons`: произвольные полигоны (районы) — lookup и растеризация тайлов.

lookup — shapely STRtree; тайл индексов — rasterio.features.rasterize (design §5.1).
Полигоны заданы в WGS84 (долгота/широта); в метры схемы переводятся при растеризации.
"""

import morecantile
import numpy as np
import numpy.typing as npt
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from shapely import STRtree
from shapely import transform as shp_transform
from shapely.geometry import Point, Polygon

from app.grids.crs import WGS84, transformer
from app.grids.rectilinear import NO_CELL

TMS: dict[str, morecantile.TileMatrixSet] = {
    "3857": morecantile.tms.get("WebMercatorQuad"),
    "3395": morecantile.tms.get("WorldMercatorWGS84Quad"),
}


class PolygonGrid:
    """Индекс ячеек-полигонов для lookup и растеризации."""

    def __init__(self, polygons: list[Polygon]) -> None:
        self.polygons = polygons
        self._tree = STRtree(polygons)

    def __len__(self) -> int:
        return len(self.polygons)

    def lookup(self, lon: npt.ArrayLike, lat: npt.ArrayLike) -> npt.NDArray[np.int32]:
        """idx полигона под каждой точкой (−1 — ни один)."""
        lon_a = np.asarray(lon, dtype=np.float64)
        lat_a = np.asarray(lat, dtype=np.float64)
        out = np.full(lon_a.shape, NO_CELL, dtype=np.int32)
        for i in range(lon_a.size):
            hits = self._tree.query(Point(lon_a.flat[i], lat_a.flat[i]), predicate="within")
            if hits.size:
                out.flat[i] = int(hits[0])
        return out

    def index_tile(
        self, tms_id: str, z: int, x: int, y: int, size: int = 256
    ) -> npt.NDArray[np.int32]:
        """Тайл индексов: номер полигона под каждым пикселем ([size, size], −1 — пусто)."""
        b = TMS[tms_id].xy_bounds(morecantile.Tile(x, y, z))
        to_m = transformer(WGS84, f"EPSG:{tms_id}")

        def to_meters(geom: Polygon) -> Polygon:
            def f(coords: np.ndarray) -> np.ndarray:
                x, y = to_m.transform(coords[:, 0], coords[:, 1])
                return np.column_stack((x, y))

            return shp_transform(geom, f)

        shapes = [(to_meters(p), i + 1) for i, p in enumerate(self.polygons)]
        out = rasterize(
            shapes,
            out_shape=(size, size),
            transform=from_bounds(b.left, b.bottom, b.right, b.top, size, size),
            fill=0,
            dtype=np.int32,
        )
        return out - 1  # 0 → NO_CELL (−1)


def create_polygon_grid(
    conn, storage, namespace: str, provider: str, polygons: list[Polygon], keys: list[str]
) -> str:
    """Создаёт сетку `polygons` + grid_cell + snapshot; возвращает grid_id."""
    import json
    import uuid

    from psycopg.types.json import Jsonb
    from shapely.geometry import mapping

    from app.grids import store

    grid_id = str(uuid.uuid4())
    snapshot_key = store.save_polygons(storage, grid_id, polygons)
    conn.execute(
        "INSERT INTO grid (id, namespace_id, provider, kind, crs, params, cell_count) "
        "VALUES (%s, %s, %s, 'polygons', 'OGC:CRS84', %s, %s)",
        (grid_id, namespace, provider, Jsonb({"polygons_key": snapshot_key}), len(polygons)),
    )
    for i, (k, p) in enumerate(zip(keys, polygons, strict=True)):
        gj = json.dumps(mapping(p))
        conn.execute(
            "INSERT INTO grid_cell (grid_id, idx, key, geom, area_m2) "
            "VALUES (%s, %s, %s, ST_GeomFromGeoJSON(%s), "
            "ST_Area(ST_GeomFromGeoJSON(%s)::geography))",
            (grid_id, i, k, gj, gj),
        )
    return grid_id
