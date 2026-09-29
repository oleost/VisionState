// UI constants: polling intervals, labels and tones. Change behaviour of the UI here.
import type { SensorStatus } from './types';

/** Refresh intervals in milliseconds. */
export const POLL = {
  status: 10_000,
  dashboard: 5_000,
  sensor: 2_000,
  liveFrame: 2_000,
  thumbnail: 15_000,
};

export const TOAST_MS = 5_000;

export type Tone = 'ok' | 'warn' | 'danger' | 'muted' | 'info';

/** CSS colour for each tone (see tokens.css). */
export const TONE_COLOR: Record<Tone, string> = {
  ok: 'var(--c-accent)',
  warn: 'var(--c-warn)',
  danger: 'var(--c-danger)',
  muted: 'var(--c-muted)',
  info: 'var(--c-info)',
};

export const SENSOR_STATUS: Record<SensorStatus, { label: string; tone: Tone }> = {
  ok: { label: 'Good', tone: 'ok' },
  untrained: { label: 'Needs training', tone: 'warn' },
  unavailable: { label: 'Camera unavailable', tone: 'danger' },
  disabled: { label: 'Paused', tone: 'muted' },
};

export const REVIEW_REASONS: Record<string, { label: string; tone: Tone; help: string }> = {
  low_confidence: { label: 'Low confidence', tone: 'warn', help: 'The AI was unsure about this frame.' },
  flip: { label: 'Flip-flopping', tone: 'info', help: 'The state changed several times in a short period.' },
  spot_check: { label: 'Spot check', tone: 'muted', help: 'A random confident frame, to catch silent mistakes.' },
};

export const SAMPLE_ORIGINS: Record<string, string> = {
  snapshot: 'Snapshot',
  upload: 'Upload',
  video: 'Video frame',
  review: 'Review',
  import: 'Import',
};

export const SENSOR_TABS = [
  { id: 'label', label: 'Label' },
  { id: 'upload', label: 'Upload' },
  { id: 'dataset', label: 'Dataset' },
  { id: 'quality', label: 'Quality' },
  { id: 'history', label: 'History' },
  { id: 'settings', label: 'Settings' },
] as const;
export type SensorTab = (typeof SENSOR_TABS)[number]['id'];

/** Domains listed first when picking trigger entities (the most useful triggers). */
export const TRIGGER_DOMAINS_FIRST = ['binary_sensor', 'cover', 'switch', 'input_boolean', 'lock', 'light', 'button', 'sensor'];
export const ENTITY_SEARCH_LIMIT = 50;
/** Mirrors ENTITY_ID in the backend (api/common.py). */
export const ENTITY_ID_PATTERN = /^[a-z0-9_]+\.[a-z0-9_]+$/;

export const TRIGGER_SOURCES: Record<string, string> = {
  entity: 'Entity changed',
  change: 'Image changed',
};

export const TIMELINE_HOURS = 24;
export const DATASET_PAGE_SIZE = 120;
