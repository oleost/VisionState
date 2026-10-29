# Pointer dials: research and plan

Reading the small **pointer dials** many water meters have beside their counter (×0.1, ×0.01,
×0.001, ×0.0001 m³), and the **last wheel's fraction** on counters without them, so a reading
sensor gives litres instead of whole cubic metres. It applies to the `reading` sensor kind only,
mechanical counters. Asked for in issue #59 (a meter of five wheels and four dials). Researched and
decided with the maintainer on 2026-10-29; nothing of it is built yet (§8).

## 1. The idea in one paragraph

The counter stays exactly as it is: its region, straightening, the wheel reader. A mechanical
counter can add **pointer dials**: in the same picture, the user adds a small circle on each dial
(*+ Add dial*), the first one ×0.1, the next ×0.01 and so on. Each dial is read as a position from
0.0 to 9.9 by finding its needle, and the wheels and the dials are put together in **one** decode,
the one the wheel reader already does for its wheels (`readers.wheel_decode`): every wheel and dial
must agree with its neighbour, so a dial near 9.9 or 0.0 is settled by the dial to its right, and
the last wheel's digit by the ×0.1 dial. The value gets as many more decimals as there are dials,
and every check a counter has (never lower, a learned limit) applies to the total. Without dials,
the last wheel is no longer rounded up half way: its digit is the one it has passed, and its
fraction is one more decimal.

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
  counter without them reads as it does today (apart from phase 1).
- **One view, not a second picture.** The dials are on the same meter face as the wheels: the
  region editor shows both, with a switch *Edit: Counter | Dials* above the picture, so a drag never
  moves the wrong thing on a phone. The wizard asks *Has the meter pointer dials?* after the reading
  test and opens the same view; Settings has a *Pointer dials* card with *+ Add dial*.
- **Needles are read without a model first** (§5): a red or dark needle on a light dial is found by
  its colour and contrast, its angle from the centre gives the position. A model only if that is
  not good enough (phase 3).
- **One decode for wheels and dials**, not a threshold per transition (§5).
- **Dials are read in the straightened plane** of the counter's region (`imaging.region_image`, the
  straightening of issue #58): the dials lie in the same plane as the wheels, so the same transform
  makes a dial seen at an angle round and puts its 0 at the region's top. A dial needs *Straighten
  the region* on (it is on for a new reading sensor).
- **Phase 1 first**, on its own: it fixes problem 2 for every counter and gives one more decimal.

## 4. Phase 1: the last wheel's fraction

- The last wheel's digit is `floor` of its position, not rounded; its fraction (0.0–0.9) is one
  more decimal of the value, when the reader is sure enough of it (a setting, *Read the last
  wheel's fraction*, on for a new counter, like straightening).
- The fraction jitters by a tenth or two from reading to reading. A counter that "goes down" by
  less than that is the same reading, not a rejected one: the check that a counter never goes down
  takes a small tolerance on the last decimal, and what is published never goes down.
- The wheel reader's benchmark (`tools/wheelreader`) counts "right" within ±1 on the last wheel; it
  gets a second measure for the fraction (how far off, in tenths) before the setting is on by
  default.
- Docs: DOCS.md (*Reading sensors*), the readings export already holds the value with the fraction
  (`"wheels"` → `"value"`).

## 5. Phase 2: pointer dials

**Settings** (in a mechanical counter's reading settings, so they go with export, import and a
backup): a list of dials, each a circle in the straightened plane (centre and radius, as shares of
the region's size, so they move with the region) and a direction (clockwise by default); the
order gives the factor (×0.1 first). At most a few (a setting; four on the meter of issue #59).

**Reading a needle** (a function of the dial's picture, no model):
1. The dial is cut from the straightened plane, a little larger than its circle, and scaled to a
   fixed size.
2. The needle: red (hue and saturation) where there is colour; else the darkest elongated part
   against the dial's light face (an infrared camera at night sees a red needle grey).
3. The hub is the needle's mass nearest the centre; the tip the part farthest out. The angle from
   the centre to the tip, measured from the region's top and in the dial's direction, is the
   position (0.0–9.9).
4. Its certainty: how much needle was found and how clearly one direction stands out; the position
   becomes a probability over the decode's 100 bins (a narrow peak for a clear needle, wide for an
   unclear one).

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
decimals plus the dials); the confidence the decode's, with the dials' certainty in it; the
plausibility checks of a counter on the total. Nothing new is sent to Home Assistant: the same
value, finer (the rate entity gains the most). *What the reader sees* shows each dial with the
needle it found drawn on it and its position; History, Review and the readings export carry the
dials (a format version more).

**Interface.**
- Wizard: after the reading test of a mechanical counter, *Has the meter pointer dials?* → the same
  view switched to *Dials*.
- Settings, *Pointer dials* card: *+ Add dial* puts a circle on the picture, labelled with its
  factor (×0.1, ×0.01 …); drag it to move it, its handle to size it; per dial the direction and
  *Remove*. Touch-first, like the region editor; an e2e test for every gesture.
- Live: the dials in *What the reader sees*; a dial that could not be read is named.

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
| 1 | The last wheel's fraction, `floor` instead of rounding (§4) | planned |
| 2 | Drawn dials in the tests, the needle reader, the decode with *smooth* links (§5) | planned |
| 3 | Dials in the settings, the editor (*Edit: Counter \| Dials*, *+ Add dial*), the wizard question, Live, History, export (§5) | planned |
| 4 | A dial model, only if step 2 is not good enough (§6) | if needed |

## 9. References

- Issue #59 in this repository (the meter, the two problems).
- AI-on-the-edge-device: [parameters](https://jomjol.github.io/AI-on-the-edge-device-docs/Parameters/),
  [analog/digit transition on water meters](https://jomjol.github.io/AI-on-the-edge-device-docs/Watermeter-specific-analog---digit-transition/),
  [issue #4112](https://github.com/jomjol/AI-on-the-edge-device/issues/4112) (premature digit
  transitions; history-based reconciliation), and its analog model,
  [neural-network-analog-needle-readout](https://github.com/jomjol/neural-network-analog-needle-readout)
  (no licence found).
- [`WHEEL_READER_PLAN.md`](WHEEL_READER_PLAN.md): the wheel reader and its benchmark, which phase 1
  extends.
