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
  entities: string[];
  burst_interval_s: number;
  burst_duration_s: number;
  change_detection: boolean;
  change_interval_s: number;
  change_threshold: number;
}

export interface ReviewRules {
  enabled: boolean;
  below: number;
  cooldown_s: number;
  flip_limit: number;
  flip_window_s: number;
  spot_rate: number;
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
export type ReadingDisplay = 'auto' | 'led' | 'lcd';

export interface ReadingSettings {
  mode: ReadingMode;
  decimals: number;
  unit: string;
  device_class: string;
  display: ReadingDisplay;
  max_step: number;
}

/** The last read of a reading sensor; reason = why it was rejected (null = accepted). */
export interface ReadingResult {
  text: string;
  score: number;
  value: string | null;
  reason: string | null;
  at: number;
}

export interface SensorReading extends ReadingSettings {
  /** The published value (formatted), null until the first accepted reading. */
  value: string | null;
  last: ReadingResult | null;
  has_image: boolean;
}

/** One object found in a frame; box = [x1, y1, x2, y2] normalised to the whole frame. */
export interface Detection {
  key: string;
  score: number;
  box: [number, number, number, number];
}

export interface ObjectSettings {
  classes: string[];
  min_size: number;
  clear_after_s: number;
}

export interface ObjectLive {
  key: string;
  on: boolean;
  count: number;
  score: number;
  last_seen: number | null;
}

export interface SensorObjects extends ObjectSettings {
  live: ObjectLive[];
  detections: Detection[];
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
  trigger_limits: Record<'burst_interval_s' | 'burst_duration_s' | 'change_interval_s' | 'change_threshold', [number, number]>;
  trigger_max_entities: number;
  review_defaults: ReviewRules;
  roi_max_points: number;
  review_limits: Record<Exclude<keyof ReviewRules, 'enabled'>, [number, number]>;
  sensor_kinds: SensorKind[];
  object_sensor_defaults: { interval_s: number; threshold: number; debounce: number };
  object_defaults: ObjectSettings;
  object_limits: Record<'min_size' | 'clear_after_s', [number, number]>;
  object_max_classes: number;
  object_labels: ObjectLabel[];
  object_popular: string[];
  reading_sensor_defaults: { interval_s: number; threshold: number; debounce: number };
  reading_defaults: ReadingSettings;
  reading_limits: Record<'decimals' | 'max_step', [number, number]>;
  reading_modes: ReadingMode[];
  reading_displays: ReadingDisplay[];
  reading_device_classes: string[];
}

export interface Status {
  version: string;
  sensors: number;
  review_count: number;
  backbone: string | null;
  backbone_name: string | null;
  provider: string | null;
  backbone_error: string;
  detector: string | null;
  detector_name: string | null;
  detector_error: string;
  reader: string | null;
  reader_name: string | null;
  reader_error: string;
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
  review_reason: 'low_confidence' | 'flip' | 'spot_check' | null;
  reviewed: boolean;
  has_frame: boolean;
  /** Object sensors: state_key = the class, published_key = 'on' | 'off'. */
  detections: Detection[] | null;
}

export interface ReviewItem extends Prediction {
  sensor: { id: number; name: string; roi: Roi | null; states: StateDef[] };
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

export interface DetectorInfo extends BackboneInfo {
  license: string;
  source: string;
}

export interface SettingsInfo {
  backbone: string;
  execution_provider: string;
  backbones: BackboneInfo[];
  detector: string;
  detectors: DetectorInfo[];
  reader: string;
  readers: DetectorInfo[];
  providers: string[];
  options: Record<string, string | number>;
}
