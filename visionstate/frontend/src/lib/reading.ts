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

export const isReadingSensor = (sensor: Pick<Sensor, 'kind'>) => sensor.kind === 'reading';
