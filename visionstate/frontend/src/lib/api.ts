// Typed client for the backend. Every URL the UI uses is built here.
import type {
  AppConfig,
  Camera,
  Detection,
  HaEntity,
  Prediction,
  Quality,
  ReadPreview,
  ReadingSettings,
  ReviewRules,
  Roi,
  ReviewItem,
  SampleItem,
  Sensor,
  SensorInput,
  SettingsInfo,
  Status,
  StorageInfo,
} from './types';

// Relative on purpose: the app lives below a dynamic Home Assistant Ingress path.
const BASE = 'api/v1/';

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function errorFrom(resp: Response): Promise<ApiError> {
  let message = resp.statusText;
  try {
    const body = await resp.json();
    message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
  } catch {
    /* not JSON */
  }
  return new ApiError(resp.status, message);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + path, init);
  if (!resp.ok) throw await errorFrom(resp);
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
});

const qs = (params: Record<string, string | number | boolean | undefined | null>) =>
  new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => [k, String(v)]),
  ).toString();

export interface Frame {
  frameId: string | null;
  url: string; // object URL; caller revokes it
}

export interface UploadOptions {
  stateKey?: string | null;
  frameIntervalS?: number;
  useRoi?: boolean;
}

export interface UploadResult {
  created: number;
  sample_ids: number[];
  errors: string[];
}

export const api = {
  config: () => request<AppConfig>('config'),
  status: () => request<Status>('status'),
  settings: () => request<SettingsInfo>('settings'),
  saveSettings: (body: { backbone: string; detector?: string; reader?: string }) =>
    request<SettingsInfo>('settings', send('PUT', body)),
  storage: () => request<StorageInfo>('storage'),
  saveStorage: (body: { history_days: number; history_max_gb: number }) =>
    request<StorageInfo>('storage', send('PUT', body)),
  cameras: () => request<Camera[]>('cameras'),
  entities: () => request<HaEntity[]>('entities'),
  previewUrl: (sourceType: string, source: string) =>
    `${BASE}preview?${qs({ source_type: sourceType, source, t: Date.now() })}`,
  /** A fresh frame (as a data URL) plus every object found in the region. */
  previewDetect: (sourceType: string, source: string, roi: Roi | null) =>
    request<{ image: string; width: number; height: number; detections: Detection[] }>(
      'preview/detect',
      send('POST', { source_type: sourceType, source, roi }),
    ),

  sensors: () => request<Sensor[]>('sensors'),
  sensor: (id: number) => request<Sensor>(`sensors/${id}`),
  createSensor: (body: SensorInput) => request<Sensor>('sensors', send('POST', body)),
  updateSensor: (id: number, body: Partial<SensorInput> & { clear_roi?: boolean }) =>
    request<Sensor>(`sensors/${id}`, send('PATCH', body)),
  deleteSensor: (id: number) => request<void>(`sensors/${id}`, send('DELETE')),
  classify: (id: number) => request(`sensors/${id}/classify`, send('POST')),
  retrain: (id: number) => request(`sensors/${id}/retrain`, send('POST')),

  async frame(id: number, cached = false): Promise<Frame> {
    const resp = await fetch(`${BASE}sensors/${id}/frame?${qs({ cached: cached || undefined, t: Date.now() })}`);
    if (!resp.ok) throw await errorFrom(resp);
    return { frameId: resp.headers.get('X-Frame-Id'), url: URL.createObjectURL(await resp.blob()) };
  },
  /** What the number reader makes of a fresh frame's region (new sensor wizard). */
  previewRead: (sourceType: string, source: string, roi: Roi | null, reading: ReadingSettings) =>
    request<ReadPreview>('preview/read', send('POST', { source_type: sourceType, source, roi, reading })),
  /** The image the reader saw in the last check; `at` busts the browser cache per check. */
  readingImageUrl: (id: number, at: number) => `${BASE}sensors/${id}/reading/image?${qs({ t: at })}`,
  /** The exact frame a check analysed (while cached); stable URL, so the browser can cache it. */
  analysedFrameUrl: (id: number, frameId: string) => `${BASE}sensors/${id}/frame?${qs({ frame_id: frameId })}`,
  capture: (id: number, stateKey: string, frameId: string | null) =>
    request<{ id: number }>(`sensors/${id}/capture`, send('POST', { state_key: stateKey, frame_id: frameId })),

  upload(id: number, files: File[], opts: UploadOptions, onProgress?: (fraction: number) => void) {
    const form = new FormData();
    files.forEach((f) => form.append('files', f, f.name));
    if (opts.stateKey) form.append('state_key', opts.stateKey);
    if (opts.frameIntervalS) form.append('frame_interval_s', String(opts.frameIntervalS));
    if (opts.useRoi !== undefined) form.append('use_roi', String(opts.useRoi));
    return new Promise<UploadResult>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${BASE}sensors/${id}/uploads`);
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText));
        else {
          let msg = xhr.statusText;
          try {
            msg = JSON.parse(xhr.responseText).detail;
          } catch {
            /* ignore */
          }
          reject(new ApiError(xhr.status, msg));
        }
      };
      xhr.onerror = () => reject(new ApiError(0, 'Network error'));
      xhr.send(form);
    });
  },

  samples: (id: number, params: { filter?: string; state?: string; limit?: number; offset?: number }) =>
    request<{ total: number; items: SampleItem[] }>(`sensors/${id}/samples?${qs(params)}`),
  labelSamples: (id: number, sampleIds: number[], stateKey: string | null) =>
    request<{ updated: number }>(`sensors/${id}/samples/label`, send('POST', { sample_ids: sampleIds, state_key: stateKey })),
  acceptSuggestions: (id: number, sampleIds: number[]) =>
    request<{ updated: number }>(`sensors/${id}/samples/accept-suggestions`, send('POST', { sample_ids: sampleIds })),
  verifySamples: (id: number, sampleIds: number[]) =>
    request<{ verified: number }>(`sensors/${id}/samples/verify`, send('POST', { sample_ids: sampleIds })),
  deleteSamples: (id: number, sampleIds: number[]) =>
    request<{ deleted: number }>(`sensors/${id}/samples/delete`, send('POST', { sample_ids: sampleIds })),
  sampleImageUrl: (sampleId: number, size: 'thumb' | 'full' = 'thumb') => `${BASE}samples/${sampleId}/image?size=${size}`,

  quality: (id: number) => request<Quality>(`sensors/${id}/quality`),
  history: (id: number, limit = 100) => request<Prediction[]>(`sensors/${id}/history?limit=${limit}`),
  historyImageUrl: (predictionId: number, size: 'thumb' | 'full' = 'full') =>
    `${BASE}history/${predictionId}/image?size=${size}`,
  exportUrl: (id: number) => `${BASE}sensors/${id}/export`,

  reviewRules: () => request<ReviewRules>('review-rules'),
  saveReviewRules: (rules: ReviewRules) => request<ReviewRules>('review-rules', send('PUT', rules)),
  review: () => request<{ total: number; items: ReviewItem[] }>('review'),
  answerReview: (predictionId: number, action: 'confirm' | 'label' | 'skip', stateKey?: string) =>
    request(`review/${predictionId}`, send('POST', { action, state_key: stateKey })),

  importBundle(file: File) {
    const form = new FormData();
    form.append('file', file, file.name);
    return request<{ id: number }>('import', { method: 'POST', body: form });
  },
};
