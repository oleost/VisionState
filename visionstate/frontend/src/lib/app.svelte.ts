// Global app state: server config, status and toasts.
import { api } from './api';
import type { AppConfig, Sensor, StateDef, Status } from './types';
import { TOAST_MAX, TOAST_MS } from './ui';

export const app = $state<{ config: AppConfig | null; status: Status | null; error: string }>({
  config: null,
  status: null,
  error: '',
});

export async function loadConfig(): Promise<void> {
  app.config = await api.config();
}

export async function refreshStatus(): Promise<void> {
  try {
    app.status = await api.status();
    app.error = '';
  } catch (err) {
    app.error = (err as Error).message;
  }
}

export interface Toast {
  id: number;
  message: string;
  tone: 'ok' | 'warn' | 'danger';
  action?: { label: string; run: () => void };
}

export const toasts = $state<Toast[]>([]);
let nextToast = 1;

export function toast(message: string, opts: Partial<Omit<Toast, 'id' | 'message'>> = {}): void {
  const item: Toast = { id: nextToast++, message, tone: opts.tone ?? 'ok', action: opts.action };
  toasts.push(item);
  if (toasts.length > TOAST_MAX) toasts.splice(0, toasts.length - TOAST_MAX);
  setTimeout(() => dismiss(item.id), TOAST_MS);
}

export function dismiss(id: number): void {
  const index = toasts.findIndex((t) => t.id === id);
  if (index >= 0) toasts.splice(index, 1);
}

export function toastError(err: unknown): void {
  toast((err as Error).message || 'Something went wrong', { tone: 'danger' });
}

/** Colour and name of a state key, including the reserved "unknown" state. */
export function stateInfo(sensor: { states: StateDef[] }, key: string | null | undefined): StateDef {
  const found = sensor.states.find((s) => s.key === key);
  if (found) return found;
  return { key: key ?? '', name: key ? key.charAt(0).toUpperCase() + key.slice(1) : '—', color: 'var(--c-unknown)' };
}

/** Confidence to show next to the published state; hidden while the state is unknown. */
export function shownConfidence(sensor: Sensor): number | null {
  const published = sensor.live.published;
  return published && published !== app.config?.unknown_state ? sensor.live.confidence : null;
}
