// An ESP32 of ours: what its check (POST esphome/check) says about its sound or picture.
import type { EsphomeCheckPart } from './types';

export function checkText(part: EsphomeCheckPart | undefined): string {
  if (!part) return '';
  if (part.ok) return part.bytes ? `OK (a picture of ${Math.round(part.bytes / 1000)} kB)` : 'OK';
  if (part.status === 401) return "Wrong password: give the one in the device's file (stream_password)";
  if (part.status === 409) return 'Busy: two listeners already (VLC, another VisionState)';
  if (part.status === 404) return 'Not in this firmware (another kind of device?)';
  return part.detail ?? `The device answered ${part.status}`;
}
