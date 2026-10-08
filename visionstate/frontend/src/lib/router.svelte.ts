// Hash-based router (works below the dynamic Ingress path). All app paths are defined in `paths`.
// A path may carry a query (`#/history?sensor=3`): `route.query` holds it, `setQuery` changes it.
import type { SensorKind } from './types';
import { TABS_BY_KIND, type SensorTab } from './ui';

function parse(): { parts: string[]; query: Record<string, string> } {
  const [path, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  return { parts: path.split('/').filter(Boolean), query: Object.fromEntries(new URLSearchParams(query)) };
}

export const route = $state(parse());

window.addEventListener('hashchange', () => {
  Object.assign(route, parse());
});

const withQuery = (path: string, query = '') => (query ? `${path}?${query}` : path);

export const paths = {
  dashboard: () => '',
  newSensor: () => 'sensors/new',
  /** Without a tab: the sensor's first tab (see TABS_BY_KIND). */
  sensor: (id: number, tab: SensorTab | '' = '') => (tab ? `sensors/${id}/${tab}` : `sensors/${id}`),
  sensorHome: (id: number, kind: SensorKind) => `sensors/${id}/${TABS_BY_KIND[kind][0]}`,
  review: () => 'review',
  /** `query`: a history filter (lib/history.ts toQuery). */
  history: (query = '') => withQuery('history', query),
  settings: () => 'settings',
};

export const href = (path: string) => `#/${path}`;

export function go(path: string): void {
  location.hash = href(path);
}

/** Change the query of the current page in place: no new step for the back button, no reload. */
export function setQuery(query: Record<string, string>): void {
  const search = new URLSearchParams(query).toString();
  history.replaceState(history.state, '', href(withQuery(route.parts.join('/'), search)));
  route.query = query;
}
