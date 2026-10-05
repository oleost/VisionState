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

/** "12 kB", "820 MB", "1.4 GB" (binary units, like the backend's size limit). */
export function size(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${Math.max(0, Math.round(bytes / 1024))} kB`;
}

/** Mirrors the backend's slugify (api/common.py) so previews match what gets created. */
export const slugify = (text: string, fallback = 'item') =>
  text.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 48) || fallback;

/** Mirrors the backend's ha_slug (mqtt.py): how Home Assistant turns a name into an entity ID. */
const TRANSLITERATE: Record<string, string> = { ø: 'o', æ: 'ae', å: 'a', ß: 'ss', đ: 'd', ł: 'l', œ: 'oe', þ: 'th' };
export const haSlug = (text: string) =>
  text
    .toLowerCase()
    .replace(/[øæåßđłœþ]/g, (c) => TRANSLITERATE[c])
    .normalize('NFKD')
    .replace(/[^\x00-\x7f]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '') || 'unknown';

export const plural =(n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
