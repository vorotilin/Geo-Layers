"""Задание `warm_cache`: прогрев дискового кэша тайлов (brief §11)."""

from typing import Any

from app.jobs.runner import JobContext, JobError
from app.storage import LocalStorage, get_storage


def handle_warm(ctx: JobContext) -> dict[str, Any]:
    from app.grids import store
    from app.grids.crs import normalize_crs
    from app.tiles import render

    p = ctx.payload
    with ctx.connect() as conn:
        row = conn.execute(
            "SELECT v.values_key, v.stats, g.crs, g.params, l.value_kind "
            "FROM layer_version v JOIN grid g ON g.id = v.grid_id "
            "JOIN layer l ON l.id = v.layer_id WHERE v.id = %s AND v.layer_id = %s",
            (p["version_id"], p["layer_id"]),
        ).fetchone()
    if row is None:
        raise JobError("version_not_found", "Версия слоя не найдена")
    values_key, stats, crs_name, grid_params, value_kind = row

    storage = get_storage(ctx.settings)
    layout = store.load_layout(storage, grid_params["layout_key"])
    values = store.load_values(storage, values_key)
    crs = normalize_crs(crs_name)
    cache = LocalStorage(ctx.settings.cache_dir)

    w, s, e, n = p["bbox"]
    tiles = render.TMS[p["tms"]].tiles(w, s, e, n, list(range(p["min_zoom"], p["max_zoom"] + 1)))
    n = 0
    for tile in tiles:
        png = render.render(
            values,
            layout,
            grid_params["layout_key"],
            crs,
            stats,
            p["tms"],
            tile.z,
            tile.x,
            tile.y,
            value_kind=value_kind,
            values_key=values_key,
        )
        rel = (
            f"tiles/{p['layer_id']}/{p['version_id']}/{p['style']}/{p['tms']}/"
            f"{tile.z}/{tile.x}/{tile.y}.png"
        )
        cache.write_bytes(rel, png)
        n += 1
        if n % 100 == 0:
            ctx.check()
    return {"tiles": n}
