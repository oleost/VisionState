// Shapes returned by the backend API (/api/v1).

export interface Roi {
  x: number;
  y: number;
  w: number;
  h: number;
  /** Polygon corners [[x, y], ...]; absent for a plain rectangle (x/y/w/h is the bounding box). */
  points?: [number, number][] | null;
}

export interface StateDef {
  id?: number;
  key: string;
  name: string;
  color: string;
}

export interface Triggers {
  /** The regular interval check; off = only triggers (and once after start-up). */
  regular: boolean;
  entities: string[];
  /** entity id → the only new state that triggers; entities not listed trigger on any change. */
  only_states: Record<string, string>;
  burst_interval_s: number;
  burst_duration_s: number;
  change_detection: boolean;
  change_interval_s: number;
  change_threshold: number;
  /** A light or switch turned on before a check takes its frame, and off afterwards ('' = none). */
  light_entity: string;
  light_delay_s: number;
}

export interface ReviewRules {
  enabled: boolean;
  below: number;
  cooldown_s: number;
  flip_limit: number;
  flip_window_s: number;
  spot_rate: number;
}

/** A reminder in Home Assistant when frames have waited for review a long time (Settings). */
export interface ReminderRules {
  enabled: boolean;
  /** Remind when the oldest frame has waited this many days … */
  after_days: number;
  /** … and at least this many frames wait. */
  min_items: number;
  /** Remind again every repeat_days while they still wait. */
  repeat: boolean;
  repeat_days: number;
  /** Also push with this notify service ('notify.mobile_app_…'); '' = only the notification in Home Assistant. */
  notify_service: string;
}

/** The reminder and when the one that is up now was sent (null: none is up). */
export interface ReminderInfo extends ReminderRules {
  sent_at: string | null;
}

/** Per-sensor overrides: null = use the global value. */
export type ReviewOverrides = { [K in keyof ReviewRules]: ReviewRules[K] | null };

export interface TriggerInfo {
  source: 'entity' | 'change';
  detail: string;
  at: number;
}

export type SensorStatus = 'ok' | 'untrained' | 'unavailable' | 'disabled';

/**
 * 'single_state' learns the user's own states; 'objects' finds common objects; 'reading' reads a
 * number from a display. Only state sensors are trained.
 */
export type SensorKind = 'single_state' | 'objects' | 'reading';

export type ReadingMode = 'counter' | 'value' | 'time_left';
/** 'counter' is a mechanical counter with rolling digit wheels; the others are digital displays. */
export type ReadingDisplay = 'auto' | 'led' | 'lcd' | 'counter';
/** What is being read, as chosen first in the reading editor ('display' covers auto, led and lcd). */
export type ReadingType = 'display' | 'counter';
/** What reads a mechanical counter: each wheel's position, or the wheels as text (OCR). */
export type CounterReader = 'wheels' | 'ocr';

export interface ReadingSettings {
  mode: ReadingMode;
  decimals: number;
  unit: string;
  device_class: string;
  display: ReadingDisplay;
  /** Mechanical counters only: the number of wheels inside the region. */
  digits: number;
  /** Mechanical counters only: what reads the wheels. */
  counter_reader: CounterReader;
  max_step: number;
  /** Share of accepted readings also sent to the review queue (rejected ones always go there). */
  spot_rate: number;
  /** Counters: the rate entity in Home Assistant is the change over about this many minutes. */
  rate_window_min: number;
}

/** One period on a reading sensor's Quality tab (today, 7 days, 30 days). */
export interface ReadingPeriod {
  days: number;
  reads: number;
  accepted: number;
  rejected: number;
  by_reason: Record<string, number>;
}

export interface ReadingQuality {
  periods: ReadingPeriod[];
  /** One entry per day, oldest first, today last. */
  daily: { day: string; reads: number; accepted: number; rejected: number }[];
  /** What the user said about stored readings. */
  verified: {
    misread_rejected: number;
    right_rejected: number;
    misread_accepted: number;
    right_accepted: number;
    waiting: number;
    /** Accepted readings nobody checked whose frame is kept (export, spot checks). */
    unchecked_accepted: number;
    /** ... of those, never in the review queue (what a spot check can send there). */
    never_asked: number;
  };
  items: Prediction[];
}

/** What the number reader makes of a fresh frame (new sensor wizard). */
export interface ReadPreview {
  image: string;
  read_image: string;
  text: string;
  score: number;
  value: string | null;
  /** A mechanical counter read with another number of digits than it has wheels. */
  wrong_digit_count: boolean;
}

/** The last read of a reading sensor; reason = why it was rejected (null = accepted). */
export interface ReadingResult {
  text: string;
  score: number;
  value: string | null;
  reason: string | null;
  /** A counter read one step below its value: the last wheel turning; the value stays, no rejection. */
  settling?: boolean;
  at: number;
}

export interface SensorReading extends ReadingSettings {
  /** The published value (formatted), null until the first accepted reading. */
  value: string | null;
  last: ReadingResult | null;
  has_image: boolean;
}

/**
 * One object found in a frame; box = [x1, y1, x2, y2] normalised to the whole frame. After the
 * sensor was taught (see backend teach.py): `filtered` boxes do not count, `label` is an own label,
 * `was` the detector's class when it was taught as another one, `rescued` a box the detector was
 * unsure about, and `match` the taught box it resembles.
 */
export interface Detection {
  key: string;
  score: number;
  box: [number, number, number, number];
  filtered?: boolean;
  label?: string;
  was?: string;
  rescued?: boolean;
  match?: { id: number; label: string; similarity: number };
  /** Kept its taught answer: the same object at the same place, a little less alike. */
  kept?: boolean;
  /** May be this own label, but not clearly: asked about in the review queue. */
  ask?: { id: number; label: string; similarity: number };
}

/** An object sensor's review question ("Is this Our car?"), in a history row's `probs.ask`. */
export interface ObjectQuestion {
  label: string;
  similarity: number;
  box: [number, number, number, number];
  detected: string;
}

/** An own label of an object sensor: a kind of one of its objects, e.g. "Our car" (parent "car"). */
export interface OwnLabel {
  key: string;
  name: string;
  parent: string;
}

export interface ObjectSettings {
  classes: string[];
  min_size: number;
  clear_after_s: number;
  /** Compare boxes with what was taught; off = the detector alone. */
  use_taught: boolean;
}

export interface ObjectLive {
  key: string;
  /** Own labels: the object they are a kind of. */
  parent: string | null;
  on: boolean;
  count: number;
  score: number;
  last_seen: number | null;
}

export interface SensorObjects extends ObjectSettings {
  custom: OwnLabel[];
  live: ObjectLive[];
  detections: Detection[];
  /** Boxes taught so far, and the objects whose boxes are compared with them. */
  taught: number;
  taught_keys: string[];
}

/** One taught box; label "none" = not what the detector said, detected null = a box it missed. */
export interface TaughtBox {
  id: number;
  label: string;
  detected: string | null;
  score: number | null;
  origin: string;
  created_at: string;
}

export interface Taught {
  use_taught: boolean;
  labels: (OwnLabel & { active: boolean })[];
  examples: TaughtBox[];
  /** History frames where an object was filtered away, newest first. */
  filtered: Prediction[];
}

/** What to teach about one box (POST /sensors/{id}/taught). */
export interface TeachInput {
  frame_id?: string | null;
  history_id?: number | null;
  box: [number, number, number, number];
  detected: string | null;
  score?: number | null;
  label?: string;
  new_label?: string;
  parent?: string;
}

export interface ObjectLabel {
  key: string;
  name: string;
  group: string;
}

export interface Live {
  available: boolean | null;
  error: string;
  published: string | null;
  top: string | null;
  confidence: number;
  probs: Record<string, number>;
  last_run: number | null;
  in_burst: boolean;
  change_score: number | null;
  last_trigger: TriggerInfo | null;
  /** Why the sensor's light could not be switched ('' = fine). */
  light_error: string;
  /** The frame the last check analysed (GET .../frame?frame_id=), while it is still cached. */
  frame_id: string | null;
}

export interface ModelSummary {
  backbone: string;
  version: number;
  trained_at: string;
  n_samples: number;
  accuracy: number | null;
  train_seconds: number;
}

export interface Counts {
  per_state: Record<string, { day: number; night: number }>;
  labelled: number;
  unlabelled: number;
}

export interface Sensor {
  id: number;
  slug: string;
  name: string;
  kind: SensorKind;
  source_type: string;
  source: string;
  roi: Roi | null;
  interval_s: number;
  threshold: number;
  debounce: number;
  enabled: boolean;
  /** Values go to Home Assistant; off: its entities stay unavailable (a sensor being tuned). */
  publish: boolean;
  triggers: Triggers;
  review: ReviewOverrides;
  review_effective: ReviewRules;
  entity_id: string;
  entity_ids: string[];
  states: StateDef[];
  objects: SensorObjects | null;
  reading: SensorReading | null;
  status: SensorStatus;
  trained: boolean;
  training: boolean;
  live: Live;
  model: ModelSummary | null;
  counts: Counts;
}

export interface SensorInput {
  name: string;
  kind?: SensorKind;
  source_type: string;
  source: string;
  roi: Roi | null;
  states: { key?: string; name: string; color?: string }[];
  objects?: ObjectSettings;
  reading?: ReadingSettings;
  interval_s?: number;
  threshold?: number;
  debounce?: number;
  enabled?: boolean;
  publish?: boolean;
  triggers?: Triggers;
  review?: Partial<ReviewOverrides>;
}

export interface AppConfig {
  version: string;
  sensor_defaults: { interval_s: number; threshold: number; debounce: number };
  sensor_limits: Record<'interval_s' | 'threshold' | 'debounce', [number, number]>;
  state_palette: string[];
  max_states: number;
  unknown_state: string;
  source_types: Record<string, string>;
  video: { frame_interval_s: number; dedupe_distance: number; max_frames: number };
  quality: { min_samples_per_state: number; min_night_samples: number; cv_folds: number };
  trigger_defaults: Triggers;
  trigger_limits: Record<
    'burst_interval_s' | 'burst_duration_s' | 'change_interval_s' | 'change_threshold' | 'light_delay_s',
    [number, number]
  >;
  light_domains: string[];
  /** Whether a new sensor sends its values to Home Assistant. */
  publish_default: boolean;
  /** A view with live frames renews its hold on a sensor's light this often; a lease runs out after lease_s. */
  light_view: { renew_s: number; lease_s: number };
  trigger_max_entities: number;
  review_defaults: ReviewRules;
  roi_max_points: number;
  review_limits: Record<Exclude<keyof ReviewRules, 'enabled'>, [number, number]>;
  reminder_defaults: ReminderRules;
  reminder_limits: Record<'after_days' | 'min_items' | 'repeat_days', [number, number]>;
  sensor_kinds: SensorKind[];
  object_sensor_defaults: { interval_s: number; threshold: number; debounce: number };
  object_defaults: ObjectSettings;
  object_limits: Record<'min_size' | 'clear_after_s', [number, number]>;
  object_max_classes: number;
  object_labels: ObjectLabel[];
  object_popular: string[];
  /** Teaching object sensors: the label of a box that is not what the detector said. */
  teach: { none_label: string; max_labels: number };
  reading_sensor_defaults: { interval_s: number; threshold: number; debounce: number };
  reading_defaults: ReadingSettings;
  reading_limits: Record<'decimals' | 'digits' | 'max_step' | 'spot_rate' | 'rate_window_min', [number, number]>;
  reading_modes: ReadingMode[];
  reading_displays: ReadingDisplay[];
  counter_readers: CounterReader[];
  reading_device_classes: string[];
  /** Mechanical counters: the share of each digit field's width that is read. */
  reading_counter_cell_share: number;
  /** At most this many checked readings go into "Export checked readings". */
  reading_export_limit: number;
  /** ... and at most this many accepted readings nobody checked, when asked for. */
  reading_export_unchecked_limit: number;
  reading_export_meter_max_chars: number;
  /** "Spot-check accepted readings" sends this many of the newest to the review queue. */
  reading_spot_check_count: number;
  storage_defaults: { history_max_gb: number };
  storage_limits: Record<'history_days' | 'history_max_gb', [number, number]>;
  /** Browsing the history: page sizes, time windows offered (hours) and how often it looks for new rows. */
  history: { page_size: number; max_page_size: number; max_filter_values: number; window_presets_h: number[]; poll_s: number };
  /** What a history row can be, per sensor kind (names are unique across kinds). */
  history_events: Record<SensorKind, string[]>;
}

export interface Status {
  version: string;
  sensors: number;
  review_count: number;
  backbone: string | null;
  backbone_name: string | null;
  backbone_error: string;
  detector: string | null;
  detector_name: string | null;
  detector_error: string;
  reader: string | null;
  reader_name: string | null;
  reader_error: string;
  /** Mechanical counters read wheel by wheel; loaded on first use. */
  wheel_reader: string | null;
  wheel_reader_name: string | null;
  wheel_reader_error: string;
  mqtt: { connected: boolean; host: string | null; error: string };
  home_assistant: boolean;
  ha_events: { enabled: boolean; connected: boolean; entities: number; error: string };
  supervised: boolean;
}

export interface HaEntity {
  entity_id: string;
  name: string;
  domain: string;
  state: string;
}

export interface Camera {
  entity_id: string;
  name: string;
  state: string;
}

export interface SampleItem {
  id: number;
  labels: string[];
  origin: string;
  is_night: boolean;
  use_roi: boolean;
  created_at: string;
  suggestion: { key: string; confidence: number } | null;
}

export interface Prediction {
  id: number;
  sensor_id: number;
  created_at: string;
  state_key: string;
  published_key: string | null;
  confidence: number;
  probs: Record<string, number>;
  is_change: boolean;
  review_reason: 'low_confidence' | 'flip' | 'spot_check' | 'rejected' | 'ask' | null;
  reviewed: boolean;
  has_frame: boolean;
  /** Object sensors: state_key = the class or own label, published_key = 'on' | 'off' | 'filtered' | 'ask'. */
  detections: Detection[] | null;
  /** Reading sensors: did the reader read the right number (null = not verified)? */
  read_ok: boolean | null;
  /** Reading sensors: the right value, when the user gave it for a misread. */
  correct_value: string | null;
}

/** Which history rows (api/history.py HistoryFilter); several values of one filter match any of them. */
export interface HistoryFilter {
  sensor?: number[];
  key?: string[];
  event?: string[];
  waiting?: boolean;
  since?: string;
  until?: string;
}

export interface HistoryPage {
  items: Prediction[];
  total: number;
  /** Cursor of the next page, null on the last. */
  next: string | null;
  /** Cursor of the newest row the filter matches (to ask what arrived since). */
  newest: string | null;
}

/** What each filter could still find: rows matching the rest of the filter. */
export interface HistoryFacets {
  sensors: { id: number; count: number }[];
  keys: { sensor_id: number; key: string; count: number }[];
  events: { event: string; count: number }[];
}

export interface ReviewItem extends Prediction {
  sensor: {
    id: number;
    name: string;
    kind: SensorKind;
    roi: Roi | null;
    states: StateDef[];
    reading: ReadingSettings | null;
    /** Object sensors: the own labels a question can be about. */
    labels: OwnLabel[];
  };
}

export interface Tip {
  level: 'ok' | 'warn';
  title: string;
  text: string;
  action: 'label' | 'upload' | 'review' | 'suspects' | null;
}

export interface Suspect {
  sample_id: number;
  label: string;
  predicted: string;
  confidence: number;
  /** The user said the label is right: no longer "possibly mislabelled", still in its matrix cell. */
  verified: boolean;
}

export interface Quality {
  sensor: Sensor;
  counts: Counts;
  confusion: { keys: string[]; matrix: number[][] } | null;
  accuracy: number | null;
  suspects: Suspect[];
  tips: Tip[];
  targets: AppConfig['quality'];
}

export interface BackboneInfo {
  id: string;
  name: string;
  description: string;
  installed: boolean;
  size: number;
}

/** GET /storage: disk use and the history limits (whichever is reached first applies). */
export interface StorageInfo {
  history_bytes: number;
  history_frames: number;
  oldest_history: string | null;
  training_bytes: number;
  training_images: number;
  free_bytes: number;
  limited_by_size: boolean;
  history_days: number;
  history_max_gb: number;
}

export interface DetectorInfo extends BackboneInfo {
  license: string;
  source: string;
}

export interface SettingsInfo {
  backbone: string;
  backbones: BackboneInfo[];
  detector: string;
  detectors: DetectorInfo[];
  reader: string;
  readers: DetectorInfo[];
  /** The wheel reader for mechanical counters (one model for now, nothing to choose). */
  wheel_reader: string;
  wheel_readers: DetectorInfo[];
  options: Record<string, string | number>;
}

/** POST /lights/hold: is the light on, and how long until frames are taken in it (null: not on). */
export interface LightHold {
  on: boolean;
  wait_s: number | null;
  error: string;
}
