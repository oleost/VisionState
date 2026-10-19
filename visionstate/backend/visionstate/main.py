"""FastAPI application: REST API plus the static frontend, served through HA Ingress."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import backup
from .api import backup as backup_api
from .api import cameras, faces, history, imports, review, samples, sensors, system, teach
from .db import Database
from .engine import Runtime
from .settings import BACKUP, INGRESS_PROXY_IP, VERSION, Settings, load_settings

log = logging.getLogger("visionstate")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        backup.clean_leftovers(settings)  # a restore cut off by a stop
        db = Database(settings.db_path)
        db.init()
        app.state.runtime = Runtime(settings, db)
        await app.state.runtime.start()
        log.info("VisionState %s started", VERSION)
        yield
        # A restored backup runs on a runtime (and database) of its own: stop the one there is now.
        await app.state.runtime.stop()
        app.state.runtime.db.engine.dispose()  # close SQLite connections cleanly on shutdown

    app = FastAPI(title="VisionState", version=VERSION, lifespan=lifespan)

    app.state.ready = asyncio.Event()  # cleared while a restore swaps the data (api/backup.py)
    app.state.ready.set()

    @app.middleware("http")
    async def while_restoring(request: Request, call_next):
        # A restore swaps the data under a stopped runtime for a few seconds: requests wait for
        # the new runtime instead of failing.
        ready: asyncio.Event = request.app.state.ready
        if not ready.is_set() and request.url.path.startswith("/api/"):
            try:
                await asyncio.wait_for(ready.wait(), BACKUP["hold_requests_s"])
            except TimeoutError:
                return JSONResponse({"detail": "Restoring a backup, try again in a moment"}, status_code=503)
        return await call_next(request)

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
    app.include_router(history.router)
    app.include_router(imports.router)
    app.include_router(backup_api.router)
    app.include_router(sensors.router)
    app.include_router(samples.router)
    app.include_router(teach.router)
    app.include_router(faces.router)
    app.include_router(faces.people_router)

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
