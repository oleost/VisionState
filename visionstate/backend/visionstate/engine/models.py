"""The AI models: the backbone (state sensors), the detector (object sensors) and the reader (reading sensors)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PIL import Image

from .. import backbones, detectors, imaging, readers
from ..redact import redact
from ..settings import DETECTION, KIND_OBJECTS, KIND_READING, KIND_STATES
from .base import RuntimeBase
from .logic import filter_detections
from .training import TrainingMixin

log = logging.getLogger(__name__)


class ModelsMixin(TrainingMixin, RuntimeBase):
    def model_path(self, spec: backbones.BackboneSpec) -> Path | None:
        return backbones.locate(spec, [self.settings.bundled_models_dir, self.settings.models_dir])

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
        self.db.set_setting("backbone", backbone_id)
        for sensor_id in await asyncio.to_thread(self._sensor_ids, KIND_STATES):
            self.schedule_retrain(sensor_id, delay=0)

    def _pin_setting(self, key: str, default: str) -> None:
        if self.db.get_setting(key) is None:
            self.db.set_setting(key, default)

    async def load_detector(self, detector_id: str) -> None:
        spec = detectors.DETECTORS.get(detector_id) or detectors.DETECTORS[detectors.DEFAULT_DETECTOR]
        path = self.model_path(spec)
        if path is None:
            path = await asyncio.to_thread(backbones.download, spec, self.settings.models_dir)
        self.detector = await asyncio.to_thread(detectors.Detector, spec, path)
        self.detector_error = ""
        log.info("Detector %s loaded", spec.id)

    async def ensure_detector(self) -> detectors.Detector:
        """The detector, loaded on first use so installations without object sensors never pay for it."""
        async with self._detector_lock:
            if self.detector is None:
                detector_id = await asyncio.to_thread(self.db.get_setting, "detector", detectors.DEFAULT_DETECTOR)
                try:
                    await self.load_detector(detector_id)
                except Exception as err:
                    self.detector_error = redact(str(err))
                    log.exception("Could not load detector %s", detector_id)
                    raise
            return self.detector

    async def set_detector(self, detector_id: str) -> None:
        object_sensors = await asyncio.to_thread(self._sensor_ids, KIND_OBJECTS)
        if self.detector is not None or object_sensors:
            async with self._detector_lock:
                await self.load_detector(detector_id)
        await asyncio.to_thread(self.db.set_setting, "detector", detector_id)
        for sensor_id in object_sensors:
            self.wake(sensor_id, force=True)

    async def load_reader(self, reader_id: str) -> None:
        spec = readers.READERS.get(reader_id) or readers.READERS[readers.DEFAULT_READER]
        path = self.model_path(spec)
        if path is None:
            path = await asyncio.to_thread(backbones.download, spec, self.settings.models_dir)
        self.reader = await asyncio.to_thread(readers.Reader, spec, path)
        self.reader_error = ""
        log.info("Reader %s loaded", spec.id)

    async def ensure_reader(self) -> readers.Reader:
        """The number reader, loaded on first use so installations without reading sensors never pay for it."""
        async with self._reader_lock:
            if self.reader is None:
                reader_id = await asyncio.to_thread(self.db.get_setting, "reader", readers.DEFAULT_READER)
                try:
                    await self.load_reader(reader_id)
                except Exception as err:
                    self.reader_error = redact(str(err))
                    log.exception("Could not load reader %s", reader_id)
                    raise
            return self.reader

    async def set_reader(self, reader_id: str) -> None:
        reading_sensors = await asyncio.to_thread(self._sensor_ids, KIND_READING)
        if self.reader is not None or reading_sensors:
            async with self._reader_lock:
                await self.load_reader(reader_id)
        await asyncio.to_thread(self.db.set_setting, "reader", reader_id)
        for sensor_id in reading_sensors:
            self.wake(sensor_id, force=True)

    async def read_number(
        self, image: Image.Image, roi: dict | None, reading: dict
    ) -> tuple[readers.Text, Image.Image]:
        """Read the region of ``image``; returns the text and the image the reader used."""
        reader = await self.ensure_reader()
        region = imaging.crop_box(image, imaging.region_box(roi))
        async with self._sem:
            return await asyncio.to_thread(reader.read_display, region, reading["display"], int(reading["digits"]))

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
