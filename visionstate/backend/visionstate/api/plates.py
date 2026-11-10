"""Licence plates: the household's cars (shared by every object sensor that reads plates) and the
plate model (docs/PLATE_RECOGNITION_PLAN.md)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import plates
from ..db import Sample, Sensor, Setting
from ..settings import PLATES, VEHICLE_CLASSES
from .common import API_PREFIX, runtime, slugify
from .faces import taken_keys
from .teach import delete_examples

router = APIRouter(prefix=API_PREFIX, tags=["plates"])


class CarIn(BaseModel):
    """A car of the household: its name (the entity's name), its plates (none: known by its look
    only; several separated by commas: a car with another plate in winter, a trailer) and what
    kind of vehicle it is. ``look``: another car whose look it shares (two cars of the same model
    and colour): that car is then the look of both, and the plates tell them apart."""

    name: str = Field(min_length=1, max_length=64)
    plate: str = Field(default="", max_length=64)
    parent: str = "car"
    look: str = ""

    @field_validator("plate")
    @classmethod
    def _readable(cls, value: str) -> str:
        parts = [p.strip() for p in value.split(",") if p.strip()]
        if any(len(plates.normalise(p)) < PLATES["min_chars"] for p in parts):
            raise ValueError(f"A plate has at least {PLATES['min_chars']} letters or digits")
        return ", ".join(parts)

    @field_validator("parent")
    @classmethod
    def _vehicle(cls, value: str) -> str:
        if value not in VEHICLE_CLASSES:
            raise ValueError(f"A car is one of {', '.join(VEHICLE_CLASSES)}")
        return value


def _cars(s: Session) -> list[dict]:
    row = s.get(Setting, "cars")
    return [dict(c) for c in row.value] if row is not None and isinstance(row.value, list) else []


def _store(s: Session, cars: list[dict]) -> None:
    row = s.get(Setting, "cars")
    if row is None:
        s.add(Setting(key="cars", value=cars))
    else:
        row.value = cars


def _check(cars: list[dict], body: CarIn, keep: str | None = None) -> None:
    if body.look:
        look = next((c for c in cars if c["key"] == body.look), None)
        if look is None or look["key"] == keep:
            raise HTTPException(400, f"No other car called {body.look!r}")
        if look.get("look"):
            raise HTTPException(400, f"{look['name']} shares the look of another car itself")
        if keep is not None and any(c.get("look") == keep for c in cars):
            raise HTTPException(400, "Other cars share this car's look")
    for car in cars:
        if car["key"] == keep:
            continue
        if car["name"].lower() == body.name.strip().lower():
            raise HTTPException(400, f"There is already a car called {car['name']}")
        theirs = {plates.normalise(p) for p in car["plate"].split(",")} - {""}
        if theirs & {plates.normalise(p) for p in body.plate.split(",")}:
            raise HTTPException(400, f"{car['name']} has that plate already")


@router.get("/cars")
def list_cars(request: Request) -> list[dict]:
    """The household's cars, each with the sensors its look was taught on ({sensor, name, boxes})."""
    rt = runtime(request)
    with rt.db.session() as s:
        cars = _cars(s)
        rows = s.execute(
            select(Sample.object_label, Sensor.id, Sensor.name, func.count())
            .join(Sensor, Sample.sensor_id == Sensor.id)
            .where(Sample.object_label.in_([c["key"] for c in cars]))
            .group_by(Sample.object_label, Sensor.id, Sensor.name)
            .order_by(Sensor.name)
        ).all()
        for car in cars:
            car["taught"] = [
                {"sensor": sid, "name": name, "boxes": n} for key, sid, name, n in rows if key == car["key"]
            ]
        return cars


def new_car(s: Session, body: CarIn) -> dict:
    """Store a new car of the household; its key never clashes with an object, a label, a person
    or another car."""
    cars = _cars(s)
    if len(cars) >= PLATES["max_cars"]:
        raise HTTPException(400, f"VisionState can know at most {PLATES['max_cars']} cars")
    _check(cars, body)
    taken = taken_keys(s)
    base = slugify(body.name, "car")
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    car = {"key": key, "name": body.name.strip(), "plate": body.plate, "parent": body.parent}
    cars.append(car)
    _share_look(s, cars, car, body.look)
    _store(s, cars)
    return car


def _share_look(s: Session, cars: list[dict], car: dict, look: str) -> None:
    """Let ``car`` share the look of the car ``look`` ("": its own look). That car becomes the look
    of all of them: its plates move to a car of their own under it (named after it and its plate).
    Each keeps its taught boxes; recognising, they count as that car's (``SensorConfig.car_looks``),
    so choosing the car's own kind again undoes it."""
    car.pop("look", None)
    if not look:
        return
    shared = next(c for c in cars if c["key"] == look)
    car["look"], car["parent"] = look, shared.get("parent", "car")
    if shared.get("plate"):
        first = shared["plate"].split(",")[0].strip()
        taken = taken_keys(s) | {c["key"] for c in cars}
        names = {c["name"].lower() for c in cars}
        name, n = f"{shared['name']} {first}"[:64], 2
        while name.lower() in names:
            name, n = f"{shared['name']} {first} ({n})"[:64], n + 1
        base = slugify(name, "car")
        key, n = base, 2
        while key in taken:
            key, n = f"{base}_{n}", n + 1
        cars.append({"key": key, "name": name, "plate": shared["plate"], "parent": car["parent"], "look": look})
        shared["plate"] = ""


@router.post("/cars")
async def add_car(body: CarIn, request: Request) -> dict:
    """A new car: an own label of every sensor that reads plates (and looks for its kind), and of
    those its look is taught on."""
    rt = runtime(request)

    def add() -> dict:
        with rt.db.session() as s:
            car = new_car(s, body)
            s.commit()
            return car

    car = await asyncio.to_thread(add)
    await rt.cars_changed()
    return car


@router.put("/cars/{key}")
async def change_car(key: str, body: CarIn, request: Request) -> dict:
    """Rename a car or change its plate or kind; its key (and entities) stay."""
    rt = runtime(request)

    def change() -> dict:
        with rt.db.session() as s:
            cars = _cars(s)
            car = next((c for c in cars if c["key"] == key), None)
            if car is None:
                raise HTTPException(404, f"No car called {key!r}")
            _check(cars, body, keep=key)
            car.update({"name": body.name.strip(), "plate": body.plate, "parent": body.parent})
            _share_look(s, cars, car, body.look)
            for other in cars:
                if other.get("look") == key:
                    other["parent"] = car["parent"]  # the cars that share its look are its kind
            _store(s, cars)
            s.commit()
            return car

    car = await asyncio.to_thread(change)
    await rt.cars_changed()
    return car


@router.delete("/cars/{key}", status_code=204)
async def forget_car(key: str, request: Request) -> Response:
    """Forget a car: its entities go from every sensor, and the boxes its look was taught with."""
    rt = runtime(request)

    def forget() -> list[int]:
        with rt.db.session() as s:
            cars = _cars(s)
            if not any(c["key"] == key for c in cars):
                raise HTTPException(404, f"No car called {key!r}")
            kept = [c for c in cars if c["key"] != key]
            for car in kept:
                if car.get("look") == key:
                    car.pop("look")  # its look is forgotten with it: known by its plate alone
            _store(s, kept)
            taught = [
                sensor_id
                for sensor_id in s.scalars(select(Sensor.id))
                if delete_examples(rt, s, sensor_id, [Sample.object_label == key])
            ]
            s.commit()
            return taught

    for sensor_id in await asyncio.to_thread(forget):
        rt.taught_changed(sensor_id)
    await rt.cars_changed()
    return Response(status_code=204)


@router.post("/plates/retry")
async def plates_retry(request: Request) -> dict:
    """Try to download and load the plate model again after it failed."""
    rt = runtime(request)
    rt.retry_plates()
    return rt.plate_status()
