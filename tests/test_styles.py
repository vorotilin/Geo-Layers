"""Юнит: классификация, стили, легенда, рендер по стилю (ядро, без БД)."""

import io

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from app.styles import Style, compute_breaks, jenks, legend
from app.tiles import render


def _stats(values: list[float]) -> dict[str, object]:
    v = np.asarray(values, dtype=np.float64)
    return {
        "count": int(v.size),
        "min": float(v.min()),
        "max": float(v.max()),
        "percentiles": [float(x) for x in np.percentile(v, list(range(1, 100)))],
    }


def _breaks(style: Style, values: list[float]) -> list[float]:
    out = compute_breaks(style, _stats(values))
    assert out is not None
    return [float(x) for x in out]


def test_equal_breaks() -> None:
    assert _breaks(Style(method="equal", classes=4), [0, 25, 50, 75, 100]) == [25, 50, 75]


def test_quantile_breaks() -> None:
    # Равномерное 0..99: p25≈24.75, p50≈49.5, p75≈74.25.
    b = _breaks(Style(method="quantile", classes=4), list(range(100)))
    assert (
        len(b) == 3 and b[0] == pytest.approx(24.75, abs=1) and b[2] == pytest.approx(74.25, abs=1)
    )


def test_log_breaks() -> None:
    # 1..100, 3 класса: границы — геометрические 10^(2/3) и 10^(4/3).
    b = _breaks(Style(method="log", classes=3), [1, 10, 100])
    assert len(b) == 2
    assert b[0] == pytest.approx(4.6416, rel=0.01)
    assert b[1] == pytest.approx(21.544, rel=0.01)


def test_log_falls_back_to_quantile_when_non_positive() -> None:
    # log неприменим при min<=0 — не падаем, отдаём квантили (перечисление открытое).
    b = _breaks(Style(method="log", classes=3), [-5, 0, 10, 20])
    assert len(b) == 2


def test_manual_and_fixed_breaks() -> None:
    assert _breaks(Style(method="manual", manual_breaks=[10, 20]), [0, 50]) == [10, 20]
    # fixed_breaks важнее метода: границы общие для периодов.
    style = Style(method="equal", fixed_breaks=[5, 15])
    assert _breaks(style, [0, 100]) == [5, 15]


def test_no_data_breaks_is_none() -> None:
    assert compute_breaks(Style(), None) is None
    empty = {"count": 0, "min": None, "max": None, "percentiles": None}
    assert compute_breaks(Style(), empty) is None


def test_jenks_splits_clusters() -> None:
    v = np.array([0, 0, 0, 1, 1, 9, 9, 10, 10, 10], dtype=np.float64)
    b = jenks(v, 2)
    assert b.size == 1 and 1 < float(b[0]) < 9


def test_jenks_monotonic_in_range() -> None:
    b = jenks(np.arange(100, dtype=np.float64), 5)
    assert b.size == 4 and float(b[0]) >= 0 and float(b[-1]) <= 99
    assert np.all(np.diff(b) > 0)


def test_colors_subsampled_to_class_count() -> None:
    assert len(Style(classes=5).colors()) == 5
    assert len(Style(method="manual", manual_breaks=[1, 2, 3]).colors()) == 4
    # Своя палитра из 3 цветов и 3 класса.
    assert Style(palette=["#000000", "#888888", "#ffffff"], classes=3).colors() == [
        (0, 0, 0),
        (136, 136, 136),
        (255, 255, 255),
    ]


def test_style_validation() -> None:
    with pytest.raises(ValidationError):
        Style(palette="no_such_palette")
    with pytest.raises(ValidationError):
        Style(method="manual", manual_breaks=[10, 5])  # не по возрастанию


def test_legend_matches_breaks_and_palette() -> None:
    style = Style(method="equal", classes=3)
    lg = legend(style, _stats([0, 50, 100]))
    assert len(lg["entries"]) == 3
    assert lg["entries"][0]["min"] is None and lg["entries"][-1]["max"] is None
    # Первый класс — первый цвет палитры.
    assert lg["entries"][0]["color"] == "#ffffb2"


def test_render_styled_produces_png_with_transparency() -> None:
    values = np.array([10.0, 20.0, 30.0, 40.0], dtype=np.float32)
    idx = np.array([[0, 1], [2, -1]], dtype=np.int32)  # последний пиксель — «нет данных»
    style = Style(method="equal", classes=4, opacity=128)
    png = render.render_styled(values, idx, _stats([10, 20, 30, 40]), style)
    img = Image.open(io.BytesIO(png))
    assert img.size == (2, 2) and img.mode == "P"
    # Прозрачность: индекс 0 (нет данных) — 0, классы — opacity.
    assert img.info["transparency"] == bytes([0, 128, 128, 128, 128])


def test_render_styled_no_data_opaque_color() -> None:
    values = np.array([10.0, 20.0], dtype=np.float32)
    idx = np.array([[0, -1]], dtype=np.int32)
    style = Style(method="equal", classes=2, no_data="#102030", opacity=255)
    png = render.render_styled(values, idx, _stats([10, 20]), style)
    img = Image.open(io.BytesIO(png))
    assert img.info["transparency"] == bytes([255, 255, 255])
    # Палитра: «нет данных» — (16, 32, 48).
    pal = img.getpalette()
    assert pal is not None and pal[0:3] == [16, 32, 48]
