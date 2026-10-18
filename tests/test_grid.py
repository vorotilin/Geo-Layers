"""Юнит: раскладка rectilinear, CRS, тайловые схемы, подпись, разбор геометрии."""

import morecantile
import numpy as np
import pytest
from pyproj import Transformer

from app.grids.crs import normalize_crs
from app.grids.rectilinear import build
from app.importer.parser import RowError, cell_key, number, rectangle
from app.signing import rounded_exp, sign, verify
from app.tiles import render
from tests.synth import crs84_squares, is_hole, utm_squares


def _build(grid):  # type: ignore[no-untyped-def]
    c = grid.cells
    arr = lambda name: np.array([getattr(x, name) for x in c])  # noqa: E731
    return build(arr("xmin"), arr("xmax"), arr("ymin"), arr("ymax"), np.arange(len(c)))


@pytest.mark.parametrize("grid", [crs84_squares(), utm_squares()], ids=["crs84", "utm"])
def test_build_recovers_rows_and_cols(grid) -> None:  # type: ignore[no-untyped-def]
    r = _build(grid)
    assert r.bad_span.size == 0 and r.overlap.size == 0
    rows = max(c.row for c in grid.cells) + 1
    cols = max(c.col for c in grid.cells) + 1
    assert r.layout.shape == (rows, cols)
    for i, c in enumerate(grid.cells):
        assert r.layout.index[c.row, c.col] == i
    holes = sum(is_hole(rr, cc) for rr in range(rows) for cc in range(cols))
    assert (r.layout.index == -1).sum() == holes


def test_lookup_matches_oracle() -> None:
    grid = crs84_squares()
    layout = _build(grid).layout
    for lon, lat in grid.random_points(300):
        idx = int(layout.lookup(np.array([lon]), np.array([lat]))[0])
        c = grid.cells[idx]
        assert grid.expected(lon, lat) == c.row * 1000 + c.col
    assert layout.lookup(np.array([10.0]), np.array([10.0]))[0] == -1


def test_misaligned_and_overlapping_cells_reported() -> None:
    x0 = np.array([0.0, 1.0, 0.0, 0.0])
    x1 = np.array([1.0, 2.0, 2.0, 1.0])  # третья — на два столбца, четвёртая — дубль первой
    y0 = np.zeros(4)
    y1 = np.ones(4)
    r = build(x0, x1, y0, y1, np.arange(4))
    assert r.bad_span.tolist() == [2]
    assert r.overlap.tolist() == [3]


@pytest.mark.parametrize(
    ("text", "name"),
    [
        (None, "OGC:CRS84"),
        ("urn:ogc:def:crs:OGC:1.3:CRS84", "OGC:CRS84"),
        ("EPSG:4326", "OGC:CRS84"),
        ("urn:ogc:def:crs:EPSG::32637", "EPSG:32637"),
    ],
)
def test_normalize_crs(text: str | None, name: str) -> None:
    assert normalize_crs(text).name == name


# --- тайловые схемы (ADR-0006): эталонные точки ---


def test_3857_reference_tiles() -> None:
    tms = render.TMS["3857"]
    assert tms.tile(0, 0, 1) == morecantile.Tile(1, 1, 1)
    # Кремль, z10 — известный тайл OSM.
    assert tms.tile(37.6176, 55.7520, 10) == morecantile.Tile(619, 320, 10)


def test_3395_differs_by_latitude_only() -> None:
    lon, lat = 37.6176, 55.7520
    x57, y57 = Transformer.from_crs("OGC:CRS84", "EPSG:3857", always_xy=True).transform(lon, lat)
    x95, y95 = Transformer.from_crs("OGC:CRS84", "EPSG:3395", always_xy=True).transform(lon, lat)
    assert x57 == pytest.approx(x95)
    # На широте Москвы эллиптический Меркатор ниже на ~35 км (ADR-0006).
    assert 30_000 < y57 - y95 < 40_000
    # Эллиптическая формула широты (EPSG:3395) — независимая проверка pyproj.
    a, e = 6378137.0, 0.0818191908426
    phi = np.radians(lat)
    y_formula = a * np.log(
        np.tan(np.pi / 4 + phi / 2) * ((1 - e * np.sin(phi)) / (1 + e * np.sin(phi))) ** (e / 2)
    )
    assert y95 == pytest.approx(y_formula, abs=0.01)


def test_tile_range() -> None:
    assert render.tile_in_range("3857", 0, 0, 0)
    assert not render.tile_in_range("3857", 1, 2, 0)
    assert not render.tile_in_range("3395", 23, 0, 0)


# --- подпись (ADR-0008) ---


def test_sign_and_verify() -> None:
    prefix = "/v1/tiles/l/v/default/3857/"
    exp = rounded_exp(3600, 3600, now=1_000_000)
    assert exp % 3600 == 0 and exp >= 1_003_600
    sig = sign(prefix, exp, "s1")
    assert verify(prefix, str(exp), sig, ["s1"], now=1_000_000)
    assert verify(prefix, str(exp), sig, ["new", "s1"], now=1_000_000)  # ротация
    assert not verify(prefix, str(exp), sig, ["other"], now=1_000_000)
    assert not verify("/v1/tiles/l/OTHER/default/3857/", str(exp), sig, ["s1"], now=1_000_000)
    assert not verify(prefix, str(exp), sig, ["s1"], now=exp + 1)  # истекла
    assert not verify(prefix, str(exp + 3600), sig, ["s1"], now=1_000_000)  # подменили exp
    assert not verify(prefix, None, sig, ["s1"])


def test_rounded_exp_stable_within_step() -> None:
    assert rounded_exp(3600, 3600, now=7200.5) == rounded_exp(3600, 3600, now=10_000)


# --- разбор объектов ---


def test_rectangle() -> None:
    sq = {"type": "Polygon", "coordinates": [[[0, 1], [0, 0], [2, 0], [2, 1], [0, 1]]]}
    assert rectangle(sq) == (0, 2, 0, 1)
    multi = {"type": "MultiPolygon", "coordinates": [sq["coordinates"]]}
    assert rectangle(multi) == (0, 2, 0, 1)
    rotated = {"type": "Polygon", "coordinates": [[[1, 0], [2, 1], [1, 2], [0, 1], [1, 0]]]}
    for bad, code in [
        (None, "missing_geometry"),
        ({"type": "Point", "coordinates": [0, 0]}, "unsupported_geometry"),
        (rotated, "not_rectangle"),
    ]:
        with pytest.raises(RowError) as exc:
            rectangle(bad)
        assert exc.value.error == code


def test_key_and_number() -> None:
    assert cell_key({"k": 74721262}, "k") == "74721262"
    assert cell_key({"k": 7.0}, "k") == "7"
    assert cell_key({"k": " a1 "}, "k") == "a1"
    with pytest.raises(RowError):
        cell_key({}, "k")
    assert number("1,5", "v") == 1.5
    assert np.isnan(number(None, "v"))
    with pytest.raises(RowError):
        number("abc", "v")
