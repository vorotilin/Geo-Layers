"""Тепловая карта с радиусом в пикселях (brief §3, design §6) — чисто визуальная.

Считается на лету из точек набора: гистограмма в пиксели тайла → свёртка гауссовым
ядром радиуса `radius_px` → цвет. Легенда относительная (без единиц).
"""

import io

import morecantile
import numpy as np
import numpy.typing as npt
from PIL import Image

from app.grids.crs import WGS84, transformer

TMS: dict[str, morecantile.TileMatrixSet] = {
    "3857": morecantile.tms.get("WebMercatorQuad"),
    "3395": morecantile.tms.get("WorldMercatorWGS84Quad"),
}


def _kernel(radius_px: float) -> npt.NDArray[np.float64]:
    r = max(1, int(round(radius_px)))
    xs = np.arange(-r, r + 1) / max(radius_px, 1e-6)
    k = np.exp(-0.5 * (xs[:, None] ** 2 + xs[None, :] ** 2))
    return k / k.sum()


def _fftconvolve_same(
    a: npt.NDArray[np.float64], k: npt.NDArray[np.float64]
) -> npt.NDArray[np.float64]:
    shape = (a.shape[0] + k.shape[0] - 1, a.shape[1] + k.shape[1] - 1)
    fa = np.fft.rfft2(a, s=shape)
    fk = np.fft.rfft2(k, s=shape)
    out = np.fft.irfft2(fa * fk, s=shape)
    oy, ox = (k.shape[0] - 1) // 2, (k.shape[1] - 1) // 2
    return out[oy : oy + a.shape[0], ox : ox + a.shape[1]]


def _colormap(dens: npt.NDArray[np.float64]) -> npt.NDArray[np.uint8]:
    """Плотность [0,1] → RGBA (синий → зелёный → красный, прозрачность по плотности)."""
    d = np.clip(dens, 0.0, 1.0)
    r = np.clip(3 * d - 2, 0, 1)
    g = np.clip(1 - np.abs(3 * d - 1), 0, 1)
    b = np.clip(1 - 3 * d, 0, 1)
    a = d
    return np.stack([r * 255, g * 255, b * 255, a * 255], axis=-1).astype(np.uint8)


def empty_png(size: int) -> bytes:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_pixel_heatmap(
    lon: npt.ArrayLike,
    lat: npt.ArrayLike,
    weights: npt.ArrayLike,
    tms_id: str,
    z: int,
    x: int,
    y: int,
    radius_px: float,
    size: int = 256,
) -> bytes:
    lon_a = np.asarray(lon, dtype=np.float64)
    lat_a = np.asarray(lat, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)

    b = TMS[tms_id].xy_bounds(morecantile.Tile(x, y, z))
    to_ll = transformer(f"EPSG:{tms_id}", WGS84)
    cx = np.array([b.left, b.right, b.left, b.right])
    cy = np.array([b.bottom, b.bottom, b.top, b.top])
    lons, lats = to_ll.transform(cx, cy)
    lon_min, lon_max = float(lons.min()), float(lons.max())
    lat_min, lat_max = float(lats.min()), float(lats.max())

    margin = radius_px * (lon_max - lon_min) / size * 3
    keep = (
        (lon_a >= lon_min - margin)
        & (lon_a <= lon_max + margin)
        & (lat_a >= lat_min - margin)
        & (lat_a <= lat_max + margin)
    )
    lon_a, lat_a, w = lon_a[keep], lat_a[keep], w[keep]
    if lon_a.size == 0:
        return empty_png(size)

    to_m = transformer(WGS84, f"EPSG:{tms_id}")
    xm, ym = to_m.transform(lon_a, lat_a)
    px = ((xm - b.left) / (b.right - b.left) * size).astype(np.int64)
    py = ((b.top - ym) / (b.top - b.bottom) * size).astype(np.int64)
    valid = (px >= 0) & (px < size) & (py >= 0) & (py < size)

    hist = np.zeros((size, size), dtype=np.float64)
    if valid.any():
        np.add.at(hist, (py[valid], px[valid]), w[valid])
    dens = _fftconvolve_same(hist, _kernel(radius_px))
    peak = dens.max()
    if peak <= 0:
        return empty_png(size)

    rgba = _colormap(dens / peak)
    img = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
