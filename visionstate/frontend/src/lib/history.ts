// The history filter as a page keeps it (in its URL, so it can be shared and survives a reload)
// and as the API takes it (lib/api.ts history).
import { stateInfo } from './app.svelte';
import { objectName } from './objects';
import type { HistoryFilter, Sensor } from './types';

export interface HistoryView {
  sensor: number[];
  key: string[];
  event: string[];
  waiting: boolean;
  /** '' = any time; a number = the last that many hours; 'custom' = from `since` to `until`. */
  window: string;
  /** Local date and time as a datetime-local input holds it ("2026-10-08T14:00"). */
  since: string;
  until: string;
  order: 'newest' | 'oldest';
}

const list = (value: string | undefined) => (value ? value.split(',').filter(Boolean) : []);

export function fromQuery(query: Record<string, string>): HistoryView {
  return {
    sensor: list(query.sensor).map(Number).filter(Number.isInteger),
    key: list(query.key),
    event: list(query.event),
    waiting: query.waiting === '1',
    window: query.window ?? '',
    since: query.since ?? '',
    until: query.until ?? '',
    order: query.order === 'oldest' ? 'oldest' : 'newest',
  };
}

/** The view as URL query values; defaults are left out. Keys are slugs, so a comma separates them. */
export function toQuery(view: HistoryView): Record<string, string> {
  const query: Record<string, string> = {};
  if (view.sensor.length) query.sensor = view.sensor.join(',');
  if (view.key.length) query.key = view.key.join(',');
  if (view.event.length) query.event = view.event.join(',');
  if (view.waiting) query.waiting = '1';
  if (view.window) query.window = view.window;
  if (view.window === 'custom' && view.since) query.since = view.since;
  if (view.window === 'custom' && view.until) query.until = view.until;
  if (view.order !== 'newest') query.order = view.order;
  return query;
}

export const queryString = (view: Partial<HistoryView>) =>
  new URLSearchParams(toQuery({ ...fromQuery({}), ...view })).toString();

function localToIso(local: string): string | undefined {
  const date = local ? new Date(local) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toISOString() : undefined;
}

/** The filter for the API; "the last hours" count back from `now`. */
export function toFilter(view: HistoryView, now = Date.now()): HistoryFilter {
  const filter: HistoryFilter = {};
  if (view.sensor.length) filter.sensor = view.sensor;
  if (view.key.length) filter.key = view.key;
  if (view.event.length) filter.event = view.event;
  if (view.waiting) filter.waiting = true;
  if (view.window === 'custom') {
    filter.since = localToIso(view.since);
    filter.until = localToIso(view.until);
  } else if (Number(view.window) > 0) {
    filter.since = new Date(now - Number(view.window) * 3_600_000).toISOString();
  }
  return filter;
}

/** "Last hour", "Last 24 hours", "Last 7 days". */
export function windowLabel(hours: number): string {
  if (hours === 1) return 'Last hour';
  if (hours % 24 === 0 && hours > 24) return `Last ${hours / 24} days`;
  return `Last ${hours} hours`;
}

/** The name of a key on a sensor: a state, an object class or an own label. */
export function keyName(sensor: Sensor | undefined, key: string): string {
  if (sensor?.kind === 'objects') return objectName(key, sensor.objects?.custom ?? []);
  if (sensor) return stateInfo(sensor, key).name;
  return objectName(key);
}
