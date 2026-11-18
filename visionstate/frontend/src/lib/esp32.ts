// An ESP32 of ours: what its check (POST esphome/check) says about its sound or picture.
import type { EsphomeCheckPart, MediaPlayer } from './types';

/** A media player as a choice: its name and integration ("Living room · Google Cast"), its entity ID
 * too when that is not enough to tell it from another, and whether it is unavailable. */
export function playerLabel(player: MediaPlayer, all: MediaPlayer[]): string {
  const same = all.filter((p) => p.name === player.name && p.integration === player.integration).length > 1;
  const parts = [player.name, player.integration, same ? player.entity_id : ''].filter(Boolean);
  return parts.join(' · ') + (player.state === 'unavailable' ? ' (unavailable)' : '');
}

export function checkText(part: EsphomeCheckPart | undefined): string {
  if (!part) return '';
  if (part.ok) return part.bytes ? `OK (a picture of ${Math.round(part.bytes / 1000)} kB)` : 'OK';
  if (part.status === 401) return "Wrong password: give the one in the device's file (stream_password)";
  if (part.status === 409) return 'Busy: two listeners already (VLC, another VisionState)';
  if (part.status === 404) return 'Not in this firmware (another kind of device?)';
  return part.detail ?? `The device answered ${part.status}`;
}
