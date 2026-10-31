# Pointer dials: research and plan

Reading the small **pointer dials** many water meters have beside their counter (×0.1, ×0.01,
×0.001, ×0.0001 m³), so a reading sensor gives litres instead of whole cubic metres. It applies to the `reading` sensor kind only,
mechanical counters. Asked for in issue #59 (a meter of five wheels and four dials). Researched and
decided with the maintainer on 2026-10-29; phase 2 is built, not yet tried on a real meter (§8,
and what is left in §10); phase 1 was built and taken out again (§4).

## 1. The idea in one paragraph

The counter stays exactly as it is: its region, straightening, the wheel reader. A mechanical
counter can add **pointer dials**: in the same picture, the user adds a small circle on each dial
(*+ Add dial*), the first one ×0.1, the next ×0.01 and so on. Each dial is read as a position from
0.0 to 9.9 by finding its needle, and the wheels and the dials are put together in **one** decode,
the one the wheel reader already does for its wheels (`readers.wheel_decode`): every wheel and dial
must agree with its neighbour, so a dial near 9.9 or 0.0 is settled by the dial to its right, and
the last wheel's digit by the ×0.1 dial. The value gets as many more decimals as there are dials,
and every check a counter has (never lower, a learned limit) applies to the total. Without dials,
the last wheel is rounded to the nearest digit, as before.

## 2. What we learned

**The meter in issue #59** (a photo in its thread): five wheels for whole cubic metres and four
dials around the face, ×0.1 on the right, ×0.01 below it, ×0.001 at the bottom and ×0.0001 on the
left — not in a row, each its own size. The needles are short red arrows on a red hub, the scales
clockwise with 0 at the top. The last wheel turns all the time with the ×0.1 dial: at about .3 it
already shows the next digit coming. The dials are about 60 pixels across in that picture.

**The user's two problems**, which are not the same:
1. *Resolution:* whole cubic metres; at about 6 m³ a month a new value comes every five days, and
   may land in the next month.
2. *"The last digit advances too early" (at .5 or .7):* our own rounding. The wheel reader knows
   how far the last wheel has turned (0.0–9.9) and makes it a digit with `floor(value + 0.5)` in
   `WheelReader.read_counter`: half way, it is read as the next digit. Reading a meter is the other
   way round: the digit a wheel has passed counts until it has fully turned.

**Other meters.** European water meters often have three or four dials (×0.1 down to ×0.0001),
placed differently on every make, mostly clockwise. Older electricity and gas meters have rows of
dials turning in turn clockwise and anticlockwise. The usual way to read them: the number the
needle has passed, and when it stands on a number, the dial to its right decides. So dials must be
placed freely, one by one, with a direction each.

**How AI-on-the-edge-device does it** (the ESP32 project many use for meters, read 2026-10-29):
digit and analog regions of their own; the analog pointers read by a small model (`ana-cont`, a
value 0–9.99 from a 32×32 picture, trained on more than 2,800 pictures of the author's cameras);
digits and pointers put together per picture with one threshold, `AnalogToDigitTransitionStart`
(9.2 by default, 6–8 for meters whose last digit turns early). Its issue #4112 shows the weak
point: decided per picture without history, a premature digit is accepted on early-turning meters;
an opt-in history-based reconciliation was added later. No licence was found for the model or its
pictures, so neither is used here (CLAUDE.md: test images and model data need a licence that fits).

## 3. Decisions (2026-10-29)

- **The counter is not changed.** Dials are an optional addition to a mechanical counter; a
  counter without them reads as it does today.
- **One view, not a second picture.** The dials are on the same meter face as the wheels: the
  region editor shows both, with a switch *Edit: Counter | Dials* above the picture, so a drag never
  moves the wrong thing on a phone. The wizard asks *Has the meter pointer dials?* after the reading
  test and opens the same view; Settings has a *Pointer dials* card with *+ Add dial*.
- **Needles are read without a model first** (§5): a red or dark needle on a light dial is found by
  its colour and contrast, its angle from the centre gives the position. A model only if that is
  not good enough (phase 3).
- **One decode for wheels and dials**, not a threshold per transition (§5).
- **Dials are read in the plane the counter is read in** (`imaging.reading_plane`): the region
  straightened (issue #58) when *Correct rotation and perspective* is on, else its box. The dials lie in the
  same plane as the wheels, so the same transform makes a dial seen at an angle round and puts its
  0 at the region's top.
- **Dials are placed on the straightened meter face, not on the camera picture** (decided while
  building it): the editor shows the plane around the region (`imaging.meter_face`), so a circle
  drawn there is the dial as it is read. Placed on the camera picture, a dial seen at an angle
  would be an ellipse, and a region's width and height come from its corners only (the mean of
  opposite edges): positions taken from anything else than that plane land beside the dial.
- **Phase 1 first**, on its own: it was to fix problem 2 for every counter and give one more
  decimal. Taken out again (§4).

## 4. Phase 1: the last wheel's fraction (taken out)

Built for 0.12.0b1 and taken out again in 0.12.0b2 (the maintainer's call, 2026-10-29): the last
wheel's digit as `floor` of its position instead of rounded, and its tenth as one more digit
(`reading["fraction"]`, on for new counters). Why it went:

- The meter of issue #59 has dials, and the ×0.1 dial settles the last wheel better (§5); with
  dials the fraction was not used.
- Without dials, `floor` is a whole digit off whenever the reader puts a wheel at rest a little
  below its digit (4 seen as 3.95 reads 3), and wheels at rest sit off their digit each its own
  way on real meters (`WHEEL_READER_PLAN.md`). Rounding is wrong by one only half way, while the
  wheel turns, and *settling* already covers that.
- It was turned on without the benchmark measure it needed, and its tenth jittered.

A sensor that stored `fraction` reads as one without it (`settings.extra_digits`). Kept from it:
how many digits a reading has is `settings.extra_digits` / `value_decimals` (now the dials), used by
`parse`, `format_value`, the digit-count check, Home Assistant's display precision and the rate's
precision; a reading up to `READING["settle_steps_fine"]` steps below the value is *settling*
(`readers.settling`), as the last dial's digit jitters. Coming back to it needs the fraction
measured on the shared real meters first (`tools/wheelreader`).

## 5. Phase 2: pointer dials

**Settings** (in a mechanical counter's reading settings, so they go with export, import and a
backup): `reading["dials"]`, a list of circles in the plane the counter is read in (centre and
radius in units of the region's height, from its top-left corner, so they move with the region)
and a direction (`ccw`, clockwise by default); the order gives the factor (×0.1 first). At most
`READING["max_dials"]`, within `dial_radius` and `dial_reach` (`DialIn` in `api/common.py`).

**Reading a needle** (`dials.read_needle`, no model):
1. The dial is cut from the plane, `dial_margin` larger than its circle, and scaled to `dial_size`
   pixels (`dials.read_needles`).
2. The needle: pixels inside the numbers that are `dial_red_contrast` redder than the dial's face
   (red less the larger of green and blue, against the face's median), so a pale needle in a
   picture with a colour cast counts as well as a bright red one — the first real meter (issue
   #59) had pale pink, drop-shaped needles and a greenish picture, and a fixed threshold found
   none of them. Where there is no red (an infrared camera at night sees a red needle grey), the
   pixels darker than half way between the darkest and the face, between the hub and the numbers
   (which are dark too).
3. Its hub (`dials._hub`): the middle of the needle's thick part, what is left with
   `dial_hub_erode` peeled off its edges — a hub, or a drop's round body. The needle is looked for
   again round it, so a circle placed up to half a radius beside the dial reads the same; a thin
   needle without a hub seen turns round the circle's centre.
4. Its direction: the mean direction from the hub of its `dial_tip_share` pixels farthest from the
   hub (the tip), so neither the hub, a drop's body nor the short tail turns it. 0 at the top, in
   the dial's direction: the position (0.0–9.9).
5. Its certainty: how much the tip's pixels agree on one direction (from `dial_spread_floor`,
   none, to 1); the position becomes a wrapped Gaussian over the decode's 100 bins, `dial_sigma_bins`
   wide (narrow for a clear needle). Less needle than `dial_min_needle` of the dial: not read (an
   even distribution, *No needle found*).

**Putting it together.** `readers.wheel_decode` finds the most likely value of all wheels at
once, right to left, with one rule between neighbours: a wheel stands on its digit and only turns
while the wheel to its right goes from 9 to 0. Dials are added as more "wheels" to the right of the
last wheel, and the decode gets a second kind of link between neighbours:
- *step* (as today, between the wheels): stands on its digit, turns while its neighbour passes 9;
- *smooth* (from the last wheel to the ×0.1 dial, and between the dials): its position is its
  digit plus its right neighbour's position / 10.

So the last wheel's digit is decided by the ×0.1 dial, a dial at 9.9 or 0.0 by the dial to its
right — the way a person reads a meter, done for all dials together. A meter whose last wheel turns
early (from 7 instead of 9, as #4112 describes) still decodes right while the wheel is less than
half a digit off what the dial says; how early a meter's wheel turns could later be learned from
its own readings, as a counter's change limit is.

**What it gives.** The value with as many more decimals as there are dials (the counter's
decimals plus the dials); the confidence the wheels' (as before) times the weakest dial's share
(its needle's probability within `wheel_weakest_bins` of where the value puts it — a needle is
never as sharp as a wheel the model knows, so not its probability at one position); the
plausibility checks of a counter on the total. Nothing new is sent to Home Assistant: the same
value, finer (the rate entity gains the most). *What the reader sees* shows each dial with the
needle it found drawn on it and its position; Review and Quality show the same picture for a stored
reading (made again from its frame, `GET /history/{id}/seen`), History and the readings export
carry the dials (a format version more).

**Interface** (`DialsEditor.svelte`, on the meter face `POST /preview/read` returns: `face`,
`face_box`, `region_width`, and each dial's needle).
- Wizard: a mechanical counter read by the wheel reader gets *Edit: Counter | Dials* above the test
  picture; *Counter* is the region editor, *Dials* the dial editor, with the hint *Has the meter
  pointer dials?* while there are none.
- Settings, *Pointer dials* card (reads a fresh frame with the settings being edited, *Read now*):
  *Add dial* puts a circle beside the last one (the first below the counter), labelled with its
  factor (×0.1, ×0.01 …); drag it to move it, its handle to size it; per dial where its needle was
  found, *Clockwise / Anticlockwise* and remove. Touch-first, like the region editor.
- Live: the dials in *What the reader sees*, after the wheels, each with the needle it found drawn
  on it.

**Tests.** Dials drawn by our own code beside the drawn counter (`visionstate/backend/tests/displays.py`):
clockwise and anticlockwise, a needle between two numbers and near 0, a red needle in greyscale (a
night camera), seen at an angle and turned; the fake camera serves a counter with dials. A test per
link rule of the decode (a dial at 9.9 settled by the next, the last wheel settled by the ×0.1
dial, an early-turning wheel).

## 6. Phase 3: a dial model (only if needed)

If needles are not read well enough without a model — night pictures, black needles, glare — a
small dial reader like the wheel reader: a position distribution from the dial's picture, trained on
drawn dials and pictures whose licence fits, measured on the same benchmark, published and pinned
like the wheel readers (`readers.json`). The decode and the interface stay the same.

## 7. Risks and open questions

- **Small dials.** About 60 pixels across in the issue's picture: enough for a needle, not for much
  less. DOCS.md says how close the camera should be.
- **Night.** A red needle under infrared is grey; the dark-needle path has to carry it, or phase 3.
- **How a meter's last wheel moves.** Smooth with the ×0.1 dial on the meter of issue #59; on
  others it may snap. The link from the last wheel to the first dial may need to be *step* for
  them — a setting, or learned from the readings.
- **More to set up.** Every dial is a circle to place; the defaults (size, direction) must make the
  usual meter a matter of four taps.
- **Exports from users** would settle most of these: a readings export (Quality tab) and a picture
  by day and by night from the meter of issue #59.

## 8. Order and status

| Step | What | Status |
|---|---|---|
| 1 | The last wheel's fraction, `floor` instead of rounding (§4) | taken out |
| 2 | Drawn dials in the tests, the needle reader, the decode with *smooth* links (§5) | **built** (not released) |
| 3 | Dials in the settings, the editor (*Edit: Counter \| Dials*, *Add dial*), Live, History, export (§5) | **built** (not released) |
| 4 | Tried on real meters, by day and at night, and on a phone (§10) | next |
| 5 | A dial model, only if step 4 shows the needle reader is not good enough (§6) | if needed |

## 9. References

- Issue #59 in this repository (the meter, the two problems).
- AI-on-the-edge-device: [parameters](https://jomjol.github.io/AI-on-the-edge-device-docs/Parameters/),
  [analog/digit transition on water meters](https://jomjol.github.io/AI-on-the-edge-device-docs/Watermeter-specific-analog---digit-transition/),
  [issue #4112](https://github.com/jomjol/AI-on-the-edge-device/issues/4112) (premature digit
  transitions; history-based reconciliation), and its analog model,
  [neural-network-analog-needle-readout](https://github.com/jomjol/neural-network-analog-needle-readout)
  (no licence found).
- [`WHEEL_READER_PLAN.md`](WHEEL_READER_PLAN.md): the wheel reader and its benchmark.

## 10. What is built, and what is left

Built, phase 2 of §5 (on top of the straightening of issue #58): settings and API (`settings.py`, `DialIn` in `api/common.py`, `/config` → `reading_dials`), the
needle reader (`dials.py`), the joint decode (`readers.wheel_decode` links, `read_counter`), the
plane and meter face (`imaging.Plane`, `reading_plane`, `meter_face`), the engine
(`engine/models.read_number` reads the needles from the frame before the region is straightened),
history rows with the dials as read (`probs["dials"]`), the readings export (format 7: each dial's
picture), MQTT precision, the UI (`DialsEditor.svelte`, the wizard switch, the Settings card). Tests: `tests/test_dials.py` (decode links, needles
clockwise and anticlockwise, greyscale, no needle, a drawn meter turned and at an angle, the
preview and a sensor end to end with History and export), the e2e test *a water meter with pointer
dials* (every gesture, on desktop, Pixel and iPhone), and `scripts/fake_camera.py` `/meter.jpg`
and `/meter-region.json` (a drawn meter with four dials, `&turn`, `&tilt`, `&ccw`).

Left before a release (for whoever takes it on):
- **More real meters.** One real meter is in the tests (`tests/assets/meter_dials.jpg`, pale drops,
  daylight; read as by eye, also with the circles placed beside the dials), and the photo in issue
  #59 (thin red needles on red hubs, not CC0: tried locally only) reads right too. More exports,
  by day and by night, from other meters: are the needles found under glass with glare, is the
  confidence sensible, does the last wheel agree with the ×0.1 dial?
- **Night pictures.** The dark-needle path is tested on a drawn dial made grey; a real infrared
  picture has glare and a dark rim. If it fails, that is the case for phase 3 (§6).
- **By hand on a phone** (the UI routine in CLAUDE.md): placing four dials on a real meter face at
  390 px, and how small the face gets when the region is small in the picture.
- **A meter whose last wheel snaps** instead of turning with the ×0.1 dial (§7): the *smooth* link
  from the last wheel to the first dial may then read it a digit off near the turn; a setting or a
  learned link if a real meter shows it.
- Answer issue #59 at release (what to set: the dials, *Correct rotation and perspective*).
