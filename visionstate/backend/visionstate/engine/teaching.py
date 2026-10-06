"""Teaching object sensors: comparing detections with the taught boxes (see teach.py)."""

from __future__ import annotations

import asyncio
import logging

import numpy as np
from PIL import Image
from sqlalchemy import select

from .. import imaging, teach
from ..db import Embedding, Sample
from ..settings import TEACH
from .state import SensorConfig

log = logging.getLogger(__name__)


class TeachingMixin:
    async def taught_index(self, cfg: SensorConfig) -> teach.TaughtIndex | None:
        """What the sensor was taught, ready to compare with; None when there is nothing to compare."""
        if not cfg.objects["use_taught"] or self.embedder is None:
            return None
        index = self.taught.get(cfg.id)
        generation = self._taught_generation.get(cfg.id, 0)
        # Rebuilt when the backbone, the own labels in use or the taught boxes changed. A check
        # may have loaded its settings before an own label was added, so the stale index it
        # builds is replaced on a later check instead of being kept.
        if (
            index is None
            or index.backbone != self.embedder.spec.id
            or index.parents != cfg.parents
            or index.generation != generation
        ):
            index = await asyncio.to_thread(self._build_taught, cfg, generation)
            self.taught[cfg.id] = index
        return index if index.ids else None

    def _build_taught(self, cfg: SensorConfig, generation: int = 0) -> teach.TaughtIndex:
        parents = cfg.parents
        with self.db.session() as s:
            examples = [
                x
                for x in s.scalars(
                    select(Sample)
                    .where(Sample.sensor_id == cfg.id, Sample.object_label.is_not(None))
                    .order_by(Sample.id)
                )
                if teach.usable(x.object_label, parents)
            ]
        vectors = self._taught_vectors(cfg, examples)
        return teach.build(
            self.embedder.spec.id,
            [x.id for x in examples],
            [x.object_label for x in examples],
            [x.detected for x in examples],
            vectors,
            parents,
            generation,
        )

    def _taught_vectors(self, cfg: SensorConfig, examples: list[Sample]) -> np.ndarray:
        """Embeddings of taught boxes, one image at a time (see teach.embed), cached in the DB."""
        embedder = self.embedder
        if embedder is None or not examples:
            return np.zeros((0, 0), dtype=np.float32)
        backbone_id = embedder.spec.id
        with self.db.session() as s:
            rows = s.scalars(
                select(Embedding).where(
                    Embedding.sample_id.in_([x.id for x in examples]),
                    Embedding.backbone == backbone_id,
                    Embedding.roi_key == teach.EMBEDDING_KEY,
                )
            ).all()
            cached = {r.sample_id: np.frombuffer(r.vector, dtype=np.float32) for r in rows}
        for x in examples:
            if x.id in cached:
                continue
            image = Image.open(self.storage.sample_path(cfg.id, x.filename)).convert("RGB")
            vector = teach.embed(embedder, [image])[0]
            with self.db.session() as s:
                s.merge(
                    Embedding(
                        sample_id=x.id, backbone=backbone_id, roi_key=teach.EMBEDDING_KEY, vector=vector.tobytes()
                    )
                )
            cached[x.id] = vector
        return np.stack([cached[x.id] for x in examples])

    async def apply_taught(
        self, cfg: SensorConfig, image: Image.Image, index: teach.TaughtIndex, found: list[dict], candidates: list[dict]
    ) -> list[dict]:
        checked, rescue = teach.plan(found, candidates, index)
        crops = [teach.crop(image, found[i]["box"]) for i in checked] + [teach.crop(image, d["box"]) for d in rescue]
        embedder = self.embedder
        if not crops or embedder is None:
            return found
        async with self._sem:
            vectors = await asyncio.to_thread(teach.embed, embedder, crops)
        n = len(checked)
        return teach.apply(found, checked, vectors[:n], rescue, vectors[n:], index, cfg.objects["classes"], cfg.parents)

    def taught_changed(self, sensor_id: int) -> None:
        """Taught boxes were added or removed: compare with the new set from the next check on."""
        self._taught_generation[sensor_id] = self._taught_generation.get(sensor_id, 0) + 1
        self.taught.pop(sensor_id, None)
        self.wake(sensor_id, force=True)

    def add_object_example(
        self,
        sensor_id: int,
        image: Image.Image,
        box: list[float],
        label: str,
        detected: str | None,
        score: float | None,
        origin: str,
        cropped: bool = False,
    ) -> int:
        """Store a taught box (its crop) as a training image of an object sensor.

        ``image`` is the whole frame, or with ``cropped`` already the crop (an imported sensor).
        """
        crop = image if cropped else teach.crop(image, box)
        filename = self.storage.save_sample(sensor_id, crop)
        with self.db.session() as s:
            sample = Sample(
                sensor_id=sensor_id,
                filename=filename,
                origin=origin,
                use_roi=False,
                is_night=imaging.is_night(crop),
                width=crop.width,
                height=crop.height,
                object_label=label,
                detected=detected,
                box=[round(float(v), 4) for v in box],
                score=round(float(score), 4) if score is not None else None,
            )
            s.add(sample)
            s.flush()
            return sample.id

    async def seen_faintly(self, cfg: SensorConfig, image: Image.Image, box: list[float]) -> bool:
        """Whether the detector sees anything at all where the user drew a box it missed."""
        found = await self.detect_objects(image, cfg.roi, cfg.objects, TEACH["seen_floor"], all_classes=True)
        return teach.seen_faintly(found, box)
