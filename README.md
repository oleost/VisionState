# VisionState

> 🚧 **Work in progress** — not ready for use yet. Follow the repository for the first release.

Teach a local AI to recognise **states** in your camera images — a garage door that is
`open`, `closed` or `partial`, a gate, a light, a parking spot — and use the result as a
regular Home Assistant sensor.

- 📸 **Train by clicking** — take a snapshot, pick the correct state. Done.
- 📦 **Bulk upload** — images, ZIP archives or video, labelled in one go.
- 🧠 **Runs locally on any CPU** — Intel, AMD and Raspberry Pi (amd64 / aarch64).
- 🏠 **Native Home Assistant** — HA app with Ingress UI, sensors via MQTT Discovery.
- 🎥 **Any camera** — every `camera.*` entity (Frigate, ESP32-CAM, Reolink, …) or RTSP/HTTP.
- 🔁 **Import / export** — move sensors, datasets and models between installations.

## Status

Currently in the design phase. See [`docs/SCOPE.md`](docs/SCOPE.md) for the agreed scope
and roadmap.

## Licence

[Apache-2.0](LICENSE)
