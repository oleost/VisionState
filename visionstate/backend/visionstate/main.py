"""FastAPI application: REST API plus the static frontend, served through HA Ingress."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import cameras, imports, review, samples, sensors, system, teach
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
        db.engine.dispose()  # close SQLite connections cleanly on shutdown

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
    app.include_router(cameras.router)
    app.include_router(review.router)
    app.include_router(imports.router)
    app.include_router(sensors.router)
    app.include_router(samples.router)
    app.include_router(teach.router)

    frontend = settings.frontend_dir.resolve()
    index = frontend / "index.html"
    if index.exists():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            candidate = (frontend / path).resolve()
            if path and frontend in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
