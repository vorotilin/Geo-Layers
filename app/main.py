from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings

app = FastAPI(
    title="apc-layers",
    version="0.1.0",
    description="Сервис гео-слоёв платформы наружной рекламы",
)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0", "env": settings.app_env}


@app.get("/ready")
async def ready():
    # Пока заглушка. Позже проверим БД и Redis.
    return {"status": "ready"}


@app.get("/")
async def root():
    return JSONResponse(
        {
            "name": "apc-layers",
            "version": "0.1.0",
            "docs": "/docs",
            "health": "/health",
        }
    )
