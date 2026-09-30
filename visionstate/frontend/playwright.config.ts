// UI tests against the real backend and a fake camera, on desktop and on a touch phone.
//   npm run build && npm run e2e          (set VS_PYTHON to the backend's Python if needed)
import { defineConfig, devices } from '@playwright/test';
import os from 'node:os';
import path from 'node:path';
import { APP_PORT, CAMERA_PORT, CAMERA_URL, PYTHON } from './e2e/env';

const root = path.resolve(import.meta.dirname, '..', '..');
const backend = path.join(root, 'visionstate', 'backend');
// A fresh data folder per run, so every run starts from the same state.
const runDir = path.join(os.tmpdir(), `visionstate-e2e-${Date.now()}`);

export default defineConfig({
  testDir: 'e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: `http://127.0.0.1:${APP_PORT}/`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: `"${PYTHON}" "${path.join(root, 'scripts', 'fake_camera.py')}" ${CAMERA_PORT}`,
      url: `${CAMERA_URL}/snapshot.jpg`,
      reuseExistingServer: false,
    },
    {
      command: `"${PYTHON}" -m visionstate`,
      cwd: backend,
      url: `http://127.0.0.1:${APP_PORT}/api/v1/status`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        VISIONSTATE_PORT: String(APP_PORT),
        VISIONSTATE_DATA: path.join(runDir, 'data'),
        VISIONSTATE_MEDIA: path.join(runDir, 'media'),
        VISIONSTATE_FRONTEND: path.join(import.meta.dirname, 'dist'),
        VISIONSTATE_BUNDLED_MODELS: path.join(backend, 'models'),
      },
    },
  ],
  projects: [
    { name: 'setup', testMatch: /seed\.setup\.ts/ },
    {
      name: 'desktop',
      testMatch: /\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
      dependencies: ['setup'],
    },
    {
      name: 'mobile',
      testMatch: /\.spec\.ts/,
      use: { ...devices['Pixel 7'] }, // 412×915, touch, mobile user agent
      dependencies: ['setup'],
    },
  ],
});
