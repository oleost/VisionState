"""FastAPI application: REST API plus the static frontend, served through HA Ingress."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import samples, sensors, system
from .db import Database
from .engine import Runtime
from .settings import INGRESS_PROXY_IP, VERSION, Settings, load_settings

log = logging.getLogger("visionstate")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        db.init()
        app.state.runtime = Runtime(settings, db)
        await app.state.runtime.start()
        log.info("VisionState %s started", VERSION)
        yield
        await app.state.runtime.stop()

    app = FastAPI(title="VisionState", version=VERSION, lifespan=lifespan)

    if settings.is_supervised:

        @app.middleware("http")
        async def ingress_only(request: Request, call_next):
            # Under Home Assistant only the Ingress proxy may talk to us.
            client = request.client.host if request.client else ""
            if client not in (INGRESS_PROXY_IP, "127.0.0.1"):
                return JSONResponse({"detail": "Forbidden"}, status_code=403)
            return await call_next(request)

    app.include_router(system.router)
    app.include_router(sensors.router)
    app.include_router(samples.router)

    index = settings.frontend_dir / "index.html"
    if index.exists():
        app.mount("/assets", StaticFiles(directory=settings.frontend_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            candidate = settings.frontend_dir / path
            if path and candidate.is_file() and settings.frontend_dir in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
