"""The history: what the sensors recorded, filtered and paged (the History page and each sensor's
History tab), what a filter can still find (facets), and the frames.

Every filter is applied in one place, ``history_query``: the list, its count and the facets use it,
so a new filter is added there and in ``HistoryFilter`` only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import ColumnElement, Select, and_, func, or_, select

from ..db import Prediction, Sensor
from ..settings import HISTORY, HISTORY_EVENTS, KIND_READING, SENSOR_KINDS
from .common import API_PREFIX, iso, runtime

router = APIRouter(prefix=API_PREFIX, tags=["history"])

EVENT_KIND = {event: kind for kind, events in HISTORY_EVENTS.items() for event in events}
# The rows each event matches, among the rows of sensors of its kind (see db.Prediction).
EVENT_ROWS: dict[str, ColumnElement[bool]] = {
    "change": Prediction.is_change.is_(True),
    "flagged": Prediction.review_reason.is_not(None),
    "appeared": Prediction.published_key == "on",
    "cleared": Prediction.published_key == "off",
    "filtered": Prediction.published_key == "filtered",
    "asked": Prediction.published_key == "ask",
    "value": Prediction.published_key.is_not(None),
    "rejected": Prediction.published_key.is_(None),
    "verified": Prediction.read_ok.is_not(None),
}
_MAX_VALUES = HISTORY["max_filter_values"]


def prediction_view(p: Prediction) -> dict:
    """A history row (also a review queue item) as the UI shows it."""
    return {
        "id": p.id,
        "sensor_id": p.sensor_id,
        "created_at": iso(p.created_at),
        "state_key": p.state_key,
        "published_key": p.published_key,
        "confidence": p.confidence,
        "probs": p.probs,
        "is_change": p.is_change,
        "review_reason": p.review_reason,
        "reviewed": p.reviewed,
        "has_frame": bool(p.frame),
        "detections": p.detections,
        "read_ok": p.read_ok,
        "correct_value": p.correct_value,
    }


def _utc(value: datetime) -> datetime:
    """As stored: UTC without a time zone (SQLite keeps none). A time without a zone is taken as UTC."""
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def encode_cursor(row: Prediction) -> str:
    """Where a page ended: the time and id of its last row (rows are ordered by both)."""
    return f"{iso(row.created_at)}|{row.id}"


def decode_cursor(value: str) -> tuple[datetime, int]:
    when, _, ident = value.rpartition("|")
    try:
        return _utc(datetime.fromisoformat(when)), int(ident)
    except ValueError:
        raise ValueError(f"Not a history cursor: {value!r}") from None


def _newer(cursor: tuple[datetime, int]) -> ColumnElement[bool]:
    when, ident = cursor
    return or_(Prediction.created_at > when, and_(Prediction.created_at == when, Prediction.id > ident))


def _older(cursor: tuple[datetime, int]) -> ColumnElement[bool]:
    when, ident = cursor
    return or_(Prediction.created_at < when, and_(Prediction.created_at == when, Prediction.id < ident))


class HistoryFilter(BaseModel):
    """Which rows: every given filter must match; several values of one filter match any of them."""

    sensor: list[int] = Field(default_factory=list, max_length=_MAX_VALUES)
    kind: list[str] = Field(default_factory=list, max_length=len(SENSOR_KINDS))
    # A state, an object class or an own label (the row's state_key); never matches a reading.
    key: list[str] = Field(default_factory=list, max_length=_MAX_VALUES)
    event: list[str] = Field(default_factory=list, max_length=len(EVENT_KIND))
    waiting: bool | None = None  # True: waiting in the review queue; False: not waiting
    since: datetime | None = None
    until: datetime | None = None  # exclusive
    newer_than: str | None = None  # a cursor: only rows newer than that one (what arrived since)

    @field_validator("kind")
    @classmethod
    def _kinds(cls, value: list[str]) -> list[str]:
        for kind in value:
            if kind not in SENSOR_KINDS:
                raise ValueError(f"Unknown sensor kind {kind!r}")
        return value

    @field_validator("event")
    @classmethod
    def _events(cls, value: list[str]) -> list[str]:
        for event in value:
            if event not in EVENT_KIND:
                raise ValueError(f"Unknown event {event!r}")
        return value

    @field_validator("since", "until")
    @classmethod
    def _times(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @field_validator("newer_than")
    @classmethod
    def _cursor(cls, value: str | None) -> str | None:
        if value is not None:
            decode_cursor(value)
        return value

    def without(self, field: Literal["sensor", "key", "event"]) -> HistoryFilter:
        """The same filter without one of its lists (a facet counts what choosing there would find)."""
        return HistoryFilter.model_validate({**self.model_dump(), field: []})


class HistoryPage(HistoryFilter):
    order: Literal["newest", "oldest"] = "newest"
    limit: int = Field(HISTORY["page_size"], ge=0, le=HISTORY["max_page_size"])  # 0: only the total
    cursor: str | None = None  # the "next" of the page before

    @field_validator("cursor")
    @classmethod
    def _page_cursor(cls, value: str | None) -> str | None:
        if value is not None:
            decode_cursor(value)
        return value


def history_query(f: HistoryFilter, *columns) -> Select:
    """``columns`` (default: the rows) of the history rows ``f`` matches."""
    query = select(*(columns or (Prediction,))).select_from(Prediction).join(Sensor, Sensor.id == Prediction.sensor_id)
    if f.sensor:
        query = query.where(Prediction.sensor_id.in_(f.sensor))
    if f.kind:
        query = query.where(Sensor.kind.in_(f.kind))
    if f.key:
        query = query.where(Prediction.state_key.in_(f.key), Sensor.kind != KIND_READING)
    if f.event:
        query = query.where(or_(*(and_(Sensor.kind == EVENT_KIND[e], EVENT_ROWS[e]) for e in f.event)))
    if f.waiting is not None:
        query = query.where(Prediction.reviewed.is_(not f.waiting))
    if f.since is not None:
        query = query.where(Prediction.created_at >= f.since)
    if f.until is not None:
        query = query.where(Prediction.created_at < f.until)
    if f.newer_than is not None:
        query = query.where(_newer(decode_cursor(f.newer_than)))
    return query


@router.get("/history")
def history(params: Annotated[HistoryPage, Query()], request: Request) -> dict:
    """One page of the rows the filter matches, how many it matches, where the next page starts and
    the newest row it matches (what a later ``newer_than`` asks about)."""
    rt = runtime(request)
    newest_first = (Prediction.created_at.desc(), Prediction.id.desc())
    with rt.db.session() as s:
        total = s.scalar(history_query(params, func.count())) or 0
        latest = s.scalars(history_query(params).order_by(*newest_first).limit(1)).first()
        items: list[Prediction] = []
        more = False
        if params.limit:
            query = history_query(params)
            if params.cursor is not None:
                cursor = decode_cursor(params.cursor)
                query = query.where(_older(cursor) if params.order == "newest" else _newer(cursor))
            order = newest_first if params.order == "newest" else (Prediction.created_at, Prediction.id)
            items = list(s.scalars(query.order_by(*order).limit(params.limit + 1)))
            more = len(items) > params.limit
            items = items[: params.limit]
        return {
            "items": [prediction_view(p) for p in items],
            "total": total,
            "next": encode_cursor(items[-1]) if more else None,
            "newest": encode_cursor(latest) if latest else None,
        }


@router.get("/history/facets")
def history_facets(params: Annotated[HistoryFilter, Query()], request: Request) -> dict:
    """What each filter could still find: per sensor, key and event, the rows matching the rest of
    the filter (so choosing more values of a filter never hides the ones offered)."""
    rt = runtime(request)
    with rt.db.session() as s:
        sensors = s.execute(
            history_query(params.without("sensor"), Prediction.sensor_id, func.count()).group_by(Prediction.sensor_id)
        ).all()
        keys = s.execute(
            history_query(params.without("key"), Prediction.sensor_id, Prediction.state_key, func.count())
            .where(Sensor.kind != KIND_READING)
            .group_by(Prediction.sensor_id, Prediction.state_key)
        ).all()
        others = params.without("event")
        events = []
        for event in EVENT_KIND:
            count = s.scalar(history_query(others.model_copy(update={"event": [event]}), func.count())) or 0
            if count:
                events.append({"event": event, "count": count})
    return {
        "sensors": [{"id": sid, "count": n} for sid, n in sensors],
        "keys": [{"sensor_id": sid, "key": key, "count": n} for sid, key, n in keys],
        "events": events,
    }


@router.get("/history/{prediction_id}/image")
def history_image(prediction_id: int, request: Request, size: str = "full") -> FileResponse:
    rt = runtime(request)
    with rt.db.session() as s:
        row = s.get(Prediction, prediction_id)
        if row is None or not row.frame:
            raise HTTPException(404, "Frame not found")
        path = rt.storage.history_path(row.sensor_id, row.frame)
    if not path.exists():
        raise HTTPException(404, "Frame file missing")
    if size == "thumb":
        path = rt.storage.thumbnail("history", prediction_id, path)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
