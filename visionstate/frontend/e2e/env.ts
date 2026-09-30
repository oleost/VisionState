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
/** A reading sensor looking at a drawn LCD counter. */
export const READING_SENSOR_NAME = 'Power meter';
export const DISPLAY_URL = (text: string, style: 'lcd' | 'led' = 'lcd') =>
  `${CAMERA_URL}/display.jpg?text=${encodeURIComponent(text)}&style=${style}`;
