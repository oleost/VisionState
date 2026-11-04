"""Thresholds suggested from review answers, the same for every sensor kind (``advice.py``): each
threshold a sensor decides by, the answers about it — the score of what was asked about, and
whether it was right — and setting the suggested one (docs/SCOPE.md §7).

Per kind, what is asked about and what decides:

- a state sensor: a frame's confidence against the sensor's threshold (below it: unknown);
- a reading sensor: a reading's confidence against the threshold it is accepted from;
- an object sensor: an own label's similarity against "Recognised as a box you taught", and a
  face's against "Recognised" (asked about between asking and that line);
- a sound sensor: each sound's best score against its threshold (asked about when only just
  heard, or almost: ``SOUND["near_share"]``).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import advice, faces, sounds
from ..db import Prediction, SampleLabel, Sensor, State
from ..engine import Runtime
from ..settings import (
    FACE_LIMITS,
    KIND_OBJECTS,
    KIND_READING,
    KIND_SOUND,
    KIND_STATES,
    SENSOR_LIMITS,
    SOUND,
    SOUND_LIMITS,
    TEACH_TUNABLE_LIMITS,
    active_custom,
    faces_on,
    merge_objects,
    merge_sound,
    merge_teach,
)
from .common import API_PREFIX, get_sensor, runtime

router = APIRouter(prefix=API_PREFIX, tags=["advice"])
ADVICE_ROWS = 2000  # the newest history rows of a sensor its answers are looked for in

THRESHOLD = "threshold"  # a state or reading sensor's own threshold
TEACH_MATCH = "teach:match_similarity"
FACE_MATCH = "faces:match_similarity"
SOUND_PREFIX = "sound:"


def _answered(s: Session, sensor_id: int, *where) -> list:  # noqa: ANN002 - SQLAlchemy clauses
    return list(
        s.scalars(
            select(Prediction)
            .where(Prediction.sensor_id == sensor_id, *where)
            .order_by(Prediction.id.desc())
            .limit(ADVICE_ROWS)
        )
    )


def _split(pairs: list[tuple[float | None, bool | None]]) -> tuple[list[float], list[float]]:
    yes = [float(score) for score, right in pairs if score is not None and right is True]
    no = [float(score) for score, right in pairs if score is not None and right is False]
    return yes, no


def _state_answers(s: Session, sensor: Sensor) -> tuple[list[float], list[float]]:
    """A state sensor's answered frames: right when the state the answer taught (the label of the
    sample it added; a row's ``probs`` are its states' probabilities) is the one it saw."""
    rows = _answered(s, sensor.id, Prediction.reviewed.is_(True), Prediction.review_reason.is_not(None))
    labels = dict(
        s.execute(
            select(SampleLabel.sample_id, State.key)
            .join(State, State.id == SampleLabel.state_id)
            .where(SampleLabel.sample_id.in_([r.sample_id for r in rows if r.sample_id is not None]))
        ).all()
    )
    pairs = []
    for row in rows:
        if row.sample_id in labels:
            pairs.append((row.confidence, labels[row.sample_id] == row.state_key))
    return _split(pairs)


def _asked_answers(s: Session, sensor_id: int, face: bool) -> tuple[list[float], list[float]]:
    """An object sensor's answered "Is this Our car?" (or, ``face``, "Is this Kari?") questions:
    the similarity asked about, and the answer."""
    pairs = []
    for row in _answered(s, sensor_id, Prediction.published_key == "ask"):
        probs = row.probs or {}
        ask = probs.get("ask") or {}
        if bool(ask.get("face")) == face and probs.get("answer") in ("yes", "no"):
            pairs.append((ask.get("similarity"), probs["answer"] == "yes"))
    return _split(pairs)


def sound_answers(s: Session, sensor_id: int, keys: list[str]) -> dict[str, tuple[list[float], list[float]]]:
    """Per sound: the best scores of the times it was heard (or almost) and answered "yes", and of
    those answered otherwise (no, one of the own sounds, a new one)."""
    out: dict[str, tuple[list[float], list[float]]] = {}
    rows = _answered(s, sensor_id, Prediction.state_key.in_(keys), Prediction.published_key.in_(("on", "near")))
    for row in rows:
        answer = (row.probs or {}).get("answer")
        if answer is None or row.confidence is None:
            continue
        yes, no = out.setdefault(row.state_key or "", ([], []))
        (yes if answer == "yes" else no).append(float(row.confidence))
    return out


def _item(key: str, name: str, verb: str, threshold: float, limits: tuple[float, float], answers) -> dict:  # noqa: ANN001
    yes, no = answers
    return {
        "key": key,
        "name": name,
        "verb": verb,
        "threshold": round(threshold, 4),
        "limits": [round(limits[0], 4), round(limits[1], 4)],
        "advice": advice.threshold_advice(yes, no, threshold, limits),
    }


def advice_items(rt: Runtime, s: Session, sensor: Sensor) -> list[dict]:
    """Every threshold the sensor decides by, with the answers about it and a suggestion."""
    if sensor.kind == KIND_STATES:
        return [
            _item(
                THRESHOLD,
                "Threshold",
                "published",
                sensor.threshold,
                SENSOR_LIMITS[THRESHOLD],
                _state_answers(s, sensor),
            )
        ]
    if sensor.kind == KIND_READING:
        rows = _answered(s, sensor.id, Prediction.read_ok.is_not(None))
        answers = _split([(r.confidence, bool(r.read_ok)) for r in rows])
        return [_item(THRESHOLD, "Threshold", "accepted", sensor.threshold, SENSOR_LIMITS[THRESHOLD], answers)]
    if sensor.kind == KIND_SOUND:
        settings = merge_sound(sensor.sound)
        own = {c["key"]: c["name"] for c in settings["custom"]}
        keys = [k for k in settings["sounds"] if k in sounds.SOUNDS and k != SOUND["speech_key"]] + list(own)
        answers = sound_answers(s, sensor.id, keys)
        items = []
        for key in keys:
            spec = sounds.SOUNDS.get(key)
            threshold = settings["thresholds"].get(key, spec.threshold if spec else SOUND["own_threshold"])
            name = spec.name if spec else own[key]
            items.append(
                _item(SOUND_PREFIX + key, name, "heard", threshold, SOUND_LIMITS[THRESHOLD], answers.get(key, ([], [])))
            )
        return items
    if sensor.kind == KIND_OBJECTS:
        objects = merge_objects(sensor.objects)
        items = []
        if active_custom(objects):
            limits = merge_teach(rt.backbone_id, rt.global_teach, objects["teach"])
            lo = max(limits["ask_similarity"], limits["keep_similarity"], TEACH_TUNABLE_LIMITS["match_similarity"][0])
            items.append(
                _item(
                    TEACH_MATCH,
                    "Own labels: recognised as a box you taught",
                    "recognised",
                    limits["match_similarity"],
                    (lo, TEACH_TUNABLE_LIMITS["match_similarity"][1]),
                    _asked_answers(s, sensor.id, face=False),
                )
            )
        if faces_on(objects):
            limits = faces.merge_limits(rt.face_choice, rt.global_faces, objects["faces"])
            lo = max(limits["ask_similarity"], limits["keep_similarity"], FACE_LIMITS["match_similarity"][0])
            hi = min(limits["sure_similarity"], FACE_LIMITS["match_similarity"][1])
            items.append(
                _item(
                    FACE_MATCH,
                    "Faces: recognised",
                    "recognised",
                    limits["match_similarity"],
                    (lo, hi),
                    _asked_answers(s, sensor.id, face=True),
                )
            )
        return items
    return []


@router.get("/sensors/{sensor_id}/advice")
def sensor_advice(sensor_id: int, request: Request) -> dict:
    """The thresholds of a sensor, each with how its answers fare and a suggested threshold."""
    rt = runtime(request)
    with rt.db.session() as s:
        return {"items": advice_items(rt, s, get_sensor(s, sensor_id))}


class ApplyIn(BaseModel):
    threshold: float


@router.post("/sensors/{sensor_id}/advice/{key}")
async def apply_advice(sensor_id: int, key: str, body: ApplyIn, request: Request) -> dict:
    """Set one of the sensor's thresholds (the key of an advice item) — a suggested one, mostly."""
    rt = runtime(request)

    def apply() -> None:
        with rt.db.session() as s:
            sensor = get_sensor(s, sensor_id)
            item = next((i for i in advice_items(rt, s, sensor) if i["key"] == key), None)
            if item is None:
                raise HTTPException(404, "No such threshold on this sensor")
            lo, hi = item["limits"]
            if not lo <= body.threshold <= hi:
                raise HTTPException(400, f"The threshold must be between {lo:g} and {hi:g}")
            value = round(body.threshold, 4)
            if key == THRESHOLD:
                sensor.threshold = value
            elif key.startswith(SOUND_PREFIX):
                stored = dict(sensor.sound or {})
                stored["thresholds"] = {**(stored.get("thresholds") or {}), key[len(SOUND_PREFIX) :]: value}
                sensor.sound = stored
            else:
                part, field = key.split(":", 1)  # teach / faces, match_similarity
                stored = dict(sensor.objects or {})
                stored[part] = {**(stored.get(part) or {}), field: value}
                sensor.objects = stored

    await asyncio.to_thread(apply)
    await rt.sensor_updated(sensor_id, retrain=False)
    return {"ok": True}
