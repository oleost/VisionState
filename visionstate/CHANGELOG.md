# Changelog

## 0.4.1b2

- Runs on Python 3.14 (was 3.12). All tests and the image smoke test run on 3.14.
- SQLite connections are closed cleanly when the app stops.

## 0.4.1b1

First beta on the new beta channel.

- Dependencies updated (web server, database, image and video libraries, AI/ML libraries).
- Models trained by an older library version are retrained automatically at startup (seconds).
- The released images are now started and tested (both architectures) before they are published.
- A **BETA** badge in the menu shows when the beta app is running.

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
