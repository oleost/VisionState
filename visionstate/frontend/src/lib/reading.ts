// Helpers for reading sensors: how a value and its unit are shown.
import { dateTime, plural } from './format';
import type { Prediction, ReadingSettings, ReadingType, Roi, Sensor } from './types';

/** The unit shown next to a value ("min" for time left). */
export function readingUnit(settings: Pick<ReadingSettings, 'mode' | 'unit'>): string {
  return settings.mode === 'time_left' ? 'min' : settings.unit;
}

/** "12345.6 kWh", "158 min" or "—" before the first accepted reading. */
export function readingText(sensor: Sensor): string {
  const r = sensor.reading;
  if (!r || r.value === null) return '—';
  const unit = readingUnit(r);
  return unit ? `${r.value} ${unit}` : r.value;
}

/** Why a reading was rejected, as a sentence fragment. */
/** The reason while a camera's picture has another size than the region was drawn on: nothing is read. */
export const CAMERA_CHANGED_REASON = 'camera changed';

export const REJECT_REASONS: Record<string, string> = {
  'nothing read': 'no number found',
  unsure: 'the reader was unsure',
  'went down': 'a counter can not go down',
  'changed too much': 'it changed more than allowed',
  'wrong digit count': 'not the number of digits the counter has',
  'camera changed': "the camera's picture changed size",
};

/** The type chosen in the reading editor: a mechanical counter or a digital display. */
export const readingType = (settings: Pick<ReadingSettings, 'display'>): ReadingType =>
  settings.display === 'counter' ? 'counter' : 'display';

/** Digits in what the reader saw ("0089.932" → 7). */
export const digitsSeen = (text: string | null | undefined): number => (text ?? '').replace(/\D/g, '').length;

/**
 * Digits after a decimal point or comma in what the reader saw ("1234.5" → 1), or null when there is
 * none. The backend ignores dots (a stray one is a common misread), so the UI only suggests this.
 */
export function decimalsSeen(text: string | null | undefined): number | null {
  const match = /\d[.,](\d+)\s*$/.exec(text ?? '');
  return match ? match[1].length : null;
}

export const isReadingSensor = (sensor: Pick<Sensor, 'kind'>) => sensor.kind === 'reading';

/**
 * What a stored reading holds: the text read, its value, why it was rejected (null = accepted), the
 * region it was read in (null = the whole frame; missing in readings from before 0.7.1b2) and, on a
 * rejected one waiting for review, how often the same reading came again since (not stored again).
 */
export const readingDetail = (p: Prediction) =>
  p.probs as unknown as {
    text?: string;
    value?: string | null;
    reason?: string | null;
    roi?: Roi | null;
    repeats?: number;
    last_seen?: string;
    /** The pointer dials it was read with. */
    dials?: unknown[];
  };

/** "Read the same 3 more times, last …" for a rejected reading that came again, or null. */
export function repeatedNote(p: Prediction): string | null {
  const { repeats, last_seen } = readingDetail(p);
  if (!repeats) return null;
  return `Read the same ${plural(repeats, 'more time')}${last_seen ? `, last ${dateTime(last_seen)}` : ''}.`;
}

/** The region a stored reading was read in, or the sensor's region for readings that did not keep it. */
export function readingRoi(p: Prediction, sensor: Pick<Sensor, 'roi'>): Roi | null {
  const detail = readingDetail(p);
  return detail.roi !== undefined ? detail.roi : sensor.roi;
}

/**
 * What a value typed as the right one is saved as, or null when it is not a number. Mirrors
 * readers.right_value + format_value in the backend: with a point or comma as written, digits only
 * the way the meter shows them ("0629558" with 3 decimals → "629.558"), time left as minutes or h:mm.
 */
export function rightValue(
  text: string,
  settings: Pick<ReadingSettings, 'mode' | 'decimals' | 'display' | 'counter_reader' | 'dials'>,
): string | null {
  const decimals = valueDecimals(settings);
  const t = text.trim();
  if (!t) return null;
  if (settings.mode === 'time_left') {
    const m = /^(\d+)(?::(\d{1,2}))?$/.exec(t);
    if (!m || (m[2] !== undefined && Number(m[2]) > 59)) return null;
    return String(m[2] === undefined ? Number(m[1]) : Number(m[1]) * 60 + Number(m[2]));
  }
  const n = /^\d+$/.test(t) ? Number(t) / 10 ** decimals : Number(t.replace(',', '.'));
  return Number.isFinite(n) ? n.toFixed(decimals) : null;
}

/** Digits a counter read by a wheel reader reads beyond its wheels: one per pointer dial
 * (settings.extra_digits). */
export function extraDigits(
  settings: Pick<ReadingSettings, 'display' | 'counter_reader' | 'dials'>,
): number {
  if (settings.display !== 'counter' || settings.counter_reader !== 'wheels') return 0;
  return settings.dials?.length ?? 0;
}

/** Decimals of the value: the counter's own and its extra digits (settings.value_decimals). */
export const valueDecimals = (
  settings: Pick<ReadingSettings, 'decimals' | 'display' | 'counter_reader' | 'dials'>,
) => settings.decimals + extraDigits(settings);

/** Share as a percentage with one decimal ("2.4 %"), or "—" without readings. */
export const rate = (part: number, whole: number) => (whole ? `${((part / whole) * 100).toFixed(1)} %` : '—');
