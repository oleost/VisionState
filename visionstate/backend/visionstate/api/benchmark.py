"""The speed test: which models a sensor can be tested with, starting and stopping, the results."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .common import API_PREFIX, runtime

router = APIRouter(prefix=API_PREFIX)


class BenchmarkPick(BaseModel):
    role: str
    id: str


class BenchmarkIn(BaseModel):
    sensor_id: int
    models: list[BenchmarkPick] = Field(min_length=1)
    keep: bool = False  # keep what is downloaded for the test


@router.get("/benchmark")
async def benchmark(request: Request) -> dict:
    rt = runtime(request)
    options = await asyncio.to_thread(rt.benchmark_options)
    return {**options, "run": rt.benchmark_view()}


@router.get("/benchmark/run")
def benchmark_run(request: Request) -> dict:
    """What the running test is doing, or the last one's results (polled while it runs)."""
    return {"run": runtime(request).benchmark_view()}


@router.get("/benchmark/report")
def benchmark_report(request: Request) -> PlainTextResponse:
    """The last results as a text file to share (a file, as copying is not allowed on http)."""
    run = runtime(request).benchmark
    if run is None or run.state == "running":
        raise HTTPException(404, "No finished speed test")
    return PlainTextResponse(
        run.report(), headers={"Content-Disposition": 'attachment; filename="visionstate-speed-test.txt"'}
    )


@router.post("/benchmark", status_code=202)
async def benchmark_start(body: BenchmarkIn, request: Request) -> dict:
    rt = runtime(request)
    if rt.benchmark_running():
        raise HTTPException(409, "A speed test is running")
    cfg = await asyncio.to_thread(rt.load_sensor, body.sensor_id)
    if cfg is None:
        raise HTTPException(404, "Sensor not found")
    roles = rt.benchmark_roles(cfg)
    picks = []
    for pick in body.models:
        if pick.role not in roles or pick.id not in rt._benchmark_specs(pick.role):
            raise HTTPException(400, f"This sensor cannot be tested with {pick.id}")
        picks.append({"role": pick.role, "id": pick.id})
    return {"run": rt.benchmark_start(cfg, picks, body.keep).view()}


@router.post("/benchmark/cancel")
def benchmark_cancel(request: Request) -> dict:
    rt = runtime(request)
    rt.benchmark_cancel()
    return {"run": rt.benchmark_view()}
