# Один образ для всех ролей: api | worker | migrate (common-requirements §4).
# Версии зависимостей зафиксированы в uv.lock.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---------- сборка окружения ----------
FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /srv
# Сначала только зависимости — слой кэшируется, пока не меняется uv.lock.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------- итоговый образ ----------
FROM ${PYTHON_IMAGE} AS runtime
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} \
    PATH=/srv/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/var/lib/apc-layers \
    CACHE_DIR=/var/cache/apc-layers

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /var/lib/apc-layers /var/cache/apc-layers \
 && chown app:app /var/lib/apc-layers /var/cache/apc-layers

WORKDIR /srv
COPY --from=build --chown=app:app /srv/.venv /srv/.venv

USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "app", "healthcheck"]

ENTRYPOINT ["python", "-m", "app"]
CMD ["api"]
