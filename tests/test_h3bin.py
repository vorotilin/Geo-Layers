"""Юнит: бининг точек в гексагоны H3."""

import numpy as np
import pytest

from app.derive.binning import BinningError
from app.derive.h3bin import bin_h3


def test_bin_h3_count() -> None:
    out = bin_h3(
        np.array([0.0, 0.0001, 0.0002]), np.array([0.0, 0.0, 0.0]), resolution=9, agg="count"
    )
    assert out["cells"] == len(out["polygons"])
    assert out["values"].sum() == 3
    assert out["values"].max() >= 2  # близкие точки в одном гексагоне
    assert all(p.is_valid for p in out["polygons"])


def test_bin_h3_mean() -> None:
    out = bin_h3(
        np.array([0.0, 0.0001]),
        np.array([0.0, 0.0]),
        resolution=9,
        agg="mean",
        value=np.array([10.0, 20.0]),
    )
    finite = out["values"][np.isfinite(out["values"])]
    assert finite.tolist() == [15.0]


def test_bin_h3_errors() -> None:
    with pytest.raises(BinningError):
        bin_h3(np.array([0.0]), np.array([0.0]), resolution=16)
    with pytest.raises(BinningError):
        bin_h3(np.array([]), np.array([]), resolution=9)
