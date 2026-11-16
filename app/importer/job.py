"""Задание `import`: файл + манифест → сетка, ячейки, версии-черновики, отчёт (design §2–4).

Шаги: парсер в подпроцессе (лимиты памяти и времени) → сборка ячеек → под
advisory lock поставщика: сопоставление с его сеткой по ключу, дописывание новых
ячеек, массивы значений и версии `draft` → отчёт в `import.report`.
"""

import json
import logging
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.grids import store
from app.grids.crs import GridCRS, UnsupportedCRS, normalize_crs
from app.grids.rectilinear import build
from app.grids.store import Cells
from app.importer.manifest import Manifest
from app.jobs.runner import JobContext, JobError
from app.stats import compute
from app.storage import get_storage

log = logging.getLogger(__name__)

MAX_REPORT_ERRORS = 1000
# Сравнение геометрии ячейки с уже известной под тем же ключом — 1 % размера ячейки.
GEOMETRY_TOLERANCE = 0.01
KEY_BATCH = 50_000


@dataclass
class Report:
    features: int = 0
    rows_ok: int = 0
    errors_by_type: Counter[str] = field(default_factory=Counter)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def error(self, feature: int, error: str, message: str) -> None:
        self.errors_by_type[error] += 1
        if len(self.errors) < MAX_REPORT_ERRORS:
            self.errors.append({"feature": int(feature), "error": error, "message": message})

    @property
    def rows_failed(self) -> int:
        return self.features - self.rows_ok

    def as_dict(self) -> dict[str, Any]:
        return {
            "features": self.features,
            "rows_ok": self.rows_ok,
            "rows_failed": self.rows_failed,
            "errors_total": sum(self.errors_by_type.values()),
            "errors_by_type": dict(self.errors_by_type),
            "errors": sorted(self.errors, key=lambda e: e["feature"]),
            "warnings": self.warnings,
        }


@dataclass
class Parsed:
    keys: npt.NDArray[np.str_]
    cells: Cells
    values: npt.NDArray[np.float32]  # [n, metrics]
    feature: npt.NDArray[np.int64]
    crs: GridCRS


# --- парсер в подпроцессе ---


def _limits(memory_mb: int, cpu_seconds: int) -> Any:
    """Лимиты памяти и CPU для подпроцесса (только Linux; на Windows — разработка)."""
    if sys.platform == "win32":
        return None
    import resource

    def apply() -> None:
        mem = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

    return apply


def run_parser(ctx: JobContext, src: Path, work: Path, manifest: Manifest) -> dict[str, Any]:
    spec = work / "spec.json"
    spec.write_text(
        json.dumps({"key": manifest.key, "columns": [m.column for m in manifest.metrics]}),
        encoding="utf-8",
    )
    out = work / "out"
    settings = ctx.settings
    deadline = time.monotonic() + settings.max_job_duration_seconds
    proc = subprocess.Popen(  # noqa: S603 — свой модуль, аргументы — пути
        [sys.executable, "-m", "app.importer.parser", str(src), str(out), str(spec)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=_limits(settings.parser_memory_limit_mb, settings.max_job_duration_seconds),  # noqa: PLW1509
    )
    if proc.stdout is None:
        raise RuntimeError("нет stdout парсера")
    try:
        for line in proc.stdout:
            if line.startswith("progress "):
                ctx.progress(float(line.split()[1]) * 60)  # разбор — 0…60 %
            else:
                ctx.check()
            if time.monotonic() > deadline:
                raise JobError("timeout", "Разбор файла не уложился в лимит времени")
        code = proc.wait(timeout=max(deadline - time.monotonic(), 1))
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    stderr = proc.stderr.read() if proc.stderr else ""
    summary_path = out / "summary.json"
    if not summary_path.exists():
        log.error("parser crashed", extra={"code": code, "stderr": stderr[-2000:]})
        if code < 0 or "MemoryError" in stderr:
            raise JobError("parser_limit", "Разбор файла превысил лимит памяти или времени")
        raise JobError("parser_failed", "Разбор файла завершился с ошибкой", retryable=True)
    summary: dict[str, Any] = json.loads(summary_path.read_text(encoding="utf-8"))
    if "fatal" in summary:
        raise JobError(summary["fatal"]["error"], summary["fatal"]["message"])
    return summary


def _tabular_parsed(
    ctx: JobContext, storage: Any, storage_key: str, manifest: Manifest, report: Report
) -> Parsed:
    """CSV/XLSX-сетки (WKT или центр+размер) → Parsed, минуя подпроцесс GeoJSON."""
    from app.importer.tabular import TabularError, parse_tabular

    data = storage.path(storage_key).read_bytes()
    fmt = "xlsx" if data[:4] == b"PK\x03\x04" else "csv"
    geom: dict[str, Any] = {"type": manifest.geometry.type}
    if manifest.geometry.type == "wkt":
        geom["column"] = manifest.geometry.column or "geom"
    else:
        geom["columns"] = manifest.geometry.columns or ["x", "y", "w", "h"]
    try:
        out = parse_tabular(
            data,
            fmt=fmt,
            key_col=manifest.key,
            metric_cols=[m.column for m in manifest.metrics],
            geom=geom,
        )
    except TabularError as exc:
        raise JobError(exc.error, exc.message) from None
    report.features = len(out["keys"]) + out["errors"]
    report.rows_ok = len(out["keys"])
    if out["errors"]:
        report.errors_by_type["bad_row"] = out["errors"]
    return Parsed(
        keys=np.array(out["keys"], dtype=np.str_),
        cells=Cells(out["xmin"], out["xmax"], out["ymin"], out["ymax"]),
        values=out["values"],
        feature=np.arange(len(out["keys"]), dtype=np.int64),
        crs=normalize_crs(manifest.crs),
    )


def load_parsed(out: Path, summary: dict[str, Any], manifest: Manifest, report: Report) -> Parsed:
    keys: list[str] = []
    parts: dict[str, list[Any]] = {
        k: [] for k in ("xmin", "xmax", "ymin", "ymax", "values", "feature")
    }
    for i in range(summary["chunks"]):
        with np.load(out / f"chunk_{i:05d}.npz") as d:
            for k in parts:
                parts[k].append(d[k])
        keys.extend((out / f"chunk_{i:05d}.keys").read_text(encoding="utf-8").splitlines())
    m = len(manifest.metrics)

    def cat(k: str, dtype: Any, shape: tuple[int, ...] = (0,)) -> Any:
        return np.concatenate(parts[k]) if parts[k] else np.zeros(shape, dtype=dtype)

    with (out / "errors.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            e = json.loads(line)
            report.errors.append(e)
    report.features = summary["features"]
    report.errors_by_type.update(summary["errors_by_type"])

    try:
        crs = normalize_crs(summary.get("crs") or manifest.crs)
    except UnsupportedCRS as exc:
        raise JobError("unsupported_crs", str(exc)) from None
    if summary.get("crs") and manifest.crs and normalize_crs(manifest.crs) != crs:
        report.warnings.append(
            {
                "warning": "crs_from_file",
                "message": f"CRS взята из файла ({crs.name}), а не из манифеста ({manifest.crs})",
            }
        )
    return Parsed(
        keys=np.array(keys, dtype=np.str_),
        cells=Cells(
            cat("xmin", np.float64),
            cat("xmax", np.float64),
            cat("ymin", np.float64),
            cat("ymax", np.float64),
        ),
        values=cat("values", np.float32, (0, m)),
        feature=cat("feature", np.int64),
        crs=crs,
    )


def _select(p: Parsed, mask: npt.NDArray[np.bool_]) -> Parsed:
    c = p.cells
    return Parsed(
        p.keys[mask],
        Cells(c.xmin[mask], c.xmax[mask], c.ymin[mask], c.ymax[mask]),
        p.values[mask],
        p.feature[mask],
        p.crs,
    )


def drop_duplicate_keys(p: Parsed, report: Report) -> Parsed:
    _, first = np.unique(p.keys, return_index=True)
    keep = np.zeros(p.keys.size, dtype=bool)
    keep[first] = True
    for i in np.flatnonzero(~keep):
        report.error(p.feature[i], "duplicate_key", f"Ключ «{p.keys[i]}» уже встречался в файле")
    return _select(p, keep)


# --- сетка ---


def _lock_provider(conn: psycopg.Connection[Any], namespace: str, provider: str, crs: str) -> None:
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"grid|{namespace}|{provider}|{crs}",)
    )


def _existing_idx(
    conn: psycopg.Connection[Any], grid_id: str, keys: npt.NDArray[np.str_]
) -> npt.NDArray[np.int64]:
    """idx уже известных ключей (−1 — новый ключ)."""
    found: dict[str, int] = {}
    for start in range(0, keys.size, KEY_BATCH):
        batch = keys[start : start + KEY_BATCH].tolist()
        rows = conn.execute(
            "SELECT key, idx FROM grid_cell WHERE grid_id = %s AND key = ANY(%s)",
            (grid_id, batch),
        ).fetchall()
        found.update({r[0]: r[1] for r in rows})
    return np.array([found.get(k, -1) for k in keys.tolist()], dtype=np.int64)


def _same_geometry(old: Cells, idx: npt.NDArray[np.int64], new: Cells) -> npt.NDArray[np.bool_]:
    w = np.maximum(new.xmax - new.xmin, 1e-12) * GEOMETRY_TOLERANCE
    h = np.maximum(new.ymax - new.ymin, 1e-12) * GEOMETRY_TOLERANCE
    return (
        (np.abs(old.xmin[idx] - new.xmin) <= w)
        & (np.abs(old.xmax[idx] - new.xmax) <= w)
        & (np.abs(old.ymin[idx] - new.ymin) <= h)
        & (np.abs(old.ymax[idx] - new.ymax) <= h)
    )


@dataclass
class GridResult:
    grid_id: str
    cell_count: int
    cells_added: int
    reused: bool
    idx: npt.NDArray[np.int64]  # idx ячейки для каждой строки p (−1 — строка отброшена)


def attach_to_grid(
    conn: psycopg.Connection[Any],
    ctx: JobContext,
    p: Parsed,
    namespace: str,
    provider: str,
    report: Report,
) -> GridResult:
    """Находит сетку поставщика или создаёт новую; дописывает новые ячейки."""
    storage = get_storage(ctx.settings)
    grid = conn.execute(
        """
        SELECT id, cell_count, params FROM grid
        WHERE namespace_id = %s AND provider = %s AND kind = 'rectilinear' AND crs = %s
        ORDER BY created_at DESC LIMIT 1
        """,
        (namespace, provider, p.crs.name),
    ).fetchone()

    old = Cells.empty()
    known = np.full(p.keys.size, -1, dtype=np.int64)
    if grid is not None:
        grid_id, cell_count = str(grid[0]), int(grid[1])
        old = store.load_cells(storage, grid[2]["cells_key"]) if cell_count else old
        known = _existing_idx(conn, grid_id, p.keys)
        has = known >= 0
        same = np.ones(p.keys.size, dtype=bool)
        if has.any():
            same[has] = _same_geometry(old, known[has], _select(p, has).cells)
        if not same.all():
            report.warnings.append(
                {
                    "warning": "grid_geometry_mismatch",
                    "message": "Ключи совпали с сеткой поставщика, а геометрия — нет: "
                    "создана новая сетка",
                    "details": {
                        "mismatched_cells": int((~same).sum()),
                        "previous_grid_id": grid_id,
                    },
                }
            )
            grid = None
    if grid is None:
        created = conn.execute(
            "INSERT INTO grid (namespace_id, provider, kind, crs) "
            "VALUES (%s, %s, 'rectilinear', %s) RETURNING id",
            (namespace, provider, p.crs.name),
        ).fetchone()
        assert created is not None  # noqa: S101 — RETURNING всегда даёт строку
        grid_id = str(created[0])
        cell_count = 0
        old = Cells.empty()
        known = np.full(p.keys.size, -1, dtype=np.int64)

    new_mask = known < 0
    new = _select(p, new_mask)
    provisional = cell_count + np.arange(new.keys.size, dtype=np.int64)
    combined = old.append(new.cells)
    built = build(
        combined.xmin,
        combined.xmax,
        combined.ymin,
        combined.ymax,
        np.concatenate([np.arange(cell_count, dtype=np.int64), provisional]),
    )
    bad = np.zeros(combined.xmin.size, dtype=bool)
    bad[built.bad_span] = True
    bad[built.overlap] = True
    if bad[:cell_count].any():
        # Новые ячейки «разрезали» старые: это уже другая сетка.
        raise JobError(
            "grid_conflict",
            "Ячейки файла не совпадают с сеткой поставщика по границам",
            {"grid_id": grid_id, "conflicting_cells": int(bad[:cell_count].sum())},
        )
    bad_new = bad[cell_count:]
    for i in np.flatnonzero(bad_new):
        msg = "Ячейка не ложится в сетку (накрывает несколько клеток или пересекает другую)"
        report.error(new.feature[i], "not_aligned", msg)

    accepted = new_mask.copy()
    accepted[np.flatnonzero(new_mask)[bad_new]] = False
    added = _select(p, accepted)
    added_idx = cell_count + np.arange(added.keys.size, dtype=np.int64)
    total = cell_count + added.keys.size

    if added.keys.size:
        _insert_cells(conn, grid_id, added, added_idx)
    all_cells = old.append(added.cells)
    final = build(
        all_cells.xmin,
        all_cells.xmax,
        all_cells.ymin,
        all_cells.ymax,
        np.arange(total, dtype=np.int64),
    )
    cells_key = store.save_cells(storage, grid_id, all_cells)
    layout_key = store.save_layout(storage, grid_id, total, final.layout)
    rows, cols = final.layout.shape
    conn.execute(
        "UPDATE grid SET cell_count = %s, params = %s WHERE id = %s",
        (
            total,
            Jsonb({"cells_key": cells_key, "layout_key": layout_key, "rows": rows, "cols": cols}),
            grid_id,
        ),
    )

    idx = known.copy()
    idx[np.flatnonzero(new_mask)[~bad_new]] = added_idx
    idx[np.flatnonzero(new_mask)[bad_new]] = -1
    return GridResult(grid_id, total, int(added.keys.size), grid is not None, idx)


def _insert_cells(
    conn: psycopg.Connection[Any], grid_id: str, p: Parsed, idx: npt.NDArray[np.int64]
) -> None:
    conn.execute(
        "CREATE TEMP TABLE new_cell (idx int, key text, x0 float8, y0 float8, "
        "x1 float8, y1 float8) ON COMMIT DROP"
    )
    c = p.cells
    with conn.cursor().copy("COPY new_cell (idx, key, x0, y0, x1, y1) FROM STDIN") as copy:
        for row in zip(
            idx.tolist(),
            p.keys.tolist(),
            c.xmin.tolist(),
            c.ymin.tolist(),
            c.xmax.tolist(),
            c.ymax.tolist(),
            strict=True,
        ):
            copy.write_row(row)
    geom = "ST_MakeEnvelope(x0, y0, x1, y1, %s)"
    if p.crs.srid != 4326:
        # Прямоугольник в своей CRS на карте — четырёхугольник; уплотняем стороны.
        geom = f"ST_Transform(ST_Segmentize({geom}, (x1 - x0) / 8), 4326)"
    conn.execute(
        f"""
        INSERT INTO grid_cell (grid_id, idx, key, geom, area_m2)
        SELECT %s, idx, key, g, ST_Area(g::geography)
        FROM (SELECT idx, key, {geom} AS g FROM new_cell) s
        """,  # noqa: S608 — geom собран из констант
        (grid_id, p.crs.srid),
    )


# --- слои и версии ---


def write_versions(
    conn: psycopg.Connection[Any],
    ctx: JobContext,
    manifest: Manifest,
    p: Parsed,
    grid: GridResult,
    namespace: str,
    import_id: str,
) -> list[dict[str, Any]]:
    storage = get_storage(ctx.settings)
    conn.execute(
        "INSERT INTO dataset (namespace_id, provider, title) VALUES (%s, %s, %s) "
        "ON CONFLICT (namespace_id, provider, title) DO NOTHING",
        (namespace, manifest.dataset.provider, manifest.dataset.title),
    )
    dataset_id = conn.execute(
        "SELECT id FROM dataset WHERE namespace_id = %s AND provider = %s AND title = %s",
        (namespace, manifest.dataset.provider, manifest.dataset.title),
    ).fetchone()[0]  # type: ignore[index]

    ok = grid.idx >= 0
    versions = []
    for col, metric in enumerate(manifest.metrics):
        conn.execute(
            "INSERT INTO layer (dataset_id, slug, title, unit, value_kind) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (dataset_id, slug) DO NOTHING",
            (dataset_id, metric.slug, metric.title, metric.unit, metric.value_kind),
        )
        layer_id, value_kind = conn.execute(
            "SELECT id, value_kind FROM layer WHERE dataset_id = %s AND slug = %s",
            (dataset_id, metric.slug),
        ).fetchone()  # type: ignore[misc]
        values = np.full(grid.cell_count, np.nan, dtype=np.float32)
        values[grid.idx[ok]] = p.values[ok, col]
        version_id, revision = conn.execute(
            """
            INSERT INTO layer_version (layer_id, period, revision, grid_id, values_key, import_id)
            SELECT %s, %s, coalesce(max(revision), 0) + 1, %s, '', %s
            FROM layer_version WHERE layer_id = %s AND period = %s
            RETURNING id, revision
            """,
            (layer_id, manifest.period, grid.grid_id, import_id, layer_id, manifest.period),
        ).fetchone()  # type: ignore[misc]
        key = store.save_values(storage, str(version_id), values)
        stats = compute(values, grid.cell_count)
        conn.execute(
            "UPDATE layer_version SET values_key = %s, stats = %s WHERE id = %s",
            (key, Jsonb(stats), version_id),
        )
        versions.append(
            {
                "layer_id": str(layer_id),
                "version_id": str(version_id),
                "slug": metric.slug,
                "period": manifest.period,
                "revision": revision,
                "status": "draft",
                "value_kind": value_kind,
                "count": stats["count"],
            }
        )
        ctx.progress(80 + 20 * (col + 1) / len(manifest.metrics))
    return versions


# --- обработчик ---


def handle_import(ctx: JobContext) -> dict[str, Any]:
    import_id = ctx.payload["import_id"]
    storage = get_storage(ctx.settings)
    with ctx.connect() as conn:
        row = (
            conn.cursor(row_factory=dict_row)
            .execute(
                "SELECT i.namespace_id, i.manifest, f.storage_key FROM import i "
                "JOIN file f ON f.id = i.file_id WHERE i.id = %s",
                (import_id,),
            )
            .fetchone()
        )
    if row is None:
        raise JobError("import_not_found", "Импорт не найден")
    manifest = Manifest.model_validate(row["manifest"])
    namespace = row["namespace_id"]
    report = Report()
    work = Path(ctx.settings.cache_dir) / "import-tmp" / ctx.id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        if manifest.geometry.type == "geojson":
            summary = run_parser(ctx, storage.path(row["storage_key"]), work, manifest)
            parsed = drop_duplicate_keys(
                load_parsed(work / "out", summary, manifest, report), report
            )
        else:
            parsed = drop_duplicate_keys(
                _tabular_parsed(ctx, storage, row["storage_key"], manifest, report), report
            )
        ctx.progress(65)
        with ctx.connect() as conn:
            _lock_provider(conn, namespace, manifest.dataset.provider, parsed.crs.name)
            grid = attach_to_grid(conn, ctx, parsed, namespace, manifest.dataset.provider, report)
            report.rows_ok = int((grid.idx >= 0).sum())
            ctx.progress(80)
            error_rate = report.rows_failed / report.features if report.features else 1.0
            if error_rate > manifest.options.max_error_rate or report.rows_ok == 0:
                conn.rollback()
                _save_report(ctx, import_id, report, None)
                raise JobError(
                    "too_many_errors",
                    "Доля ошибок в файле больше допустимой",
                    {
                        "error_rate": round(error_rate, 6),
                        "max_error_rate": manifest.options.max_error_rate,
                        "errors_by_type": dict(report.errors_by_type),
                    },
                )
            versions = write_versions(conn, ctx, manifest, parsed, grid, namespace, import_id)
            grid_info = {
                "grid_id": grid.grid_id,
                "kind": "rectilinear",
                "crs": parsed.crs.name,
                "cells_total": grid.cell_count,
                "cells_added": grid.cells_added,
                "reused": grid.reused,
            }
            ctx.check()  # отменили — не фиксируем
            _save_report(ctx, import_id, report, grid_info, conn)
            conn.commit()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "import_id": import_id,
        "grid": grid_info,
        "versions": versions,
        "rows_ok": report.rows_ok,
        "rows_failed": report.rows_failed,
        "errors_by_type": dict(report.errors_by_type),
    }


def _save_report(
    ctx: JobContext,
    import_id: str,
    report: Report,
    grid: dict[str, Any] | None,
    conn: psycopg.Connection[Any] | None = None,
) -> None:
    body = report.as_dict() | {"grid": grid}
    sql = "UPDATE import SET report = %s WHERE id = %s"
    if conn is not None:
        conn.execute(sql, (Jsonb(body), import_id))
        return
    with ctx.connect() as own:
        own.execute(sql, (Jsonb(body), import_id))
