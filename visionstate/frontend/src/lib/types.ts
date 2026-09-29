// Shapes returned by the backend API (/api/v1).

export interface Roi {
  x: number;
  y: number;
  w: number;
  h: number;
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

export interface TriggerInfo {
  source: 'entity' | 'change';
  detail: string;
  at: number;
}

export type SensorStatus = 'ok' | 'untrained' | 'unavailable' | 'disabled';

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
  kind: string;
  source_type: string;
  source: string;
  roi: Roi | null;
  interval_s: number;
  threshold: number;
  debounce: number;
  enabled: boolean;
  triggers: Triggers;
  entity_id: string;
  states: StateDef[];
  status: SensorStatus;
  trained: boolean;
  training: boolean;
  live: Live;
  model: ModelSummary | null;
  counts: Counts;
}

export interface SensorInput {
  name: string;
  source_type: string;
  source: string;
  roi: Roi | null;
  states: { key?: string; name: string; color?: string }[];
  interval_s?: number;
  threshold?: number;
  debounce?: number;
  enabled?: boolean;
  triggers?: Triggers;
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
}

export interface Status {
  version: string;
  sensors: number;
  review_count: number;
  backbone: string | null;
  backbone_name: string | null;
  provider: string | null;
  backbone_error: string;
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
}

export interface ReviewItem extends Prediction {
  sensor: { id: number; name: string; roi: Roi | null; states: StateDef[] };
}

export interface Tip {
  level: 'ok' | 'warn';
  title: string;
  text: string;
  action: 'label' | 'upload' | 'review' | null;
}

export interface Quality {
  sensor: Sensor;
  counts: Counts;
  confusion: { keys: string[]; matrix: number[][] } | null;
  accuracy: number | null;
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

export interface SettingsInfo {
  backbone: string;
  execution_provider: string;
  backbones: BackboneInfo[];
  providers: string[];
  options: Record<string, string | number>;
}
