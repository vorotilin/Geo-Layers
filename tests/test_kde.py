"""Юнит: KDE (тепловая карта) — пик плотности и сохранение массы."""

import numpy as np
import pytest

from app.derive.binning import BinningError
from app.derive.kde import kde


def test_kde_peak_and_mass() -> None:
    out = kde(np.array([0.0]), np.array([0.0]), bandwidth_m=100, step=25)
    values = out["values"]
    # Пик одной точки с ядром σ=100: 1/(2πσ²) × 1e6 ≈ 15.9 точек/км².
    assert 12 < values.max() < 20
    # Масса сохраняется: сумма плотности × площадь = число точек.
    total = values.sum() * 25**2 / 1e6
    assert total == pytest.approx(1.0, rel=0.05)


def test_kde_mass_with_known_step() -> None:
    lon = np.array([0.0, 0.001, 0.002])
    lat = np.array([0.0, 0.0, 0.0])
    out = kde(lon, lat, bandwidth_m=200, step=50)
    total = out["values"].sum() * 50**2 / 1e6
    assert total == pytest.approx(3.0, rel=0.05)


def test_kde_errors() -> None:
    with pytest.raises(BinningError):
        kde(np.array([0.0]), np.array([0.0]), bandwidth_m=-1)
    with pytest.raises(BinningError):
        kde(np.array([]), np.array([]), bandwidth_m=100)
