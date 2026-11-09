"""Reading licence plates on object sensors (see plates.py and docs/PLATE_RECOGNITION_PLAN.md).

The plate model is downloaded and loaded in the background when a sensor needs it — a sensor keeps
working as an object sensor without plates meanwhile — and let go when no sensor uses it. The
household's cars are shared by every sensor (the DB setting "cars": [{key, name, plate, parent}],
the plate may be empty: a car known by its look); each is an own label of every sensor that reads
plates, and of every sensor its look was taught on (state.with_shared).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from datetime import UTC

from PIL import Image
from sqlalchemy import func, select

from .. import backbones, plates, timing
from ..db import Sample, Sensor, Setting
from ..mqtt import topics
from ..redact import redact
from ..settings import KIND_OBJECTS, PLATES, TEACH, VEHICLE_CLASSES, merge_objects, plates_on
from .base import RuntimeBase
from .faces import _iou
from .models import ModelsMixin
from .publishing import PublishingMixin
from .state import SensorConfig
from .teaching import TeachingMixin

log = logging.getLogger(__name__)


def same_vehicle(previous: list[dict], box: Sequence[float]) -> dict | None:
    """The last check's vehicle box that is this one (it overlaps by TEACH["keep_iou"])."""
    best = max(
        (d for d in previous if d.get("key") in VEHICLE_CLASSES or d.get("was") in VEHICLE_CLASSES),
        key=lambda d: _iou(d["box"], box),
        default=None,
    )
    return best if best is not None and _iou(best["box"], box) >= TEACH["keep_iou"] else None


def _overlaps_vehicle(det: dict, found: list[dict]) -> bool:
    """Whether another vehicle box covers part of this one: its plate may be that vehicle's, so the
    box is not learned from it."""
    x1, y1, x2, y2 = det["box"]
    for d in found:
        if d is det or d["key"] not in VEHICLE_CLASSES or d.get("filtered"):
            continue
        ox1, oy1, ox2, oy2 = d["box"]
        if min(x2, ox2) > max(x1, ox1) and min(y2, oy2) > max(y1, oy1):
            return True
    return False


def identified_by(found: list[dict], key: str) -> str | None:
    """How a car of the household is known in a check, for its entities' attributes: "plate_and_look"
    (its plate and its taught look agree), "plate", "look" (its taught look alone: parked side-on,
    the plate not seen), or None (no box of it). The surest box wins. A plate read while the box
    stayed counts until the box goes (the plate's ``kept``)."""
    rank, how = 0, None
    for d in found:
        if d.get("label") != key or d.get("filtered"):
            continue
        plate = d.get("plate") or {}
        by_plate = plate.get("state") == "match" and plate.get("car") == key
        by_look = (d.get("match") or {}).get("label") == key
        this = (3, "plate_and_look") if by_plate and by_look else (2, "plate") if by_plate else (1, "look")
        if this[0] > rank:
            rank, how = this
    return how


class PlatesMixin(TeachingMixin, ModelsMixin, PublishingMixin, RuntimeBase):
    # --- The model ------------------------------------------------------------------------------

    def plate_status(self) -> dict:
        """The plate model for the UI: chosen, loaded, downloading (bytes), or why it failed."""
        spec = plates.model_spec(self.plate_choice)
        return {
            "model": spec.id,
            "loaded": self.plate_model is not None and self.plate_model.id == spec.id,
            "loading": self._plate_task is not None and not self._plate_task.done(),
            "progress": self.plate_progress,
            "error": self.plate_error,
            "downloaded": self.plate_files_present(spec.id),
        }

    def plate_files_present(self, model_id: str) -> bool:
        return all(self.model_path(f) is not None for f in plates.model_spec(model_id).files)

    def wanted_plates(self) -> plates.PlateModel | None:
        """The plate model when it is loaded; otherwise its loading is started in the background
        (unless it failed: then only ``retry_plates`` tries again) and None is returned."""
        model = self.plate_model
        if model is not None and model.id == self.plate_choice:
            return model
        if self._on_loop(self.wanted_plates):
            return None
        if not self.plate_error and (self._plate_task is None or self._plate_task.done()):
            self._plate_task = self._spawn(self._load_plates())
        return None

    def retry_plates(self) -> None:
        self.plate_error = ""
        self.wanted_plates()

    async def _load_plates(self) -> None:
        async with self._plate_lock:
            spec = plates.model_spec(self.plate_choice)
            try:
                missing = [f for f in spec.files if self.model_path(f) is None]
                total, before = sum(f.size for f in missing), 0
                paths = []
                for file in spec.files:
                    path = self.model_path(file)
                    if path is None:
                        start = before
                        self.plate_progress = {"done": start, "total": total}
                        try:
                            path = await asyncio.to_thread(
                                backbones.download,
                                file,
                                self.settings.models_dir,
                                lambda n, start=start: setattr(
                                    self, "plate_progress", {"done": start + n, "total": total}
                                ),
                            )
                        except Exception as err:
                            raise RuntimeError(
                                f"Could not download {file.filename} from {file.url} ({err}). "
                                f"Without internet, put that file in {self.settings.models_dir} by hand."
                            ) from err
                        before += file.size
                    paths.append(path)
                model = await asyncio.to_thread(
                    lambda: plates.PlateModel(
                        spec, plates.Finder(spec.finder, paths[0]), plates.PlateReader(spec.reader, paths[1])
                    )
                )
            except Exception as err:  # noqa: BLE001 - shown in the UI; the sensors go on without plates
                self.plate_error = redact(str(err)) or type(err).__name__
                log.exception("Could not load the plate model %s", spec.id)
                return
            finally:
                self.plate_progress = None
            replaced, self.plate_model, self.plate_error = self.plate_model, model, ""
        if replaced is not None:
            await asyncio.to_thread(backbones.release_memory)
        log.info("Plate model %s loaded", spec.id)
        sensor_ids = await asyncio.to_thread(self.plate_sensor_ids)
        if not sensor_ids:
            await self.plates_settled()  # turned off while it loaded
        for sensor_id in sensor_ids:
            self.wake(sensor_id, force=True)

    def plate_sensor_ids(self) -> list[int]:
        with self.db.session() as s:
            rows = s.scalars(select(Sensor).where(Sensor.kind == KIND_OBJECTS)).all()
            return [row.id for row in rows if plates_on(merge_objects(row.objects))]

    async def plates_settled(self) -> None:
        """A sensor turned plates on or off: load the model when one uses it, let it go when none does."""
        if await asyncio.to_thread(self.plate_sensor_ids):
            self.wanted_plates()
            return
        if self.plate_model is not None:
            self.plate_model = None
            await asyncio.to_thread(backbones.release_memory)
            log.info("Plate model let go: no sensor reads plates")

    async def set_plate_model(self, model_id: str) -> None:
        """Choose the plate model; loaded in the background when a sensor reads plates."""
        await asyncio.to_thread(self.db.set_setting, "plate_model", model_id)
        self.plate_choice = model_id
        self.plate_error = ""
        if self.plate_model is not None or await asyncio.to_thread(self.plate_sensor_ids):
            self.wanted_plates()

    def delete_plate_files(self, model_id: str) -> None:
        """Delete a downloaded plate model's files that no other plate model and no reading sensor's
        reader uses (only while no sensor reads plates with it)."""
        spec = plates.model_spec(model_id)
        keep = {f.filename for m in plates.MODELS.values() if m.id != model_id for f in m.files}
        keep.add(f"{self.db.get_text('reader', '')}.onnx")
        for file in spec.files:
            if file.filename not in keep:
                (self.settings.models_dir / file.filename).unlink(missing_ok=True)

    # --- The household's cars (shared by every sensor) -----------------------------------------

    def cars(self) -> list[dict]:
        value = self.db.get_setting("cars", [])
        return value if isinstance(value, list) else []

    def object_sensor_ids(self) -> list[int]:
        with self.db.session() as s:
            return list(s.scalars(select(Sensor.id).where(Sensor.kind == KIND_OBJECTS)))

    def adopt_car_labels(self) -> list[int]:
        """Own labels of a vehicle kind (made on one sensor, before cars were shared, or brought by
        an imported sensor) become cars of the household, keeping their key, so their taught boxes
        and their entities stay as they were. A label whose key is a car's already is that car (a
        sensor exported from another installation); a name a car has already gets the sensor's
        name after it. Returns the sensors whose labels moved."""
        with self.db.session() as s:
            row = s.get(Setting, "cars")
            cars = [dict(c) for c in row.value] if row is not None and isinstance(row.value, list) else []
            keys = {c["key"] for c in cars}
            names = {c["name"].lower() for c in cars}
            moved = []
            for sensor in s.scalars(select(Sensor).where(Sensor.kind == KIND_OBJECTS)):
                own = merge_objects(sensor.objects)["custom"]
                keep = []
                for label in own:
                    if label.get("parent") not in VEHICLE_CLASSES:
                        keep.append(label)
                    elif label["key"] not in keys:
                        if len(cars) >= PLATES["max_cars"]:
                            keep.append(label)
                            continue
                        name = (
                            label["name"] if label["name"].lower() not in names else f"{label['name']} ({sensor.name})"
                        )
                        cars.append({"key": label["key"], "name": name[:64], "plate": "", "parent": label["parent"]})
                        keys.add(label["key"])
                        names.add(name.lower())
                if len(keep) != len(own):
                    sensor.objects = {**(sensor.objects or {}), "custom": keep}
                    moved.append(sensor.id)
            if moved:
                if row is None:
                    s.add(Setting(key="cars", value=cars))
                else:
                    row.value = cars
                log.info("Own labels of a vehicle kind became cars of the household on sensors %s", moved)
            return moved

    async def cars_changed(self) -> None:
        """A car was added, changed or removed: the entities of every object sensor follow (a car is
        an entity of the sensors that read plates and of those its look was taught on)."""
        for sensor_id in await asyncio.to_thread(self.object_sensor_ids):
            cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
            if cfg is not None:
                await self.publish_discovery(cfg)
            self.taught.pop(sensor_id, None)
            self.wake(sensor_id, force=True)

    # --- One check ------------------------------------------------------------------------------

    async def apply_plates(
        self, cfg: SensorConfig, image: Image.Image, found: list[dict], previous: list[dict]
    ) -> list[dict]:
        """Read the plates of the vehicle boxes of a check and tell whose car each is.

        Each vehicle box that was looked at gets ``plate`` ({text, score, box (normalised), car,
        distance, state, sure, age, count, announced}); state is "match" (a car of the household,
        which becomes the box's own ``label`` so it counts for that car), "pending" (close to a
        car's plate: one more read must agree), "unknown" (read the same way on
        PLATES["unknown_after"] checks, a car nobody named), "reading" (not yet) or "none" (no
        plate read). A box that stays where it was keeps its car while its plate is not read.

        A car can also be taught by its look (teaching ran before this: the box's ``match``). When
        the look says it is a car and the plate is read at most ``look_distance`` from that car's
        plate (the sensor's setting), the plate agrees: the car is named at once (``look`` True).
        A plate read the same way on PLATES["unknown_after"] checks that is far from the car the
        look says is another car: the plate decides, and the box loses the look's name.
        """
        model = self.wanted_plates()
        if model is None:
            return found
        cars = [c for c in await asyncio.to_thread(self.cars) if c.get("parent", "car") in cfg.objects["classes"]]
        result = [dict(d) for d in found]
        todo: list[tuple[int, dict]] = []
        for i, d in enumerate(result):
            if d["key"] not in VEHICLE_CLASSES or d.get("filtered"):
                continue
            before = (same_vehicle(previous, d["box"]) or {}).get("plate") or {}
            if (
                before.get("state") == "match"
                and before.get("sure")
                and before.get("age", 0) + 1 < PLATES["recheck_every"]
            ):
                result[i]["plate"] = {**before, "age": before.get("age", 0) + 1}
                result[i]["label"] = before["car"]
                continue
            todo.append((i, before))
        todo.sort(
            key=lambda t: (
                -((result[t[0]]["box"][2] - result[t[0]]["box"][0]) * (result[t[0]]["box"][3] - result[t[0]]["box"][1]))
            )
        )
        w, h = image.width, image.height
        for n, (i, before) in enumerate(todo):
            x1, y1, x2, y2 = result[i]["box"]
            px = (max(0, round(x1 * w)), max(0, round(y1 * h)), min(w, round(x2 * w)), min(h, round(y2 * h)))
            if n >= PLATES["max_vehicles"] or px[2] - px[0] < PLATES["min_vehicle_px"]:
                self._keep_car(result[i], before)
                continue
            crop = image.crop(px)
            async with timing.slot(self._sem):
                reads = await asyncio.to_thread(plates.read_plates, model, crop)
            reads = [r for r in reads if r.score >= PLATES["min_score"]]
            self._name_car(result[i], reads, before, cars, px, (w, h), cfg.objects["plates"]["look_distance"])
        return result

    @staticmethod
    def _keep_car(det: dict, before: dict) -> None:
        """No plate read this check: a box keeps the car it was (and what it already announced)."""
        if not before:
            return
        det["plate"] = {**before, "age": before.get("age", 0) + 1, "kept": True}
        if before.get("state") == "match" and before.get("car"):
            det["label"] = before["car"]

    def _name_car(
        self,
        det: dict,
        reads: list[plates.PlateRead],
        before: dict,
        cars: list[dict],
        px: tuple[int, int, int, int],
        frame: tuple[int, int],
        look_distance: float = 0.0,
    ) -> None:
        if not reads:
            self._keep_car(det, before)
            return
        # The car the box looks like (its taught look), if that is one of the cars.
        look = next((c for c in cars if c["key"] == (det.get("match") or {}).get("label")), None)
        # The read closest to a car's plate wins (a sticker read better than the plate must not hide
        # the plate); without a car close, the reader's best read.
        matches = [(plates.match(r.text, cars), r) for r in reads]
        near = [(m, r) for m, r in matches if m.key is not None and m.distance <= PLATES["match_distance"]]
        m, read = min(near, key=lambda mr: (mr[0].distance, -mr[1].score)) if near else matches[0]
        if (
            look is not None
            and look_distance > 0
            and not (m.key is not None and m.key != look["key"] and m.distance == 0)
        ):
            # The look says which car: the read closest to that car's plate, if close enough.
            to_look = min(
                ((plates.match(r.text, [look]), r) for r in reads), key=lambda mr: (mr[0].distance, -mr[1].score)
            )
            if to_look[0].distance <= look_distance:
                m, read = to_look
        w, h = frame
        bx1, by1, bx2, by2 = read.box
        entry = {
            "text": read.text,
            "score": round(read.score, 3),
            "box": [
                round((px[0] + bx1) / w, 4),
                round((px[1] + by1) / h, 4),
                round((px[0] + bx2) / w, 4),
                round((px[1] + by2) / h, 4),
            ],
            "car": None,
            "distance": round(m.distance, 2) if m.key else None,
            "age": 0,
            "announced": before.get("announced"),
        }
        by_look = look is not None and m.key == look["key"] and m.distance <= look_distance
        if m.key is not None and (m.distance <= PLATES["match_distance"] or by_look):
            sure = m.distance == 0 or by_look
            agreed = before.get("candidate") == m.key or (before.get("state") == "match" and before.get("car") == m.key)
            if sure or agreed:
                entry.update({"state": "match", "car": m.key, "sure": sure})
                if by_look and m.distance > 0:
                    entry["look"] = True
                det["label"] = m.key
            else:
                entry.update({"state": "pending", "candidate": m.key})
                if before.get("state") == "match" and before.get("car"):
                    det["label"] = before["car"]  # still the car it was while the new read is checked
        else:
            count = before.get("count", 0) + 1 if before.get("text") == read.text else 1
            entry.update({"state": "unknown" if count >= PLATES["unknown_after"] else "reading", "count": count})
            if before.get("state") == "match" and before.get("car") and count < PLATES["unknown_after"]:
                entry.update({"state": "match", "car": before["car"], "kept": True})
                det["label"] = before["car"]  # one other read does not make it another car
            elif look is not None and det.get("label") == look["key"] and count >= PLATES["unknown_after"]:
                # A plate read twice that is not the car's: another car that looks the same. (Read
                # once, the look keeps naming it.)
                det.pop("label", None)
        det["plate"] = entry

    async def learn_from_plates(self, cfg: SensorConfig, image: Image.Image, found: list[dict]) -> None:
        """Learn a car's look from its plate (``objects["plates"]["learn"]``): a vehicle box whose plate
        was read exactly as a car's in this check, surely (PLATES["learn_score"]), that the look does
        not know as that car, is taught as that car on this sensor (origin "plate", shown and
        forgotten like any taught box). At most once per PLATES["learn_every_s"] per car and sensor,
        up to PLATES["learn_max"] such boxes, within the sensor's TEACH["max_examples"]."""
        if not cfg.objects["plates"]["learn"]:
            return
        live = self.live_state(cfg.id)
        now = time.time()
        learned = False
        for d in found:
            plate = d.get("plate") or {}
            car = plate.get("car")
            if (
                not car
                or plate.get("state") != "match"
                or not plate.get("sure")
                or plate.get("kept")
                or plate.get("age", 0)  # carried over from an earlier check, not read now
                or plate.get("distance")  # not read exactly
                or plate.get("score", 0.0) < PLATES["learn_score"]
                or d.get("filtered")
                or (d.get("match") or {}).get("label") == car  # its look knows it already
                or _overlaps_vehicle(d, found)  # the plate may be the car's behind or in front
                or now - live.plate_learned.get(car, 0.0) < PLATES["learn_every_s"]
            ):
                continue
            live.plate_learned[car] = now
            count, total, last = await asyncio.to_thread(self._plate_learning, cfg.id, car)
            if count >= PLATES["learn_max"] or total >= TEACH["max_examples"]:
                continue
            if last is not None and now - last < PLATES["learn_every_s"]:
                live.plate_learned[car] = last
                continue
            await asyncio.to_thread(
                self.add_object_example, cfg.id, image, d["box"], car, d["key"], d["score"], "plate"
            )
            learned = True
            log.info("Sensor %s: learned the look of %s from its plate %s", cfg.slug, car, plate.get("text"))
        if learned:
            self.taught_changed(cfg.id)

    def _plate_learning(self, sensor_id: int, car: str) -> tuple[int, int, float | None]:
        """Boxes of ``car`` learned from its plate on a sensor, its taught boxes in all, and when the
        last one was learned (a restart does not learn again at once)."""
        with self.db.session() as s:
            count, last = s.execute(
                select(func.count(), func.max(Sample.created_at)).where(
                    Sample.sensor_id == sensor_id, Sample.object_label == car, Sample.origin == "plate"
                )
            ).one()
            total = s.scalar(
                select(func.count()).where(Sample.sensor_id == sensor_id, Sample.object_label.is_not(None))
            )
        when = None
        if last is not None:
            when = (last if last.tzinfo else last.replace(tzinfo=UTC)).timestamp()  # stored naive, in UTC
        return int(count or 0), int(total or 0), when

    async def announce_plates(self, cfg: SensorConfig, found: list[dict]) -> list[tuple[str, float]]:
        """An event in Home Assistant when a car of the household comes, and when an unknown plate
        is read (when the sensor reports those), once per vehicle while it stays, and not again for
        the same plate within PLATES["event_cooldown_s"]. Returns what to keep in the history: the
        car (or the vehicle's class) and the read's confidence, per event."""
        live = self.live_state(cfg.id)
        names = {c["key"]: c["name"] for c in cfg.custom_labels}
        now = time.time()
        events: list[tuple[str, float]] = []
        for d in found:
            plate = d.get("plate")
            if not plate:
                continue
            if plate.get("state") == "match" and plate.get("car"):
                event, what = "known_car", plate["car"]
            elif plate.get("state") == "unknown" and cfg.objects["plates"]["unknown"]:
                event, what = "unknown_plate", plate["text"]
            else:
                continue
            if plate.get("announced") == what:
                continue
            plate["announced"] = what
            if now - live.plate_events.get(what, 0.0) < PLATES["event_cooldown_s"]:
                continue
            live.plate_events[what] = now
            payload = {
                "event_type": event,
                "plate": plate["text"],
                "car": plate.get("car"),
                "car_name": names.get(plate.get("car") or ""),
                "confidence": plate["score"],
            }
            await self._send(cfg, topics(cfg.slug)["plate"], payload)
            plate["event"] = event
            events.append((plate.get("car") or d["key"], plate["score"]))
        return events
