// Helpers for object sensors: display names and colours of object classes and own labels.
import { app } from './app.svelte';
import type { Detection, OwnLabel, Sensor } from './types';

/**
 * Display name of an object class ("traffic_light" → "Traffic light"), from the backend label
 * list, or of one of a sensor's own labels ("Our car") when `own` is given.
 */
export function objectName(key: string, own: OwnLabel[] = []): string {
  return (
    own.find((l) => l.key === key)?.name ??
    app.config?.object_labels.find((l) => l.key === key)?.name ??
    key.replace(/_/g, ' ')
  );
}

/** What a sensor reports, in order: its classes, then its own labels in use. */
export function objectKeys(sensor: Pick<Sensor, 'objects'>): string[] {
  const objects = sensor.objects;
  if (!objects) return [];
  return [...objects.classes, ...activeLabels(sensor).map((l) => l.key)];
}

/** Own labels whose object is still selected (the others are kept but not used). */
export function activeLabels(sensor: Pick<Sensor, 'objects'>): OwnLabel[] {
  const objects = sensor.objects;
  return objects ? objects.custom.filter((l) => objects.classes.includes(l.parent)) : [];
}

/** Colour of a class or own label within a sensor: its position in `keys` picks a palette colour. */
export function objectColor(keys: string[], key: string): string {
  const palette = app.config?.state_palette ?? ['#7ee2b8'];
  const index = keys.indexOf(key);
  return palette[(index < 0 ? keys.length : index) % palette.length];
}

const IRREGULAR_PLURALS: Record<string, string> = {
  person: 'people',
  mouse: 'mice',
  knife: 'knives',
  sheep: 'sheep',
  skis: 'skis',
};

/** "1 person", "3 people", "2 buses" — lower case, for sentences. Own labels keep their case ("1 Rex"). */
export function objectCount(n: number, key: string, own: OwnLabel[] = []): string {
  const label = own.find((l) => l.key === key);
  const name = label ? label.name : objectName(key).toLowerCase();
  if (n === 1 || label) return `${n} ${name}`;
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
  const own = sensor.objects?.custom ?? [];
  const on = (sensor.objects?.live ?? []).filter((o) => o.on);
  if (!on.length) return 'Nothing detected';
  const text = on.map((o) => objectCount(o.count, o.key, own)).join(', ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** The taught label of a box that is not what the detector said (from the backend config). */
export const noneLabel = () => app.config?.teach.none_label ?? 'none';

/** The name a box is shown with: its own label, else its class. */
export const boxName = (d: Detection, own: OwnLabel[] = []) => objectName(d.label ?? d.key, own);

/** Why a box was changed by what the sensor was taught, in a few words ("" when it was not). */
export function boxReason(d: Detection, own: OwnLabel[] = []): string {
  if (d.filtered) {
    const taughtAs = d.match?.label;
    return taughtAs && taughtAs !== noneLabel()
      ? `Filtered away: looks like a box you taught as ${objectName(taughtAs, own)}, which this sensor does not look for`
      : `Filtered away: looks like a box you marked as not a ${objectName(d.key).toLowerCase()}`;
  }
  if (d.rescued) return `Counted although the AI was unsure: looks like a ${boxName(d, own)} you showed it`;
  if (d.was) return `The AI said ${objectName(d.was).toLowerCase()}; it looks like a box you taught as ${boxName(d, own)}`;
  if (d.label) return `Looks like the box you taught as ${boxName(d, own)}`;
  return '';
}
