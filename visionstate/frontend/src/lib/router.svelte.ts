// Hash-based router (works below the dynamic Ingress path). All app paths are defined in `paths`.
import type { SensorTab } from './ui';

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
  sensor: (id: number, tab: SensorTab = 'label') => `sensors/${id}/${tab}`,
  review: () => 'review',
  settings: () => 'settings',
};

export const href = (path: string) => `#/${path}`;

export function go(path: string): void {
  location.hash = href(path);
}
