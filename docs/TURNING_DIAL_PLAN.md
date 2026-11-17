# Turning dials: research and plan

A reading sensor that watches **one dial go round** and tells how fast something flows: the test
dial of a gas meter (½ or 2 ft³ a turn), the sweep hand of a water meter (often 10 gallons a turn),
the last pointer dial of a European water meter, later a star-shaped leak indicator or the disc of an
electricity meter. It gives the flow now (ft³/min, L/min, kW …) and whether it runs, not a meter
reading: the meter's own counter stays a mechanical counter ([`POINTER_DIAL_PLAN.md`](POINTER_DIAL_PLAN.md)).
It applies to the `reading` sensor kind. Asked for on the community forum (a US gas meter, 2026-11-14:
"just the 2 ft³ dial, revolutions per minute, for a flow rate"); planned with the maintainer on
2026-11-17.

## 1. What was asked, and what is proposed

**Asked for** (the maintainer): a sensor of its own, general rather than for one meter, with its
own settings (the amount per turn, the direction …), built into what VisionState already has —
reusing its parts, not copying them.

**Proposed here** (not asked for; each a decision of §3): its outputs (flow and *running*), the way
turns are counted, the faster checks while it turns, and the order of §6.

## 2. What we learned

**Meters that have such a dial** (2026-11-17):

| Meter | What turns | Per turn | Read by camera? |
|---|---|---|---|
| US gas meter (the forum's) | test dials "½ FOOT", "2 FEET": black needles with a counterweight | 0.5, 2 ft³ | yes |
| US water meter | the sweep hand across the whole face | 1, 10 or 100 gal or ft³ (mostly 10 gal) | yes |
| water meter | a leak indicator: a red star, triangle or gear | rarely printed | its movement; an angle only within one blade |
| European water meter | the last pointer dial (×0.0001 m³) | 0.1 L (decimal) | yes (red needles) |
| electricity meter (Ferraris) | a disc seen edge-on, one red or black mark | 1/75–1/600 kWh ("U/kWh", "rev/kWh"; US: "Kh", Wh a turn) | not shown: every project found uses an IR sensor |
| electricity meter (LED) | a blink, often 1000 per kWh | — | no: a blink of milliseconds falls between pictures |

- The amount a turn is printed three ways: an amount (2 ft³, 10 gal), turns per unit (150 U/kWh)
  and Kh (7.2 Wh a turn). The settings take both "a turn is" and "turns per".
- Dials turn either way, and on old meters every other one the other way.
- Many dials have print inside the circle ("FEET", "½ FOOT", "2"), as dark as a black needle.
- Gas fitters clock an appliance on the 2 ft³ dial (the ½ ft³ one is unreliable with a remote
  reading module): ft³/h = 3600 × ft³ a turn / seconds a turn.
- Home Assistant's `volume_flow_rate` takes m³/h, m³/min, m³/s, ft³/min, L/h, L/min, L/s, gal/h,
  gal/min, gal/d and mL/s — **not ft³/h**, so a gas dial reports ft³/min (Home Assistant can show it
  in another unit of its class).

**Pictures with a licence the tests may use** (Wikimedia Commons): *Gas meter indicator.jpg*
(public domain: the forum's layout — wheels with "00" painted after them, a ½ foot and a 2 feet dial
with black needles), *Drehstromzaehler-Obernjesa.jpg* (CC0: a Ferraris disc with its red mark,
150 U/kWh), *Mechanical electricity meter 1965 (1).jpg* (public domain: 250 revs/kWh, dials turning
in turn), *The South Eastern Electricity Board electricity meter.jpg* (CC0: 150 rev/kWh). None of a
US sweep hand or a leak star.

**The needle reader on a black needle** (`dials.read_needle` on *Gas meter indicator.jpg*, each dial
cut five times with its circle moved by up to a third of its radius): the ½ foot dial was read wrong
every time (about 1.7–2.6 where it points at 9.5), the 2 feet dial off by up to 1 in two of five.
Its dark path was made for red needles seen grey at night: it looks between 0.2 and 0.62 of the
radius only, so a long black needle loses its tip and its counterweight is what is left, and print
inside that ring counts as needle. Tried in a script, not built: the dark pixels of the whole face
(inside 0.9 of the radius), thin lines (the circle, its ticks) taken away by an opening, and only the
part joined to the middle kept (the needle with its hub; print is not joined to it) — 9.47–9.53 and
4.67–4.72 for all ten, as by eye.

## 3. Decisions (2026-11-17)

- **A sensor type of its own in the UI, a mode of the reading kind in the code.** *What does it look
  like?* gets a third choice, *Turning dial*, beside *Digital display* and *Mechanical counter*
  (`settings.READING_DISPLAYS` gets `dial`, `settings.READING_MODES` gets `flow`; one goes with the
  other). Region, straightening, Live, History, Review, Quality, export, backup, the settings page
  and the e2e seed are the reading sensor's; no fifth `settings.SENSOR_KINDS`.
- **The region is the dial.** The user draws the region round the dial (four corners when the camera
  sees it at an angle); the dial is the circle inside the straightened region. No dial editor.
- **The needle reader is improved for every dial, not copied.** The joined-to-the-middle reading of
  §2 becomes the dark path of `dials.read_needle`, so a mechanical counter's black dials gain too;
  the red path stays. The real dials in the tests must read as before.
- **Turns are counted between checks** (`turns.py`, pure logic): the direction is known, so a move of
  up to `TURNING["max_turn"]` of a turn between two checks is counted; more, or a gap in the checks,
  starts the count again and nothing is guessed. A move smaller than `TURNING["deadband"]` is no move
  (the last position stays, so a slow leak still adds up), a move back within it is jitter. A new
  position counts once the next reading agrees with it (it lies between the last counted one and the
  next): one misread needle never adds a turn. As two moves in a row are then judged together,
  `max_turn` is under half a turn.
- **Outputs:** the state is the flow now, over `flow_window_s`, in the flow unit of the dial's unit
  (`settings.READING_RATE_UNITS`, the table the counter's rate uses, which gains ft³, gal, CCF and
  MCF); `0` when it stands still. *Running* is a binary sensor (`running` device class). The needle's
  position is the raw reading (diagnostic). No total in phase 1: the meter's counter is its own sensor,
  and a total that loses a turn never finds it again.
- **It checks often while it turns.** A check costs no AI model (the needle is read in a few
  milliseconds); what costs is the camera's picture. Any move — one too far to count too — and the
  first reading start a burst (the trigger's `burst_interval_s` for `burst_duration_s`, as a trigger
  entity does); standing still, it checks at its interval. A dial that goes round a whole number of
  times between two idle checks looks still; the e2e test found it with 4 turns a minute and a 30 s
  interval, and a real dial seldom keeps such a speed.
- **History** keeps a row when *running* changes and every rejected reading (no needle, unsure,
  a jump the next reading did not confirm) for Review; *read_ok* / *misread* answer them and set the
  threshold advice, as for other readings. A flow is never anchored like a counter's value.

## 4. Settings

In the reading settings (`settings.READING_DEFAULTS`, export, import and backup carry them):
- `per_turn`: what one turn is, in `unit` (2 with ft³; 1/150 with kWh). The UI also takes "turns
  per unit" and Kh and stores the amount.
- `ccw`: the dial turns anticlockwise.
- `flow_window_s`: the flow is the turns over about this many seconds.
- `unit` and `device_class` as for every reading: the flow's unit and class come from the unit
  (`mqtt.rate_unit`).

Tunables in `settings.TURNING`: `max_turn`, `deadband`, `max_gap_s`, `min_span_s`. The needle
reader's own (`settings.READING["dial_dark_face"]` and the others beside it) are shared with a
counter's pointer dials.

## 5. Phase 2 and later (not built)

- **A total** (`ReadingWatch` can hold it across a restart without a new database version), for
  meters without a counter of their own.
- **A star or gear** (N blades: an angle within 1/N of a turn, so N times the checks), mostly for
  *running* as a leak warning.
- **The disc of an electricity meter**: the mark seen pass, about once a second at 3 kW and
  75 turns/kWh. Only with a meter to try it on.
- **Print taken away over time**: the dial changes only where the needle moves; a face kept from the
  last pictures would leave the needle alone. Only if the joined-part reader falls short.

## 6. Order and status

| Step | What | Status |
|---|---|---|
| 1 | The dark path of the needle reader (§2), the public-domain gas meter in the tests | built |
| 2 | Counting turns (`turns.py`), the flow, *running*, the engine, MQTT, Review | built |
| 3 | The UI: the type, its settings, the wizard; the fake camera's turning dial; e2e | built |
| 4 | Tried on real meters (the forum's, an export by day) | not started |

## 7. Risks and open questions

- Whether a camera gives pictures fast enough: a Home Assistant camera every 2 s while it turns is
  not measured.
- A dial whose needle hides under glare or a lens (the forum's meter has a dome over the hub).
- Night without a light: no needle, so nothing is counted and the flow stays as it was until the
  dial is seen again (*problem* says why). A flow sent as unknown instead is an option if users
  find the kept value misleading.
