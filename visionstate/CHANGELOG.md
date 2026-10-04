# Changelog

## 0.6.3b13

- **Counters: the last wheel turning is no longer a rejection.** A wheel between two digits is read
  as the one or the other; once the higher one was published, every right reading until the
  counter got there was rejected as "a counter can not go down" — many rejections and review
  items for nothing. A reading exactly one step of the last digit below the value now keeps the
  value without counting as rejected (the Live tab says *Last wheel turning*). Lower readings than
  that are still rejected.
- **Misread → the value it showed:** the field starts with what was read, so usually one digit is
  changed; digits typed without a point are placed like the reader does (`0629558` → `629.558`),
  and the value that will be saved is shown before saving.

## 0.6.3b12

- **More from a reading sensor in Home Assistant**, off by default — turn on what you need:
  *Raw reading* (what was read, also when rejected), *Problem* (`ok` or why the last reading was
  rejected), *Accepted (24 h)* (%), and for counters a **Rate**: how fast it goes up over the last
  15 minutes (adjustable) — kW for kWh, m³/h for m³, L/min for L. Useful to spot a water leak.
- **Help improve reading:** *Export checked readings* on the Quality tab downloads the readings you
  checked — only the region of each, not the whole picture — with what was read and what was right,
  ready to share in GitHub Discussions (shared as public domain, CC0).
- Clearer that answering a reading (*Read correctly* / *Misread*) does not train the reader: it shows
  how reliable the reading is. Answers for state sensors still train them.

## 0.6.3b11

- The **Live** tab of an object sensor no longer jumps when a new check comes in: the list of
  boxes under the frame used to disappear for a moment until the new picture had loaded (most
  visible on a phone). Boxes now change together with the picture they belong to.

## 0.6.3b10

- **Teach an object sensor your camera.** Tap a box on the Live tab or on a history frame:
  **Correct**, **Not a person** (the garden statue, a shadow taken for a dog), **another object**,
  or a **new label of your own** such as *Our car* or *Rex* — with its own on/off sensor and count
  in Home Assistant. **Missed something?** lets you draw a box around what the AI missed. From then
  on, a box that clearly looks like one you taught gets your answer; otherwise the AI's own answer
  stands. Nothing changes until you teach something.
- Every check decides anew by what a box looks like. Boxes filtered away are still shown, dashed,
  in the frame, and listed in the history and on the new **Quality** tab (which appears once you
  taught something). **Settings → What you taught** turns it off or
  forgets it all. Export and import take it along.
- Going back to an older version (e.g. restoring a backup) after making an own label: the older
  version does not know the label, so its entities stay in Home Assistant with their last value.
  Remove own labels first, or delete those entities in Home Assistant.

## 0.6.3b9

- **Send to Home Assistant** can be switched off per sensor (last step of the wizard, or
  Settings → General): the sensor runs and records everything in VisionState, but its entities in
  Home Assistant stay *unavailable* — no values, no statistics. For tuning a sensor before Home
  Assistant uses it, e.g. one that takes over the entity ID (and statistics) of an older sensor.
  Marked on the dashboard; existing sensors keep sending.
- Object sensors that check very often no longer show a broken frame for a moment: when the
  analysed frame is already gone, the latest one is shown (without boxes that belong to another).

## 0.6.3b8

- **The light is picked with the camera** — in the first step of the wizard and under
  **Settings → General** — instead of under *When to check*.
- **The light is on while you look**: in the wizard (region and test), when you draw the region
  in the settings and on the Label tab, so you can see what you frame and the labelled images are
  taken in the same light as the checks. A note above the frame shows it, with a switch to keep
  the light off for that view. It goes off when you leave (at the latest 30 s after a tab is closed
  or a phone is put away); a light that was already on is left alone.

## 0.6.3b7

- **iPhone and iPad** (the Home Assistant app, Safari): drop-down menus such as the AI model
  choice under Settings were light with light text; they are dark and readable now.
- The app now says which Home Assistant it needs (**2025.10 or newer**), so an older Home Assistant
  is not offered an update that cannot work there.
- Behind the scenes: every release is now also tested as an update from the last stable release
  (and back), on an emulated Raspberry Pi 3/4 CPU, and in Safari's engine.

## 0.6.3b6

- **Entity IDs like other integrations**: new sensors are named by Home Assistant after the sensor
  (the device) and the entity, without the `visionstate_` prefix — a sensor named *Water meter*
  becomes `sensor.water_meter`, `sensor.water_meter_confidence`, `image.water_meter_last_frame` …
- **Existing sensors keep their entity IDs** (`sensor.visionstate_…`); nothing changes for them,
  also when they are exported and imported.
- The app shows the entity IDs Home Assistant actually uses — also when you changed one in Home
  Assistant or it got a `_2`.

## 0.6.3b5

- Review page: the number waiting per sensor now counts down with each answer, and a sensor
  with nothing left no longer shows **Dismiss all**.

## 0.6.3b4

- **Dismiss all** in the review queue: the Review page shows how many items wait per sensor, and
  one button skips all of a sensor's items at once — for the many rejected readings that can pile
  up while a sensor is being set up. Also on a reading sensor's Quality tab. Answers already
  given and the counts in Quality are kept.

## 0.6.3b3

- Quality tab of reading sensors: the cards show the share of **accepted** readings (100 % = none
  rejected) instead of the rejected share, which read like a bad score.

## 0.6.3b2

- **Quality tab for reading sensors**: how many readings were rejected today, in the last 7 and
  30 days, why, and a chart per day — to see how reliable a meter is read.
- **Every rejected reading is kept** with its frame (before: at most one every 5 minutes) and
  waits in the **review queue**. Tell whether the reader read the meter right or misread it,
  optionally with the right value — there or on the Quality tab. Verified readings are kept for
  good, like training images.
- **Spot checks** for reading sensors (Settings → Sensor output, off by default): a share of the
  accepted readings goes to the review queue too, to find misreads that passed every check.

## 0.6.3b1

All under **Settings → When to check** (and the last step of the new sensor wizard), for every
kind of sensor:

- The **regular check can be switched off**: the sensor then only checks when triggered, once
  after start-up, and with its *check now* button in Home Assistant.
- A trigger entity can be limited to **one state**: *only when it becomes* `on`, `Flow finished` …
  Other changes of that entity are ignored.
- **Switch on a light for each check**: pick a light, switch or helper; it is turned on before
  the frame is taken and off again afterwards — for cameras in dark places such as a meter
  cabinet. A light that is already on is left alone.

## 0.6.2

- **Fixed: the app did not start on older CPUs and in virtual machines with a generic CPU
  type** (for example Proxmox's default `kvm64`) since 0.6.0 — the log ended with
  `NumPy was built with baseline optimizations: (X86_V2) but your machine doesn't support`.
  VisionState is back on a NumPy version without that requirement, and every release is now
  checked on such a CPU before it is published.

## 0.6.1

### New

- **Mechanical counters** — the rolling digit wheels of water and gas meters — can now be read.
  In the new sensor wizard choose *Reading*, then *Mechanical counter*, draw the region from the
  first wheel to the last and enter the number of digits. The region is split into one field
  per wheel (shown on the image), so the dividers between the wheels and the half digits above
  and below are no longer read as digits. A reading with another number of digits than the
  counter has is rejected. Existing reading sensors are not changed; switch one over under
  Settings → Reading.
  - **This is new and has hardly been tried on real meters yet** — so far on one type of water
    meter. It reads well while the wheels stand still and less reliably in the moment a wheel
    turns. Feedback is very welcome: what kind of meter it is, whether it read correctly, and a
    screenshot of *What the reader sees* — in
    [GitHub Discussions](https://github.com/oleost/VisionState/discussions) or as an issue.
- The reading settings start with *What does it look like?* (digital display or mechanical
  counter); the LED / LCD choice is shown for digital displays.
- **Storage** under Settings: how much space history frames and training images use, and how
  much is free.
- History now has a **size limit** besides the number of days: by default 7 days but at most
  2 GB, whichever is reached first. The oldest frames go first, frames waiting for review last;
  training images are never removed. Both limits are set under Settings → Storage and apply
  right away.
- Object sensors show the object's own icon in Home Assistant — a car, a person, a dog … — for
  all 80 objects, instead of the same house icon for every one. The icons come from the icon set
  Home Assistant ships, so no extra setup is needed; existing sensors get them automatically.
- Reading sensors: when the display shows a decimal point (`1234.5`) that the settings do not
  match, the wizard and Settings offer the right number of decimals — 0 would have made it 12345.
- History of state sensors: tap a frame to see it whole; "Added as …" says which state.

### Changed

- The app option *History retention (days)* is gone from the Configuration tab: how long history
  is kept (and its size limit) is set under Settings → Storage. A value you had changed there is
  not carried over — history is kept for the default 7 days until you set it again under
  Settings → Storage.
- Settings no longer offers "Runs on": VisionState runs on the CPU only, by design. The list
  only ever had the CPU and ONNX Runtime's built-in "Azure" option (which did nothing here).
- The model pill in the top bar and the model name under "Sensors" are gone — there are three
  models now; Settings → Status lists them.
- The history clean-up runs every 10 minutes instead of every hour.
- An imported sensor whose name is taken is called "… (2)".

### Fixed

- Object boxes: labels no longer overlap when objects stand close together.
- Phones: Import and New sensor sit together on one row on the Sensors page; "Add state" puts
  the cursor in the new field; Export/Pause and the region buttons no longer jump when a chip or
  "Reset to rectangle" appears; at most two messages at a time; the wizard's keyboard hint is
  hidden on touch screens; "check every … s for … s" keeps values with their units.
- The sensor overview no longer says "0 labelled images" when there are no state sensors; the
  count of labelled images under the title is gone (each sensor card shows its own).
- Smaller fixes: the reading wizard's button says "Create sensor" (no labelling needed), a region
  that is a rectangle again no longer offers "Reset to rectangle", wide displays sit in the middle
  of their dashboard card, and the region step's text fits every kind of sensor.

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
  - **New and looking for testers**: reading has been tested on drawn displays and a set of
    photos, but hardly on real cameras yet. Please share how it works on your meter or display
    — what it is, whether it read correctly, and a screenshot of *What the reader sees* — in
    [GitHub Discussions](https://github.com/oleost/VisionState/discussions) or as an issue.
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
