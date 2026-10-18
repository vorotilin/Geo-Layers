"""Синтетические сетки с заранее известным ответом (design §12, план M1 шаг 6).

Значение ячейки кодирует её положение: `metric·10⁶ + row·10³ + col` — точно
представимо во float32, поэтому любой ответ сервиса проверяется формулой.
Оракул (`expected`) ищет ячейку независимым путём: shapely `within` по исходным
прямоугольникам, а не через границы/`searchsorted`, как сервис.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Transformer
from shapely import STRtree, box
from shapely.geometry import Point


def cell_value(metric: int, row: int, col: int) -> float:
    return metric * 1_000_000 + row * 1_000 + col


def is_hole(row: int, col: int) -> bool:
    """«Дырявая» сетка: часть ячеек отсутствует (= неизвестно, не ноль)."""
    return (row * 31 + col * 17) % 13 == 0


@dataclass
class Cell:
    key: int
    row: int
    col: int
    xmin: float
    ymin: float
    xmax: float
    ymax: float


@dataclass
class SynthGrid:
    crs: str  # CRS ячеек: "OGC:CRS84" или "EPSG:xxxx"
    cells: list[Cell]
    metrics: int = 1
    _tree: STRtree | None = field(default=None, repr=False)
    _to_grid: Transformer | None = field(default=None, repr=False)

    # --- файлы ---

    def feature(self, c: Cell) -> dict[str, Any]:
        ring = [
            [c.xmin, c.ymax],
            [c.xmin, c.ymin],
            [c.xmax, c.ymin],
            [c.xmax, c.ymax],
            [c.xmin, c.ymax],
        ]
        props: dict[str, Any] = {"zone_id": c.key}
        for m in range(self.metrics):
            props[f"m{m}"] = cell_value(m, c.row, c.col)
        return {
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        }

    def geojson(
        self, *, crs_member: bool = True, extra: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        doc: dict[str, Any] = {"type": "FeatureCollection"}
        if crs_member:
            name = (
                "urn:ogc:def:crs:OGC:1.3:CRS84"
                if self.crs == "OGC:CRS84"
                else f"urn:ogc:def:crs:EPSG::{self.crs.split(':')[1]}"
            )
            doc["crs"] = {"type": "name", "properties": {"name": name}}
        doc["features"] = [self.feature(c) for c in self.cells] + (extra or [])
        return doc

    def write(self, path: Path, **kw: Any) -> Path:
        path.write_text(json.dumps(self.geojson(**kw)), encoding="utf-8")
        return path

    def manifest(
        self, *, provider: str = "synth", period: str = "2024Q1", **kw: Any
    ) -> dict[str, Any]:
        m: dict[str, Any] = {
            "kind": "grid",
            "dataset": {"provider": provider, "title": f"Синтетика {self.crs}"},
            "period": period,
            "geometry": {"type": "geojson"},
            "key": "zone_id",
            "metrics": [
                {
                    "column": f"m{i}",
                    "slug": f"metric_{i}",
                    "title": f"Метрика {i}",
                    "unit": "ед.",
                    "value_kind": "extensive" if i % 2 else "intensive",
                }
                for i in range(self.metrics)
            ],
        }
        m.update(kw)
        return m

    # --- оракул ---

    def expected(self, lon: float, lat: float, metric: int = 0) -> float | None:
        """Значение в точке (WGS84) или None, если точка вне ячеек."""
        if self._tree is None:
            self._tree = STRtree([box(c.xmin, c.ymin, c.xmax, c.ymax) for c in self.cells])
            self._to_grid = Transformer.from_crs("OGC:CRS84", self.crs, always_xy=True)
        assert self._to_grid is not None
        x, y = self._to_grid.transform(lon, lat)
        hits = self._tree.query(Point(x, y), predicate="within")
        if len(hits) == 0:
            return None
        c = self.cells[int(hits[0])]
        return cell_value(metric, c.row, c.col)

    def random_points(
        self, n: int, seed: int = 1, margin: float = 0.02
    ) -> list[tuple[float, float]]:
        """Случайные точки (lon, lat) внутри охвата сетки, не ближе margin·ячейки к границам."""
        rng = np.random.default_rng(seed)
        to_wgs = Transformer.from_crs(self.crs, "OGC:CRS84", always_xy=True)
        out = []
        for i in rng.integers(0, len(self.cells), n):
            c = self.cells[int(i)]
            fx, fy = rng.uniform(margin, 1 - margin, 2)
            x = c.xmin + fx * (c.xmax - c.xmin)
            y = c.ymin + fy * (c.ymax - c.ymin)
            out.append(to_wgs.transform(x, y))
        return out

    def bounds_wgs84(self) -> tuple[float, float, float, float]:
        to_wgs = Transformer.from_crs(self.crs, "OGC:CRS84", always_xy=True)
        xs = [c.xmin for c in self.cells] + [c.xmax for c in self.cells]
        ys = [c.ymin for c in self.cells] + [c.ymax for c in self.cells]
        return to_wgs.transform_bounds(min(xs), min(ys), max(xs), max(ys))


def crs84_squares(
    lon0: float = 37.30,
    lat0: float = 55.50,
    dlon: float = 0.0045,
    dlat: float = 0.0027,
    nx: int = 40,
    ny: int = 30,
    *,
    wobble: float = 2e-7,
    holes: bool = True,
    metrics: int = 1,
) -> SynthGrid:
    """Квадраты по параллелям (≈ 300 м). Шаг по широте «плавает», как в образце."""
    lat_edges = [lat0]
    for r in range(ny):
        lat_edges.append(lat_edges[-1] + dlat + wobble * math.sin(r))
    cells = []
    for row in range(ny):
        for col in range(nx):
            if holes and is_hole(row, col):
                continue
            cells.append(
                Cell(
                    key=70_000_000 + row * 10_000 + col,
                    row=row,
                    col=col,
                    xmin=round(lon0 + col * dlon, 6),
                    xmax=round(lon0 + (col + 1) * dlon, 6),
                    ymin=round(lat_edges[row], 6),
                    ymax=round(lat_edges[row + 1], 6),
                )
            )
    return SynthGrid("OGC:CRS84", cells, metrics)


def utm_squares(
    epsg: int = 32637,
    x0: float = 400_000,
    y0: float = 6_160_000,
    step: float = 1_000,
    nx: int = 25,
    ny: int = 20,
    *,
    holes: bool = True,
    metrics: int = 1,
) -> SynthGrid:
    """Квадраты 1 км в UTM: на карте Меркатора они повёрнуты и не выровнены."""
    cells = [
        Cell(
            key=row * 1_000 + col,
            row=row,
            col=col,
            xmin=x0 + col * step,
            xmax=x0 + (col + 1) * step,
            ymin=y0 + row * step,
            ymax=y0 + (row + 1) * step,
        )
        for row in range(ny)
        for col in range(nx)
        if not (holes and is_hole(row, col))
    ]
    return SynthGrid(f"EPSG:{epsg}", cells, metrics)


def broken_features() -> list[dict[str, Any]]:
    """Битые объекты для отчёта об ошибках: по одному каждого вида."""
    sq = [[0.0, 1.0], [0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
    tri = [[0.0, 0.0], [1.0, 0.0], [0.5, 1.0], [0.0, 0.0]]
    return [
        {"type": "Feature", "properties": {"zone_id": 1, "m0": 1}, "geometry": None},
        {
            "type": "Feature",
            "properties": {"zone_id": 2, "m0": 1},
            "geometry": {"type": "Polygon", "coordinates": [tri]},
        },
        {
            "type": "Feature",
            "properties": {"m0": 1},
            "geometry": {"type": "Polygon", "coordinates": [sq]},
        },
        {
            "type": "Feature",
            "properties": {"zone_id": 3, "m0": 1},
            "geometry": {"type": "Point", "coordinates": [0, 0]},
        },
    ]
