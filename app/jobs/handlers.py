"""Обработчики заданий по типу."""

from app.importer.job import handle_import
from app.jobs.runner import Handler
from app.jobs.warm import handle_warm

HANDLERS: dict[str, Handler] = {
    "import": handle_import,
    "warm_cache": handle_warm,
}
