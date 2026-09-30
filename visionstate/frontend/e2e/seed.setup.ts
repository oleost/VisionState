// Creates a trained sensor (plus review items and one wrong label) that the UI tests look at.
import { expect, test as setup } from '@playwright/test';
import { CAMERA_URL, DISPLAY_URL, OBJECT_SENSOR_NAME, PHOTO_URL, READING_SENSOR_NAME, SENSOR_NAME } from './env';

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

setup('seed an object sensor', async ({ request }) => {
  const created = await request.post('api/v1/sensors', {
    data: {
      name: OBJECT_SENSOR_NAME,
      kind: 'objects',
      source_type: 'http',
      source: PHOTO_URL('beach'),
      objects: { classes: ['dog', 'person', 'car'] },
      interval_s: 5,
    },
  });
  expect(created.ok()).toBeTruthy();
  const sensor = await created.json();
  // The detector loads on first use; then the dogs and the person are reported.
  await expect
    .poll(
      async () => {
        const view = await (await request.get(`api/v1/sensors/${sensor.id}`)).json();
        return view.objects.live.filter((o: { on: boolean }) => o.on).length;
      },
      { timeout: 90_000 },
    )
    .toBe(2);
});

setup('seed a reading sensor', async ({ request }) => {
  const created = await request.post('api/v1/sensors', {
    data: {
      name: READING_SENSOR_NAME,
      kind: 'reading',
      source_type: 'http',
      source: DISPLAY_URL('0012345.6'),
      reading: { mode: 'counter', decimals: 1, unit: 'kWh', device_class: 'energy' },
      interval_s: 5,
      debounce: 1,
    },
  });
  expect(created.ok()).toBeTruthy();
  const sensor = await created.json();
  await expect
    .poll(async () => (await (await request.get(`api/v1/sensors/${sensor.id}`)).json()).reading.value, { timeout: 60_000 })
    .toBe('12345.6');
});
