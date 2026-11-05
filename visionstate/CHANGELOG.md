# Changelog

## 0.14.0

**Teach sounds as your own microphone hears them, get a threshold suggested from your answers, and
see how fast the AI is on your machine.**

### New

- **Teach the built-in sounds with clips of your own.** Every built-in sound a sensor listens for
  — a siren, a dog, a door — now has a card on **Own sounds** with *It is this* and *It is not
  this*: record clips there, add recordings, or teach a clip from the History. Clips of it make it
  hear the sound as your microphone hears it, also when the AI alone was not sure enough. A clip of
  what it is not — a cupboard door, a siren on TV — holds the sound back when something is clearly
  like that clip; the Live tab shows *Held back*. It stays the same sensor in Home Assistant.
  - Answers in Review now teach too: *yes* adds the clip to the sound, *no* to what it is not.
  - **Almost heard:** with clips on, a sound the AI itself almost heard is asked about in Review
    too. A *yes* teaches the clip, and says the threshold may be too high. After five *no* in a
    row about a sound almost heard, it is not asked about almost heard any more: just below the
    threshold is then mostly the room's own noise.
  - **Review plays one sound after the other:** tap a clip once, and it plays over and over until
    you answer; the next question's clip then plays by itself (on a phone too).
  - Every clip taught to a sound, your own sounds' too, is now learned from all its loud moments
    instead of only the loudest, so one recording teaches more.
- **Record an own sound straight from the sensor's microphone.** On **Own sounds**, *Record* sits
  beside *Add recordings*: tap it, make the sound (shut the door, ring the bell), and tap *Stop*.
  The recording goes on for a few seconds after *Stop*, since a stream comes a little late; then
  trim the clip on its waveform (drag either end, or the whole frame, with a finger or the mouse),
  play the part kept, and *Teach* it. As in every clip, seconds with someone talking are silenced
  before you hear or keep it.
- **A threshold suggested from your answers, on every kind of sensor.** **From your answers**, at
  the top of a sensor's **Settings**, shows how your right and wrong answers in Review fare at each
  threshold the sensor decides by: a state sensor's, a reading sensor's, an object sensor's line
  for your own labels and for faces, and each sound's. It suggests one that lets more of the right
  ones and fewer of the wrong ones through, and *Use* sets it. Nothing changes by itself. When no
  threshold tells them apart, it says so: then teaching does. An answer counts while what it
  taught is there: forget a clip, or delete a taught box or face, because it was wrong or is no
  longer wanted, and its answer no longer counts either. How does it work for you in daily use?
  Tell me in [#74](https://github.com/oleost/VisionState/issues/74).
- **Sound sensors can listen to a camera in Home Assistant.** The sound source is picked like the
  camera of the other sensors: your cameras in Home Assistant first, a stream URL (RTSP, go2rtc, an
  ESP32 microphone) below. A camera picked there is heard through Home Assistant's own stream of
  it, with no address or password to type, but several seconds later than its RTSP stream (about
  10 on the doorbell it was tried on). When a sound must be known at once, or when the wizard hears
  nothing (Home Assistant passes on only some kinds of sound), use the camera's RTSP stream.
- **Speed test** (#71): **Settings → Speed test** measures AI models with the camera or microphone
  of one of your sensors: the time of a whole check and its parts (camera, decoding, AI), the
  memory each model takes, and whether it keeps up with the sensor's interval. Models you do not
  have are downloaded for the test and deleted afterwards, unless you tick *Keep the downloaded
  models*. All sensors pause while it runs and go on by themselves afterwards. *Download report*
  gives the numbers as a text file to share, without names or addresses.
- **Choose how many CPU threads the AI uses** (#71, thanks to @ecdlguy): **Settings → AI model →
  Threads per AI model**. *Automatic* uses one per CPU core, as before. On a virtual machine or NAS
  that shares its CPU with other work, fewer threads can be faster: on a NAS VM with 4 vCPUs and
  one guaranteed core, a check took 1.7 s where 2 vCPUs took 1 s. The change applies at once, and
  the speed test shows whether it helped.

### Changed

- **Sensors on the same camera share their pictures** (#63, thanks to @Docter77). Sensors on the
  exact same camera or address take one picture together, and their regular checks fall on the
  same moments (a sensor every 30 s and one every 60 s meet every minute). Each still checks its
  own region with its own model. The camera gets far fewer requests: with four sensors on a
  camera's RTSP stream, 6 connections a minute instead of 17. A check can come a little sooner
  than its interval after a trigger, never later. The CPU has short, higher peaks and its average
  use is a little higher; the memory is a little lower. A sensor with a light, or alone on its
  camera, takes its pictures by itself as before.
- **Clips with the camera's video, and all of their sound.** A clip's video now starts at the
  camera's last key frame before the sound, so a camera that sends a key frame only every few
  seconds no longer gives some clips sound only, and a clip with video keeps the sound from before
  its first key frame. The video may be silent for a moment before the sound begins.
- **The AI's threads still sleep while they wait for work** instead of keeping the CPU busy.
  Measured again: keeping it busy was at most about 10 % faster, for up to three times the CPU, and
  twice as slow when there were more threads than free cores.

### Fixed

- **No more telemetry to Microsoft.** ONNX Runtime, the library that runs the AI models, sends
  telemetry to Microsoft by default (an ID for the install and details of the machine, not camera
  pictures or sound), and VisionState had not switched that off. It is off now, and every release
  is checked beforehand for any connection beyond your cameras, Home Assistant and MQTT. Thanks to
  Immich, whose fix (immich-app/immich#32956) brought it to light.

### After this update

- Answers about own labels and faces given before this version were not kept as right or wrong,
  so the suggested thresholds for those count from now on.

### Tested

- RTSP sound from a real camera: a Reolink doorbell, with speech, a dog and a siren.

## 0.13.1

**Fixes for counters, above all water meters with pointer dials (#59).**

- **A counter with dials no longer jumps a whole digit when its last wheel reaches the next one.** A
  last wheel on a clean digit with the ×0.1 dial nearly round can be read either side of that digit
  (187.99 or 188.99): the last value now decides. A sensor without a last value treats such a reading
  as unsure.
- **The counter limit now counts the dials.** The limit and its margin looked at the wheels alone, so a
  counter of whole m³ could rise 2 m³ at any reading. Both now follow the smallest step of the whole
  sensor. **The standard limit of such a counter becomes much lower:** with four dials on whole m³, from
  2400 to 2.4 m³/h. Until it has learned your meter, a higher real use is rejected and waits in Review:
  raise *Reject a reading that goes up faster than* in Settings → Sensor output if needed, and answer
  the newest rejected reading to let the counter go on from there.
- **Adding dials to a counter that already has a value** no longer makes the finer value count as a
  rise over the limit.

## 0.13.0

**VisionState can now hear.** This release adds sound sensors, reads meters seen at an angle and
meters with pointer dials, and gives you more control over what reaches Home Assistant.

### New

- **Sound sensors**: a sound sensor listens to a stream and tells Home Assistant what it hears: a
  smoke alarm, a siren, a dog barking, someone talking and about 80 other sounds, each its own
  on/off sensor. Everything runs locally. It knows that someone is talking, never what is said.
  Choose **Listen** in the new sensor wizard. The sound model (about 7 MB) is downloaded when you
  make the first sound sensor.
- **Your own sounds**: teach it your doorbell or the washing machine's beep from a few recordings
  or clips.
- **Clips** (off by default): keep the seconds around a sound, with the camera's video if it has
  any, to listen to and teach from. Speech in a clip is silenced before it is stored.
- **Meters and displays seen turned or at an angle are read upright** (#58): put the region's four
  corners on the corners of the digits or wheels and mark the top edge. The region is then read as
  if the camera looked straight at it. This is on for new reading sensors. Existing sensors read as
  before until you turn on *Correct rotation and perspective* on their Region card.
- **Pointer dials** (#59): the small dials beside the wheels of many water meters (×0.1, ×0.01 …)
  can be added to a mechanical counter read by the wheel reader, with *Edit: Dials* in the wizard or
  in Settings. Each dial adds a digit, and wheels and dials are read together. Pale, drop-shaped and
  wide two-ended needles are read too, and so are wheels that stand still until the dial is nearly
  round. Review and the Quality tab show the wheels and each dial with the needle that was found.
- **Object snapshots**: each object on an object sensor can have its own picture entity, such as
  *Car snapshot*, for notifications and dashboards. Off until you tick it.
  - **Shape** (#62): *Snapshot shape* is `free` or a width:height such as `1:1` or `16:9`. The
    object is never cut.
  - **Movement** (#62): a new picture can also be sent when the object has moved by a share you
    set, so something parked sends nothing new. Every picture is a new row in Home Assistant's
    history. Set it to 0 to send one only when the count changes.
- **A Home Assistant tab on every sensor**: choose *Send values*, *Don't send values* (the entities
  stay, unavailable) or *Not in Home Assistant* (no entities, nothing on MQTT), and tick which
  entities the sensor has. *Send to Home Assistant* moved here from Settings.

### Changed

- **A simpler wizard: Camera, Detect, Checks.** The camera's picture and the low-resolution warning
  now show where you pick the camera, and the region is drawn in the Detect step, for every kind. A
  camera's light comes on in the first step. The last step offers *Not in Home Assistant*, so a
  sensor can be set up before anything reaches Home Assistant or MQTT.
- **Clearer errors**: a form's validation error now reads as a sentence.
- **Updated dependencies**: FastAPI, ONNX Runtime, SQLAlchemy, PyAV, websockets, Svelte and Vite.

### Fixed

- **The live page no longer says "Checks every 10 s" with the regular check off** (#62). Only the
  text was wrong: the regular check did stop.
- **The list of lights and entities in the wizard** was cut off and could not be scrolled to its
  end, and dragging its scrollbar closed it.

### After this update

Optional entities of reading sensors (raw reading, problem, accepted, rate, reader image) that are
on in Home Assistant stay. Those that are off there are removed, once. New reading sensors have them
only when ticked on the new tab.

### What sound sensors have been tested on

- **Tested:** a real Home Assistant with live HTTP streams, including one real smoke alarm heard
  through an iPhone's microphone. That is one alarm in one home, not proof that it hears yours.
- **Not tested:** RTSP from a real camera, Raspberry Pi 4, ESP32 microphones, clips in the Home
  Assistant app on iOS, and false alarms over days in a real home. Thresholds are a best guess, so
  try a sound on the Live tab and adjust. If you try a camera, please tell us the model and how it
  went.

**A smoke alarm sensor is an extra notification, never a replacement for a certified detector.**

## 0.11.0

### Better

- **Much less in Home Assistant's database.** VisionState no longer sends values that change at
  every check when the result does not: on a test installation with nine sensors, Home Assistant
  stored about 500–750 states an hour instead of about 4,400. What changed for you:
  - the confidence is in whole percent, and the `probabilities` of a state sensor have two
    decimals;
  - the `last_update` attribute is gone (Home Assistant shows when a sensor last reported);
  - the *Last frame* pictures, and an object's boxes and *last seen*, are sent at once when the
    state, the objects or the value change, and otherwise every few minutes.
  If an automation reads `last_update`, or the boxes while an object stays, look at it.
- **Far fewer MQTT messages**: a sensor's values are sent when they change, and every few
  minutes all the same (and right after the connection to the broker comes back). A sensor that
  sees the same thing all day now sends almost nothing.
- **Faster checks with less CPU**: the AI models no longer keep processor cores busy while they
  wait — about 30 % less CPU per check on the test machine, which leaves more for Home Assistant.
  A camera picture is no longer copied twice while it is read (about 20 % faster, and less memory
  for large pictures).
- **RTSP cameras are much quicker during a burst of checks and in the live view**: the stream is
  kept open while frames are asked for often (about 30 ms a frame instead of 1–3 s), and closed
  again a few seconds after.
- **History pictures are the camera's own**: a frame is kept as the camera sent it instead of
  being saved again, so it takes no time, loses nothing and usually takes less space — the same
  history limit holds more. Pictures a camera sends turned (by a tag in the file) are still saved
  upright as before.
- **The app rests in the background**: a page that is not shown (another browser tab, the Home
  Assistant app put away) no longer asks the app for anything, and is brought up to date the
  moment it is shown again.
- Change detection and the history's thumbnails decode camera pictures only as large as they
  need — several times faster for large pictures.
- The app no longer stalls while its database is busy, and the browser keeps the app's files
  instead of loading them every time the panel opens.

### Fixed

- History pictures are written whole or not at all, and removed only together with their
  history entries; pictures left behind (the app was stopped while saving one) are removed.
- A camera from Home Assistant has the same size limit for its pictures as a snapshot URL.
- An RTSP stream's connection timeout works again (FFmpeg had renamed the option).

## 0.10.1

### New

- **Wheel reader v2 for mechanical counters.** It reads every wheel twice, on its own and with
  the wheels next to it in view, so a region that does not quite fit is still read right, and it
  only passes a reading when it is sure of every wheel, not just on average. Tested on two water
  meters it was not trained on, it published no wrong value where v1 published six (most of them
  2–3 litres too high, two 20 litres), and it read more frames right. New counters use it.
  Counters you have keep v1 until you choose v2 under *Read with* on their Settings tab; their
  Live tab suggests it.
- On a meter photographed from the side whose last wheel is turning, v2 waits more often for a
  clear picture, so the value can come a little later. It takes about twice as long per reading
  as v1 (still a few milliseconds), and the app is 6 MB larger.

### Better

- **Exported readings say where the region is in each image** (Export readings on a reading
  sensor's Quality tab), also for readings made before this version (since 0.8.0). The margin
  around the region is cut off at the picture's edge, so the region was not always in the middle.
  For the wheel reader they also say how sure it was of each wheel the way it decides.

### Fixed

- The suggestions on a counter's Live tab (to try the wheel reader) no longer break into columns
  on narrow screens.

## 0.10.0

### New

- **Backup of everything** (Settings → Backup): one file with your sensors and settings, training
  images, people and their faces, history and downloaded AI models — to move VisionState to
  another Home Assistant, or between the stable and the beta app. Everything is ticked; untick what
  you do not need (each shows its size). Camera passwords are left out unless you tick them.
- **Restore** replaces everything in the app with a backup. Sensors keep their entities and entity
  IDs in Home Assistant; sensors and people that are not in the backup are removed only once the
  restored data runs. The backup is checked first (its version, every sensor's settings, and the
  free disk space, also where the history goes when the media folder is on network storage), and
  if anything goes wrong, even if the app is stopped in the middle, it keeps what it had. A backup
  from an older version is upgraded; one from a newer version is refused (update the app first).
  The sensors whose camera password must be entered again are listed. Only the AI models and
  trained sensor models VisionState itself makes come back from a backup.

### Better

- **Uses much less memory**: memory an AI model needed for one image is now given back after
  each check instead of being held for the rest of the run, and the state classifier no longer
  loads scikit-learn. On a test installation with nine sensors and face recognition the app went
  from about 940 MB to about 580 MB. A check takes a few percent longer.
- State sensors are retrained once after the update, from their stored training images (it takes
  a moment, and the sensors keep their last state meanwhile). Going back to 0.9.2 afterwards
  works too: it retrains them the same way.

### Fixed

- Uploads larger than 16 MB (videos, sensor exports) were refused when the app was opened in
  Home Assistant.
- An older version started on a newer database (going back to a backup made before an update) no
  longer makes the next update repeat its database upgrade.
- A rare "ResizeObserver loop" error on the dashboard in Safari and the Home Assistant app on iPhone.
- Pillow, the library that reads and writes the camera images, is updated to 13.0.0 for 24
  security fixes.

### Help wanted: Raspberry Pi and other ARM boards

The lower memory use may cost a little more CPU. On an x86 PC the difference is negligible, but on
ARM it has not been measured yet. If you run VisionState on a Raspberry Pi or another ARM board,
please share in [issue #52](https://github.com/oleost/VisionState/issues/52) how memory and CPU
compare with 0.9.2. Even rough numbers or "no difference I can see" help.

## 0.9.2

### Better

- **A counter's value is sent at its first reading**: new reading sensors no longer wait for two
  equal readings, which a counter in use seldom gives (a running tap published nothing until the
  water stopped). The checks on each reading still keep misreads out. Sensors you already have
  keep their setting.
- **A counter's rate shows its decimals in Home Assistant**: a slow flow such as 0.03 m³/h was
  shown as 0. A display precision you chose in Home Assistant still wins.
- **A meter that stands still no longer fills the review queue**: when the same reading is
  rejected again for the same reason (a water meter that is read lower than before, all night
  long), it waits in Review once instead of once per check. The item says how many more times it
  was read and when last; it is still counted in the Quality tab. A new value, or the same one
  after *Skip*, is queued as before.
- **Review shows a reading's region, large**: for reading sensors the Review page shows only the
  part of the frame that was read — the same cut as the *Last frame* image in Home Assistant — as
  large as fits, so the digits can be checked without opening anything, also on a phone. *Whole
  frame* shows all of it. The list beside it shows the whole region too.
- **Review one sensor at a time**: tap a sensor under *Waiting per sensor* on the Review page to
  see only its items — no more mixing up two sensors that look at the same spot. *All sensors*
  shows everything again. The choice stays after a reload, and the links to the review queue on
  a sensor's Quality tab open it for that sensor.

### Fixed

- Long camera names no longer run out of their boxes when picking the image source.
- The Live / History / Settings buttons of the sensor cards line up with the cards next to them.

## 0.9.1

### Better

- **Counters accept the value after a restart or update.** With *Learn it* or *Fixed rate* (below),
  how much a counter may go up grows with the time it was not seen — the app was away, or the
  readings were unsure — so the first reading after an update is no longer rejected as *changed
  too much*. A wheel read one digit too high over and over still stays out.
- **Choose how a counter's limit works** (Settings → Sensor output):
  - **Learn it** — how fast it goes up on a busy day, learned from a week of its readings. Until
    then a standard rate applies (for a water meter with three decimals, 0.04 m³ a minute), shown
    there and yours to change.
  - **Fixed rate** — a rise per hour that you set.
  - **Fixed per reading** — the largest rise from one reading to the next, the same however long
    the app was away.
  - **No limit.**

  Counters you already have keep the limit you set, as *Fixed per reading*: switch to *Learn it*
  or *Fixed rate* for the value after a restart to be accepted by itself.
- **Read correctly in Review moves the counter on**: answering it for the newest rejected reading
  makes that the value — the way on after a real jump over the limit (say the light failed during
  a shower), and the way out for a counter stuck on a value read too high.
- **Readings are held when the camera's picture changes size** (a camera that comes back from a
  restart with another resolution shows something else in the region): *problem* becomes
  `camera_changed`, Home Assistant gets a notification, and the sensor asks you to check the
  region — **The region is right** or **Adjust the region**. It reads again by itself when the
  size comes back.
- **Training images are kept in the app's own storage**, next to the database that lists them,
  so a Home Assistant backup of the app holds both. They were in the media folder, which the
  app's backups — also the one Home Assistant makes before every update — do not include: after a
  restore, the database could list images that were not there. The media folder is also shown to
  every Home Assistant user by the media browser. Your images are copied over by themselves after
  the update; the old copies in `/media/visionstate/samples` are removed 30 days later, so going
  back to the backup made before the update still works until then. Backups of the app get larger
  by the size of your training images (Settings → Storage shows it). History frames stay in the
  media folder.
- **A light waits 2 seconds before the frame by default** (was 1 second): an ESP32 camera at full
  size needs about a second per picture, and with 1 second the frame read was often dark. Sensors
  you already have keep their setting — raise *Wait before taking the frame* to 2 s if readings
  come out dark.
- **Export readings** also holds the rejected readings nobody answered, and each reading's
  picture size, so an export shows the sensor's checks in order.
- **Switching the face model shows its progress** on the sensors' cards, the Faces card, People and
  Settings, and the taught faces are learned again right after the new model is loaded.

### Fixed

- **A reading sensor with the unit `m3` now shows its value in Home Assistant.** Home Assistant
  only takes `m³` for water and gas, and dropped the whole value entity without a word — only the
  rate and the diagnostics appeared. VisionState now sends the unit the way Home Assistant writes
  it (`m3` → `m³`, `kwh` → `kWh`); a unit the chosen type does not take at all is sent without the
  type, so the entity still appears (not in the Energy dashboard; the app's log says so). Sensors
  whose entities already work are not changed.
- **An object sensor no longer stops when the image of a taught box is gone** (issue #45, thanks
  to @terryhonn for the report and @wzd8888 for the first fix). It showed *No such file or
  directory* on every check. A box, or a state sensor's training image, whose file is missing or
  unreadable is now left out — the log names it — and the sensor keeps working.
- **Deleting a training image, a taught box or a face no longer shows an error** when the file
  cannot be removed (in use, or no permission): it is gone from VisionState, and the log names the
  file.

## 0.9.0

### New

- **Recognise people by their face** on object sensors that look for Person. Off until you turn it
  on per sensor; the faces never leave your Home Assistant.
  - Name the people of your household by tapping them on the Live tab, in the history or in the
    review queue, or add photos from your phone on the new **People** page.
  - A person named on one camera is known on every camera, with their own sensor that says where
    they are now. **Unknown person** turns on for a clear face that matches nobody.
  - While someone walks up, the sensor looks for their face about every second, and when it is
    not sure who it is, it asks you in Review: each answer teaches it how that camera sees them.
  - Made for doorbells: faces in front of a bright sky are evened out before they are compared,
    and the Live tab shows whether your camera sees faces well enough.
  - Two face models: **SFace** (fine on a Raspberry Pi 4) and **AuraFace** (for a fast PC).
- **Larger AI models for fast PCs** under **Settings → AI model**: **D-FINE M** and **L** find more
  small and far-off objects, **DINOv2 base** tells similar states apart best. Compare every model's
  memory, speed and accuracy there, and delete downloaded ones you no longer use.
- **Teaching limits you can tune**, for all object sensors and per sensor.
- **The wizard warns about a small picture**, which may be the camera's low-resolution stream.

### Better

- **The History shows what you taught from it** (*Taught: Our car*).
- **"Last seen" survives an update or restart**: a sensor no longer says *Not seen yet* about
  something it has seen before.

### Fixed

- Boxes on the Live tab now always match the picture shown.
- Switching AI models gives the memory of the old model back without a restart.

## 0.8.0

### New

- **Wheel reader for mechanical counters.** Water and gas meters are now read wheel by wheel
  with a small model made for VisionState: it sees how far each wheel has turned, so a wheel
  half way between two digits no longer turns into a wrong digit. The wheels are read together,
  the way they turn (a wheel only moves on while the one to its right goes from 9 to 0), so a
  reading where they do not fit together is never chosen. On the shared images of a water meter
  with red wheels (thanks, @pedromfa — issue #40) it read all 17 right; PP-OCRv6 small read 15
  and the included tiny reader 8. It is included (2 MB, a few milliseconds per reading) and is
  used by every new mechanical counter. Counters you already have keep the text reader until you
  choose *Read with → Wheel reader* on their Settings tab; their Live tab points to it. **Settings
  → AI model** shows the wheel reader with its licence and source, and whether it is loaded. The
  model, its training code and where its data comes from are in `tools/wheelreader`.
- **Review reminder**: when frames have waited for review a long time, a notification shows up in
  Home Assistant — *There are 11 frames waiting for review in VisionState, the oldest for 8 days*
  — with a link to the app. It never comes just because a frame arrived: by default once the
  oldest frame has waited 7 days, and it goes away by itself once they are reviewed. Set it under
  **Settings → Review reminder**: after how many days, from how many frames, whether to remind
  again every few days (off by default) and whether to push it to a phone as well (off by
  default; any notify service, such as the Companion app). **Send a test** shows what it looks
  like. The review queue entity has a new attribute, `oldest_waiting_since`, for automations of
  your own.

### Better

- **Mechanical counters with coloured wheels** read more reliably with the text reader too. The
  red decimal wheels of a water meter were seen as light grey, and a red "9" was read as a "5";
  they are now read as dark as the black ones.
- **Export readings** (Quality tab of a reading sensor, was *Export checked readings*) gives more
  to help improve reading:
  - the accepted readings nobody checked come along too, marked as unchecked, so an export is no
    longer mostly the readings that were rejected;
  - as many readings as fit in 24 MB (about 1,000), so GitHub takes the file: the checked ones
    first, then the newest unchecked ones;
  - an optional **What meter is this?** text goes into the export, so it is not forgotten;
  - every reading says which reader read it and with which VisionState version, whether the
    picture was greyscale (infrared at night) and whether VisionState had the sensor's light on;
    for the wheel reader also where it saw each wheel and how sure it was.

### Fixed

- **Export readings** cut every image with the sensor's region as it is now, so after moving the
  camera or the region the older images showed the wrong part of the picture. Every reading now
  keeps the region it was read in, and the export, the review queue, the History and the Quality
  tab use it. Readings from before this version still use the current region (the export marks
  them `"region": "current"`).
- On a phone, a rejected reading in the History with a long reason (*not the number of digits
  the counter has*) made the page scroll sideways; the reason now wraps.

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
