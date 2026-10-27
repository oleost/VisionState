"""Recognising people by their face (docs/FACE_RECOGNITION_PLAN.md). The people and their faces
are shared by every object sensor that recognises faces: a face is taught on any of them (a
person box in a frame of that sensor), and the people are listed, renamed and forgotten for all
of them at once. Turning it on and off is a setting of each sensor (``objects.faces``); the face
model is chosen in /settings."""

from __future__ import annotations

import asyncio
import itertools
import tempfile
from datetime import datetime
from functools import partial
from pathlib import Path

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import detectors, faces, imaging, uploads
from ..db import Face, Person, Sensor
from ..engine import Runtime, SensorConfig
from ..settings import FACES, KIND_OBJECTS, TEACH, UPLOAD_LIMITS, merge_objects
from .common import API_PREFIX, iso, runtime, slugify
from .samples import copy_limited
from .teach import RESERVED_KEYS, frame_image, remember_taught

router = APIRouter(prefix=f"{API_PREFIX}/sensors", tags=["faces"])
people_router = APIRouter(prefix=API_PREFIX, tags=["faces"])

# Why a face cannot be taught, in words (faces.problem).
PROBLEMS = {
    "none": "No face found here. Pick a moment where the face is visible.",
    "cut": "The edge of the picture cuts the face here. Pick a moment where the whole face is in view.",
    "small": "The face is too small here. Pick a moment where the person is closer to the camera.",
    "turned": "The face is turned away too far here. Pick a moment where they look towards the camera.",
    "blurry": "The face is blurry here. Pick a sharper moment.",
    "unsure": "This does not clearly look like a face. Pick another moment.",
}


# The same, about a photo from a phone (People page).
PHOTO_PROBLEMS = {
    "none": "No face found in this photo.",
    "cut": "The edge of this photo cuts the face.",
    "small": "The face is too small in this photo: take it closer.",
    "turned": "The face is turned away in this photo: look at the camera.",
    "blurry": "This photo is blurry.",
    "unsure": "This does not clearly look like a face.",
}


class FaceBoxIn(BaseModel):
    """A person box on the frame a check analysed (``frame_id``) or on a history frame."""

    frame_id: str | None = Field(default=None, max_length=64)
    history_id: int | None = None
    box: list[float] = Field(min_length=4, max_length=4)  # x1, y1, x2, y2 normalised to the frame

    @field_validator("box")
    @classmethod
    def _valid_box(cls, value: list[float]) -> list[float]:
        x1, y1, x2, y2 = (min(max(float(v), 0.0), 1.0) for v in value)
        if x2 - x1 < TEACH["min_box"] or y2 - y1 < TEACH["min_box"]:
            raise ValueError("The box is too small")
        return [x1, y1, x2, y2]


class FaceTeachIn(FaceBoxIn):
    """Who the face in a person box is: one of the people, or a new person."""

    score: float | None = Field(default=None, ge=0, le=1)
    person: str | None = Field(default=None, max_length=64)
    new_person: str | None = Field(default=None, min_length=1, max_length=64)


class FaceMoveIn(BaseModel):
    person: str = Field(max_length=64)


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


# --- The people -----------------------------------------------------------------------------------


def person_by_key(s: Session, key: str) -> Person:
    person = s.scalar(select(Person).where(Person.key == key))
    if person is None:
        raise HTTPException(404, f"No person called {key!r}")
    return person


def _check_name(s: Session, name: str, keep: int | None = None) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(400, "Give the person a name")
    for other in s.scalars(select(Person)):
        if other.id != keep and other.name.lower() == name.lower():
            raise HTTPException(400, f"There is already a person called {name}")
    return name


def taken_keys(s: Session) -> set[str]:
    """Keys a new person or own label may not have: objects VisionState knows, every own label of
    every sensor and every person (all can be entities of one sensor)."""
    taken = set(detectors.LABELS.by_key) | RESERVED_KEYS
    taken |= set(s.scalars(select(Person.key)))
    for objects in s.scalars(select(Sensor.objects).where(Sensor.kind == KIND_OBJECTS)):
        taken |= {label["key"] for label in merge_objects(objects)["custom"]}
    return taken


def new_person(s: Session, name: str) -> Person:
    """A new person; their key never clashes with an object, an own label or another person."""
    if (s.scalar(select(func.count()).select_from(Person)) or 0) >= FACES["max_people"]:
        raise HTTPException(400, f"VisionState can know at most {FACES['max_people']} people")
    name = _check_name(s, name)
    taken = taken_keys(s)
    base = slugify(name, "person")
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    person = Person(key=key, name=name)
    s.add(person)
    s.flush()
    return person


# --- Teaching a face on a sensor ------------------------------------------------------------------


async def _face_model(rt: Runtime) -> faces.FaceModel:
    model = rt.wanted_faces()
    if model is None:
        status = rt.face_status()
        raise HTTPException(409, status["error"] or "The face model is still loading. Try again in a moment.")
    return model


async def _find(
    rt: Runtime, sensor_id: int, body: FaceBoxIn, cfg: SensorConfig
) -> tuple[Image.Image, str, faces.Face | None, str | None]:
    """The frame, its origin, the face in the person box (frame pixels) and why it cannot be used."""
    model = await _face_model(rt)
    image, origin = await frame_image(rt, sensor_id, body.frame_id, body.history_id)
    [(face, aligned)] = await rt.find_faces(model, image, [body.box])
    if face is None or aligned is None:
        return image, origin, None, "none"
    min_px = rt.face_limits(cfg)["min_face_px"]
    return image, origin, face, faces.problem(face, aligned, min_px, (image.width, image.height))


def _config(rt: Runtime, sensor_id: int) -> SensorConfig:
    cfg = rt.load_sensor(sensor_id)
    if cfg is None or not cfg.is_objects:
        raise HTTPException(404, "Sensor not found")
    if not cfg.faces_on:
        raise HTTPException(400, "Turn on face recognition for this sensor first (its Settings tab)")
    return cfg


@router.post("/{sensor_id}/faces/check")
async def check_face(sensor_id: int, body: FaceBoxIn, request: Request) -> dict:
    """Whether there is a face in a person box that can be taught: {face (normalised box), problem, message}."""
    rt = runtime(request)
    cfg = await asyncio.to_thread(_config, rt, sensor_id)
    image, _origin, face, problem = await _find(rt, sensor_id, body, cfg)
    box = None
    if face is not None:
        w, h = image.width, image.height
        box = [
            round(face.box[0] / w, 4),
            round(face.box[1] / h, 4),
            round(face.box[2] / w, 4),
            round(face.box[3] / h, 4),
        ]
    return {"face": box, "problem": problem, "message": PROBLEMS.get(problem or "", "")}


@router.post("/{sensor_id}/faces", status_code=201)
async def teach_face(sensor_id: int, body: FaceTeachIn, request: Request) -> dict:
    """Teach the face in a person box as one of the people, or as a new person; every sensor
    that recognises faces knows it from then on.

    Only a face that passes the quality gate is taught: a back or a jacket never becomes someone."""
    rt = runtime(request)
    cfg = await asyncio.to_thread(_config, rt, sensor_id)
    if not body.person and not body.new_person:
        raise HTTPException(400, "Say who it is: a person or a new person")

    def check() -> None:
        with rt.db.session() as s:
            if body.person:
                person = person_by_key(s, body.person)
                count = s.scalar(select(func.count()).select_from(Face).where(Face.person_id == person.id)) or 0
                if count >= FACES["max_faces_per_person"]:
                    raise HTTPException(400, f"A person can have at most {FACES['max_faces_per_person']} faces")
            else:
                # Checked now, made only once the face is good.
                if (s.scalar(select(func.count()).select_from(Person)) or 0) >= FACES["max_people"]:
                    raise HTTPException(400, f"VisionState can know at most {FACES['max_people']} people")
                _check_name(s, body.new_person or "")

    await asyncio.to_thread(check)
    image, origin, face, problem = await _find(rt, sensor_id, body, cfg)
    if problem or face is None:
        raise HTTPException(400, PROBLEMS.get(problem or "none", PROBLEMS["none"]))
    crop, local = faces.face_crop(image, face)
    filename = await asyncio.to_thread(rt.storage.save_face, crop)

    def add() -> tuple[int, str, str, int]:
        with rt.db.session() as s:
            person = person_by_key(s, body.person) if body.person else new_person(s, body.new_person or "")
            face_id = add_face_row(s, person.id, sensor_id, filename, crop, faces.face_to_dict(local), origin)
            key, name = person.key, person.name
            n = s.scalar(select(func.count()).select_from(Face).where(Face.person_id == person.id)) or 0
            return face_id, key, name, n

    face_id, key, name, n = await asyncio.to_thread(add)
    if body.new_person:
        await rt.people_changed()  # the person's entities on every sensor that recognises faces
    else:
        await asyncio.to_thread(rt.faces_changed)
    if body.history_id is not None:
        await asyncio.to_thread(remember_taught, rt, sensor_id, body.history_id, body.box, key, "person", True)
        await rt.person_last_seen_changed(key)  # Home Assistant gets that last_seen too
    return {"id": face_id, "label": key, "name": name, "faces": n}


def add_face_row(
    s: Session, person_id: int, sensor_id: int | None, filename: str, crop: Image.Image, face: dict, origin: str
) -> int:
    row = Face(
        person_id=person_id,
        sensor_id=sensor_id,
        filename=filename,
        origin=origin,
        is_night=imaging.is_night(crop),
        face=face,
    )
    s.add(row)
    s.flush()
    return row.id


# --- Shared: the people and their faces -----------------------------------------------------------


@people_router.get("/people")
async def people(request: Request) -> dict:
    """Every person with their faces (and the camera each was taught on), where each was last
    seen; look-alikes and faces that may have the wrong name (only while the face model is
    loaded: they need its embeddings)."""
    rt = runtime(request)

    def read() -> tuple[list[tuple[str, str]], list[tuple], dict[int, str]]:
        with rt.db.session() as s:
            persons = [(p.key, p.name) for p in s.scalars(select(Person).order_by(Person.created_at))]
            rows = s.execute(
                select(Face.id, Person.key, Face.origin, Face.is_night, Face.created_at, Face.sensor_id)
                .join(Person, Face.person_id == Person.id)
                .order_by(Face.created_at.desc())
            ).all()
            names: dict[int, str] = {i: n for i, n in s.execute(select(Sensor.id, Sensor.name)).all()}
        return persons, [tuple(r) for r in rows], names

    persons, rows, sensor_names = await asyncio.to_thread(read)
    suspects: dict[int, str] = {}
    look_alikes: list[dict] = []
    if rt.face_model is not None:
        index = await rt.face_index_for(rt.face_model)
        suspects, look_alikes = _quality(index, rt.face_limits(None))
    oldest: dict[str, datetime] = {}
    for _id, key, _origin, _night, created, _sensor in rows:
        if key not in oldest or created < oldest[key]:
            oldest[key] = created
    return {
        "model": rt.face_status(),
        "sensors": await asyncio.to_thread(rt.face_sensor_ids),
        "people": [
            {
                "key": key,
                "name": name,
                "seen": {**(seen := rt.person_seen(key)), "sensor_name": sensor_names.get(seen["sensor_id"] or 0)},
                "oldest": iso(oldest.get(key)),
                "faces": [
                    {
                        "id": fid,
                        "origin": origin,
                        "is_night": night,
                        "created_at": iso(created),
                        "sensor_id": sensor_id,
                        "sensor_name": sensor_names.get(sensor_id) if sensor_id is not None else None,
                        "suspect": suspects.get(fid),
                    }
                    for fid, person, origin, night, created, sensor_id in rows
                    if person == key
                ],
            }
            for key, name in persons
        ],
        "look_alikes": look_alikes,
    }


def _quality(index: faces.FaceIndex, limits: dict[str, float]) -> tuple[dict[int, str], list[dict]]:
    """Faces closer to another person than to their own (id -> that person), and pairs of people
    whose closest faces come near the match threshold (FACES["look_alike"])."""
    suspects: dict[int, str] = {}
    pairs: dict[tuple[str, str], float] = {}
    if not index.ids:
        return suspects, []
    sims = index.vectors @ index.vectors.T
    labels = np.array(index.labels)
    for i, face_id in enumerate(index.ids):
        own = labels == labels[i]
        own[i] = False
        per: dict[str, float] = {}
        for label in set(index.labels):
            mask = (labels == label) if label != labels[i] else own
            values = np.sort(sims[i][mask])[::-1][: FACES["top_k"]]
            if len(values):
                per[label] = float(values.mean())
        mine = per.get(str(labels[i]))
        others = {k: v for k, v in per.items() if k != labels[i]}
        if mine is not None and others:
            closest, value = max(others.items(), key=lambda kv: kv[1])
            if value > mine:
                suspects[face_id] = closest
        for label in others:
            a, b = sorted((str(labels[i]), label))
            pairs[(a, b)] = max(pairs.get((a, b), -1.0), float(sims[i][labels == label].max()))
    limit = limits["match_similarity"] * FACES["look_alike"]
    look_alikes = [{"a": a, "b": b, "similarity": round(v, 3)} for (a, b), v in pairs.items() if v >= limit]
    return suspects, look_alikes


@people_router.post("/people/photos", status_code=201)
async def add_photos(
    request: Request,
    files: list[UploadFile] = File(...),
    person: str | None = Form(default=None, max_length=64),
    new_name: str | None = Form(default=None, alias="new_person", max_length=64),
) -> dict:
    """Teach faces from photos or short videos (taken with a phone or picked from its gallery;
    read like a state sensor's upload: images, ZIP archives, videos) as one of the people, or as a
    new person (made only when there is a usable face). The best face of each picture goes
    through the same quality gate as on a camera; at most FACES["max_photos"] faces are added."""
    rt = runtime(request)
    if not person and not (new_name or "").strip():
        raise HTTPException(400, "Say who it is: a person or a new person")
    if not await asyncio.to_thread(rt.face_sensor_ids):  # the face model is only fetched after a sensor's confirmation
        raise HTTPException(400, "Turn on face recognition on a sensor first (its Settings → Faces)")

    def check() -> None:
        with rt.db.session() as s:
            if person:
                person_by_key(s, person)
            else:
                if (s.scalar(select(func.count()).select_from(Person)) or 0) >= FACES["max_people"]:
                    raise HTTPException(400, f"VisionState can know at most {FACES['max_people']} people")
                _check_name(s, new_name or "")

    await asyncio.to_thread(check)
    model = await _face_model(rt)
    min_px = rt.face_limits(None)["min_face_px"]
    found: list[tuple[Image.Image, faces.Face]] = []
    skipped: list[dict] = []
    for upload_file in files:
        if len(found) >= FACES["max_photos"]:
            break
        name = upload_file.filename or "photo"
        try:
            pictures = await asyncio.to_thread(_pictures, upload_file, name, FACES["max_photos"] * 3)
        except (uploads.UploadError, OSError) as err:
            skipped.append({"file": name, "message": str(err) or "Not an image this app can read."})
            continue
        problems: list[str] = []
        for image in pictures:
            face, aligned = await rt.face_in_photo(model, image)
            problem = "none" if face is None or aligned is None else faces.problem(face, aligned, min_px, image.size)
            if problem or face is None:
                problems.append(problem or "none")
                continue
            found.append((image, face))
            if len(found) >= FACES["max_photos"]:
                break
        if problems and len(problems) == len(pictures):  # nothing usable in this file
            skipped.append({"file": name, "message": PHOTO_PROBLEMS.get(problems[0], PHOTO_PROBLEMS["none"])})
    if not found:
        return {"label": person, "name": None, "added": 0, "skipped": skipped, "faces": None}

    def add() -> dict:
        with rt.db.session() as s:
            who = person_by_key(s, person) if person else new_person(s, new_name or "")
            count = s.scalar(select(func.count()).select_from(Face).where(Face.person_id == who.id)) or 0
            added = 0
            for image, face in found:
                if count >= FACES["max_faces_per_person"]:
                    skipped.append(
                        {"file": "", "message": f"A person has at most {FACES['max_faces_per_person']} faces."}
                    )
                    break
                crop, local = faces.face_crop(image, face)
                filename = rt.storage.save_face(crop)
                add_face_row(s, who.id, None, filename, crop, faces.face_to_dict(local), "upload")
                count, added = count + 1, added + 1
            result = {"label": who.key, "name": who.name, "added": added, "skipped": skipped, "faces": count}
            return result

    result = await asyncio.to_thread(add)
    if not person:
        await rt.people_changed()
    else:
        await asyncio.to_thread(rt.faces_changed)
    return result


def _pictures(upload_file: UploadFile, name: str, limit: int) -> list[Image.Image]:
    """The pictures in an uploaded file (an image, the images of a ZIP, frames of a video), at
    most ``limit``; the file goes through a temporary file within the upload limit."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(name).suffix) as tmp:
        path = Path(tmp.name)
        within = copy_limited(upload_file.file, tmp, UPLOAD_LIMITS["max_file_mb"] * 1_000_000)
    try:
        if not within:
            raise uploads.UploadError(f"Larger than {UPLOAD_LIMITS['max_file_mb']} MB.")
        frames = uploads.frames_from_file(path, name, FACES["photo_video_interval_s"])
        return [image for image, _origin in itertools.islice(frames, limit)]
    finally:
        path.unlink(missing_ok=True)


@people_router.patch("/people/{key}")
async def rename_person(key: str, body: PersonIn, request: Request) -> dict:
    """A person's name (their entities' names follow; their key and entity IDs stay)."""
    rt = runtime(request)

    def rename() -> dict:
        with rt.db.session() as s:
            person = person_by_key(s, key)
            person.name = _check_name(s, body.name, keep=person.id)
            result = {"key": person.key, "name": person.name}
            return result

    result = await asyncio.to_thread(rename)
    await rt.people_changed()
    return result


@people_router.delete("/people/{key}", status_code=204)
async def forget_person(key: str, request: Request) -> Response:
    """Forget a person: their faces, embeddings and entities on every sensor."""
    rt = runtime(request)
    await asyncio.to_thread(_forget_people, rt, key)
    await rt.people_changed()
    return Response(status_code=204)


@people_router.delete("/people", status_code=204)
async def forget_everyone(request: Request) -> Response:
    """Forget every person and every face."""
    rt = runtime(request)
    await asyncio.to_thread(_forget_people, rt, None)
    await rt.people_changed()
    return Response(status_code=204)


def _forget_people(rt: Runtime, key: str | None) -> None:
    with rt.db.session() as s:
        persons = [person_by_key(s, key)] if key is not None else list(s.scalars(select(Person)))
        for person in persons:
            for face in s.scalars(select(Face).where(Face.person_id == person.id)):
                rt.db.after_commit(s, partial(rt.storage.delete_face, face.filename))
            s.delete(person)  # their faces and embeddings go with them (foreign keys)


@people_router.get("/faces/{face_id}/image")
def face_image(face_id: int, request: Request) -> FileResponse:
    rt = runtime(request)
    with rt.db.session() as s:
        face = s.get(Face, face_id)
        if face is None:
            raise HTTPException(404, "Face not found")
        path = rt.storage.face_path(face.filename)
    if not path.exists():
        raise HTTPException(404, "Image file missing")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@people_router.patch("/faces/{face_id}")
def move_face(face_id: int, body: FaceMoveIn, request: Request) -> dict:
    """Give a taught face another person's name (it was taught under the wrong one)."""
    rt = runtime(request)
    with rt.db.session() as s:
        face = s.get(Face, face_id)
        if face is None:
            raise HTTPException(404, "Face not found")
        face.person_id = person_by_key(s, body.person).id
    rt.faces_changed()
    return {"id": face_id, "label": body.person}


@people_router.delete("/faces/{face_id}", status_code=204)
def forget_face(face_id: int, request: Request) -> Response:
    rt = runtime(request)
    with rt.db.session() as s:
        face = s.get(Face, face_id)
        if face is None:
            raise HTTPException(404, "Face not found")
        rt.db.after_commit(s, partial(rt.storage.delete_face, face.filename))
        s.delete(face)  # its cached embeddings go with it (foreign key)
    rt.faces_changed()
    return Response(status_code=204)
