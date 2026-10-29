// Ports and paths shared by the Playwright config and the tests.
export const APP_PORT = Number(process.env.VS_E2E_PORT ?? 8199);
export const CAMERA_PORT = Number(process.env.VS_E2E_CAMERA_PORT ?? 8198);
export const CAMERA_URL = `http://127.0.0.1:${CAMERA_PORT}`;
/** Python interpreter with the backend requirements (e.g. ../backend/.venv/Scripts/python on Windows). */
export const PYTHON = process.env.VS_PYTHON ?? 'python';

export const SENSOR_NAME = 'Garage door';
/** An object sensor looking at a real photo (a person with dogs, see backend/tests/assets). */
export const OBJECT_SENSOR_NAME = 'Beach';
export const PHOTO_URL = (name: string) => `${CAMERA_URL}/photo/${name}.jpg`;
/** An object sensor that recognises faces, looking at two NASA astronauts (public domain); taught one of them. */
export const FACE_SENSOR_NAME = 'Crew';
/** A reading sensor looking at a drawn LCD counter. */
export const READING_SENSOR_NAME = 'Power meter';
export const DISPLAY_URL = (text: string, style: 'lcd' | 'led' = 'lcd') =>
  `${CAMERA_URL}/display.jpg?text=${encodeURIComponent(text)}&style=${style}`;
/** A reading sensor looking at a drawn mechanical counter (rolling digit wheels). */
export const COUNTER_SENSOR_NAME = 'Water meter';
export const COUNTER_URL = (value: number, digits = 7, decimals = 3) =>
  `${CAMERA_URL}/counter.jpg?value=${value}&digits=${digits}&decimals=${decimals}`;
/** The window with the seven wheels in that frame (backend/tests/displays.py, counter_box(7)). */
export const COUNTER_BOX = { x: 99 / 800, y: 118 / 360, w: 602 / 800, h: 130 / 360 };
/** A sound sensor listening to the fake camera's stream with sound (H.264 video and AAC). */
export const SOUND_SENSOR_NAME = 'Hallway';
export const SOUND_CAMERA_URL = `${CAMERA_URL}/camera.ts`;
/** An ESP32-like microphone: sound only, as WAV. */
export const SOUND_WAV_URL = `${CAMERA_URL}/sound.wav`;
/** Plays a sound once in the fake camera's streams (CC0 recordings, or "chime": a drawn doorbell). */
export const PLAY_URL = (sound: 'siren' | 'dog_bark' | 'smoke_alarm' | 'speech' | 'chime') => `${CAMERA_URL}/play?sound=${sound}`;
