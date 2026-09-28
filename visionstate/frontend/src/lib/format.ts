// Formatting helpers.

export const pct = (value: number | null | undefined, digits = 0) =>
  value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`;

export function ago(input: string | number | null | undefined, nowMs = Date.now()): string {
  if (input === null || input === undefined) return 'never';
  const ms = typeof input === 'number' ? input * 1000 : Date.parse(input);
  const s = Math.max(0, Math.round((nowMs - ms) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86_400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86_400)} d ago`;
}

export function dateTime(input: string): string {
  return new Date(input).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export const mb = (bytes: number) => `${Math.round(bytes / 1_000_000)} MB`;

/** Mirrors the backend's slugify (api/common.py) so previews match what gets created. */
export const slugify = (text: string, fallback = 'item') =>
  text.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 48) || fallback;
