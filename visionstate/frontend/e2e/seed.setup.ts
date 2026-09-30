// Creates a trained sensor (plus review items and one wrong label) that the UI tests look at.
import { expect, test as setup } from '@playwright/test';
import { CAMERA_URL, SENSOR_NAME } from './env';

setup('seed a trained sensor', async ({ request }) => {
  const created = await request.post('api/v1/sensors', {
    data: {
      name: SENSOR_NAME,
      source_type: 'http',
      source: `${CAMERA_URL}/snapshot.jpg`,
      roi: { x: 0.2, y: 0.25, w: 0.6, h: 0.65 },
      states: [{ name: 'Open' }, { name: 'Closed' }, { name: 'Partial' }],
      interval_s: 2,
      // Make every frame a review candidate so the review queue has content.
      review: { below: 0.999, cooldown_s: 0 },
    },
  });
  expect(created.ok()).toBeTruthy();
  const sensor = await created.json();

  for (const state of ['open', 'closed', 'partial']) {
    await request.get(`${CAMERA_URL}/set?state=${state}`);
    for (let i = 0; i < 5; i++) {
      const res = await request.post(`api/v1/sensors/${sensor.id}/capture`, { data: { state_key: state } });
      expect(res.ok()).toBeTruthy();
    }
  }
  // One deliberately wrong label, for the "possibly mislabelled" list.
  await request.get(`${CAMERA_URL}/set?state=open`);
  await request.post(`api/v1/sensors/${sensor.id}/capture`, { data: { state_key: 'closed' } });

  await expect
    .poll(async () => (await (await request.get(`api/v1/sensors/${sensor.id}`)).json()).status, { timeout: 60_000 })
    .toBe('ok');
  await expect
    .poll(async () => (await (await request.get('api/v1/review')).json()).total, { timeout: 60_000 })
    .toBeGreaterThan(0);
});
