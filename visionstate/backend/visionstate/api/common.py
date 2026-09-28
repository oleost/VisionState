"""Shared helpers for the REST API."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import ModelInfo, Sample, SampleLabel, Sensor
from ..engine import Runtime
from ..settings import APP_SLUG, MAX_STATES, UNKNOWN_STATE

API_PREFIX = "/api/v1"


def runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def slugify(text: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:48] or fallback


def unique_slug(session: Session, name: str) -> str:
    base = slugify(name, "sensor")
    slug, n = base, 2
    while session.scalar(select(Sensor.id).where(Sensor.slug == slug)) is not None:
        slug, n = f"{base}_{n}", n + 1
    return slug


def get_sensor(session: Session, sensor_id: int) -> Sensor:
    sensor = session.get(Sensor, sensor_id)
    if sensor is None:
        raise HTTPException(404, "Sensor not found")
    return sensor


def state_id_for(sensor: Sensor, key: str | None) -> int | None:
    if key is None:
        return None
    for state in sensor.states:
        if state.key == key:
            return state.id
    raise HTTPException(400, f"Unknown state {key!r}")


class Roi(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class StateIn(BaseModel):
    key: str | None = None
    name: str = Field(min_length=1, max_length=64)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


def validate_states(states: list[StateIn]) -> None:
    if not 2 <= len(states) <= MAX_STATES:
        raise HTTPException(400, f"A sensor needs between 2 and {MAX_STATES} states")
    keys = [s.key or slugify(s.name, "state") for s in states]
    if len(set(keys)) != len(keys):
        raise HTTPException(400, "State names must be unique")
    if UNKNOWN_STATE in keys:
        raise HTTPException(400, f"'{UNKNOWN_STATE}' is reserved")


def sample_counts(session: Session, sensor: Sensor) -> dict:
    """Labelled samples per state (split by day/night) and number of unlabelled samples."""
    rows = session.execute(
        select(SampleLabel.state_id, Sample.is_night, func.count())
        .join(Sample, Sample.id == SampleLabel.sample_id)
        .where(Sample.sensor_id == sensor.id)
        .group_by(SampleLabel.state_id, Sample.is_night)
    ).all()
    per_state = {st.key: {"day": 0, "night": 0} for st in sensor.states}
    key_by_id = {st.id: st.key for st in sensor.states}
    for state_id, night, count in rows:
        if state_id in key_by_id:
            per_state[key_by_id[state_id]]["night" if night else "day"] += count
    total = session.scalar(select(func.count()).select_from(Sample).where(Sample.sensor_id == sensor.id)) or 0
    labelled = sum(v["day"] + v["night"] for v in per_state.values())
    return {"per_state": per_state, "labelled": labelled, "unlabelled": total - labelled}


def sensor_view(rt: Runtime, session: Session, sensor: Sensor) -> dict:
    live = rt.live.get(sensor.id)
    info = session.get(ModelInfo, sensor.id)
    head = rt.heads.get(sensor.id)
    counts = sample_counts(session, sensor)
    trained = head is not None and rt.embedder is not None and head.backbone == rt.embedder.spec.id
    if not sensor.enabled:
        status = "disabled"
    elif live and live.available is False:
        status = "unavailable"
    elif not trained:
        status = "untrained"
    else:
        status = "ok"
    return {
        "id": sensor.id,
        "slug": sensor.slug,
        "name": sensor.name,
        "kind": sensor.kind,
        "source_type": sensor.source_type,
        "source": sensor.source,
        "roi": sensor.roi,
        "interval_s": sensor.interval_s,
        "threshold": sensor.threshold,
        "debounce": sensor.debounce,
        "enabled": sensor.enabled,
        "entity_id": f"sensor.{APP_SLUG}_{sensor.slug}",
        "states": [{"id": s.id, "key": s.key, "name": s.name, "color": s.color} for s in sensor.states],
        "status": status,
        "trained": trained,
        "training": sensor.id in rt.training,
        "live": {
            "available": live.available if live else None,
            "error": live.error if live else "",
            "published": live.debouncer.published if live else None,
            "top": live.top if live else None,
            "confidence": live.confidence if live else 0.0,
            "probs": live.probs if live else {},
            "last_run": live.last_run if live else None,
        },
        "model": {
            "backbone": info.backbone,
            "version": info.version,
            "trained_at": iso(info.trained_at),
            "n_samples": info.n_samples,
            "accuracy": info.accuracy,
            "train_seconds": info.train_seconds,
        }
        if info
        else None,
        "counts": counts,
    }
