"""Файлы сетки и массивы значений в хранилище.

- grids/<grid>/cells-<N>.npz — прямоугольники всех ячеек по idx (для дописывания);
- grids/<grid>/layout-<N>.npz — границы и таблица index[row, col] (для тайлов/значений);
- values/<version>.f32 — float32[N] значений версии (ADR-0004).

N — число ячеек: файл с новым N пишется рядом, старые читатели не ломаются.
"""

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any

import numpy as np
import numpy.typing as npt

from app.grids.rectilinear import F64, Layout
from app.storage import LocalStorage

F32 = npt.NDArray[np.float32]


@dataclass(frozen=True)
class Cells:
    xmin: F64
    xmax: F64
    ymin: F64
    ymax: F64

    def __len__(self) -> int:
        return int(self.xmin.size)

    @staticmethod
    def empty() -> "Cells":
        z = np.zeros(0, dtype=np.float64)
        return Cells(z, z, z, z)

    def append(self, other: "Cells") -> "Cells":
        return Cells(
            np.concatenate([self.xmin, other.xmin]),
            np.concatenate([self.xmax, other.xmax]),
            np.concatenate([self.ymin, other.ymin]),
            np.concatenate([self.ymax, other.ymax]),
        )


def cells_key(grid_id: str, n: int) -> str:
    return f"grids/{grid_id}/cells-{n}.npz"


def layout_key(grid_id: str, n: int) -> str:
    return f"grids/{grid_id}/layout-{n}.npz"


def values_key(version_id: str) -> str:
    return f"values/{version_id}.f32"


def polygons_key(grid_id: str) -> str:
    return f"grids/{grid_id}/polygons.npz"


def save_polygons(storage: LocalStorage, grid_id: str, polygons: list[Any]) -> str:
    """Полигоны (WKB) в порядке idx; ключ лежит в `grid.params`."""
    from shapely import to_wkb

    key = polygons_key(grid_id)
    wkb = np.array([to_wkb(p) for p in polygons], dtype=object)
    with storage.writer(key) as fh:
        np.savez(fh, wkb=wkb)
    return key


def load_polygons(storage: LocalStorage, key: str) -> list[Any]:
    from shapely import from_wkb

    with np.load(storage.path(key), allow_pickle=True) as d:
        return [from_wkb(w) for w in d["wkb"]]


def save_cells(storage: LocalStorage, grid_id: str, cells: Cells) -> str:
    key = cells_key(grid_id, len(cells))
    with storage.writer(key) as fh:
        np.savez(fh, xmin=cells.xmin, xmax=cells.xmax, ymin=cells.ymin, ymax=cells.ymax)
    return key


def load_cells(storage: LocalStorage, key: str) -> Cells:
    with np.load(storage.path(key)) as d:
        return Cells(d["xmin"], d["xmax"], d["ymin"], d["ymax"])


def save_layout(storage: LocalStorage, grid_id: str, n: int, layout: Layout) -> str:
    key = layout_key(grid_id, n)
    with storage.writer(key) as fh:
        np.savez(fh, x_edges=layout.x_edges, y_edges=layout.y_edges, index=layout.index)
    return key


def save_values(storage: LocalStorage, version_id: str, values: F32) -> str:
    key = values_key(version_id)
    storage.write_bytes(key, np.ascontiguousarray(values, dtype="<f4").tobytes())
    return key


class _LRU:
    """Небольшой потокобезопасный LRU для неизменяемых по ключу объектов."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._items: OrderedDict[str, Any] = OrderedDict()
        self._lock = Lock()

    def get(self, key: str) -> Any:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        return None

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_layouts = _LRU(32)
_values = _LRU(256)


def load_layout(storage: LocalStorage, key: str) -> Layout:
    cached = _layouts.get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    with np.load(storage.path(key)) as d:
        layout = Layout(d["x_edges"], d["y_edges"], d["index"])
    _layouts.put(key, layout)
    return layout


def load_values(storage: LocalStorage, key: str) -> F32:
    """Массив значений через mmap: память — только страницы, которые читали."""
    cached = _values.get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    path = storage.path(key)
    arr: F32 = (
        np.memmap(path, dtype="<f4", mode="r")
        if path.stat().st_size
        else np.zeros(0, dtype=np.float32)
    )
    _values.put(key, arr)
    return arr


def take(values: F32, idx: npt.NDArray[np.int32]) -> F32:
    """values[idx] с NaN для «нет ячейки» (−1) и хвоста за концом старого массива."""
    out = np.full(idx.shape, np.nan, dtype=np.float32)
    ok = (idx >= 0) & (idx < values.size)
    out[ok] = values[idx[ok]]
    return out
