"""The review queue: frames waiting for an answer, and the answers."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from functools import partial
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.orm import Session

from .. import imaging, readers, sounds
from ..db import Face, Prediction, ReadingWatch, Sample, SampleLabel, Sensor
from ..engine import Runtime, with_people
from ..settings import (
    FACE_UNKNOWN_KEY,
    KIND_OBJECTS,
    KIND_READING,
    KIND_SOUND,
    REVIEW_QUEUE,
    merge_objects,
    merge_reading,
    merge_sound,
)
from .common import (
    API_PREFIX,
    get_sensor,
    runtime,
    state_id_for,
)
from .faces import FaceTeachIn, teach_face
from .history import prediction_view
from .teach import TeachIn, teach_box

log = logging.getLogger(__name__)
router = APIRouter(prefix=API_PREFIX, tags=["review"])


class ReviewIn(BaseModel):
    # State sensors: confirm | label (state_key) | skip. Reading sensors: read_ok | misread
    # (optionally the value that was right) | skip. Object sensors ("Is this Our car?"): yes | no | skip;
    # about a face ("Is this Kari?", "Who is this?") also person (state_key: who it is) and new
    # (value: the new person's name). Sound sensors ("Was this a siren?"): yes | no | sound (state_key:
    # one of its own sounds) | new (value: a new own sound's name) | skip.
    action: str
    state_key: str | None = None
    value: str | None = Field(None, max_length=64)


@router.get("/review")
def review_queue(
    request: Request,
    limit: int = REVIEW_QUEUE["page_size"],
    sensor_id: Annotated[int | None, Query(alias="sensor")] = None,
) -> dict:
    """The newest ``limit`` items waiting for review (with ``sensor``: only that sensor's, ``total``
    counts only those and ``sensor_name`` names it, also when nothing of it waits), and how many
    wait per sensor (always all sensors)."""
    rt = runtime(request)
    with rt.db.session() as s:
        query = select(Prediction).where(Prediction.reviewed.is_(False))
        if sensor_id is not None:
            query = query.where(Prediction.sensor_id == sensor_id)
        total = s.scalar(select(func.count()).select_from(query.subquery()))
        rows = s.scalars(query.order_by(Prediction.created_at.desc()).limit(limit)).all()
        # Waiting items per sensor, most first (the list itself only holds the newest `limit`).
        waiting = s.execute(
            select(Sensor.id, Sensor.name, func.count(Prediction.id))
            .join(Prediction, Prediction.sensor_id == Sensor.id)
            .where(Prediction.reviewed.is_(False))
            .group_by(Sensor.id, Sensor.name)
            .order_by(func.count(Prediction.id).desc(), Sensor.name)
        ).all()
        sensors = {x.id: x for x in s.scalars(select(Sensor).where(Sensor.id.in_({r.sensor_id for r in rows})))}
        items = []
        for row in rows:
            sensor = sensors[row.sensor_id]
            items.append(
                {
                    **prediction_view(row),
                    "sensor": {
                        "id": sensor.id,
                        "name": sensor.name,
                        "kind": sensor.kind,
                        "roi": sensor.roi,
                        "states": [{"key": st.key, "name": st.name, "color": st.color} for st in sensor.states],
                        "reading": merge_reading(sensor.reading) if sensor.kind == KIND_READING else None,
                        # object sensors: the own labels (and people) a question can be about
                        "labels": with_people(merge_objects(sensor.objects), s)["custom"]
                        if sensor.kind == KIND_OBJECTS
                        else [],
                        # sound sensors: the sounds it listens for, and its own sounds an answer can name
                        "sounds": _sound_names(sensor) if sensor.kind == KIND_SOUND else [],
                    },
                }
            )
        chosen = s.get(Sensor, sensor_id) if sensor_id is not None else None
        return {
            "total": total,
            "items": items,
            "sensors": [{"id": sid, "name": name, "count": n} for sid, name, n in waiting],
            **({"sensor_name": chosen.name if chosen else None} if sensor_id is not None else {}),
        }


@router.post("/review/sensors/{sensor_id}/dismiss")
async def review_dismiss_all(sensor_id: int, request: Request) -> dict:
    """Take every waiting item of one sensor out of the queue, as if each was skipped.

    Nothing is learned from them; answers already given and the reading counts stay.
    """
    rt = runtime(request)

    def dismiss() -> int:
        with rt.db.session() as s:
            get_sensor(s, sensor_id)
            result = s.execute(
                update(Prediction)
                .where(Prediction.sensor_id == sensor_id, Prediction.reviewed.is_(False))
                .values(reviewed=True)
            )
            return cast(CursorResult, result).rowcount

    dismissed = await asyncio.to_thread(dismiss)
    await rt.publish_review_count()
    return {"dismissed": dismissed}


# Rejected readings whose value, confirmed in Review, becomes the last value (see _newest_rejected).
ANCHOR_REASONS = ("unsure", "went down", "changed too much")


def _newest_rejected(s: Session, row: Prediction) -> bool:
    """Whether a reading is a rejected one read after the last accepted value (so the value it
    confirms can take that value's place: engine.reading.anchor_reading)."""
    if row.published_key is not None:
        return False
    watch = s.get(ReadingWatch, row.sensor_id)
    read_at = (row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)).timestamp()
    if watch is not None and (watch.accepted_at or 0.0) >= read_at:
        return False
    newer = s.scalar(
        select(func.count())
        .select_from(Prediction)
        .where(
            Prediction.sensor_id == row.sensor_id,
            Prediction.published_key.is_not(None),
            Prediction.created_at > row.created_at,
        )
    )
    return not newer


@router.post("/review/{prediction_id}")
async def review_answer(prediction_id: int, body: ReviewIn, request: Request) -> dict:
    rt = runtime(request)

    def kind_of() -> str:
        with rt.db.session() as s:
            row = s.get(Prediction, prediction_id)
            if row is None:
                raise HTTPException(404, "Review item not found")
            return get_sensor(s, row.sensor_id).kind

    kind = await asyncio.to_thread(kind_of)
    if kind == KIND_OBJECTS:
        return await _answer_object(rt, request, prediction_id, body)
    if kind == KIND_SOUND:
        return await _answer_sound(rt, prediction_id, body)

    def answer_reading() -> tuple[int, str, datetime] | None:
        """Checks that the answer fits the kind; stores a reading's. Returns the reading to anchor on, if any."""
        anchor: tuple[int, str, datetime] | None = None
        with rt.db.session() as s:
            row = s.get(Prediction, prediction_id)
            if row is None:
                raise HTTPException(404, "Review item not found")
            sensor = get_sensor(s, row.sensor_id)
            if sensor.kind == KIND_READING:
                if body.action not in ("read_ok", "misread", "skip"):
                    raise HTTPException(400, "Answer read_ok, misread or skip for a reading")
                if body.action != "skip":
                    row.read_ok = body.action == "read_ok"
                    row.correct_value = None
                    if body.action == "misread" and body.value and body.value.strip():
                        settings = merge_reading(sensor.reading)
                        number = readers.right_value(body.value, settings)  # digits only: placed like the reader
                        if number is None:
                            raise HTTPException(400, f"Not a number: {body.value!r}")
                        row.correct_value = readers.format_value(number, settings)
                    # A reading with the wrong number of digits is no value, even when read as it is shown.
                    probs = row.probs or {}
                    kept = probs.get("reason") in ANCHOR_REASONS
                    right = row.correct_value if body.action == "misread" else probs.get("value") if kept else None
                    if right is not None and _newest_rejected(s, row):
                        anchor = (row.sensor_id, right, row.created_at)
                row.reviewed = True
            elif body.action not in ("confirm", "label", "skip"):
                raise HTTPException(400, "Answer confirm, label or skip")
        return anchor

    anchor = await asyncio.to_thread(answer_reading)
    if kind == KIND_READING:
        if anchor is not None:
            await rt.anchor_reading(*anchor)
        await rt.publish_review_count()
        return {"ok": True}

    def answer_state() -> tuple[int, int | None, str | None]:
        with rt.db.session() as s:
            row = s.get(Prediction, prediction_id)
            if row is None:  # removed in the meantime (history clean-up)
                raise HTTPException(404, "Review item not found")
            sensor = get_sensor(s, row.sensor_id)
            key = row.state_key if body.action == "confirm" else body.state_key
            state_id = state_id_for(sensor, key) if body.action in ("confirm", "label") else None
            sensor_id, frame = row.sensor_id, row.frame
            row.reviewed = True
            # Answered before: change (or, when skipped now, remove) the sample that answer added.
            sample = s.get(Sample, row.sample_id) if row.sample_id is not None else None
            if sample is not None and sample.sensor_id == sensor_id:
                if state_id is None:
                    rt.db.after_commit(s, partial(rt.storage.delete_sample, sensor_id, sample.id, sample.filename))
                    s.delete(sample)
                    row.sample_id = None
                else:
                    s.execute(delete(SampleLabel).where(SampleLabel.sample_id == sample.id))
                    s.add(SampleLabel(sample_id=sample.id, state_id=state_id))
                    sample.verified = False
                rt.schedule_retrain(sensor_id)
                state_id = None  # nothing more to add
            return sensor_id, state_id, frame

    sensor_id, state_id, frame = await asyncio.to_thread(answer_state)
    if state_id is not None and frame:
        path = rt.storage.history_path(sensor_id, frame)
        if path.exists():
            image = await asyncio.to_thread(lambda: imaging.load(path))
            sample_id = await asyncio.to_thread(rt.add_sample, sensor_id, image, "review", state_id)
            await asyncio.to_thread(_link_answer, rt, prediction_id, sample_id)
            rt.schedule_retrain(sensor_id)
    await rt.publish_review_count()
    return {"ok": True}


async def _answer_object(rt: Runtime, request: Request, prediction_id: int, body: ReviewIn) -> dict:
    """ "Is this Our car?" — yes teaches the box as the own label, no as its plain class (both in
    the light of that frame, so later boxes like it are told apart); skip only takes it out of the
    queue. Answering again replaces the box the first answer taught.

    About a face: yes teaches the face as that person, person as the person named in
    ``state_key``, new as a new person called ``value``; no and skip teach nothing (a face is
    never taught as "not someone")."""
    action = body.action

    def about_face() -> bool:
        with rt.db.session() as s:
            row = s.get(Prediction, prediction_id)
            return bool(row is not None and ((row.probs or {}).get("ask") or {}).get("face"))

    is_face = await asyncio.to_thread(about_face)
    if action not in (("yes", "no", "skip", "person", "new") if is_face else ("yes", "no", "skip")):
        raise HTTPException(400, "Answer yes, no or skip")

    def take_out() -> tuple[dict, int, float, int | None]:
        """Out of the queue; the box or face an earlier answer taught goes."""
        with rt.db.session() as s:
            row = s.get(Prediction, prediction_id)
            if row is None:
                raise HTTPException(404, "Review item not found")
            question = (row.probs or {}).get("ask")
            if not question:
                raise HTTPException(400, "Not a question about an own label")
            sensor_id, score, taught = row.sensor_id, row.confidence, row.sample_id
            row.reviewed = True
            row.sample_id = None
            if taught is not None and is_face and (face := s.get(Face, taught)) is not None:
                rt.db.after_commit(
                    s, partial(rt.storage.delete_face, face.filename)
                )  # the face the first answer taught
                s.delete(face)
            elif taught is not None and not is_face and (sample := s.get(Sample, taught)) is not None:
                rt.db.after_commit(s, partial(rt.storage.delete_sample, sensor_id, sample.id, sample.filename))
                s.delete(sample)
            return question, sensor_id, score, taught

    question, sensor_id, score, taught = await asyncio.to_thread(take_out)
    if taught is not None:
        if is_face:
            await asyncio.to_thread(rt.faces_changed)
        else:
            rt.taught_changed(sensor_id)
    if is_face:
        await _answer_face(rt, request, prediction_id, sensor_id, question, score, body)
        rt.live_state(sensor_id).asked.pop(question["label"], None)  # answered: the next may come now
    elif action != "skip":
        label = question["label"] if action == "yes" else question["detected"]
        taught_box = TeachIn(
            history_id=prediction_id, box=question["box"], label=label, detected=question["detected"], score=score
        )
        result = await teach_box(sensor_id, taught_box, request)
        await asyncio.to_thread(_link_answer, rt, prediction_id, result["id"])
    await rt.publish_review_count()
    return {"ok": True}


async def _answer_face(
    rt: Runtime, request: Request, prediction_id: int, sensor_id: int, question: dict, score: float, body: ReviewIn
) -> None:
    """Teach the face of a question when the answer says who it is (see _answer_object)."""
    person, new_person = None, None
    if body.action == "yes" and question["label"] != FACE_UNKNOWN_KEY:
        person = question["label"]
    elif body.action == "person":
        person = body.state_key
    elif body.action == "new":
        new_person = (body.value or "").strip() or None
        if new_person is None:
            raise HTTPException(400, "Give the new person a name")
    if person is None and new_person is None:
        return
    face = FaceTeachIn(history_id=prediction_id, box=question["box"], score=score, person=person, new_person=new_person)
    result = await teach_face(sensor_id, face, request)
    await asyncio.to_thread(_link_answer, rt, prediction_id, result["id"])


def _sound_names(sensor: Sensor) -> list[dict]:
    """A sound sensor's sounds as an answer offers them: {key, name, own}."""
    settings = merge_sound(sensor.sound)
    model = [{"key": k, "name": sounds.SOUNDS[k].name, "own": False} for k in settings["sounds"] if k in sounds.SOUNDS]
    return model + [{"key": c["key"], "name": c["name"], "own": True} for c in settings["custom"]]


async def _answer_sound(rt: Runtime, prediction_id: int, body: ReviewIn) -> dict:
    """ "Was this a siren?" — the answer is kept with the row (``probs["answer"]``), and teaches its
    clip where it can: yes about an own sound adds the clip to it; sound / new teach it as one of
    the own sounds or a new one; no teaches it as another sound (``sounds.OTHER``) when the sensor
    has own sounds. The model's own sounds are not changed by an answer. Answering again replaces
    the clip the first answer taught."""
    from .sounds import TeachClipIn, teach_history_clip

    if body.action not in ("yes", "no", "sound", "new", "skip"):
        raise HTTPException(400, "Answer yes, no, sound, new or skip")

    def take_out() -> tuple[int, str, dict, int | None]:
        with rt.db.session() as s:
            row = s.get(Prediction, prediction_id)
            if row is None:
                raise HTTPException(404, "Review item not found")
            sensor = get_sensor(s, row.sensor_id)
            taught = row.sample_id
            row.reviewed, row.sample_id = True, None
            if body.action != "skip":
                answer = (
                    body.state_key if body.action == "sound" else body.value if body.action == "new" else body.action
                )
                row.probs = {**(row.probs or {}), "answer": answer}
            if taught is not None and (sample := s.get(Sample, taught)) is not None:
                rt.db.after_commit(s, partial(rt.storage.delete_sample, sensor.id, sample.id, sample.filename))
                s.delete(sample)
            return sensor.id, row.state_key, merge_sound(sensor.sound), taught

    sensor_id, key, settings, taught = await asyncio.to_thread(take_out)
    own = {c["key"] for c in settings["custom"]}
    teach: TeachClipIn | None = None
    if body.action == "yes" and key in own:
        teach = TeachClipIn(history_id=prediction_id, label=key)
    elif body.action == "sound":
        if body.state_key not in own:
            raise HTTPException(400, "Name one of the sensor's own sounds")
        teach = TeachClipIn(history_id=prediction_id, label=body.state_key)
    elif body.action == "new":
        if not (body.value or "").strip():
            raise HTTPException(400, "Give the new sound a name")
        teach = TeachClipIn(history_id=prediction_id, new_name=(body.value or "").strip())
    elif body.action == "no" and own:
        teach = TeachClipIn(history_id=prediction_id, label=sounds.OTHER)
    if teach is not None:
        result = await teach_history_clip(rt, sensor_id, teach)
        await asyncio.to_thread(_link_answer, rt, prediction_id, result["id"])
    elif taught is not None:
        await rt.sensor_updated(sensor_id, retrain=False)  # the clip the first answer taught is gone
    await rt.publish_review_count()
    return {"ok": True}


def _link_answer(rt: Runtime, prediction_id: int, sample_id: int) -> None:
    """Remember what an answer taught (a sample, a taught box or a face), so answering again replaces it."""
    with rt.db.session() as s:
        if (answered := s.get(Prediction, prediction_id)) is not None:
            answered.sample_id = sample_id
