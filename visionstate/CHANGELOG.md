# Changelog

## 0.6.0

VisionState now has **three kinds of sensors**. Besides states you teach it, it can find
**objects** and **read numbers** — neither needs any training. Existing sensors keep working as
before; the database is upgraded automatically.

### New

- **Object sensors**: find people, cars, bicycles, cats, dogs and 75 more common objects. Choose
  *Objects* in step 3 of the new sensor wizard, pick the objects (popular ones first, all others
  under *Show all*) and see a test on a fresh frame right away.
  - Each object becomes an on/off `binary_sensor` and a `…_count` sensor in Home Assistant.
  - An object counts when it stands inside the region; it is cleared a while after it was last
    seen (default 30 s), so a person turning around does not flicker.
  - **Live** tab with the checked frame and its boxes, **History** of when objects appeared and
    cleared; the Home Assistant image shows the boxes too.
  - Detector: D-FINE S (Apache-2.0) is included; D-FINE N (faster) can be chosen under Settings.
- **Reading sensors**: read a number from a display — power meters, fuel prices, the minutes left
  on a washing machine. Choose *Reading* in step 3; it reads a fresh frame right away and shows
  the value and what the reader saw.
  - Modes: *Counter* (only goes up, works in the Energy dashboard), *Value* and *Time left*
    (`1:25` = 85 minutes).
  - Unsure or implausible readings (a counter going down, too big a jump) are rejected and the
    last value stays — also after a restart. The History tab lists new values and rejected
    readings with the reason.
  - Reader: PP-OCRv6 tiny (Apache-2.0) is included; PP-OCRv6 small can be chosen under Settings.
  - Not yet for mechanical counters with rolling digits (most water meters).
- **Shape the region**: besides a rectangle, the region can be any shape. Drag a **+** on an edge
  to add a corner, drag corners to fit an object at an angle, and double-click / double-tap or
  hold a corner to remove it. Everything outside the shape is ignored.
- **Adjust the region on the test image** in step 3 of the wizard (object and reading sensors);
  it is tested again straight away.
- **Beta channel**: new versions can be tried first as a separate *VisionState (beta)* app (see the
  documentation); a **BETA** badge in the menu shows when it is running.

### Improved

- **Phones**: the wizard steps fit on one row, state buttons and history rows no longer get
  squeezed, the Quality figures sit two per row, keyboard hints are hidden on touch screens, the
  dataset's selection bar only follows the scroll while something is selected, and swiping on a
  camera image scrolls the page.
- AI models are only loaded while a sensor of their kind exists, and the models you use stay
  selected when a later version recommends other ones.
- Models trained by an older library version are retrained automatically at startup (seconds).
- The Review badge in the menu updates when the review list is opened.
- Runs on Python 3.14; dependencies updated (web server, database, image, video and AI libraries).

### Fixed

- Stopping the app showed **Error** instead of **Stopped** in Home Assistant — both because of the
  exit code and because it waited for an AI check still in progress on slow devices.
- SQLite connections are closed cleanly when the app stops.

### Behind the scenes

- Every release is tested in real browsers on desktop and on a phone with touch, and the images
  are started and tested (both architectures, including an object detection and a reading)
  before they are published.

## 0.4.0

- **Possibly mislabelled images** (Quality tab): images the AI disagrees with after training.
  Confirm the label, change it or delete the image with one click — a single wrong label can pull
  a whole sensor down.
- **Review queue entity**: `sensor.visionstate_review_queue` shows how many frames are waiting,
  with a per-sensor breakdown, for dashboards and automations.
- **New sensor wizard**: optional fourth step to set up triggers (motion sensor, opener, image
  change detection) right away.
- Fixed: changing or deleting labelled images (Dataset, Upload, Quality) showed an error and did
  not retrain the model.
- Weekly dependency updates via Dependabot.

## 0.3.1

- Installs and updates now download a ready-made image instead of building on your machine:
  seconds instead of minutes (especially on Raspberry Pi).

## 0.3.0

- **Adjustable review queue**: global rules under *Settings → Review queue*, with per-sensor
  overrides on the sensor's Settings tab (empty field = use the global value, or turn review off
  for a sensor).
  - "Send to review below" is now an absolute percentage (default 85 %) instead of threshold + 15.
  - Minimum time between reviews, flip-flop detection and random spot checks are adjustable.
  - Random spot checks are now **off** by default.

## 0.2.1

Security and privacy hardening.

- Camera passwords in RTSP/HTTP URLs (including `?user=&password=`) are hidden in logs and error messages.
- Exported sensor bundles no longer contain camera credentials; after import you are asked to re-enter the URL.
- Imported bundles are validated exactly like sensors created in the UI.
- Size limits for uploads and ZIP archives (see `UPLOAD_LIMITS` in `settings.py`).
- Stricter static file serving.

## 0.2.0

- **Triggers** (sensor → Settings → *When to check*):
  - Check when any chosen Home Assistant entity changes state (motion sensor, door contact, garage opener, …).
  - Optional change detection: compares the region every few seconds and only runs the AI when it changed;
    shows the measured change so the sensitivity is easy to tune.
  - After a trigger the sensor keeps checking at a faster pace for a while (burst) to catch the final state.
  - The regular interval stays as a safety net and can now be set much longer.
- Label tab shows what triggered the last check; Settings shows the trigger event connection.
- `last_trigger` attribute on the state entity.
- Existing databases are upgraded automatically.

## 0.1.0

First test release.

- Sensors with 2–9 states, a region of interest and any Home Assistant camera, HTTP snapshot URL or RTSP stream.
- Label live frames with one click or keys 1–9; the model retrains in about a second.
- Bulk upload of images, ZIP archives and video (frame extraction with duplicate skipping) with model suggestions.
- Review queue for low-confidence, flip-flopping and spot-check frames.
- Quality page: cross-validated accuracy, confusion matrix, day/night coverage and tips.
- Home Assistant entities via MQTT discovery: state, confidence, last frame, classify button, enable switch.
- Export and import of sensors as ZIP bundles.
- Bundled DINOv2-small (8-bit) model; optional full-precision model.
