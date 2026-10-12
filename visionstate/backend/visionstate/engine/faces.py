"""Recognising people by their face on object sensors (see faces.py and docs/FACE_RECOGNITION_PLAN.md).

The face model is downloaded and loaded in the background when a sensor needs it — a sensor keeps
working as an object sensor without faces meanwhile — and let go when no sensor uses it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

import numpy as np
from PIL import Image
from sqlalchemy import delete, select

from .. import backbones, faces, imaging
from ..db import Face, FaceEmbedding, Person, Sensor
from ..mqtt import person_topics
from ..redact import redact
from ..settings import FACE_UNKNOWN_KEY, FACES, KIND_OBJECTS, PERSON_NOT_SEEN, TEACH, faces_on, merge_objects
from .base import RuntimeBase
from .models import ModelsMixin
from .publishing import PublishingMixin
from .state import SensorConfig

log = logging.getLogger(__name__)


def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def same_box(previous: list[dict], box: Sequence[float]) -> dict | None:
    """The last check's person box that is this one (it overlaps by TEACH["keep_iou"])."""
    best = max(
        (d for d in previous if d.get("key") == "person" or d.get("was") == "person"),
        key=lambda d: _iou(d["box"], box),
        default=None,
    )
    return best if best is not None and _iou(best["box"], box) >= TEACH["keep_iou"] else None


class FacesMixin(ModelsMixin, PublishingMixin, RuntimeBase):
    # --- The model ------------------------------------------------------------------------------

    def face_status(self) -> dict:
        """The face model for the UI: chosen, loaded, downloading (bytes), or why it failed."""
        spec = faces.recogniser_spec(self.face_choice)
        return {
            "model": spec.id,
            "loaded": self.face_model is not None and self.face_model.id == spec.id,
            "loading": self._face_task is not None and not self._face_task.done(),
            "progress": self.face_progress,
            "error": self.face_error,
            "downloaded": self.face_files_present(spec.id),
        }

    def face_files_present(self, model_id: str) -> bool:
        spec = faces.recogniser_spec(model_id)
        return self.model_path(faces.DETECTOR) is not None and self.model_path(spec) is not None

    def wanted_faces(self) -> faces.FaceModel | None:
        """The face model when it is loaded; otherwise its loading is started in the background
        (unless it failed: then only ``retry_faces`` tries again) and None is returned."""
        model = self.face_model
        if model is not None and model.id == self.face_choice:
            return model
        if self._on_loop(self.wanted_faces):
            return None  # called from a worker thread: started on the event loop
        if not self.face_error and (self._face_task is None or self._face_task.done()):
            self._face_task = self._spawn(self._load_faces())
        return None

    def retry_faces(self) -> None:
        self.face_error = ""
        self.wanted_faces()

    def _set_progress(self, done: int, total: int) -> None:
        self.face_progress = {"done": done, "total": total}

    async def _load_faces(self) -> None:
        async with self._face_lock:
            detector_spec, spec = faces.DETECTOR, faces.recogniser_spec(self.face_choice)
            try:
                missing = [s for s in (detector_spec, spec) if self.model_path(s) is None]
                total, before = sum(s.size for s in missing), 0
                paths = []
                for model_spec in (detector_spec, spec):
                    path = self.model_path(model_spec)
                    if path is None:
                        start = before
                        self._set_progress(start, total)
                        try:
                            path = await asyncio.to_thread(
                                backbones.download,
                                model_spec,
                                self.settings.models_dir,
                                lambda n, start=start: self._set_progress(start + n, total),
                            )
                        except Exception as err:
                            raise RuntimeError(
                                f"Could not download {model_spec.filename} from {model_spec.url} ({err}). "
                                f"Without internet, put that file in {self.settings.models_dir} by hand."
                            ) from err
                        before += model_spec.size
                    paths.append(path)
                model = await asyncio.to_thread(
                    lambda: faces.FaceModel(
                        faces.FaceDetector(detector_spec, paths[0]), faces.Recogniser(spec, paths[1])
                    )
                )
            except Exception as err:  # noqa: BLE001 - shown in the UI; the sensors go on without faces
                self.face_error = redact(str(err)) or type(err).__name__
                log.exception("Could not load the face model %s", spec.id)
                return
            finally:
                self.face_progress = None
            replaced, self.face_model, self.face_error = self.face_model, model, ""
            log.info("Face model %s loaded", spec.id)
        if replaced is not None:
            await asyncio.to_thread(backbones.release_memory)
        sensor_ids = await asyncio.to_thread(self.face_sensor_ids)
        if not sensor_ids:
            # Turned off while it loaded (a check in flight asked for it): let it go again.
            await self.faces_settled()
            return
        for sensor_id in sensor_ids:
            self.wake(sensor_id, force=True)

    def face_sensor_ids(self) -> list[int]:
        with self.db.session() as s:
            rows = s.scalars(select(Sensor).where(Sensor.kind == KIND_OBJECTS)).all()
            return [row.id for row in rows if faces_on(merge_objects(row.objects))]

    async def faces_settled(self) -> None:
        """A sensor turned faces on or off: load the model when one uses it, let it go when none does."""
        if await asyncio.to_thread(self.face_sensor_ids):
            self.wanted_faces()
            return
        if self.face_model is not None:
            self.face_model = None
            self.face_index = None
            await asyncio.to_thread(backbones.release_memory)
            log.info("Face model let go: no sensor recognises faces")

    async def set_face_model(self, model_id: str) -> None:
        """Choose the recogniser. Taught faces are embedded again with it (their crops are kept)."""
        await asyncio.to_thread(self.db.set_setting, "face_model", model_id)
        self.face_choice = model_id
        self.face_error = ""
        self.face_index = None
        if self.face_model is not None or await asyncio.to_thread(self.face_sensor_ids):
            self.wanted_faces()

    def delete_face_files(self, model_id: str) -> None:
        """Delete a downloaded face model (only while no sensor uses it); the face detector goes
        with the last one."""
        models = self.settings.models_dir
        (models / faces.RECOGNISERS[model_id].filename).unlink(missing_ok=True)
        if not any((models / spec.filename).exists() for spec in faces.RECOGNISERS.values()):
            (models / faces.DETECTOR.filename).unlink(missing_ok=True)

    # --- What was taught (the people, shared by every sensor) -------------------------------------

    async def face_index_for(self, model: faces.FaceModel) -> faces.FaceIndex:
        index = self.face_index
        if index is None or index.model != model.id or index.generation != self._face_generation:
            index = await asyncio.to_thread(self._build_face_index, model, self._face_generation)
            self.face_index = index
        return index

    def _build_face_index(self, model: faces.FaceModel, generation: int) -> faces.FaceIndex:
        cache_key = f"{model.id}/{faces.EMBEDDING_VERSION}"  # cached embeddings of another version are not used
        with self.db.session() as s:
            s.execute(delete(FaceEmbedding).where(FaceEmbedding.model == model.id))  # before versions
            examples = [
                (face_id, key, filename, dict(face))
                for face_id, key, filename, face in s.execute(
                    select(Face.id, Person.key, Face.filename, Face.face)
                    .join(Person, Face.person_id == Person.id)
                    .order_by(Face.id)
                ).all()
            ]
            cached = {
                r.face_id: np.frombuffer(r.vector, dtype=np.float32)
                for r in s.scalars(
                    select(FaceEmbedding).where(
                        FaceEmbedding.face_id.in_([e[0] for e in examples]), FaceEmbedding.model == cache_key
                    )
                )
            }
        ids, labels, vectors = [], [], []
        for face_id, key, filename, face in examples:
            vector = cached.get(face_id)
            if vector is None:
                path = self.storage.face_path(filename)
                if not path.exists():
                    continue
                crop = imaging.load(path)
                aligned = faces.align(crop, faces.face_from_dict(face), model.recogniser.spec.input_size)
                vector = model.recogniser.embed([aligned])[0]
                with self.db.session() as s:
                    s.merge(FaceEmbedding(face_id=face_id, model=cache_key, vector=vector.tobytes()))
            ids.append(face_id)
            labels.append(key)
            vectors.append(vector)
        stacked = np.stack(vectors) if vectors else np.zeros((0, 0), dtype=np.float32)
        return faces.FaceIndex(model.id, ids, labels, stacked, generation)

    def faces_changed(self) -> None:
        """Taught faces were added, moved or removed: every sensor that recognises faces compares
        with the new set from its next check on."""
        self._face_generation += 1
        self.face_index = None
        for sensor_id in self.face_sensor_ids():
            self.wake(sensor_id, force=True)

    async def people_changed(self) -> None:
        """A person was added, renamed or forgotten: the entities of every sensor that recognises
        faces, and each person's own entity, follow."""
        await asyncio.to_thread(self.faces_changed)
        for sensor_id in await asyncio.to_thread(self.face_sensor_ids):
            cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
            if cfg is not None:
                await self.publish_discovery(cfg)
        await self.publish_people()

    # --- Where each person is (sensor.visionstate_<key>) ----------------------------------------

    def _people(self) -> list[tuple[str, str]]:
        with self.db.session() as s:
            return [(p.key, p.name) for p in s.scalars(select(Person).order_by(Person.created_at))]

    def person_seen(self, key: str) -> dict:
        """Where a person is seen now (a sensor's slug, or PERSON_NOT_SEEN), and when and on which
        sensor they were last seen."""
        at, sensor_id = self._person_last.get(key, (0.0, 0))
        return {"now": self._person_where.get(key, PERSON_NOT_SEEN), "at": at or None, "sensor_id": sensor_id or None}

    async def publish_people(self) -> None:
        """Each person's own entity (again): discovery for the people there are, removed for the
        ones forgotten, and where each is."""
        people = await asyncio.to_thread(self._people)
        keys = {key for key, _ in people}
        for key in set(self._person_where) - keys:
            await self.mqtt.remove_person(key)
            self._person_where.pop(key, None)
            self._person_last.pop(key, None)
        await self.mqtt.publish_people_discovery(people)
        for key, _ in people:
            await self._publish_person(key, self._person_where.get(key, PERSON_NOT_SEEN), force=True)

    async def person_last_seen_changed(self, key: str) -> None:
        """A person was taught from a frame (they were there then): their entity's last_seen now."""
        await self._publish_person(key, self._person_where.get(key, PERSON_NOT_SEEN), force=True)

    async def update_people(self, cfg: SensorConfig) -> None:
        """After a check of a sensor that recognises faces: where each person is now. A person
        seen on several sensors is where they were seen last."""
        live = self.live_state(cfg.id)
        keys = [c["key"] for c in cfg.custom_labels if c.get("face")]
        for key in keys:
            track = live.tracks.get(key)
            if track is not None and track.on and track.last_seen:
                last = self._person_last.get(key)
                if last is None or track.last_seen >= last[0]:
                    self._person_last[key] = (track.last_seen, cfg.id)
        for key in keys:
            where = PERSON_NOT_SEEN
            _at, sensor_id = self._person_last.get(key, (0.0, 0))
            track = self.live_state(sensor_id).tracks.get(key) if sensor_id else None
            if track is not None and track.on:
                where = cfg.slug if sensor_id == cfg.id else await self._sensor_slug(sensor_id) or PERSON_NOT_SEEN
            await self._publish_person(key, where)

    async def _sensor_slug(self, sensor_id: int) -> str:
        cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
        return cfg.slug if cfg else ""

    async def _publish_person(self, key: str, where: str, force: bool = False) -> None:
        if not force and self._person_where.get(key) == where:
            return
        self._person_where[key] = where
        t = person_topics(key)
        at, sensor_id = self._person_last.get(key, (0.0, 0))
        last = await asyncio.to_thread(self.load_sensor, sensor_id) if sensor_id else None
        attributes = {
            "last_seen": datetime.fromtimestamp(at, UTC).isoformat() if at else None,
            "last_sensor": last.slug if last else None,
            "last_sensor_name": last.name if last else None,
        }
        await self.mqtt.publish(t["state"], where, retain=True)
        await self.mqtt.publish(t["attributes"], attributes, retain=True)
        if attributes["last_seen"]:  # never seen: the timestamp sensor stays unknown
            await self.mqtt.publish(t["last_seen"], attributes["last_seen"], retain=True)

    # --- One check ------------------------------------------------------------------------------

    async def face_in_photo(
        self, model: faces.FaceModel, image: Image.Image
    ) -> tuple[faces.Face | None, Image.Image | None]:
        """The best face in a whole photo (a selfie from a phone), with it aligned."""
        async with self._sem:
            [face] = await asyncio.to_thread(model.detector.detect, [image])
        if face is None:
            return None, None
        return face, await asyncio.to_thread(faces.align, image, face, model.recogniser.spec.input_size)

    async def find_faces(
        self, model: faces.FaceModel, image: Image.Image, boxes: list[Sequence[float]]
    ) -> list[tuple[faces.Face | None, Image.Image | None]]:
        """The best face in each person box (normalised), in frame pixels, with it aligned."""
        regions, offsets = [], []
        for box in boxes:
            x1, y1, x2, y2 = faces.head_region(box, image.width, image.height)
            regions.append(image.crop((x1, y1, max(x2, x1 + 1), max(y2, y1 + 1))))
            offsets.append((x1, y1))
        async with self._sem:
            found = await asyncio.to_thread(model.detector.detect, regions)
        result: list[tuple[faces.Face | None, Image.Image | None]] = []
        for face, (dx, dy) in zip(found, offsets, strict=True):
            if face is None:
                result.append((None, None))
                continue
            moved = face.moved(dx, dy, 1.0)
            aligned = await asyncio.to_thread(faces.align, image, moved, model.recogniser.spec.input_size)
            result.append((moved, aligned))
        return result

    async def apply_faces(
        self, cfg: SensorConfig, image: Image.Image, found: list[dict], previous: list[dict]
    ) -> list[dict]:
        """Look for faces in the person boxes of a check and tell who they are.

        Each searched person box gets ``face`` ({box (normalised), problem, label, similarity,
        state, sure, unknown, age}); a recognised person becomes the box's own ``label`` (it
        counts for that person), a maybe ``ask``, and a clearly seen face that matched nobody for
        FACES["unknown_after"] checks ``label`` = FACE_UNKNOWN_KEY. A box that stays where it was
        keeps its name while no usable face is seen (and while the face stays close enough).
        """
        model = self.wanted_faces()
        if model is None:
            return found
        live = self.live_state(cfg.id)
        index = await self.face_index_for(model)
        limits = self.face_limits(cfg)
        settings = cfg.objects["faces"]
        result = [dict(d) for d in found]
        searched: list[int] = []
        waiting: list[tuple[int, dict]] = []  # person boxes to search, with what they were last check
        for i, d in enumerate(result):
            if d["key"] != "person" or d.get("filtered"):
                continue
            face_before = (same_box(previous, d["box"]) or {}).get("face") or {}
            if face_before.get("sure") and face_before.get("age", 0) + 1 < FACES["recheck_every"]:
                # A sure name: carried over for a few checks without looking again (saves CPU).
                result[i]["face"] = {**face_before, "age": face_before.get("age", 0) + 1}
                result[i]["label"] = face_before["label"]
                continue
            waiting.append((i, face_before))
        # At most FACES["max_faces"] per check: new boxes first, then the ones searched longest ago,
        # so in a crowd every person is looked at in turn.
        waiting.sort(key=lambda w: (bool(w[1]), -w[1].get("age", 0), -result[w[0]]["score"]))
        for i, face_before in waiting:
            if len(searched) < FACES["max_faces"]:
                searched.append(i)
            elif face_before:  # not looked at this check: it stays what it was
                result[i]["face"] = {**face_before, "age": face_before.get("age", 0) + 1}
                if face_before.get("label") and face_before.get("state") == "match":
                    result[i]["label"] = face_before["label"]
                elif face_before.get("unknown", 0) >= FACES["unknown_after"] and not face_before.get("named"):
                    result[i]["label"] = FACE_UNKNOWN_KEY
        if not searched:
            return result
        seen = await self.find_faces(model, image, [result[i]["box"] for i in searched])
        live.face_frame = (image.width, image.height)
        live.face_min_px = limits["min_face_px"]
        taken: list[tuple[float, float, float, float]] = []  # one face counts for one person box
        usable: list[tuple[int, faces.Face, Image.Image]] = []
        for i, (face, aligned) in zip(searched, seen, strict=True):
            problem = "none"
            if face is not None and aligned is not None:
                if any(_iou(face.box, other) > 0.5 for other in taken):
                    face, problem = None, "none"
                else:
                    taken.append(face.box)
                    min_px = limits["min_face_px"]
                    problem = faces.problem(face, aligned, min_px, (image.width, image.height)) or ""
            entry: dict = {"problem": problem or None}
            if face is not None:
                w, h = image.width, image.height
                x1, y1, x2, y2 = face.box
                entry["box"] = [round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)]
                entry["face_px"] = round(face.width)
            result[i]["face"] = entry
            live.face_stats.append(problem or "ok")
            if not problem and face is not None and aligned is not None:
                usable.append((i, face, aligned))
        vectors = np.zeros((0, 0), dtype=np.float32)
        if usable and index.ids:
            async with self._sem:
                vectors = await asyncio.to_thread(model.recogniser.embed, [a for _, _, a in usable])
        for n, (i, face, _aligned) in enumerate(usable):
            clear = faces.clearly_seen(face, limits["min_face_px"])
            self._name_face(result[i], previous, index, vectors[n] if index.ids else None, limits, settings, clear)
        for i in searched:
            if result[i]["face"].get("problem"):
                self._keep_name(result[i], previous)  # no usable face now: keep what the box was
        return result

    def _name_face(
        self,
        det: dict,
        previous: list[dict],
        index: faces.FaceIndex,
        vector: np.ndarray | None,
        limits: dict[str, float],
        settings: dict,
        clear: bool = True,
    ) -> None:
        """Who the usable face of a person box is (see apply_faces). ``clear``: seen well enough
        (faces.clearly_seen) that matching nobody counts towards Unknown person."""
        face = det["face"]
        before = same_box(previous, det["box"])
        face_before = (before or {}).get("face") or {}
        named_before = face_before.get("label") if face_before.get("state") == "match" else None
        if vector is None:  # nobody taught yet: a usable face, nothing to compare with
            identity = faces.Identity(None, 0.0, 0.0, None)
        else:
            identity = faces.identify(index, vector)
        decision = faces.decide(identity, limits, named_before)
        named = bool(face_before.get("named")) or named_before is not None
        face.update({"similarity": identity.similarity, "candidate": identity.label, "age": 0, "named": named})
        if decision == "match" and identity.label is not None:
            agreed = named_before == identity.label or face_before.get("candidate") == identity.label
            if identity.similarity >= limits["sure_similarity"] or agreed:
                sure = identity.similarity >= limits["sure_similarity"]
                face.update({"label": identity.label, "state": "match", "sure": sure, "named": True})
                det["label"] = identity.label
                det["match"] = {"id": identity.example_id, "label": identity.label, "similarity": identity.similarity}
            else:
                face["state"] = "pending"  # a second check must agree first
            return
        if decision == "ask" and identity.label is not None:
            face["state"] = "ask"
            det["ask"] = {
                "id": identity.example_id,
                "label": identity.label,
                "similarity": identity.similarity,
                "face": True,
            }
            return
        if not clear:  # small or turned: not looking like anyone may only be the angle
            face.update({"state": "unclear", "unknown": face_before.get("unknown", 0)})
            if face["unknown"] >= FACES["unknown_after"] and not named and not det.get("label"):
                det["label"] = FACE_UNKNOWN_KEY  # a stranger seen clearly before stays one
            return
        face["state"] = "unknown"
        if not index.ids:
            return  # nobody was taught: there is no "unknown" yet
        face["unknown"] = face_before.get("unknown", 0) + 1
        if not named and face["unknown"] >= FACES["unknown_after"] and not det.get("label"):
            det["label"] = FACE_UNKNOWN_KEY
        if settings["ask_unknown"]:
            det["ask"] = {"id": None, "label": FACE_UNKNOWN_KEY, "similarity": identity.similarity, "face": True}

    @staticmethod
    def _keep_name(det: dict, previous: list[dict]) -> None:
        """A person box without a usable face this check keeps the name it had in the last one."""
        face_before = (same_box(previous, det["box"]) or {}).get("face") or {}
        if face_before.get("state") == "match" and face_before.get("label"):
            det["face"].update(
                {
                    "label": face_before["label"],
                    "state": "match",
                    "kept": True,
                    "named": True,
                    "similarity": face_before.get("similarity"),
                }
            )
            det["label"] = face_before["label"]
        elif face_before.get("unknown"):
            det["face"].update({"unknown": face_before["unknown"], "named": bool(face_before.get("named"))})
            if face_before["unknown"] >= FACES["unknown_after"] and not face_before.get("named"):
                det["label"] = det.get("label") or FACE_UNKNOWN_KEY
