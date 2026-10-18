"""Сетка `rectilinear`: прямоугольники, выровненные по осям своей CRS (design §4).

Храним отсортированные границы столбцов (`x_edges`) и строк (`y_edges`) и таблицу
`index[row, col] → idx` (−1 — ячейки нет). Шаг может плавать: в образце шаг по
широте меняется в 6-м знаке, поэтому границы берём из самих ячеек, а не из «шага».
"""

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

NO_CELL = -1

F64 = npt.NDArray[np.float64]
I32 = npt.NDArray[np.int32]
I64 = npt.NDArray[np.int64]


@dataclass(frozen=True)
class Layout:
    x_edges: F64
    y_edges: F64
    index: I32  # [rows, cols]

    @property
    def shape(self) -> tuple[int, int]:
        return int(self.index.shape[0]), int(self.index.shape[1])

    def lookup(self, x: npt.ArrayLike, y: npt.ArrayLike) -> I32:
        """Номера ячеек для точек в CRS сетки (−1 — мимо). Векторно, любой формы.

        Точка на общей границе относится к ячейке справа / сверху.
        """
        xa = np.asarray(x, dtype=np.float64)
        ya = np.asarray(y, dtype=np.float64)
        col = np.searchsorted(self.x_edges, xa, side="right") - 1
        row = np.searchsorted(self.y_edges, ya, side="right") - 1
        rows, cols = self.shape
        ok = (col >= 0) & (col < cols) & (row >= 0) & (row < rows) & np.isfinite(xa + ya)
        out = np.full(xa.shape, NO_CELL, dtype=np.int32)
        out[ok] = self.index[row[ok], col[ok]]
        return out


def cluster_edges(values: F64, tol: float) -> F64:
    """Сливает близкие координаты границ (разница < tol) в одну; возвращает средние."""
    v = np.sort(values[np.isfinite(values)])
    if v.size == 0:
        return v
    breaks = np.flatnonzero(np.diff(v) >= tol) + 1
    groups = np.split(v, breaks)
    return np.array([g.mean() for g in groups], dtype=np.float64)


def edge_tolerance(xmin: F64, xmax: F64, ymin: F64, ymax: F64) -> tuple[float, float]:
    """Допуск слияния границ — 1 % медианного размера ячейки по оси."""
    tx = float(np.median(xmax - xmin)) * 0.01
    ty = float(np.median(ymax - ymin)) * 0.01
    return max(tx, 1e-12), max(ty, 1e-12)


def _nearest(edges: F64, v: F64) -> I64:
    """Индекс ближайшей границы для каждого значения."""
    pos = np.clip(np.searchsorted(edges, v), 1, len(edges) - 1)
    left = edges[pos - 1]
    right = edges[pos]
    return np.where(np.abs(v - left) <= np.abs(right - v), pos - 1, pos).astype(np.int64)


@dataclass(frozen=True)
class BuildResult:
    layout: Layout
    # Ячейки, которые не легли в сетку: span — накрывает несколько столбцов/строк,
    # overlap — занимает уже занятую клетку. Номера — позиции во входных массивах.
    bad_span: I64
    overlap: I64


def build(xmin: F64, xmax: F64, ymin: F64, ymax: F64, idx: I64) -> BuildResult:
    """Строит раскладку по прямоугольникам ячеек (порядок любой, idx — их номера)."""
    if xmin.size == 0:
        empty = np.zeros(0, dtype=np.float64)
        return BuildResult(
            Layout(empty, empty, np.zeros((0, 0), dtype=np.int32)),
            np.zeros(0, dtype=np.int64),
            np.zeros(0, dtype=np.int64),
        )
    tx, ty = edge_tolerance(xmin, xmax, ymin, ymax)
    x_edges = cluster_edges(np.concatenate([xmin, xmax]), tx)
    y_edges = cluster_edges(np.concatenate([ymin, ymax]), ty)
    c0, c1 = _nearest(x_edges, xmin), _nearest(x_edges, xmax)
    r0, r1 = _nearest(y_edges, ymin), _nearest(y_edges, ymax)

    one_cell = (c1 == c0 + 1) & (r1 == r0 + 1)
    bad_span = np.flatnonzero(~one_cell)

    rows, cols = max(len(y_edges) - 1, 0), max(len(x_edges) - 1, 0)
    index = np.full((rows, cols), NO_CELL, dtype=np.int32)
    good = np.flatnonzero(one_cell)
    flat = r0[good] * cols + c0[good]
    # Первый по idx занимает клетку, остальные — overlap.
    order = np.lexsort((idx[good], flat))
    flat_sorted = flat[order]
    first = np.ones(flat_sorted.size, dtype=bool)
    first[1:] = flat_sorted[1:] != flat_sorted[:-1]
    winners = good[order[first]]
    overlap = good[order[~first]]
    index.reshape(-1)[r0[winners] * cols + c0[winners]] = idx[winners]
    return BuildResult(Layout(x_edges, y_edges, index), bad_span, np.sort(overlap))
