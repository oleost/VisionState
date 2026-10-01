// UI constants: polling intervals, labels and tones. Change behaviour of the UI here.
import type { ReadingDisplay, ReadingMode, SensorKind, SensorStatus } from './types';

/** Refresh intervals in milliseconds. */
export const POLL = {
  status: 10_000,
  dashboard: 5_000,
  sensor: 2_000,
  liveFrame: 2_000,
  thumbnail: 15_000,
};

export const TOAST_MS = 5_000;
/** At most this many toasts on screen; older ones make room (labelling fast stacked them over the page). */
export const TOAST_MAX = 2;

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

/**
 * Review rule fields as shown in the UI. `scale` converts the stored value to the displayed one
 * (fractions → %, seconds → minutes). Limits come from the backend config.
 */
export const REVIEW_FIELDS = [
  { key: 'below', label: 'Send to review when the AI is less sure than', unit: '%', scale: 100, step: 1 },
  { key: 'cooldown_s', label: 'At most one review per sensor every', unit: 'min', scale: 1 / 60, step: 1 },
  { key: 'flip_limit', label: 'Flip-flopping: the state changes', unit: 'times', scale: 1, step: 1 },
  { key: 'flip_window_s', label: '… within', unit: 'min', scale: 1 / 60, step: 1 },
  { key: 'spot_rate', label: 'Random spot checks of confident frames', unit: '%', scale: 100, step: 0.5 },
] as const;

export const SAMPLE_ORIGINS: Record<string, string> = {
  snapshot: 'Snapshot',
  upload: 'Upload',
  video: 'Video frame',
  review: 'Review',
  import: 'Import',
};

export const SENSOR_TABS = [
  { id: 'live', label: 'Live' },
  { id: 'label', label: 'Label' },
  { id: 'upload', label: 'Upload' },
  { id: 'dataset', label: 'Dataset' },
  { id: 'quality', label: 'Quality' },
  { id: 'history', label: 'History' },
  { id: 'settings', label: 'Settings' },
] as const;
export type SensorTab = (typeof SENSOR_TABS)[number]['id'];

/** Tabs per sensor kind, in order; the first one is where the sensor opens. */
export const TABS_BY_KIND: Record<SensorKind, SensorTab[]> = {
  single_state: ['label', 'upload', 'dataset', 'quality', 'history', 'settings'],
  objects: ['live', 'history', 'settings'],
  reading: ['live', 'history', 'settings'],
};

/** The two kinds as offered in the new sensor wizard. */
export const SENSOR_KIND_INFO: Record<SensorKind, { title: string; text: string; example: string }> = {
  single_state: {
    title: 'States',
    text: 'Your own states, learned from a few labelled examples.',
    example: 'e.g. garage door open / closed / partial',
  },
  objects: {
    title: 'Objects',
    text: 'People, cars, animals and more. Works right away, no training.',
    example: 'e.g. a person or a car in the driveway',
  },
  reading: {
    title: 'Reading',
    text: 'A number from a display or counter. Works right away, no training.',
    example: 'e.g. kWh on the power meter, minutes left on the washer',
  },
};

/** Reading modes as offered in the UI; `units` are suggestions for the unit field. */
export const READING_MODE_INFO: Record<ReadingMode, { title: string; text: string; units: string[] }> = {
  counter: {
    title: 'Counter',
    text: 'Only goes up — power, water or gas meters. Works in the Energy dashboard.',
    units: ['kWh', 'm³', 'L', 'Wh'],
  },
  value: { title: 'Value', text: 'Any number that can go up and down — prices, readouts.', units: ['kr', 'NOK', 'kr/L', '€', '°C'] },
  time_left: { title: 'Time left', text: 'A countdown like 1:25 on an appliance. Reported in minutes.', units: [] },
};

export const READING_DISPLAY_INFO: Record<ReadingDisplay, string> = {
  auto: 'Auto (recommended)',
  led: 'Light digits on dark (LED)',
  lcd: 'Dark digits on light (LCD)',
};

/** Default Home Assistant device class for a unit (the user can change it). */
export const UNIT_DEVICE_CLASS: Record<string, string> = {
  kWh: 'energy',
  Wh: 'energy',
  'm³': 'water',
  L: 'water',
  kr: 'monetary',
  NOK: 'monetary',
  '€': 'monetary',
  '°C': 'temperature',
};

/** Domains listed first when picking trigger entities (the most useful triggers). */
export const TRIGGER_DOMAINS_FIRST = ['binary_sensor', 'cover', 'switch', 'input_boolean', 'lock', 'light', 'button', 'sensor'];
export const ENTITY_SEARCH_LIMIT = 50;
/** Mirrors ENTITY_ID in the backend (api/common.py). */
export const ENTITY_ID_PATTERN = /^[a-z0-9_]+\.[a-z0-9_]+$/;

export const TRIGGER_SOURCES: Record<string, string> = {
  entity: 'Entity changed',
  change: 'Image changed',
};

/** Placeholder the backend puts where credentials were removed from an exported URL (redact.MASK). */
export const REDACTED_MARK = '***';

/** Beta versions look like 0.4.1b1 (mirrors scripts/channel.py). */
export const BETA_VERSION_PATTERN = /^\d+\.\d+\.\d+b\d+$/;

export const TIMELINE_HOURS = 24;
export const DATASET_PAGE_SIZE = 120;
