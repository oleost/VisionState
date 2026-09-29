# VisionState

![Labelling a garage door](https://raw.githubusercontent.com/oleost/VisionState/main/docs/promo/label.png)

Teach a local AI to recognise **states** in camera images — a garage door that is `open`,
`closed` or `partial`, a gate, a parking spot, a light — and use the result as a normal
Home Assistant sensor. Everything runs on this machine; no cloud.

## Requirements

- **MQTT**: VisionState publishes its sensors through MQTT discovery. Install the
  **Mosquitto broker** app and the **MQTT** integration if you do not have them yet.
  VisionState finds the broker automatically.
- **A camera** in Home Assistant (any `camera.*` entity: Frigate, ESP32-CAM, Reolink,
  generic camera…), or a direct HTTP snapshot / RTSP URL.
- amd64 (Intel/AMD) or aarch64 (Raspberry Pi 4/5, 64-bit OS).

## Getting started

1. Open **VisionState** from the sidebar.
2. Click **New sensor**:
   1. Give it a name and pick a camera.
   2. Draw a box around the thing to watch (for example the garage door). The AI only looks
      inside the box, which makes it far more accurate.
   3. Name the states, for example *Open*, *Closed*, *Partial*.
   4. Optionally choose when it should check the camera — for example when your motion
      sensor or garage opener changes (see *When it checks* below). You can skip this step.
3. On the **Label** tab, click the matching state button (or press `1`–`9`) while the live
   image shows each state. The model retrains in about a second after every label.
4. Label roughly **20 images per state**, including some at night. The **Quality** tab tells
   you what is missing.

The sensor appears in Home Assistant as a device with these entities:

| Entity | What it is |
|---|---|
| `sensor.visionstate_<name>` | The state (`open`, `closed`, …, or `unknown` when unsure) |
| `sensor.visionstate_<name>_confidence` | How sure the AI is, in % |
| `image.visionstate_<name>_frame` | The image region that was classified |
| `button.visionstate_<name>_classify` | Classify right now (use it in automations) |
| `switch.visionstate_<name>_enabled` | Pause / resume the sensor |

In addition, the **VisionState** device has `sensor.visionstate_review_queue`: the number of
frames waiting for review (with a per-sensor breakdown as attribute).

The state entity also has a `probabilities` attribute with the score of every state and a
`last_trigger` attribute telling what caused the last check.

## Training tips

- **Upload tab**: drop many images, a ZIP file or a **video**. Videos are split into one
  frame every few seconds (near-duplicates are skipped). The current model suggests a label
  for each frame — check them and click **Accept all suggestions**, or select frames and
  press a state key.
- **Review** (top menu): frames the AI was unsure about and frames where the state flipped back
  and forth (optionally also random spot checks). Answering these is the fastest way to improve.
  Tune it under **Settings → Review queue** (all sensors) or on a sensor's **Settings** tab —
  e.g. lower "Send to review when the AI is less sure than" for a sensor that is rarely above
  80 %, or turn review off for it. Empty sensor fields use the global value.
- **History tab**: every state change with its frame. If one was wrong, add it to the dataset
  with the correct state.
- **Quality tab → Possibly mislabelled**: after each training VisionState checks every image
  against a model trained on the *other* images. Images it strongly disagrees with are listed —
  usually a wrong click. Keep the label, change it or delete the image.
- Include different light: day, night (IR), sun, rain, snow.
- Changing the region on the **Settings** tab retrains the model from the stored images.

## When it checks (triggers)

Set up per sensor under **Settings → When to check**:

| Setting | Default | Meaning |
|---|---|---|
| Regular check | every 10 s | The safety net. With triggers set up it can be minutes. |
| Check when these change | none | Any state change of these entities starts a check — a motion sensor, a door contact, the garage opener, a Frigate motion sensor… |
| Detect changes in the image | off | Compares the region every *N* seconds (cheap) and only runs the AI when at least *X* % of it changed. The measured change is shown next to the setting so you can pick a threshold above normal noise. |
| After a trigger | every 2 s for 30 s | Keeps checking faster for a while, so both the moving door and its final state are seen. |

A typical garage setup: regular check every 300 s, the garage motion sensor and the opener as
trigger entities, and change detection on for cameras without a motion sensor.

You can still call `button.visionstate_<name>_classify` from your own automations.

## How it decides

| Setting (per sensor) | Default | Meaning |
|---|---|---|
| Report unknown below | 70 % | Confidence needed to report a state |
| Change after N matching results | 2 | Avoids flicker when someone walks past |

## Backup, export and import

- Settings, the database and trained models live in the app's data folder and are part of
  Home Assistant backups.
- Training images are stored in `/media/visionstate` (the beta app uses `/media/visionstate_beta`).
- **Export** (on a sensor) downloads a ZIP with the sensor's settings (region, states, triggers,
  review overrides) and all its images with labels. Camera passwords are removed from the file.
- **Import** (dashboard or Settings) adds it as a new sensor — also on another installation — and
  trains it automatically. If the camera URL needed a password, enter it again on the sensor's
  Settings tab.

## Beta channel

New versions are released as beta first. To test them, add
`https://github.com/oleost/VisionState#beta` as a repository and install **VisionState (beta)**.
It is a separate app with its own data: move sensors with Export/Import, and run only one of the
two apps at a time (both publish the same entities).

## AI model

The default model (DINOv2 small, 8-bit) is included and runs on any CPU, including a
Raspberry Pi 4. A slightly more accurate full-precision model can be selected under
**Settings**; it is downloaded on first use (89 MB).

## App options

| Option | Description |
|---|---|
| `log_level` | Amount of logging. |
| `history_retention_days` | How long history frames are kept. |
| `discovery_prefix` | MQTT discovery prefix (normally `homeassistant`). |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | Only needed for a broker that Home Assistant does not provide. |

## Support

Report issues at <https://github.com/oleost/VisionState/issues>.
