// Helpers for object sensors: display names and colours of object classes.
import { app } from './app.svelte';
import type { Sensor } from './types';

/** Display name of an object class ("traffic_light" → "Traffic light"), from the backend label list. */
export function objectName(key: string): string {
  return app.config?.object_labels.find((l) => l.key === key)?.name ?? key.replace(/_/g, ' ');
}

/** Colour of a class within a sensor: its position in the sensor's classes picks a palette colour. */
export function objectColor(classes: string[], key: string): string {
  const palette = app.config?.state_palette ?? ['#7ee2b8'];
  const index = classes.indexOf(key);
  return palette[(index < 0 ? classes.length : index) % palette.length];
}

const IRREGULAR_PLURALS: Record<string, string> = {
  person: 'people',
  mouse: 'mice',
  knife: 'knives',
  sheep: 'sheep',
  skis: 'skis',
};

/** "1 person", "3 people", "2 buses" — lower case, for sentences. */
export function objectCount(n: number, key: string): string {
  const name = objectName(key).toLowerCase();
  if (n === 1) return `1 ${name}`;
  let many = IRREGULAR_PLURALS[key];
  if (!many) {
    if (/(s|sh|ch|x)$/.test(name)) many = `${name}es`;
    else if (/[^aeiou]y$/.test(name)) many = `${name.slice(0, -1)}ies`;
    else many = `${name}s`;
  }
  return `${n} ${many}`;
}

export const isObjectSensor = (sensor: Pick<Sensor, 'kind'>) => sensor.kind === 'objects';

/** "2 people, 1 dog" for what an object sensor currently reports; "Nothing detected" when all are off. */
export function objectSummary(sensor: Sensor): string {
  const on = (sensor.objects?.live ?? []).filter((o) => o.on);
  if (!on.length) return 'Nothing detected';
  const text = on.map((o) => objectCount(o.count, o.key)).join(', ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}
