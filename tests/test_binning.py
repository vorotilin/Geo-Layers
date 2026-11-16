"""Юнит: биннинг точек в квадратную сетку."""

import numpy as np
import pytest

from app.derive.binning import BinningError, bin_squares, utm_epsg


def test_utm_epsg() -> None:
    assert utm_epsg(37.6, 55.7) == 32637  # Москва, север
    assert utm_epsg(37.6, -33.9) == 32737  # южное полушарие


def test_bin_squares_count() -> None:
    lon = np.array([0.0, 0.005, 0.02])  # ~0, ~555 м, ~2222 м
    lat = np.array([0.0, 0.0, 0.0])
    out = bin_squares(lon, lat, step=1000, agg="count")
    values = out["values"]
    assert values.sum() == 3  # число точек сохраняется
    assert values.max() == 2  # две ближние в одной ячейке
    assert int((values > 0).sum()) == 2  # две занятые ячейки
    assert out["cells"] == values.size
    assert out["layout"].x_edges.size == out["layout"].shape[1] + 1


def test_bin_squares_sum_and_mean() -> None:
    lon = np.array([0.0, 0.005, 0.02])
    lat = np.array([0.0, 0.0, 0.0])
    w = np.array([10.0, 20.0, 5.0])
    out = bin_squares(lon, lat, step=1000, agg="sum", weight=w)
    assert out["values"].sum() == pytest.approx(35.0)

    out2 = bin_squares(lon, lat, step=1000, agg="mean", value=w)
    finite = out2["values"][np.isfinite(out2["values"])]
    assert sorted(np.round(finite, 1).tolist()) == [5.0, 15.0]
    # Пустая ячейка — «нет данных», не ноль.
    assert np.isnan(out2["values"]).any()


def test_bin_squares_errors() -> None:
    with pytest.raises(BinningError):
        bin_squares(np.array([]), np.array([]), step=100)
    with pytest.raises(BinningError):
        bin_squares(np.array([0.0]), np.array([0.0]), step=-1)
    with pytest.raises(BinningError):
        bin_squares(np.array([0.0]), np.array([0.0]), step=100, agg="median")
    with pytest.raises(BinningError):
        bin_squares(np.array([0.0, 1.0]), np.array([0.0]), step=100)
