# Changelog

## 0.7.1b5

### Changed

- **Export checked readings tells more** (Quality tab of a reading sensor), so a shared export
  helps improve reading more:
  - every reading says which reader read it and with which VisionState version (readings from
    now on), whether the picture was greyscale (infrared at night) and
    whether VisionState had the sensor's light on;
  - for the wheel reader: where it saw each wheel, how sure it was, and the value with the last
    wheel's fraction;
  - an optional **What meter is this?** text goes into the export, so it is not forgotten in the post;
  - optionally the **accepted readings nobody checked**, marked as unchecked;
  - **Check 10 accepted readings** sends the newest accepted readings to the review queue, so the
    export is not only the readings that were rejected.

## 0.7.1b4

### Changed

- **Counters you already have keep the text reader.** In 0.7.1b3 every mechanical counter moved
  to the new wheel reader; now only new counters use it, and the ones set up before keep reading
  the way they did until you choose *Read with → Wheel reader* on their Settings tab. Their Live
  tab points to it. A sensor imported from an export made before the wheel reader keeps the text
  reader too.
- **Settings → AI model** shows the wheel reader under *Reading sensors: mechanical counters*,
  with its licence and source, and the status at the top of the page has a line for it (loaded,
  or why it could not be loaded).

## 0.7.1b3

### New

- **Wheel reader for mechanical counters.** Water and gas meters are now read wheel by wheel
  with a small model made for VisionState: it sees how far each wheel has turned, so a wheel
  half way between two digits no longer turns into a wrong digit. The wheels are read together,
  the way they turn (a wheel only moves on while the one to its right goes from 9 to 0), so a
  reading where they do not fit together is never chosen. On the shared images of a water meter
  with red wheels (thanks again, @pedromfa — issue #40) it read all 17 right; PP-OCRv6 small read
  15 and the included tiny reader 8. It is included (2 MB, a few milliseconds per reading) and is
  the default for every mechanical counter, also the ones you already have; *Read with → Text
  reader* on the Settings tab keeps the old way. The model, its training code and where its
  data comes from are in `tools/wheelreader`.

## 0.7.1b2

### Better

- **Mechanical counters with coloured wheels** read more reliably. The red decimal wheels of a
  water meter were seen as light grey, and a red "9" was read as a "5"; they are now read as
  dark as the black ones. On the checked readings a user shared of such a meter (thanks,
  @pedromfa — issue #40) this took exact readings from 61 to 70 of 120, without changing how
  meters with black digits read. For water and gas meters, choose **PP-OCRv6 small** as the
  number reader (Settings → AI model): on that meter it read 15 of 17 images right, the included
  tiny reader 8.

### Fixed

- **Export checked readings** cut every image with the sensor's region as it is now, so after
  moving the camera or the region the older images showed the wrong part of the picture. Every
  reading now keeps the region it was read in, and the export, the review queue, the History and
  the Quality tab use it. Readings from before this version still use the current region (the
  export marks them `"region": "current"`).
- On a phone, a rejected reading in the History with a long reason (*not the number of digits
  the counter has*) made the page scroll sideways; the reason now wraps.

## 0.7.1b1

### New

- **Review reminder**: when frames have waited for review a long time, a notification shows up in
  Home Assistant — *There are 11 frames waiting for review in VisionState, the oldest for 8 days*
  — with a link to the app. It never comes just because a frame arrived: by default once the
  oldest frame has waited 7 days, and it goes away by itself once they are reviewed. Set it under
  **Settings → Review reminder**: after how many days, from how many frames, whether to remind
  again every few days (off by default) and whether to push it to a phone as well (off by
  default; any notify service, such as the Companion app). **Send a test** shows what it looks
  like.
- The review queue entity has a new attribute, `oldest_waiting_since`, for automations of your own.

## 0.7.0

A release about looking back: everything your sensors recorded in one place, and the images
behind a sensor's mistakes one tap away.

### New

- **History page**, next to Review: what every sensor recorded, in one list. Filter it by
  sensors, by *what* (a state, an object or your own label — *Car* and *Our car* apart), by
  event (changed, detected, cleared, rejected reading, sent to review …), by time (the last hour,
  day, week or month, or from … to …) and by *waiting for review*; each filter takes several
  choices and shows how many entries each would find. The filter is part of the page address, so
  it survives a reload and can be bookmarked.
- **Each sensor's History tab** is the same list for that sensor, with the same filters, and
  **Open in all history** takes them to the History page. It shows 50 entries at a time with
  **Load more** (it used to stop at the newest 100).
- New entries no longer move the list while you read it: **Show N new** appears instead.
- **Quality tab: tap a red cell of *What gets mixed up*** to see the images behind it — e.g. the
  ones labelled *Partial* that the AI took for *Closed* — and keep, relabel or delete them right
  under the matrix. Images you already said are right are shown too, marked as such.

### Fixed

- A red cell of *What gets mixed up* counted images you had said are right, while they were
  hidden and left out at the next training. They are kept and shown now (after the next
  training; an empty cell offers **Retrain now**).

### Changed

- A state sensor's card on the dashboard links to **History** instead of **Upload**, like the
  cards of object and reading sensors (Upload is still a tab of the sensor).

## 0.6.5

### Fixed

- **Views with a live camera picture fetched frames as fast as they could** instead of every few
  seconds: the dashboard, a sensor's Label tab and the region in its Settings asked for a new
  frame dozens of times a second while they were open on a screen — from the camera itself on the
  Label and Settings tabs. They now fetch one frame every 2 seconds (the dashboard every 15), as
  intended. This was there since the first version.
- Switching away from such a view and back while a frame was on its way started another round of
  fetching next to the first one; the light held on while you look could likewise be renewed
  several times over. Now only one runs at a time, and nothing is fetched while the view is hidden.
- The Dataset tab, the Quality tab of a state sensor and the latest labels on the Label tab were
  loaded again every 2 seconds; they now reload when something changes there.

## 0.6.4

A release about sturdiness, and object sensors that know your own labels better.

### New

- **An object sensor asks when it is not sure about your own label.** A box that looks somewhat
  like *Our car*, but not clearly (in light you never taught it in), goes to the review queue:
  *Is this Our car?* **Yes** or **No** teaches it, so next time it knows. While it is unsure, the
  label stays as it was instead of switching off. It asks at most one question per label at a
  time, and no more often than the review cooldown.

### Fixed

- **A taught object that stands still no longer flickers.** A parked car taught as an own label
  (*Our car*) was sometimes taken for an ordinary car when the light changed, and its sensor
  switched off and on again. An object that stays where it is now keeps its taught answer while
  it still looks much like it; a new object must still clearly look like what you taught.
- **The MQTT connection could stop for good** after an unexpected error (for one, a message
  that is not text sent to one of VisionState's command topics). It now reconnects, like after a
  lost connection.
- **A sensor could stop checking until a restart** when its settings could not be read at that
  moment (a busy database). It now tries again after 30 seconds.
- **Importing a sensor with a damaged image** failed halfway and left a sensor behind that did
  not run. Unreadable images are now skipped, and the message says how many. A file that is no
  ZIP gives a clear error instead of an internal one.
- A message on a sensor's pause switch topic that was not ON or OFF paused the sensor; such a
  message is now ignored.
- A camera that did not answer showed "Camera unavailable:" without a reason; it now says it
  gave no answer in time.
- Answering a review item or taking a training image while that item or sensor was being removed
  gave an internal error; it now says it is gone.

### Changed

- **An object is only cleared after it was missing twice in a row.** When something that was
  there is missing, the sensor looks again a few seconds later instead of waiting for its next
  check; one missed check no longer switches it off.
- **Camera passwords are now also masked in error details in the log** (tracebacks), not only
  in the messages themselves, and in every error shown in the app.
- **Camera addresses are checked before a picture is fetched**: a snapshot URL starts with
  `http://` or `https://`, a stream with a network address such as `rtsp://`.
- **Limits for pictures**: a snapshot larger than 50 MB or an image of more than 60 megapixels is
  refused instead of filling the memory (a 4K camera picture is 8 megapixels).
- An unexpected error in a check is logged once, with its details, instead of on every check.
- Updated libraries: FastAPI 0.142 and PyAV 19 (camera streams and video uploads). With PyAV 19,
  turning video frames into images uses less memory.
- Inside: the code is reorganised so it is easier to read and change (a map of the code in
  `docs/ARCHITECTURE.md`), its types are checked on every change, and more of it is covered by
  tests.

## 0.6.3

Needs **Home Assistant 2025.10 or newer** (an older Home Assistant is not offered this update).

### New

- **Teach an object sensor your camera.** Tap a box on the Live tab or on a history frame:
  **Correct**, **Not a person** (the garden statue, a shadow taken for a dog), **another object**,
  or a **new label of your own** such as *Our car* or *Rex* — with its own on/off sensor and count
  in Home Assistant. **Missed something?** lets you draw a box around what the AI missed. From then
  on, a box that clearly looks like one you taught gets your answer; otherwise the AI's own answer
  stands. Nothing changes until you teach something.
  - Every check decides anew by what a box looks like. Boxes filtered away are still shown, dashed,
    in the frame, and listed in the history and on the new **Quality** tab (which appears once you
    taught something). **Settings → What you taught** turns it off or forgets it all. Export and
    import take it along.
  - Going back to an older version (e.g. restoring a backup) after making an own label: the older
    version does not know the label, so its entities stay in Home Assistant with their last value.
    Remove own labels first, or delete those entities in Home Assistant.
- **Switch on a light for each check** — for cameras in dark places such as a meter cabinet. Pick
  a light, switch or helper with the camera (first step of the wizard, or Settings → General); it
  is turned on before the frame is taken and off again afterwards. A light that is already on is
  left alone.
  - While the light warms up, VisionState fetches frames and throws them away, so the frame it
    reads is taken in the light — also with cameras that hand out a picture taken earlier (an
    ESP32 camera, for one).
  - **The light is on while you look**: in the wizard, when you draw the region and on the Label
    tab, so labelled images are taken in the same light as the checks. A switch keeps it off for
    that view; it goes off when you leave.
  - With **Detect changes in the image**, a change is read on a new frame taken in the light, and
    change detection pauses while the light is on.
- **When to check** (Settings, and the last step of the wizard): the **regular check can be
  switched off** — the sensor then only checks when triggered, once after start-up and with its
  *check now* button — and a trigger entity can be limited to **one state** (*only when it
  becomes* `on`, `Flow finished` …).
- **Quality tab for reading sensors**: the share of accepted readings today, in the last 7 and 30
  days, why the others were rejected, and a chart per day.
  - **Every rejected reading is kept** with its frame and waits in the **review queue**. Tell
    whether the reader read the meter right or misread it, optionally with the value it showed
    (the field starts with what was read, so usually one digit is changed). This does not train the
    reader: it shows how reliable the reading is and which setting to change.
  - **Spot checks** (Settings → Sensor output, off by default): a share of the accepted readings
    goes to the review queue too, to find misreads that passed every check.
  - **Export checked readings** downloads the readings you checked — only the region of each — ready
    to share in GitHub Discussions (as public domain, CC0) to help improve reading.
- **More from a reading sensor in Home Assistant**, off by default — turn on what you need:
  *Raw reading* (also when rejected), *Problem* (`ok` or why the last reading was rejected),
  *Accepted (24 h)* (%), *Reader image* (what the reader saw at the last reading), and for counters
  a **Rate**: how fast it goes up over the last 15 minutes (adjustable) — kW for kWh, m³/h for m³,
  L/min for L. Useful to spot a water leak.
- **Review queue**: the Review page shows how many items wait per sensor, and **Dismiss all**
  skips all of a sensor's items at once (also on a reading sensor's Quality tab). **Change an
  answer**: answered frames stay in the list on the left; click one to answer it again — the image
  your first answer added to the dataset gets the new state (or is taken out with *Skip*) instead
  of being added a second time.
- **Send to Home Assistant** can be switched off per sensor (last step of the wizard, or Settings →
  General): the sensor runs and records everything in VisionState, but its entities in Home
  Assistant stay *unavailable* — for tuning a sensor before Home Assistant uses it.

### Changed

- **Entity IDs like other integrations**: new sensors are named by Home Assistant after the sensor
  and the entity, without the `visionstate_` prefix — a sensor named *Water meter* becomes
  `sensor.water_meter`, `sensor.water_meter_confidence` … **Existing sensors keep their entity
  IDs** (`sensor.visionstate_…`), also when they are exported and imported. The app shows the
  entity IDs Home Assistant actually uses.
- **Mechanical counters: the last wheel turning is no longer a rejection.** A reading exactly one
  step of the last digit below the value keeps the value without counting as rejected (the Live tab
  says *Last wheel turning*). Lower readings than that are still rejected.
- A paused sensor stays paused: it no longer checks the camera after a retrain, a model change or
  teaching boxes. *Check now* still checks it.

### Fixed

- **iPhone and iPad** (the Home Assistant app, Safari): drop-down menus such as the AI model
  choice were light with light text; they are dark and readable now.
- The Live tab of an object sensor no longer jumps when a new check comes in, and sensors that
  check very often no longer show a broken frame for a moment.
- Behind the scenes: every release is now also tested as an update from the last stable release
  (and back), on an emulated Raspberry Pi 3/4 CPU, and in Safari's engine.

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
