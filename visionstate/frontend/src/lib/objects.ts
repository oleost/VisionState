// Helpers for object sensors: display names and colours of object classes and own labels.
import { app } from './app.svelte';
import type { Detection, FaceProblem, OwnLabel, PhotoResult, Sensor } from './types';

/** What LabelPicker shows for object sensors: the detector's objects, popular ones first. */
export function objectPicker() {
  const config = app.config;
  return {
    labels: config?.object_labels ?? [],
    popular: config?.object_popular ?? [],
    max: config?.object_max_classes ?? 0,
    noun: 'objects',
    example: 'bicycle',
  };
}

/** LiveChips of an object sensor: each class and own label it reports, with its count. */
export function objectChips(sensor: Sensor) {
  const keys = objectKeys(sensor);
  const own = sensor.objects?.custom ?? [];
  return {
    chips: (sensor.objects?.live ?? []).map((o) => ({
      key: o.key,
      name: objectName(o.key, own),
      on: o.on,
      color: objectColor(keys, o.key),
      count: o.count,
    })),
    summary: objectSummary(sensor),
  };
}

/**
 * Display name of an object class ("traffic_light" → "Traffic light"), from the backend label
 * list, or of one of a sensor's own labels ("Our car") when `own` is given.
 */
export function objectName(key: string, own: OwnLabel[] = []): string {
  if (key === app.config?.face_unknown_key) return 'Unknown person';
  return (
    own.find((l) => l.key === key)?.name ??
    app.config?.object_labels.find((l) => l.key === key)?.name ??
    key.replace(/_/g, ' ')
  );
}

/** What a sensor reports, in order: its classes, then its own labels in use (and, recognising
 * faces, the unknown person). */
export function objectKeys(sensor: Pick<Sensor, 'objects'>): string[] {
  const objects = sensor.objects;
  if (!objects) return [];
  const unknown = facesOn(sensor) && app.config ? [app.config.face_unknown_key] : [];
  return [...objects.classes, ...activeLabels(sensor).map((l) => l.key), ...unknown];
}

/** Whether an object sensor recognises people by their face (switched on, and it looks for people). */
export const facesOn = (sensor: Pick<Sensor, 'objects'>) =>
  !!sensor.objects?.faces?.enabled && sensor.objects.classes.includes('person');

/** The people (shared by every sensor) among an object sensor's labels while it recognises faces. */
export const peopleOf = (sensor: Pick<Sensor, 'objects'>): OwnLabel[] =>
  (sensor.objects?.custom ?? []).filter((l) => l.face);

/** What adding photos did, for a toast: "2 faces added to Kari", and why a file was skipped. */
export function photoMessage(result: PhotoResult): string {
  const added = result.added
    ? `${result.added} ${result.added === 1 ? 'face' : 'faces'} added to ${result.name}`
    : 'No face added';
  const skipped = result.skipped.map((s) => (s.file ? `${s.file}: ${s.message}` : s.message));
  return [added, ...skipped].join(' · ');
}

/** Why the face of a person box cannot be used, in a few words. */
export const FACE_PROBLEMS: Record<FaceProblem, string> = {
  none: 'no face seen',
  cut: 'face cut off by the edge',
  small: 'face too small',
  turned: 'face turned away',
  blurry: 'face blurry',
  unsure: 'face unclear',
};

/** What the face search says about a person box, in a few words ("" when nothing was looked at). */
export function faceNote(d: Detection, own: OwnLabel[] = []): string {
  const face = d.face;
  if (!face) return '';
  if (face.problem) {
    const kept = face.kept && face.label ? `${objectName(face.label, own)} · ` : '';
    return `${kept}${FACE_PROBLEMS[face.problem]}`;
  }
  if (face.state === 'match' && face.label) return `${objectName(face.label, own)}${face.kept ? ' (still here)' : ''}`;
  if (face.state === 'pending') return 'checking who it is';
  if (face.state === 'unclear') return 'not sure who';
  if (face.state === 'ask' && face.candidate) return `maybe ${objectName(face.candidate, own)}`;
  return own.some((l) => l.face) ? 'someone you have not named' : 'face seen';
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
  if (name.endsWith('person')) return `${n} ${name.slice(0, -'person'.length)}people`; // "unknown people"
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

/** What a running object sensor that recognises faces waits for, as a chip ("" when ready, or when the
 * face model failed: its Faces card says why). It works without faces meanwhile. */
export function facesBusy(sensor: Pick<Sensor, 'kind' | 'enabled' | 'objects'>): string {
  const f = app.status?.faces;
  if (!f || f.error || !sensor.enabled || !isObjectSensor(sensor) || !sensor.objects?.faces?.enabled) return '';
  if (f.progress) return 'Downloading faces…';
  if (f.embedding) return `Learning faces… ${f.embedding.done}/${f.embedding.total}`;
  return f.loaded ? '' : 'Loading faces…';
}

/** Whether a box's label comes from its face: a person taught by face, or the unknown person. */
export const byFace = (label: string, own: OwnLabel[] = []) =>
  label === app.config?.face_unknown_key || own.some((l) => l.key === label && l.face);

/** The taught label of a box that is not what the detector said (from the backend config). */
export const noneLabel = () => app.config?.teach.none_label ?? 'none';

/** The name a box is shown with: its own label, else its class. */
export const boxName = (d: Detection, own: OwnLabel[] = []) => objectName(d.label ?? d.key, own);

/** Why a box was changed by what the sensor was taught, in a few words ("" when it was not). */
export function boxReason(d: Detection, own: OwnLabel[] = []): string {
  if (d.label && byFace(d.label, own)) {
    return d.label === app.config?.face_unknown_key
      ? 'A face this sensor does not know'
      : `Recognised by the face as ${objectName(d.label, own)}${d.face?.kept ? ' (still here, face not seen now)' : ''}`;
  }
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
