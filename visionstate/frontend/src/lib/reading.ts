// Helpers for reading sensors: how a value and its unit are shown.
import type { ReadingSettings, Sensor } from './types';

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
};

/**
 * Digits after a decimal point or comma in what the reader saw ("1234.5" → 1), or null when there is
 * none. The backend ignores dots (a stray one is a common misread), so the UI only suggests this.
 */
export function decimalsSeen(text: string | null | undefined): number | null {
  const match = /\d[.,](\d+)\s*$/.exec(text ?? '');
  return match ? match[1].length : null;
}

export const isReadingSensor = (sensor: Pick<Sensor, 'kind'>) => sensor.kind === 'reading';
