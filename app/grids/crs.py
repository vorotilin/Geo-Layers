"""Нормализация CRS. Везде порядок осей «x, y» (долгота, широта) — always_xy."""

from dataclasses import dataclass
from functools import lru_cache

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

WGS84 = "OGC:CRS84"
_CRS84 = CRS.from_user_input(WGS84)


class UnsupportedCRS(ValueError):
    pass


@dataclass(frozen=True)
class GridCRS:
    name: str  # "OGC:CRS84" или "EPSG:<код>"
    srid: int  # SRID для PostGIS

    @property
    def is_wgs84(self) -> bool:
        return self.name == WGS84


def normalize_crs(text: str | None) -> GridCRS:
    """CRS из файла/манифеста → каноническое имя и SRID. None — WGS84 (как в GeoJSON)."""
    if not text:
        return GridCRS(WGS84, 4326)
    try:
        crs = CRS.from_user_input(text)
    except CRSError as exc:
        raise UnsupportedCRS(f"неизвестная CRS: {text}") from exc
    if crs.equals(_CRS84, ignore_axis_order=True):
        return GridCRS(WGS84, 4326)
    epsg = crs.to_epsg()
    if epsg is None:
        raise UnsupportedCRS(f"CRS без кода EPSG пока не поддерживается: {text}")
    if epsg == 4326:
        # В GeoJSON координаты всегда «долгота, широта», даже если указан EPSG:4326.
        return GridCRS(WGS84, 4326)
    return GridCRS(f"EPSG:{epsg}", epsg)


@lru_cache(maxsize=64)
def transformer(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)
