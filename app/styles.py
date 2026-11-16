"""Стили слоёв: палитра, число классов, метод разбиения, непрозрачность, цвет
«нет данных», правила зумов и легенда (design §7, brief §4–5).

Стиль меняется без перезагрузки данных. Легенда строится из той же пары
«границы классов + палитра», что и картинки (ADR-0005), поэтому они не расходятся.

Методы разбиения: equal (равные интервалы), quantile (квантили), log (логарифм),
manual (ручные границы), jenks (естественные границы, по перцентилям статистики —
приближение полного Jenks на выборке; полная выборка — позже). Неизвестный метод
и неприменимый log падают в quantile: перечисление открытое (common-requirements §2).
"""

from typing import Any

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Именованные последовательные палитры (в порядке возрастания значения, ColorBrewer).
PALETTES: dict[str, list[str]] = {
    "yellow_red": ["#ffffb2", "#fed976", "#feb24c", "#fd8d3c", "#fc4e2a", "#e31a1c", "#b10026"],
    "blues": ["#eff3ff", "#c6dbef", "#9ecae1", "#6baed6", "#3182bd", "#08519c"],
    "greens": ["#edf8e9", "#c7e9c0", "#a1d99b", "#74c476", "#31a354", "#006d2c"],
    "purples": ["#fcfbfd", "#efedf5", "#dadaeb", "#bcbddc", "#9e9ac8", "#756bb1", "#54278f"],
}
DEFAULT_PALETTE = "yellow_red"
MIN_CLASSES = 2
MAX_CLASSES = 9

F64 = npt.NDArray[np.float64]


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    text = color.lstrip("#")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


class Style(BaseModel):
    """Спецификация стиля слоя. Неизвестные поля игнорируются."""

    model_config = ConfigDict(extra="ignore")

    palette: str | list[str] = DEFAULT_PALETTE
    classes: int = Field(default=5, ge=MIN_CLASSES, le=MAX_CLASSES)
    method: str = "quantile"  # equal | quantile | log | manual | jenks
    manual_breaks: list[float] | None = None
    # Общие границы для сравнения периодов по цвету (breaks_from=fixed, design §7).
    fixed_breaks: list[float] | None = None
    opacity: int = Field(default=200, ge=0, le=255)
    no_data: str = "transparent"  # "transparent" или "#rrggbb"
    min_zoom: int | None = Field(default=None, ge=0, le=22)
    max_zoom: int | None = Field(default=None, ge=0, le=22)

    @field_validator("palette")
    @classmethod
    def _known_palette(cls, v: str | list[str]) -> str | list[str]:
        if isinstance(v, str) and v not in PALETTES:
            raise ValueError(f"неизвестная палитра: {v}")
        if isinstance(v, list) and not MIN_CLASSES <= len(v) <= MAX_CLASSES:
            raise ValueError(f"палитра должна содержать {MIN_CLASSES}..{MAX_CLASSES} цветов")
        return v

    @field_validator("manual_breaks", "fixed_breaks")
    @classmethod
    def _ascending(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and (not v or any(b <= a for a, b in zip(v, v[1:], strict=False))):
            raise ValueError("границы классов должны строго возрастать")
        return v

    def n_classes(self) -> int:
        return len(self.manual_breaks) + 1 if self.manual_breaks else self.classes

    def colors(self) -> list[tuple[int, int, int]]:
        """Ровно `n_classes()` цветов: из именованной палитры — равномерной выборкой."""
        hexes = PALETTES[self.palette] if isinstance(self.palette, str) else self.palette
        k = self.n_classes()
        if len(hexes) == k:
            return [hex_to_rgb(h) for h in hexes]
        idx = [round(i * (len(hexes) - 1) / (k - 1)) for i in range(k)]
        return [hex_to_rgb(hexes[i]) for i in idx]


# Встроенный стиль (M1): 7 квантильных классов, жёлто-красная палитра.
BUILTIN_STYLE = Style(palette=DEFAULT_PALETTE, classes=7, method="quantile", opacity=200)


def compute_breaks(style: Style, stats: dict[str, Any] | None) -> F64 | None:
    """Внутренние границы классов (их `n_classes() − 1`) или None, если данных нет.

    None → все значения считаются «нет данных», тайл прозрачный.
    """
    if style.fixed_breaks is not None:
        return np.asarray(style.fixed_breaks, dtype=np.float64)
    if style.manual_breaks is not None:
        return np.asarray(style.manual_breaks, dtype=np.float64)
    if not stats or not stats.get("percentiles") or stats.get("count", 0) == 0:
        return None
    k = style.classes
    lo, hi = stats.get("min"), stats.get("max")
    if lo is None or hi is None or hi <= lo:
        return None
    if style.method == "equal":
        return np.linspace(lo, hi, k + 1)[1:-1]
    if style.method == "log" and lo > 0:
        return np.logspace(np.log10(lo), np.log10(hi), k + 1)[1:-1]
    if style.method == "jenks":
        p = np.asarray(stats["percentiles"], dtype=np.float64)
        return jenks(p, k)
    return _quantile(stats, k)


def _quantile(stats: dict[str, Any], k: int) -> F64:
    p = np.asarray(stats["percentiles"], dtype=np.float64)
    idx = [round(100 * i / k) for i in range(1, k)]
    return np.array([p[j - 1] for j in idx])


def _sse(v: F64) -> F64:
    """SSE[i, j] — сумма квадратов отклонений от среднего внутри v[i:j] (O(n²))."""
    n = v.size
    p = np.concatenate([[0.0], np.cumsum(v)])
    p2 = np.concatenate([[0.0], np.cumsum(v * v)])
    sse = np.zeros((n + 1, n + 1))
    for i in range(n):
        for j in range(i + 1, n + 1):
            cnt = j - i
            mean = (p[j] - p[i]) / cnt
            sse[i, j] = p2[j] - p2[i] - cnt * mean * mean
    return sse


def jenks(values: npt.ArrayLike, k: int) -> F64:
    """Естественные границы классов (Fisher/Jenks) динамическим программированием.

    Вход — отсортированные значения; результат — `k − 1` границ (средние между
    последним значением класса и первым следующего). Для перцентилей (≈100 точек)
    считается мгновенно; для большой выборки это O(k·n²) — вызывать на выборке.
    """
    v = np.sort(np.asarray(values, dtype=np.float64))
    v = v[np.isfinite(v)]
    n = v.size
    if n < 2 or k < 2:
        return np.zeros(0, dtype=np.float64)
    k = min(k, n)
    sse = _sse(v)
    inf = np.inf
    dp = np.full((k + 1, n + 1), inf)
    dp[1, :] = sse[0, :]
    last = np.zeros((k + 1, n + 1), dtype=np.int64)
    for m in range(2, k + 1):
        for j in range(m, n + 1):
            best_i = int(np.argmin(dp[m - 1, m - 1 : j] + sse[m - 1 : j, j])) + (m - 1)
            dp[m, j] = dp[m - 1, best_i] + sse[best_i, j]
            last[m, j] = best_i
    bounds: list[float] = []
    j = n
    for m in range(k, 1, -1):
        i = int(last[m, j])
        bounds.append((v[i - 1] + v[i]) / 2)
        j = i
    return np.array(sorted(bounds), dtype=np.float64)


def _fmt(v: float | None) -> str:
    if v is None:
        return ""
    a = abs(v)
    if a == 0:
        return "0"
    if a >= 1000:
        return f"{v:,.0f}".replace(",", " ")
    if a >= 1:
        return f"{v:.1f}"
    return f"{v:.4g}"


def legend(style: Style, stats: dict[str, Any] | None, unit: str | None = None) -> dict[str, Any]:
    """Легенда из тех же breaks + palette, что и картинки (не расходятся по построению)."""
    breaks = compute_breaks(style, stats)
    colors = style.colors()
    entries: list[dict[str, Any]] = []
    if breaks is not None and breaks.size:
        edges: list[float | None] = [None, *[float(b) for b in breaks], None]
        for i, color in enumerate(colors):
            lo, hi = edges[i], edges[i + 1]
            label = ""
            if lo is None:
                label = f"< {_fmt(hi)}"
            elif hi is None:
                label = f"≥ {_fmt(lo)}"
            else:
                label = f"{_fmt(lo)} … {_fmt(hi)}"
            if unit:
                label = f"{label} {unit}"
            entries.append(
                {
                    "index": i + 1,
                    "min": lo,
                    "max": hi,
                    "color": rgb_to_hex(color),
                    "label": label,
                }
            )
    return {
        "palette": style.palette if isinstance(style.palette, str) else None,
        "method": style.method,
        "no_data": style.no_data,
        "entries": entries,
    }
