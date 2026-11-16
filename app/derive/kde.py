"""KDE (тепловая карта) точек (brief §3, design §6).

Гауссово ядро с полосой в метрах, нормированное ядро, легенда в «точках на км²».
Результат — обычная прямоугольная сетка (UTM), value_kind=intensive (плотность).
Свёртка через FFT на numpy (scipy не требуется).
"""

import numpy as np
import numpy.typing as npt
from pyproj import CRS, Transformer

from app.derive.binning import BinningError, utm_epsg
from app.grids.rectilinear import Layout
from app.stats import compute

F64 = npt.NDArray[np.float64]


def _gaussian_kernel(sigma_m: float, step: float, radius: float = 3.0) -> F64:
    """2D-ядро Гаусса (в пикселях), нормированное на 1."""
    r = max(1, int(np.ceil(radius * sigma_m / step)))
    xs = np.arange(-r, r + 1) * step / sigma_m
    k = np.exp(-0.5 * (xs[:, None] ** 2 + xs[None, :] ** 2))
    return k / k.sum()


def _fftconvolve_same(a: F64, k: F64) -> F64:
    """Свёртка через FFT с обрезкой до размера `a` (замена scipy.signal.fftconvolve)."""
    shape = (a.shape[0] + k.shape[0] - 1, a.shape[1] + k.shape[1] - 1)
    fa = np.fft.rfft2(a, s=shape)
    fk = np.fft.rfft2(k, s=shape)
    out = np.fft.irfft2(fa * fk, s=shape)
    oy, ox = (k.shape[0] - 1) // 2, (k.shape[1] - 1) // 2
    return out[oy : oy + a.shape[0], ox : ox + a.shape[1]]


def kde(
    lon: npt.ArrayLike,
    lat: npt.ArrayLike,
    *,
    bandwidth_m: float,
    step: float | None = None,
    weight: npt.ArrayLike | None = None,
) -> dict:
    """Точки → плотность (точек на км²) в прямоугольной сетке (UTM).

    `step` — шаг растра, по умолчанию bandwidth/4. Охват расширяется на 3·bandwidth,
    чтобы края ядра не обрезались.
    """
    if bandwidth_m <= 0:
        raise BinningError("bandwidth должен быть положительным")
    lon_a = np.asarray(lon, dtype=np.float64)
    lat_a = np.asarray(lat, dtype=np.float64)
    if lon_a.size == 0 or lon_a.shape != lat_a.shape:
        raise BinningError("нет точек или разные длины")
    epsg = utm_epsg(float(lon_a.mean()), float(lat_a.mean()))
    to_utm = Transformer.from_crs("OGC:CRS84", CRS.from_epsg(epsg), always_xy=True)
    x, y = to_utm.transform(lon_a, lat_a)

    step = bandwidth_m / 4 if step is None else float(step)
    if step <= 0:
        raise BinningError("step должен быть положительным")
    pad = 3 * bandwidth_m
    x0 = np.floor((x.min() - pad) / step) * step
    y0 = np.floor((y.min() - pad) / step) * step
    x1 = np.ceil((x.max() + pad) / step) * step
    y1 = np.ceil((y.max() + pad) / step) * step
    ncols = int(round((x1 - x0) / step))
    nrows = int(round((y1 - y0) / step))

    cols = np.clip(((x - x0) / step).astype(np.int64), 0, ncols - 1)
    rows = np.clip(((y - y0) / step).astype(np.int64), 0, nrows - 1)
    w = np.ones(lon_a.size) if weight is None else np.asarray(weight, dtype=np.float64)
    hist = np.bincount(rows * ncols + cols, weights=w, minlength=nrows * ncols).reshape(
        nrows, ncols
    )

    kernel = _gaussian_kernel(bandwidth_m, step)
    density = _fftconvolve_same(hist.astype(np.float64), kernel) / step**2 * 1e6  # на км²

    x_edges = x0 + np.arange(ncols + 1) * step
    y_edges = y0 + np.arange(nrows + 1) * step
    n = nrows * ncols
    index = np.arange(n, dtype=np.int32).reshape(nrows, ncols)
    values32 = density.astype(np.float32)
    return {
        "crs": f"EPSG:{epsg}",
        "layout": Layout(x_edges, y_edges, index),
        "values": values32,
        "stats": compute(values32, n),
        "cells": n,
    }
