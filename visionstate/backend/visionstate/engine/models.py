"""The AI models: the backbone (state sensors), the detector (object sensors) and the reader (reading sensors)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path

from PIL import Image
from sqlalchemy import select

from .. import backbones, detectors, imaging, readers
from ..db import Sensor
from ..dials import read_needles
from ..readers import uses_wheels, wheel_reader_of
from ..redact import redact
from ..settings import DETECTION, KIND_OBJECTS, KIND_READING, KIND_STATES, merge_reading
from .base import RuntimeBase
from .logic import filter_detections
from .training import TrainingMixin

log = logging.getLogger(__name__)


class ModelsMixin(TrainingMixin, RuntimeBase):
    def model_path(self, spec: backbones.ModelFile) -> Path | None:
        return backbones.locate(spec, [self.settings.bundled_models_dir, self.settings.models_dir])

    def downloaded(self, spec: backbones.ModelFile) -> bool:
        """Whether a model was downloaded (and can be deleted again), not included in the app."""
        return (self.settings.models_dir / spec.filename).exists()

    async def load_embedder(self, backbone_id: str) -> None:
        spec = backbones.BACKBONES.get(backbone_id) or backbones.BACKBONES[backbones.DEFAULT_BACKBONE]
        path = self.model_path(spec)
        if path is None:
            path = await asyncio.to_thread(backbones.download, spec, self.settings.models_dir)
        self.embedder = await asyncio.to_thread(backbones.Embedder, spec, path)
        self.embedder_error = ""
        log.info("Backbone %s loaded", spec.id)

    async def set_backbone(self, backbone_id: str) -> None:
        await self.load_embedder(backbone_id)
        await asyncio.to_thread(backbones.release_memory)
        await asyncio.to_thread(self.db.set_setting, "backbone", backbone_id)
        for sensor_id in await asyncio.to_thread(self._sensor_ids, KIND_STATES):
            self.schedule_retrain(sensor_id, delay=0)

    def _pin_setting(self, key: str, default: str) -> None:
        if self.db.get_setting(key) is None:
            self.db.set_setting(key, default)

    async def load_detector(self, detector_id: str) -> detectors.Detector:
        spec = detectors.DETECTORS.get(detector_id) or detectors.DETECTORS[detectors.DEFAULT_DETECTOR]
        path = self.model_path(spec)
        if path is None:
            path = await asyncio.to_thread(backbones.download, spec, self.settings.models_dir)
        self.detector = detector = await asyncio.to_thread(detectors.Detector, spec, path)
        self.detector_error = ""
        log.info("Detector %s loaded", spec.id)
        return detector

    async def ensure_detector(self) -> detectors.Detector:
        """The detector, loaded on first use so installations without object sensors never pay for it."""
        async with self._detector_lock:
            if self.detector is not None:
                return self.detector
            detector_id = await asyncio.to_thread(self.db.get_text, "detector", detectors.DEFAULT_DETECTOR)
            try:
                return await self.load_detector(detector_id)
            except Exception as err:
                self.detector_error = redact(str(err))
                log.exception("Could not load detector %s", detector_id)
                raise

    async def set_detector(self, detector_id: str) -> None:
        object_sensors = await asyncio.to_thread(self._sensor_ids, KIND_OBJECTS)
        if self.detector is not None or object_sensors:
            async with self._detector_lock:
                await self.load_detector(detector_id)
            await asyncio.to_thread(backbones.release_memory)
        await asyncio.to_thread(self.db.set_setting, "detector", detector_id)
        for sensor_id in object_sensors:
            self.wake(sensor_id, force=True)

    async def load_reader(self, reader_id: str) -> readers.Reader:
        spec = readers.READERS.get(reader_id) or readers.READERS[readers.DEFAULT_READER]
        path = self.model_path(spec)
        if path is None:
            path = await asyncio.to_thread(backbones.download, spec, self.settings.models_dir)
        self.reader = reader = await asyncio.to_thread(readers.Reader, spec, path)
        self.reader_error = ""
        log.info("Reader %s loaded", spec.id)
        return reader

    async def ensure_reader(self) -> readers.Reader:
        """The number reader, loaded on first use so installations without reading sensors never pay for it."""
        async with self._reader_lock:
            if self.reader is not None:
                return self.reader
            reader_id = await asyncio.to_thread(self.db.get_text, "reader", readers.DEFAULT_READER)
            try:
                return await self.load_reader(reader_id)
            except Exception as err:
                self.reader_error = redact(str(err))
                log.exception("Could not load reader %s", reader_id)
                raise

    async def set_reader(self, reader_id: str) -> None:
        reading_sensors = await asyncio.to_thread(self._sensor_ids, KIND_READING)
        if self.reader is not None or reading_sensors:
            async with self._reader_lock:
                await self.load_reader(reader_id)
            await asyncio.to_thread(backbones.release_memory)
        await asyncio.to_thread(self.db.set_setting, "reader", reader_id)
        for sensor_id in reading_sensors:
            self.wake(sensor_id, force=True)

    async def ensure_wheel_reader(self, reader_id: str) -> readers.WheelReader:
        """A wheel reader for mechanical counters (each counter names its own), loaded on first use."""
        spec = readers.WHEEL_READERS.get(reader_id) or readers.WHEEL_READERS[readers.DEFAULT_WHEEL_READER]
        async with self._wheel_reader_lock:
            if (loaded := self.wheel_readers.get(spec.id)) is not None:
                return loaded
            try:
                path = self.model_path(spec)
                if path is None:
                    path = await asyncio.to_thread(backbones.download, spec, self.settings.models_dir)
                self.wheel_readers[spec.id] = loaded = await asyncio.to_thread(readers.WheelReader, spec, path)
            except Exception as err:
                self.wheel_reader_error = redact(str(err))
                log.exception("Could not load wheel reader %s", spec.id)
                raise
            self.wheel_reader_error = ""
            log.info("Wheel reader %s loaded", spec.id)
            return loaded

    def wheel_readers_in_use(self) -> set[str]:
        """The wheel readers the counters read with."""
        with self.db.session() as s:
            stored = s.scalars(select(Sensor.reading).where(Sensor.kind == KIND_READING))
            return {wheel_reader_of(merge_reading(r)) for r in stored if uses_wheels(merge_reading(r))}

    async def release_wheel_readers(self) -> None:
        """Let go of the wheel readers no counter reads with any more (deleted, or switched)."""
        in_use = await asyncio.to_thread(self.wheel_readers_in_use)
        async with self._wheel_reader_lock:
            for reader_id in [k for k in self.wheel_readers if k not in in_use]:
                del self.wheel_readers[reader_id]
                log.info("Wheel reader %s released", reader_id)

    async def read_number(
        self, image: Image.Image, roi: dict | None, reading: dict
    ) -> tuple[readers.Text, Image.Image]:
        """Read the region of ``image``; returns the text and the image the reader used.

        The text's ``details`` name the reader that read it (``reader``). A straightened region
        (``reading["straighten"]``) is read upright: the readers get it already straightened. A
        counter's pointer dials (``reading["dials"]``) are read from the whole frame first: they lie
        around the region, in the plane it is read in."""
        straightened = bool(reading.get("straighten")) and imaging.region_corners(roi) is not None
        if uses_wheels(reading):
            wheels = await self.ensure_wheel_reader(wheel_reader_of(reading))
            needles = await asyncio.to_thread(read_needles, image, roi, reading, straightened)
            if straightened:
                image = await asyncio.to_thread(imaging.region_image, image, roi, True)
            box = None if straightened else imaging.pixel_box(image, imaging.region_box(roi))
            async with self._sem:
                text, used = await asyncio.to_thread(
                    wheels.read_counter,
                    image,
                    int(reading["digits"]),
                    box,
                    needles,
                )
            return replace(text, details={"reader": wheels.spec.id, **(text.details or {})}), used
        region = await asyncio.to_thread(imaging.region_image, image, roi, straightened)
        reader = await self.ensure_reader()
        async with self._sem:
            text, used = await asyncio.to_thread(
                reader.read_display, region, reading["display"], int(reading["digits"])
            )
        return replace(text, details={"reader": reader.spec.id, **(text.details or {})}), used

    async def detect_objects(
        self, image: Image.Image, roi: dict | None, objects: dict, threshold: float, all_classes: bool = False
    ) -> list[dict]:
        """Objects in the region of ``image`` (boxes normalised to the whole frame).

        The detector sees a margin around the region, so an object at the edge is seen whole and
        its box (and bottom centre) is not cut off by the crop.
        """
        detector = await self.ensure_detector()
        analysed = imaging.region_box(roi, DETECTION["context_margin"])
        region = imaging.crop_box(image, analysed)
        async with self._sem:
            found = await asyncio.to_thread(detector.detect, region, threshold)
        return filter_detections(found, analysed, roi, objects, all_classes)
