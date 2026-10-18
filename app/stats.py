"""Статистика версии слоя (design §7). NaN = «неизвестно»: в расчёт не входит."""

from typing import Any

import numpy as np
import numpy.typing as npt

PERCENTILES = list(range(1, 100))
HISTOGRAM_BINS = 64


def compute(values: npt.NDArray[np.float32], cell_count: int) -> dict[str, Any]:
    v = values[np.isfinite(values)].astype(np.float64)
    stats: dict[str, Any] = {
        "count": int(v.size),
        "cells": int(cell_count),
        "coverage": round(v.size / cell_count, 6) if cell_count else 0.0,
    }
    if v.size == 0:
        return stats | {"min": None, "max": None, "mean": None, "sum": None, "percentiles": None}
    lo, hi = float(v.min()), float(v.max())
    counts, edges = np.histogram(v, bins=HISTOGRAM_BINS, range=(lo, hi) if hi > lo else None)
    return stats | {
        "min": lo,
        "max": hi,
        "mean": float(v.mean()),
        "sum": float(v.sum()),
        # p1…p99: квантильные границы классов для любого их числа (до 100).
        "percentiles": [float(x) for x in np.percentile(v, PERCENTILES)],
        "histogram": {"edges": [float(x) for x in edges], "counts": [int(c) for c in counts]},
    }
