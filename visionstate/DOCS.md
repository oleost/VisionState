# VisionState

![VisionState with a garage door, a driveway and a power meter sensor](https://raw.githubusercontent.com/oleost/VisionState/main/docs/promo/dashboard.png)

Teach a local AI to recognise **states** in camera images — a garage door that is `open`,
`closed` or `partial`, a gate, a parking spot, a light — and use the result as a normal
Home Assistant sensor. Or let it find **objects** — people, cars, animals and more — or **read a
number** from a display, without any training. Everything runs on this machine; no cloud.

## Requirements

- **MQTT**: VisionState publishes its sensors through MQTT discovery. Install the
  **Mosquitto broker** app and the **MQTT** integration if you do not have them yet.
  VisionState finds the broker automatically.
- **A camera** in Home Assistant (any `camera.*` entity: Frigate, ESP32-CAM, Reolink,
  generic camera…), or a direct HTTP snapshot / RTSP URL.
- amd64 (Intel/AMD) or aarch64 (Raspberry Pi 4/5, 64-bit OS). Any 64-bit Intel/AMD CPU works,
  also in a virtual machine with a generic CPU type (for example Proxmox's `kvm64`).

## Getting started

1. Open **VisionState** from the sidebar.
2. Click **New sensor**:
   1. Give it a name and pick a camera.
   2. Draw a box around the thing to watch (for example the garage door). The AI only looks
      inside the box, which makes it far more accurate. For an object that sits at an angle,
      shape the box: drag a corner to move it, drag a **+** on an edge to add a corner, and
      double-click / double-tap (or press and hold) a corner to remove it. Everything outside
      the shape is ignored. **Reset to rectangle** goes back to a plain box.
   3. Choose what it detects: **States** (name them, for example *Open*, *Closed*, *Partial*),
      **Objects** (see *Object sensors* below) or **Reading** (see *Reading sensors* below).
      This can not be changed later.
   4. Optionally choose when it should check the camera — for example when your motion
      sensor or garage opener changes (see *When it checks* below). You can skip this step.
3. On the **Label** tab, click or tap the matching state button (or press `1`–`9`) while the live
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

## Object sensors

An object sensor finds common objects — people, cars, bicycles, cats, dogs, birds and 70 more —
with a pretrained detector. There is nothing to label: pick the objects in the wizard (popular
ones first, all others under **Show all**) and the wizard tests it on a fresh frame right away.

- **The region** decides what counts: an object counts when the bottom of its box (where a
  person or car stands) is inside it. The detector sees a little more than the region, so an
  object at the edge is still recognised whole.
- Each object you pick becomes two entities:

  | Entity | What it is |
  |---|---|
  | `binary_sensor.visionstate_<name>_<object>` | `on` while the object is there (occupancy) |
  | `sensor.visionstate_<name>_<object>_count` | How many there are |

  plus `image.…_frame` (the region with boxes drawn), `button.…_classify` (detect now) and
  `switch.…_enabled`, like every sensor. The binary sensor's attributes list the confidence
  and the boxes. Removing an object from the list removes its entities.
- **Settings tab → Sensor output**:

  | Setting | Default | Meaning |
  |---|---|---|
  | Count an object when the AI is at least … sure | 60 % | Weaker boxes are ignored |
  | Report it after this many checks in a row | 1 | Raise it if single false detections turn it on |
  | Clear it when not seen for | 30 s | Keeps a person detected while they turn around |
  | Ignore objects smaller than | 0 % | Share of the region; filters far-away or tiny false hits |

- **Live** shows the last checked frame with every box, **History** lists when each object
  appeared and cleared (tap a row for the frame).
- Object sensors use the same triggers as state sensors. **Detect changes in the image** is
  a good fit: the detector only runs when something in the region changes.

## Reading sensors

A reading sensor reads a **number** from a display or counter — the kWh on a power meter, a fuel
price, the minutes left on a washing machine, the wheels of a water or gas meter. Nothing to
label: pick what it looks like and what the number is, and the wizard reads a fresh frame right
away, showing both the value and the image the reader saw. You can also draw or adjust the
region right on that test image; it is read again straight away.

First choose **what it looks like**:

- **Digital display** — LCD or LED digits, or a printed number. Draw the region **tightly around
  the digits** (no labels or units inside it).
- **Mechanical counter** — digit wheels that roll behind a window, as on most water and gas
  meters. Draw the region from the first wheel to the last and set the **number of digits**
  (every wheel inside the region, coloured ones too). The region is split into that many equal
  fields, shown on the image: each field should hold one wheel. A little room above and below
  is fine; a region that cuts the digits is not. Only the middle of each field is read, so the
  dividers between the wheels are never mistaken for digits, and a reading with another number
  of digits than the counter has is rejected.

| Mode | For | In Home Assistant |
|---|---|---|
| **Counter** | Power, water and gas meters — only goes up | `state_class: total_increasing`, works in the Energy dashboard |
| **Value** | Prices and other numbers that go up and down | `measurement` (none for money, as Home Assistant requires) |
| **Time left** | Countdowns like `1:25` on appliances | minutes (`1:25` = 85 min), device class *duration* |

- **Digits after the decimal point** decide where the decimal point is. Dots and commas on the
  display are ignored, because a stray dot is the most common misread. When the test read shows
  a decimal point (for example `1234.5`), VisionState offers to set the matching number.
- **Display** (digital displays): *Auto* works for most displays. Choose *LED* (light digits on
  dark) or *LCD* (dark digits on light) if faint, unlit segments are read as digits — a 3 read
  as 8.
- **Mechanical counters**: the coloured wheels are usually the decimals — a water meter with
  five black and three red wheels has 8 digits and 3 digits after the decimal point. While a
  wheel is turning its digit can be misread; a counter that reads lower than before is
  rejected, and a limit on how much the value may change (Settings → Sensor output) catches a
  misread that is too high. If the last wheel never stands still, leave it out of the region
  and count one digit and one decimal less.
- **Safety net**: a reading is rejected — and the last value kept — when the reader is less sure
  than the minimum (default 70 %), finds no number, a counter reads lower than before, a
  mechanical counter is read with the wrong number of digits, or the value changes more than the
  limit you set. Every rejected reading is listed in the **History** tab with the reason and
  waits in the **Review** queue. A new value is published after 2 equal readings in a row
  (adjustable).
- **Quality** tab: how many readings were rejected today, in the last 7 and 30 days, why, and a
  chart per day. Below it the rejected readings with their frame: tell whether the reader read
  the meter right (*Read correctly*) or not (*Misread*, optionally with the value it showed) —
  here or in the review queue. A misread that was rejected shows the checks work; a right
  reading that was rejected points at a setting, for example a change limit that is too low.
- **Spot checks** (Settings → Sensor output, off by default): a share of the *accepted*
  readings also goes to the review queue, to find misreads that passed every check.
- The entity is `sensor.visionstate_<name>` with the value, plus `…_confidence`, `image.…_frame`,
  `button.…_classify` (read now) and `switch.…_enabled`. Attributes: the text read and why the
  last reading was rejected, if it was.
- Works best on LCD and LED displays and printed signs. **Mechanical counters** are new and
  have so far only been tried on one type of water meter: they read well while the wheels stand
  still and less reliably in the moment a wheel turns. Small pointer dials (the red hands on
  some water meters) are not read.
- Reading sensors are **new** and have hardly been tried on real cameras yet. Feedback helps a
  lot: what the display or meter is, whether it read correctly, and a screenshot of *What the reader
  sees* — in [GitHub Discussions](https://github.com/oleost/VisionState/discussions) or as an
  issue.
- The default check interval is 30 s; a trigger (for example a motion sensor or image change
  detection) makes it read right away.

## Training tips (state sensors)

- **Upload tab**: drop many images, a ZIP file or a **video**. Videos are split into one
  frame every few seconds (near-duplicates are skipped). The current model suggests a label
  for each frame — check them and click **Accept all suggestions**, or select frames and
  press a state key.
- **Review** (top menu): frames the AI was unsure about and frames where the state flipped back
  and forth (optionally also random spot checks). Answering these is the fastest way to improve.
  Tune it under **Settings → Review queue** (all sensors) or on a sensor's **Settings** tab —
  e.g. lower "Send to review when the AI is less sure than" for a sensor that is rarely above
  80 %, or turn review off for it. Empty sensor fields use the global value.
- **History tab**: every state change with its frame (tap it to see the whole frame). If one
  was wrong, add it to the dataset with the correct state.
- **Quality tab → Possibly mislabelled**: after each training VisionState checks every image
  against a model trained on the *other* images. Images it strongly disagrees with are listed —
  usually a wrong click. Keep the label, change it or delete the image.
- Include different light: day, night (IR), sun, rain, snow.
- Changing the region on the **Settings** tab retrains the model from the stored images.

## When it checks (triggers)

Set up per sensor under **Settings → When to check**:

| Setting | Default | Meaning |
|---|---|---|
| Regular check | every 10 s | The safety net. It is counted from the last check, whatever caused it, so with frequent triggers it rarely runs. Switch it **off** to check only when triggered (plus once after start-up). |
| Check when these change | none | Any state change of these entities starts a check — a motion sensor, a door contact, the garage opener, a Frigate motion sensor… For each entity you can enter the one state that should count (*only when it becomes* `on`, `Flow finished` …). |
| Detect changes in the image | off | Compares the region every *N* seconds (cheap) and only runs the AI when at least *X* % of it changed. The measured change is shown next to the setting so you can pick a threshold above normal noise. |
| Switch on a light for each check | none | A light, switch or helper that is turned on before the frame is taken and off again afterwards — for a camera in a dark place, such as a meter cabinet. *Wait before taking the frame* (default 1 s) gives the light and the camera time. A light that is already on is left alone, and it stays on during the checks that follow a trigger. |
| After a trigger | every 2 s for 30 s | Keeps checking faster for a while, so both the moving door and its final state are seen. Set the time to 0 for a single check per trigger. |

A typical garage setup: regular check every 300 s, the garage motion sensor and the opener as
trigger entities, and change detection on for cameras without a motion sensor.

A meter read by a battery or ESP camera: regular check off, the device's status entity as
trigger with *only when it becomes* the state that means a new picture is ready, and the time
after a trigger set to 0.

With a light set, the live view in VisionState shows the frame of the last check instead of
taking new ones (they would be dark, or make the light flash), and change detection compares
frames without the light.

You can still call `button.visionstate_<name>_classify` from your own automations.

## How it decides

| Setting (per sensor) | Default | Meaning |
|---|---|---|
| Report unknown below | 70 % | Confidence needed to report a state |
| Change after N matching results | 2 | Avoids flicker when someone walks past |

## Backup, export and import

- Settings, the database and trained models live in the app's data folder and are part of
  Home Assistant backups.
- Training images and history frames are stored in `/media/visionstate` (the beta app uses
  `/media/visionstate_beta`).
- **Settings → Storage** shows how much space history frames and training images use and how
  much is free. History is kept for **7 days** but at most **2 GB** by default — whichever is
  reached first; the oldest frames are removed first, frames waiting for review last (they are
  kept twice as many days). Set either limit there (`0` GB = no size limit). Training images, and
  readings you verified on a reading sensor's Quality tab or in the review queue, are never
  removed automatically.
- **Export** (on a sensor) downloads a ZIP with the sensor's settings (region, states, objects or
  reading settings, triggers, review overrides) and all its images with labels. Camera passwords are removed from the file.
- **Import** (dashboard or Settings) adds it as a new sensor (named "… (2)" when the name is
  taken) — also on another installation — and
  trains it automatically. If the camera URL needed a password, enter it again on the sensor's
  Settings tab.

## Beta channel

New versions are released as beta first. To test them, add
`https://github.com/oleost/VisionState#beta` as a repository and install **VisionState (beta)**.
It is a separate app with its own data: move sensors with Export/Import, and run only one of the
two apps at a time (both publish the same entities).

## AI models

Two models, chosen under **Settings → AI model** for all sensors of a kind:

- **State sensors:** DINOv2 small, 8-bit — included, runs on any CPU including a Raspberry
  Pi 4. A slightly more accurate full-precision version is downloaded on first use (89 MB).
  Switching retrains every state sensor from its stored images.
- **Object sensors:** D-FINE S — included, about 0.1 s per check on a modern PC and a few
  seconds on a Raspberry Pi 4. D-FINE N is faster and lighter but misses more (downloaded on
  first use, 15 MB). The detector is only loaded while at least one object sensor exists.
- **Reading sensors:** PP-OCRv6 tiny (PaddleOCR) — included, a few milliseconds per reading.
  PP-OCRv6 small is larger and can help with unusual fonts (downloaded on first use, 21 MB).
  Only loaded while at least one reading sensor exists.

A choice you made stays when a later version recommends another model.

## App options

| Option | Description |
|---|---|
| `log_level` | Amount of logging. |
| `discovery_prefix` | MQTT discovery prefix (normally `homeassistant`). |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | Only needed for a broker that Home Assistant does not provide. |

## Support

Report issues at <https://github.com/oleost/VisionState/issues>.
