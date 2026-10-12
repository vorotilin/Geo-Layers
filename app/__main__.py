"""Команды образа: api | worker | migrate | healthcheck.

Один образ — несколько ролей (common-requirements §4): `python -m app <команда>`.
"""

import argparse
import os
import sys
import time
import urllib.request
from pathlib import Path

from app.config import get_settings

WORKER_HEARTBEAT_FILE = Path("/tmp/apc-worker-heartbeat")  # noqa: S108
WORKER_HEARTBEAT_MAX_AGE = 60


def cmd_api() -> None:
    settings = get_settings()
    # exec: gunicorn становится PID 1 и сам получает SIGTERM.
    os.execvp(  # noqa: S606
        "gunicorn",  # noqa: S607 — gunicorn из venv в PATH образа
        [
            "gunicorn",
            "app.main:app_factory()",
            "--worker-class",
            "uvicorn_worker.UvicornWorker",
            "--workers",
            str(settings.api_workers),
            "--bind",
            f"0.0.0.0:{settings.http_port}",
            "--graceful-timeout",
            "30",
            "--timeout",
            "60",
        ],
    )


def cmd_worker() -> None:
    from app.worker import run_worker

    run_worker(get_settings(), heartbeat_file=WORKER_HEARTBEAT_FILE)


def cmd_migrate() -> None:
    from app.migrate import migrate

    migrate(get_settings())


def cmd_healthcheck(worker: bool) -> int:
    if worker:
        try:
            age = time.time() - WORKER_HEARTBEAT_FILE.stat().st_mtime
        except FileNotFoundError:
            return 1
        return 0 if age < WORKER_HEARTBEAT_MAX_AGE else 1
    url = f"http://127.0.0.1:{get_settings().http_port}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310
            return 0 if resp.status == 200 else 1
    except OSError:
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("api", help="HTTP API (gunicorn + uvicorn)")
    sub.add_parser("worker", help="воркер заданий")
    sub.add_parser("migrate", help="миграции БД и создание бакета")
    hc = sub.add_parser("healthcheck", help="проверка для HEALTHCHECK контейнера")
    hc.add_argument("--worker", action="store_true", help="проверять воркер, а не HTTP")
    args = parser.parse_args()

    if args.command == "api":
        cmd_api()
    elif args.command == "worker":
        cmd_worker()
    elif args.command == "migrate":
        cmd_migrate()
    elif args.command == "healthcheck":
        sys.exit(cmd_healthcheck(args.worker))


if __name__ == "__main__":
    main()
