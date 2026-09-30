// Hash-based router (works below the dynamic Ingress path). All app paths are defined in `paths`.
import type { SensorKind } from './types';
import { TABS_BY_KIND, type SensorTab } from './ui';

function parse(): string[] {
  return location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
}

export const route = $state({ parts: parse() });

window.addEventListener('hashchange', () => {
  route.parts = parse();
});

export const paths = {
  dashboard: () => '',
  newSensor: () => 'sensors/new',
  /** Without a tab: the sensor's first tab (see TABS_BY_KIND). */
  sensor: (id: number, tab: SensorTab | '' = '') => (tab ? `sensors/${id}/${tab}` : `sensors/${id}`),
  sensorHome: (id: number, kind: SensorKind) => `sensors/${id}/${TABS_BY_KIND[kind][0]}`,
  review: () => 'review',
  settings: () => 'settings',
};

export const href = (path: string) => `#/${path}`;

export function go(path: string): void {
  location.hash = href(path);
}
