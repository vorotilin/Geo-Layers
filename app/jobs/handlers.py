"""Обработчики заданий по типу. Импорт (`import`) добавится на шаге 7."""

from app.jobs.runner import Handler

HANDLERS: dict[str, Handler] = {}
