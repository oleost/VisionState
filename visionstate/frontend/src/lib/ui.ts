// UI constants: polling intervals, labels and tones. Change behaviour of the UI here.
import type { BackupPart, CounterReader, ReadingDisplay, ReadingMode, ReadingType, SensorKind, SensorStatus } from './types';

/** Refresh intervals in milliseconds. */
export const POLL = {
  status: 10_000,
  dashboard: 5_000,
  sensor: 2_000,
  liveFrame: 2_000,
  thumbnail: 15_000,
};

export const TOAST_MS = 5_000;

/** Shown on a sensor that is held back: readings, or what is sent to Home Assistant (its Home Assistant tab). */
export const CAMERA_CHANGED = { label: 'Camera changed', help: 'Readings are held: check the region' };
export const NOT_SENT = { label: 'Values not sent', help: 'Its entities in Home Assistant are unavailable until you choose “Send values” on its Home Assistant tab.' };
export const NOT_IN_HA = { label: 'Not in Home Assistant', help: 'It has no entities in Home Assistant. Choose “Send values” on its Home Assistant tab to create them.' };
/** A sensor's Home Assistant tab: what it has there. */
export type HaMode = 'send' | 'hold' | 'none';
export const HA_MODES: Record<HaMode, { label: string; help: string }> = {
  send: { label: 'Send values', help: 'Its entities exist and follow what the sensor finds.' },
  hold: { label: 'Don’t send values', help: 'Its entities stay, but are unavailable: no values, no statistics. For tuning a sensor before it is used.' },
  none: { label: 'Not in Home Assistant', help: 'No entities at all: the sensor is used here in VisionState only.' },
};

/** Browser storage key of the light switch in views with live frames (a per-viewer convenience). */
export const LIGHT_HOLD_STORAGE_KEY = 'visionstate.lightWhileViewing';
/** At most this many toasts on screen; older ones make room (labelling fast stacked them over the page). */
export const TOAST_MAX = 2;

/** Settings → Backup: what each part of a backup holds. */
export const BACKUP_PARTS: Record<BackupPart, { label: string; help: string }> = {
  sensors: { label: 'Sensors and settings', help: 'Every sensor, its trained model and the app’s settings (always included)' },
  samples: { label: 'Training images', help: 'Labelled images and taught boxes' },
  faces: { label: 'People and their faces', help: 'Everyone taught by face, with their face images' },
  history: { label: 'History', help: 'Stored frames, sound clips, readings and the review queue' },
  models: { label: 'Downloaded AI models', help: 'Downloaded again when needed if left out' },
};

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
  rejected: { label: 'Rejected reading', tone: 'warn', help: 'The reading did not pass the checks; the last value was kept.' },
  ask: { label: 'Is it yours?', tone: 'info', help: 'This may be one of your own labels, but the AI is not sure. Your answer is taught.' },
  unsure: { label: 'Was it this sound?', tone: 'info', help: 'The sound was heard, but only just. Listen to the clip; your answer is kept and can teach the sensor.' },
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

/** The teaching limits a user can change (settings.TEACH_TUNABLE), in the order shown. */
export const TEACH_FIELDS = [
  {
    key: 'match_similarity',
    label: 'Recognised as a box you taught from',
    help: 'How close a box must be to one you taught to take its answer.',
  },
  {
    key: 'keep_similarity',
    label: 'Keeps that answer, while it stays in place, down to',
    help: 'A parked car stays "Our car" when the light changes a little.',
  },
  {
    key: 'ask_similarity',
    label: 'Asks you in the review queue from',
    help: 'Between this and the first value, a box that may be one of your own labels is asked about.',
  },
  {
    key: 'rescue_similarity',
    label: 'Finds a missed object again from',
    help: 'Once you drew a box the AI missed, unsure boxes this close to it count.',
  },
  {
    key: 'rescue_floor',
    label: 'Looks again at AI boxes down to',
    help: 'The weakest AI confidence looked at for missed objects.',
  },
] as const;

/** The wizard warns of a low-resolution stream: a picture whose longest side is below this, or a
 * camera entity or URL whose name says so (Reolink "fluent", "sub" streams, "low"). */
export const SMALL_FRAME_PX = 1280;
export const LOW_STREAM_NAME = /(^|[^a-z])(fluent|sub|low|lowres)([^a-z]|$)/i;

/** Files a phone offers for faces: photos (its camera or gallery), short videos, ZIP archives. */
export const PHOTO_ACCEPT = 'image/*,video/*,.zip';

/** The faces' limits, on the Settings page and an object sensor's Faces card (in this order). */
export const FACE_FIELDS = [
  {
    key: 'min_face_px',
    label: 'Smallest face',
    help: 'Its width in the picture. Smaller faces are not used: they are easily mistaken.',
    unit: 'px',
  },
  {
    key: 'match_similarity',
    label: 'Recognised as a person from',
    help: 'How close a face must be to the faces you taught; a second check must agree.',
  },
  {
    key: 'sure_similarity',
    label: 'Sure at once from',
    help: 'Named in the first check, without waiting for a second one.',
  },
  {
    key: 'ask_similarity',
    label: 'Asks you in the review queue from',
    help: 'Between this and the first similarity: "Is this Kari?"',
  },
  {
    key: 'keep_similarity',
    label: 'Keeps the name, while they stay, down to',
    help: 'Someone who stays where they were keeps their name as their face turns.',
  },
] as const;

/** The model comparison under Settings → AI model: one row per kind, in the order shown. */
export const MODEL_KINDS = [
  {
    key: 'backbones',
    title: 'State sensors',
    text: 'Turns the region into numbers that a small model per sensor learns your states from. A larger model tells similar states (and taught objects) apart better.',
  },
  {
    key: 'detectors',
    title: 'Object sensors',
    text: 'Finds people, vehicles and animals. Larger models find more small, far-off and partly hidden objects.',
  },
  {
    key: 'face_models',
    title: 'Faces (object sensors)',
    text: 'Tells who a person is from their face; downloaded when a sensor recognises faces. Its size includes the face finder.',
  },
  { key: 'readers', title: 'Displays', text: 'Reads the digits of a display; small helps on unusual fonts.' },
  { key: 'wheel_readers', title: 'Mechanical counters', text: 'Reads each wheel of a counter.' },
] as const;
export const MODEL_COMPARE_NOTE =
  'Rough numbers: extra memory once loaded, and time per check on 4 cores of a desktop PC. A Raspberry Pi 4 takes about 20–40 times as long; a check only runs when it is due or something changed.';

export const SAMPLE_ORIGINS: Record<string, string> = {
  snapshot: 'Snapshot',
  upload: 'Upload',
  video: 'Video frame',
  review: 'Review',
  import: 'Import',
  history: 'History',
};

export const SENSOR_TABS = [
  { id: 'live', label: 'Live' },
  { id: 'label', label: 'Label' },
  { id: 'upload', label: 'Upload' },
  { id: 'dataset', label: 'Dataset' },
  { id: 'quality', label: 'Quality' },
  { id: 'history', label: 'History' },
  { id: 'settings', label: 'Settings' },
  { id: 'home-assistant', label: 'Home Assistant' },
] as const;
export type SensorTab = (typeof SENSOR_TABS)[number]['id'];

/**
 * Tabs per sensor kind, in order; the first one is where the sensor opens. An object sensor shows
 * its Quality tab (what it was taught) only once something was taught (SensorPage).
 */
export const TABS_BY_KIND: Record<SensorKind, SensorTab[]> = {
  single_state: ['label', 'upload', 'dataset', 'quality', 'history', 'settings', 'home-assistant'],
  objects: ['live', 'history', 'quality', 'settings', 'home-assistant'],
  reading: ['live', 'history', 'quality', 'settings', 'home-assistant'],
  sound: ['live', 'history', 'quality', 'settings', 'home-assistant'],
};
/** A tab's label where a kind names it otherwise (a sound sensor's Quality tab holds its own sounds). */
export const TAB_LABELS: Partial<Record<SensorKind, Partial<Record<SensorTab, string>>>> = {
  sound: { quality: 'Own sounds' },
};

/** What a new sensor does, as asked first in the wizard: look at the picture, or listen. */
export const SENSOR_MODE_INFO = {
  picture: { title: 'Watch the picture', text: 'States, objects or a number in a camera image.', icon: 'camera' },
  sound: {
    title: 'Listen',
    text: 'Sounds in a camera’s audio or a microphone stream: sirens, alarms, a dog, your own doorbell.',
    icon: 'sound',
  },
} as const;

/** The picture kinds as offered in the new sensor wizard. */
export const SENSOR_KIND_INFO: Record<Exclude<SensorKind, 'sound'>, { title: string; text: string; example: string }> = {
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
  counter: 'Mechanical counter',
};

/** What reads a mechanical counter (reading editor). */
export const COUNTER_READER_INFO: Record<CounterReader, string> = {
  wheels: 'Wheel reader (recommended)',
  ocr: 'Text reader',
};

/** What is being read, as offered first in the reading editor. */
export const READING_TYPE_INFO: Record<ReadingType, { title: string; text: string; example: string }> = {
  display: {
    title: 'Digital display',
    text: 'LCD or LED digits, or a printed number.',
    example: 'e.g. a power meter display, a washer, a price sign',
  },
  counter: {
    title: 'Mechanical counter',
    text: 'Digit wheels that roll behind a window.',
    example: 'e.g. a water or gas meter',
  },
};
/** Modes that make sense per type (a mechanical counter shows no countdown). */
export const READING_TYPE_MODES: Record<ReadingType, ReadingMode[]> = {
  display: ['counter', 'value', 'time_left'],
  counter: ['counter', 'value'],
};

/** A new pointer dial (DialsEditor): its radius in units of the counter region's height, and how far
 * beside the last dial it is put (in its radii). */
export const DIAL_NEW = { radius: 0.6, gap: 2.4 };

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
  person: 'Person seen',
};

/** Placeholder the backend puts where credentials were removed from an exported URL (redact.MASK). */
export const REDACTED_MARK = '***';

/** Beta versions look like 0.4.1b1 (mirrors scripts/channel.py). */
export const BETA_VERSION_PATTERN = /^\d+\.\d+\.\d+b\d+$/;

/** History events (backend settings.HISTORY_EVENTS) as offered in the filter. */
export const HISTORY_EVENTS: Record<string, string> = {
  change: 'State changed',
  flagged: 'Sent to review',
  appeared: 'Detected',
  cleared: 'Cleared',
  filtered: 'Filtered away',
  asked: 'Asked “is it yours?”',
  face_seen: 'Face seen, not named',
  value: 'New value',
  rejected: 'Rejected reading',
  verified: 'Checked by hand',
  heard: 'Heard',
  quiet: 'No longer heard',
};

/** What a sensor's History tab lists, per kind (the History page lists all of it). */
export const HISTORY_INTRO: Record<SensorKind, string> = {
  single_state: 'State changes and frames flagged for review. Tap a frame to see it whole; use “Add as” to turn it into training data — especially when the AI got it wrong.',
  objects: 'When each object appeared and cleared. Tap a row to see the frame with what the AI found, and tap a box there to correct it.',
  reading: 'Every new value and every rejected reading (readings you verified are kept for good). Tap a row to see the frame; the Quality tab sums them up.',
  sound: 'When each sound was heard and when it stopped. With clips on, tap a row to listen (and watch, on a camera) — and teach the clip as one of your own sounds.',
};

export const TIMELINE_HOURS = 24;
export const DATASET_PAGE_SIZE = 120;
