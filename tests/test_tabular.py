"""Юнит: табличный парсер CSV/XLSX (WKT, центр+размер)."""

import io

import pytest

from app.importer.tabular import TabularError, parse_tabular


def test_csv_center_size() -> None:
    csv = "id;x;y;w;h;val\n1;500;500;100;100;10\n2;700;500;100;100;20\n"
    out = parse_tabular(
        csv.encode(),
        fmt="csv",
        key_col="id",
        metric_cols=["val"],
        geom={"type": "center_size", "columns": ["x", "y", "w", "h"]},
    )
    assert out["keys"] == ["1", "2"]
    assert out["xmin"].tolist() == [450.0, 650.0]
    assert out["values"].tolist() == [[10.0], [20.0]]
    assert out["errors"] == 0


def test_csv_wkt() -> None:
    csv = "id;geom;val\n1;POLYGON((0 0,0 1,1 1,1 0,0 0));5\n"
    out = parse_tabular(
        csv.encode(),
        fmt="csv",
        key_col="id",
        metric_cols=["val"],
        geom={"type": "wkt", "column": "geom"},
    )
    assert out["xmin"].tolist() == [0.0] and out["ymax"].tolist() == [1.0]


def test_xlsx_center_size() -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["id", "x", "y", "w", "h", "val"])
    ws.append([1, 500, 500, 100, 100, 7])
    buf = io.BytesIO()
    wb.save(buf)
    out = parse_tabular(
        buf.getvalue(),
        fmt="xlsx",
        key_col="id",
        metric_cols=["val"],
        geom={"type": "center_size", "columns": ["x", "y", "w", "h"]},
    )
    assert out["values"].tolist() == [[7.0]]


def test_bad_row_skipped() -> None:
    csv = "id;x;y;w;h;val\n1;500;500;100;100;10\n2;bad;500;100;100;20\n"
    out = parse_tabular(
        csv.encode(),
        fmt="csv",
        key_col="id",
        metric_cols=["val"],
        geom={"type": "center_size", "columns": ["x", "y", "w", "h"]},
    )
    assert out["keys"] == ["1"] and out["errors"] == 1


def test_no_valid_rows() -> None:
    with pytest.raises(TabularError):
        parse_tabular(
            b"id;x;y;w;h;val\n1;bad;500;100;100;20\n",
            fmt="csv",
            key_col="id",
            metric_cols=["val"],
            geom={"type": "center_size", "columns": ["x", "y", "w", "h"]},
        )
