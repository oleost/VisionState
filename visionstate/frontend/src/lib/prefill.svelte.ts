// What another page hands the new sensor wizard (the ESP32 guide: the device's name and address).
// Kept in memory only, never in the URL: the address holds the device's password.
export interface NewSensorPrefill {
  mode: 'picture' | 'sound';
  name: string;
  sourceType: string;
  source: string;
  /** A light the device has (an ESP32-CAM's flash): offered, not switched on by itself. */
  light?: string | null;
}

export const prefill = $state<{ newSensor: NewSensorPrefill | null }>({ newSensor: null });

/** The wizard takes it once. */
export function takeNewSensorPrefill(): NewSensorPrefill | null {
  const value = prefill.newSensor;
  prefill.newSensor = null;
  return value;
}
