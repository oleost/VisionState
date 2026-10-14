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
  source: 'entity' | 'change' | 'person';
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

/** Counters: how fast the value may go up — learned from its readings, a fixed rate, a fixed
 * change per reading, or no limit. */
export type ChangeLimit = 'auto' | 'fixed' | 'step' | 'off';

/** A counter's limit as it is now (backend change_limit.py). */
export interface CounterLimit {
  /** Days with readings it learns from, and how many it needs before the learned limit applies. */
  days: number;
  needed: number;
  /** Learned: the busiest rise per hour and per day (null until learned). */
  per_hour: number | null;
  per_day: number | null;
  /** What "auto" allows per hour once learned, with its room to spare (null until learned). */
  rate: number | null;
  /** How much the last reading could go up (null: no limit). */
  allowed: number | null;
  /** Read with confidence over the limit since the last accepted reading. */
  held: boolean;
}

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
  /** Values and times left, and counters with change_limit "step": the largest change between two
   * readings (0 = no limit). */
  max_step: number;
  change_limit: ChangeLimit;
  /** change_limit "fixed": how much the counter may go up per hour (0 = no limit); "auto": until it
   * learned (0 = the standard rate). */
  max_rate: number;
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
    /** Accepted readings nobody checked whose frame is kept (they go into the export too). */
    unchecked: number;
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
  /** A counter's readings over its limit agreed with each other: accepted after all. */
  agreed?: boolean;
  at: number;
}

export interface SensorReading extends ReadingSettings {
  /** The published value (formatted), null until the first accepted reading. */
  value: string | null;
  last: ReadingResult | null;
  has_image: boolean;
  /** The camera's picture has another size than the region was drawn on: readings are held. */
  camera_changed: { from: [number, number]; to: [number, number]; since: number } | null;
  /** Counters only. */
  limit: CounterLimit | null;
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
  ask?: { id: number | null; label: string; similarity: number; face?: boolean };
  /** Person boxes of a sensor that recognises faces: what the face search found (see FaceInfo). */
  face?: FaceInfo;
  /** On a history frame: taught from it since (its label is the answer; filtered: "not it"). */
  taught?: boolean;
}

/**
 * The face search in one person box: `box` the face (normalised to the frame), `problem` why it
 * cannot be used (null: usable) — 'none' no face, 'cut' by the frame's edge, 'small', 'turned',
 * 'blurry', 'unsure'; `state` what a usable face is: 'match' (it is `label`), 'pending' (a second
 * check must agree), 'ask' (maybe `candidate`), 'unknown', 'unclear' (matched nobody, but too small
 * or turned to call a stranger).
 */
export interface FaceInfo {
  box?: [number, number, number, number];
  problem: FaceProblem | null;
  face_px?: number; // the face's width in the frame, in pixels
  state?: 'match' | 'pending' | 'ask' | 'unknown' | 'unclear';
  label?: string;
  candidate?: string | null;
  similarity?: number;
  kept?: boolean;
}

export type FaceProblem = 'none' | 'cut' | 'small' | 'turned' | 'blurry' | 'unsure';

/** Recognising people by their face on an object sensor (defaults: config face_defaults), with its
 * own face limits: an unset (or null) one follows the global value. */
export interface FaceSettings extends FaceOverrides {
  enabled: boolean;
  /** Put faces nobody was taught for in the review queue ("Who is this?"). */
  ask_unknown: boolean;
}

/** The tunable face limits: the smallest face (its width in pixels) and the similarities. */
export type FaceKey = 'min_face_px' | 'match_similarity' | 'sure_similarity' | 'ask_similarity' | 'keep_similarity';
export type FaceLimits = Record<FaceKey, number>;
export type FaceOverrides = Partial<Record<FaceKey, number | null>>;

/** GET /face-limits: the face model's defaults, what the user set and the result. */
export interface FaceLimitsInfo {
  model: string;
  defaults: FaceLimits;
  values: FaceOverrides;
  effective: FaceLimits;
}

/** GET /people: the people (shared by every object sensor that recognises faces). */
export interface People {
  model: FaceStatus;
  /** The sensors that recognise faces. */
  sensors: number[];
  people: Person[];
  /** Pairs of people whose closest faces come near the match threshold. */
  look_alikes: { a: string; b: string; similarity: number }[];
}

export interface Person {
  key: string;
  name: string;
  /** Their own entity: the slug of the sensor that sees them now ('not_seen': none), and when and
   * on which sensor they were last seen. */
  seen: { now: string; at: number | null; sensor_id: number | null; sensor_name: string | null };
  /** When the oldest taught face was taught. */
  oldest: string | null;
  /** `sensor_name`: the camera it was taught on (null once that sensor is deleted); `suspect`:
   * the person a face is closer to than to its own (possibly the wrong name). */
  faces: {
    id: number;
    origin: string;
    is_night: boolean;
    created_at: string;
    sensor_id: number | null;
    sensor_name: string | null;
    suspect: string | null;
  }[];
}

/** POST /people/photos: faces added from photos or videos, and the files that had none usable. */
export interface PhotoResult {
  label: string | null;
  name: string | null;
  added: number;
  skipped: { file: string; message: string }[];
  faces: number | null;
}

/** POST /import: the new sensor; `faces_off`: it recognised faces where it was exported (now off). */
export interface ImportResult {
  id: number;
  skipped: number;
  faces_off: boolean;
}

/** The face model: chosen, loaded, downloading (bytes), embedding the taught faces (count) or why it failed. */
export interface FaceStatus {
  model: string;
  loaded: boolean;
  loading: boolean;
  progress: { done: number; total: number } | null;
  /** The taught faces being embedded with a newly loaded model: how many of how many. */
  embedding: { done: number; total: number } | null;
  error: string;
  downloaded: boolean;
}

export interface FaceModelInfo extends BackboneInfo {
  /** What its makers say it was trained on. */
  data: string;
  detector: { name: string; license: string; data: string; source: string };
}

/** An object sensor's review question ("Is this Our car?"), in a history row's `probs.ask`. About a
 * face ("Is this Kari?", "Who is this?" when label is the unknown key): `face` is the face's box. */
export interface ObjectQuestion {
  label: string;
  similarity: number;
  box: [number, number, number, number];
  detected: string;
  face?: [number, number, number, number];
  /** About a face: the closest face taught as that person, to compare with. */
  example?: number | null;
}

/** An own label of an object sensor: a kind of one of its objects, e.g. "Our car" (parent "car"). */
export interface OwnLabel {
  key: string;
  name: string;
  parent: string;
  /** A person (shared by every sensor, GET /people): added by the backend while the sensor recognises faces. */
  face?: boolean;
}

export interface ObjectSettings {
  classes: string[];
  min_size: number;
  clear_after_s: number;
  /** Compare boxes with what was taught; off = the detector alone. */
  use_taught: boolean;
  /** This sensor's own teaching limits; a missing one follows the global value. */
  teach?: TeachOverrides;
  faces?: FaceSettings;
}

export type TeachKey = 'match_similarity' | 'keep_similarity' | 'ask_similarity' | 'rescue_similarity' | 'rescue_floor';
export type TeachLimits = Record<TeachKey, number>;
/** Teaching limits that may be unset (null): globally the state model's default, on a sensor the global value. */
export type TeachOverrides = Partial<Record<TeachKey, number | null>>;

/** GET /teach-limits: the global teaching limits. */
export interface TeachLimitsInfo {
  backbone: string;
  defaults: TeachLimits;
  values: TeachOverrides;
  effective: TeachLimits;
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
  /** Boxes taught so far, faces taught on this camera (the people are shared: GET /people), and the
   * objects whose boxes are compared with the boxes. */
  taught: number;
  taught_faces: number;
  taught_keys: string[];
  /** Of the last person boxes looked at for a face, how many had a usable one, and why the others not. */
  face_stats: {
    looked: number;
    usable: number;
    problems: Partial<Record<FaceProblem, number>>;
    frame: [number, number] | null; // width and height of the last frame looked at
    min_face_px: number | null; // the smallest face it was looked at with
  };
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
  /** Seconds between checks of that burst (a trigger's, or looking for a face while a person is there). */
  burst_interval_s?: number | null;
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
  teach: { none_label: string; max_labels: number; tunable: TeachKey[]; limits: Record<TeachKey, [number, number]> };
  face_defaults: FaceSettings;
  face_limits: Record<FaceKey, [number, number]>;
  /** The key (and entity) of a face nobody was taught for. */
  face_unknown_key: string;
  faces: { max_people: number; max_faces_per_person: number; suggest_faces: number; stats_window: number };
  reading_sensor_defaults: { interval_s: number; threshold: number; debounce: number };
  reading_defaults: ReadingSettings;
  reading_limits: Record<'decimals' | 'digits' | 'max_step' | 'max_rate' | 'spot_rate' | 'rate_window_min', [number, number]>;
  /** A counter's standard limit: this many steps of its last digit a minute. */
  counter_standard_steps_per_min: number;
  reading_modes: ReadingMode[];
  reading_displays: ReadingDisplay[];
  counter_readers: CounterReader[];
  reading_device_classes: string[];
  /** Mechanical counters: the share of each digit field's width that is read. */
  reading_counter_cell_share: number;
  /** Longest "What meter is this?" text of a readings export. */
  reading_export_meter_max_chars: number;
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
  /** The face model, and how many people and sensors that recognise faces there are. */
  faces: FaceStatus & { people: number; sensors: number };
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
  /** Object sensors: state_key = the class or own label, published_key = 'on' | 'off' | 'filtered' | 'ask' |
   * 'face' (a face nobody is named for). */
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
  /** Downloaded (not included in the app): it can be deleted while not in use. */
  downloaded: boolean;
  /** Why it cannot be deleted now ("" if it can). */
  in_use: string;
  size: number;
  license: string;
  source: string;
  /** Rough extra memory once loaded, and the time of one check (4 threads of a desktop CPU). */
  ram_mb: number;
  time_ms: number;
  /** Rank within its kind, 1-5. */
  accuracy: number;
}

/** The AI models a user chooses, by their list in GET /settings. */
export type ModelKind = 'backbones' | 'detectors' | 'face_models' | 'readers';

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

export type DetectorInfo = BackboneInfo;

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
  /** Recognising faces on object sensors: the recogniser chosen; downloaded when a sensor turns faces on. */
  face_model: string;
  face_models: FaceModelInfo[];
  options: Record<string, string | number>;
}

/** POST /lights/hold: is the light on, and how long until frames are taken in it (null: not on). */
export interface LightHold {
  on: boolean;
  wait_s: number | null;
  error: string;
}
