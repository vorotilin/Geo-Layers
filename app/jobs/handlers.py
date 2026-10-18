"""Обработчики заданий по типу."""

from app.importer.job import handle_import
from app.jobs.runner import Handler

HANDLERS: dict[str, Handler] = {
    "import": handle_import,
}
