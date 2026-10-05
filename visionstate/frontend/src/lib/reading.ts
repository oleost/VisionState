// Helpers for reading sensors: how a value and its unit are shown.
import type { Prediction, ReadingSettings, ReadingType, Sensor } from './types';

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
export const REJECT_REASONS: Record<string, string> = {
  'nothing read': 'no number found',
  unsure: 'the reader was unsure',
  'went down': 'a counter can not go down',
  'changed too much': 'it changed more than allowed',
  'wrong digit count': 'not the number of digits the counter has',
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

/** What a stored reading holds: the text read, its value and why it was rejected (null = accepted). */
export const readingDetail = (p: Prediction) =>
  p.probs as unknown as { text?: string; value?: string | null; reason?: string | null };

/**
 * What a value typed as the right one is saved as, or null when it is not a number. Mirrors
 * readers.right_value + format_value in the backend: with a point or comma as written, digits only
 * the way the meter shows them ("0629558" with 3 decimals → "629.558"), time left as minutes or h:mm.
 */
export function rightValue(text: string, settings: Pick<ReadingSettings, 'mode' | 'decimals'>): string | null {
  const t = text.trim();
  if (!t) return null;
  if (settings.mode === 'time_left') {
    const m = /^(\d+)(?::(\d{1,2}))?$/.exec(t);
    if (!m || (m[2] !== undefined && Number(m[2]) > 59)) return null;
    return String(m[2] === undefined ? Number(m[1]) : Number(m[1]) * 60 + Number(m[2]));
  }
  const n = /^\d+$/.test(t) ? Number(t) / 10 ** settings.decimals : Number(t.replace(',', '.'));
  return Number.isFinite(n) ? n.toFixed(settings.decimals) : null;
}

/** Share as a percentage with one decimal ("2.4 %"), or "—" without readings. */
export const rate = (part: number, whole: number) => (whole ? `${((part / whole) * 100).toFixed(1)} %` : '—');
