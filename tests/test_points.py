"""Юнит: парсер наборов точек (CSV / GeoJSON / NDJSON)."""

import pytest

from app.importer.points import PointsError, parse_points


def test_csv_semicolon() -> None:
    data = (
        "id;name;category;lat;lng;seats\n"
        "1;Кофейня;cafe;55.7522;37.6156;24\n"
        "2;Аптека;pharmacy;55.76;37.62;0\n"
    )
    out = parse_points(data, lat="lat", lon="lng", weight="seats", category="category")
    assert out["count"] == 2 and out["errors"] == 0
    assert out["lon"].tolist() == [37.6156, 37.62]
    assert out["weight"].tolist() == [24.0, 0.0]
    assert out["category"].tolist() == ["cafe", "pharmacy"]


def test_csv_comma_and_defaults() -> None:
    data = "latitude,longitude\n55.0,37.0\n56.0,38.0\n"
    out = parse_points(data)
    assert out["count"] == 2
    assert out["weight"].tolist() == [1.0, 1.0]  # вес по умолчанию 1
    assert out["category"].tolist() == ["", ""]


def test_geojson_feature_collection() -> None:
    data = (
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","geometry":{"type":"Point","coordinates":[37.6,55.7]},'
        '"properties":{"w":3,"c":"a"}},'
        '{"type":"Feature","geometry":{"type":"Point","coordinates":[37.7,55.8]},'
        '"properties":{"w":5,"c":"b"}}]}'
    )
    out = parse_points(data, weight="w", category="c")
    assert out["count"] == 2
    assert out["lon"].tolist() == [37.6, 37.7]
    assert out["weight"].tolist() == [3.0, 5.0]
    assert out["category"].tolist() == ["a", "b"]


def test_ndjson() -> None:
    data = (
        '{"type":"Feature","geometry":{"type":"Point","coordinates":[1.0,2.0]},"properties":{}}\n'
        '{"type":"Feature","geometry":{"type":"Point","coordinates":[3.0,4.0]},"properties":{}}\n'
    )
    out = parse_points(data)
    assert out["count"] == 2 and out["lon"].tolist() == [1.0, 3.0]


def test_bad_rows_skipped_and_counted() -> None:
    data = (
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","geometry":{"type":"Point","coordinates":[1.0,2.0]},"properties":{}},'
        '{"type":"Feature","geometry":null,"properties":{}}]}'
    )
    out = parse_points(data)
    assert out["count"] == 1 and out["errors"] == 1


def test_no_points_raises() -> None:
    with pytest.raises(PointsError) as exc:
        parse_points('{"type":"FeatureCollection","features":[]}')
    assert exc.value.error == "no_points"
    with pytest.raises(PointsError):
        parse_points("lat,lng\n")  # нет строк, кроме заголовка
