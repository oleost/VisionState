// Sound sensors: names, colours and what the sensor hears now.
import { app } from './app.svelte';
import type { Sensor } from './types';

export const isSoundSensor = (s: Sensor) => s.kind === 'sound';

/** A sound's name: the model's, or the sensor's own sound's. */
export function soundName(key: string, sensor?: Sensor | null): string {
  const own = sensor?.sound?.custom.find((c) => c.key === key);
  return own?.name ?? app.config?.sounds.find((s) => s.key === key)?.name ?? key;
}

/** A colour per sound, in the order the sensor lists them (from the state palette). */
export function soundColor(sensor: Sensor, key: string): string {
  const palette = app.config?.state_palette ?? [];
  const index = (sensor.sound?.live ?? []).findIndex((s) => s.key === key);
  return palette.length ? palette[Math.max(0, index) % palette.length] : 'var(--c-accent)';
}

/** What a sound sensor hears now, in a few words (dashboard and header). */
export function soundSummary(sensor: Sensor): string {
  const heard = (sensor.sound?.live ?? []).filter((s) => s.on).map((s) => s.name);
  if (heard.length) return heard.join(', ');
  if (sensor.live.available === false) return 'No sound';
  return 'Quiet';
}

/** A level in dBFS, as shown (rounded). */
export const db = (value: number | null | undefined) => (value == null ? '—' : `${Math.round(value)} dB`);
