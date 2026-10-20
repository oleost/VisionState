"""Training the state sensors' classifier heads, and their training images."""

from __future__ import annotations

import asyncio
import logging

import numpy as np
from PIL import Image
from sqlalchemy import delete, select

from .. import classifier, imaging
from ..db import Embedding, ModelInfo, Sample, SampleLabel, Sensor, utcnow
from ..settings import KIND_STATES, RUNTIME
from .base import RuntimeBase
from .state import SensorConfig

log = logging.getLogger(__name__)
EMBED_BATCH = 16


class TrainingMixin(RuntimeBase):
    def heads_load(self, sensor_id: int) -> bool:
        """Loads a sensor's head. Returns True when it must be retrained (unreadable, outdated or
        from before HEAD_FORMAT); False also for a sensor that has none (untrained)."""
        path = classifier.head_path(self.settings.heads_dir, sensor_id)
        head = classifier.load(path)
        if head is not None:
            self.heads[sensor_id] = head  # keep using it until the retrain finishes
        backbone = self.embedder.spec.id if self.embedder else None
        if not path.exists():
            return classifier.legacy_path(self.settings.heads_dir, sensor_id).exists()
        return head is None or not classifier.is_current(head, backbone)

    def schedule_retrain(self, sensor_id: int, delay: float | None = None) -> None:
        """Retrain soon; repeated calls within the delay are coalesced into one run."""
        if self._on_loop(self.schedule_retrain, sensor_id, delay):
            self.training.add(sensor_id)  # show "training" right away
            return
        loop = asyncio.get_running_loop()
        handle = self._retrain_handles.pop(sensor_id, None)
        if handle:
            handle.cancel()
        wait = RUNTIME["retrain_delay_s"] if delay is None else delay
        self.training.add(sensor_id)
        self._retrain_handles[sensor_id] = loop.call_later(wait, lambda: self._spawn(self.retrain(sensor_id)))

    async def retrain(self, sensor_id: int) -> None:
        self._retrain_handles.pop(sensor_id, None)
        self.training.add(sensor_id)
        try:
            async with self._sem:
                await asyncio.to_thread(self._retrain_sync, sensor_id)
        except Exception:  # noqa: BLE001
            log.exception("Training sensor %s failed", sensor_id)
        finally:
            if sensor_id not in self._retrain_handles:
                self.training.discard(sensor_id)
        self.wake(sensor_id, force=True)

    def _labelled_samples(self, sensor_id: int) -> tuple[SensorConfig | None, list[Sample], list[str]]:
        with self.db.session() as s:
            row = s.get(Sensor, sensor_id)
            if row is None:
                return None, [], []
            cfg = SensorConfig.from_row(row)
            key_by_state = {st["id"]: st["key"] for st in cfg.states}
            samples, labels = [], []
            for sample in s.scalars(select(Sample).where(Sample.sensor_id == sensor_id).order_by(Sample.id)):
                keys = [key_by_state[lab.state_id] for lab in sample.labels if lab.state_id in key_by_state]
                if keys:
                    samples.append(sample)
                    labels.append(keys[0])
            return cfg, samples, labels

    def _retrain_sync(self, sensor_id: int) -> None:
        if self.embedder is None:
            return
        cfg, samples, labels = self._labelled_samples(sensor_id)
        if cfg is None or cfg.kind != KIND_STATES:
            return
        kept, vectors = self.vectors_for(cfg, samples)
        if len(kept) < len(samples):  # images gone: trained on the others
            label_of = {x.id: label for x, label in zip(samples, labels, strict=True)}
            samples, labels = kept, [label_of[x.id] for x in kept]
        with self.db.session() as s:
            info = s.get(ModelInfo, sensor_id)
            version = (info.version if info else 0) + 1
        result = classifier.train(vectors, labels, self.embedder.spec.id, version, cfg.state_keys)
        # Every sample guessed wrong, also those the user said are right: each is counted in a red
        # cell of the confusion matrix, which lists them (api/sensors.py current_suspects).
        suspects = [
            {"sample_id": samples[item["index"]].id, **{k: v for k, v in item.items() if k != "index"}}
            for item in result.suspects
        ]
        head_path = classifier.head_path(self.settings.heads_dir, sensor_id)
        if result.head is None:
            self.heads.pop(sensor_id, None)
            head_path.unlink(missing_ok=True)
        else:
            classifier.save(result.head, head_path)
            self.heads[sensor_id] = result.head
        with self.db.session() as s:
            info = s.get(ModelInfo, sensor_id) or ModelInfo(sensor_id=sensor_id)
            info.backbone = self.embedder.spec.id
            info.version = version
            info.trained_at = utcnow()
            info.n_samples = result.n_samples
            info.accuracy = result.accuracy
            info.confusion = result.confusion
            info.suspects = suspects
            info.train_seconds = result.seconds
            s.merge(info)
        log.info(
            "Sensor %s trained on %d samples in %.2fs (accuracy %s)",
            cfg.slug,
            result.n_samples,
            result.seconds,
            f"{result.accuracy:.1%}" if result.accuracy is not None else "n/a",
        )

    def vectors_for(self, cfg: SensorConfig, samples: list[Sample]) -> tuple[list[Sample], np.ndarray]:
        """Embeddings for samples, computing and caching the missing ones.

        Returns the samples that have one: a sample whose image is gone or unreadable (a backup
        restored without the images) is left out instead of failing the training.
        """
        embedder = self.embedder
        if embedder is None or not samples:
            return [], np.zeros((0, 0), dtype=np.float32)
        backbone_id = embedder.spec.id
        keys = {s.id: imaging.roi_key(cfg.roi if s.use_roi else None) for s in samples}
        with self.db.session() as s:
            rows = s.scalars(
                select(Embedding).where(Embedding.sample_id.in_(list(keys)), Embedding.backbone == backbone_id)
            ).all()
            cached = {
                r.sample_id: np.frombuffer(r.vector, dtype=np.float32) for r in rows if r.roi_key == keys[r.sample_id]
            }
        missing = [sample for sample in samples if sample.id not in cached]
        gone = []
        for start in range(0, len(missing), EMBED_BATCH):
            batch, images = [], []
            for sample in missing[start : start + EMBED_BATCH]:
                try:
                    image = imaging.load(self.storage.sample_path(cfg.id, sample.filename))
                except OSError:  # missing, or not a whole image
                    gone.append(sample.id)
                    continue
                batch.append(sample)
                images.append(imaging.crop(image, cfg.roi) if sample.use_roi else image)
            if not images:
                continue
            vectors = embedder.embed(images)
            with self.db.session() as s:
                for sample, vector in zip(batch, vectors, strict=False):
                    s.execute(
                        delete(Embedding).where(Embedding.sample_id == sample.id, Embedding.backbone == backbone_id)
                    )
                    s.add(
                        Embedding(
                            sample_id=sample.id, backbone=backbone_id, roi_key=keys[sample.id], vector=vector.tobytes()
                        )
                    )
                    cached[sample.id] = vector
        if gone:
            log.warning(
                "Sensor %s: %d training images are missing or unreadable and left out: %s", cfg.slug, len(gone), gone
            )
            samples = [sample for sample in samples if sample.id in cached]
        if not samples:
            return [], np.zeros((0, 0), dtype=np.float32)
        return samples, np.stack([cached[sample.id] for sample in samples])

    def suggest(self, cfg: SensorConfig, samples: list[Sample]) -> dict[int, tuple[str, float]]:
        """Predicted state for (unlabelled) samples, used as upload suggestions."""
        head = self.heads.get(cfg.id)
        if not samples or head is None or self.embedder is None or head.backbone != self.embedder.spec.id:
            return {}
        samples, vectors = self.vectors_for(cfg, samples)
        if not samples:
            return {}
        result = {}
        for sample, probs in zip(samples, head.predict(vectors, cfg.state_keys), strict=False):
            key, p = max(probs.items(), key=lambda kv: kv[1])
            result[sample.id] = (key, p)
        return result

    def add_sample(
        self, sensor_id: int, image: Image.Image, origin: str, state_id: int | None, use_roi: bool = True
    ) -> int:
        """Store a training image of a state sensor, labelled when ``state_id`` is given; returns its id."""
        filename = self.storage.save_sample(sensor_id, image)
        with self.db.session() as s:
            sample = Sample(
                sensor_id=sensor_id,
                filename=filename,
                origin=origin,
                use_roi=use_roi,
                is_night=imaging.is_night(image),
                width=image.width,
                height=image.height,
            )
            if state_id is not None:
                sample.labels.append(SampleLabel(state_id=state_id))
            s.add(sample)
            s.flush()
            return sample.id
