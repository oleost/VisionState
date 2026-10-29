// Creates a trained sensor (plus review items and one wrong label) that the UI tests look at.
import { expect, test as setup } from '@playwright/test';
import {
  CAMERA_URL,
  COUNTER_BOX,
  COUNTER_SENSOR_NAME,
  COUNTER_URL,
  DISPLAY_URL,
  FACE_SENSOR_NAME,
  OBJECT_SENSOR_NAME,
  PHOTO_URL,
  PLAY_URL,
  READING_SENSOR_NAME,
  SENSOR_NAME,
  SOUND_CAMERA_URL,
  SOUND_SENSOR_NAME,
} from './env';
import { soundModelInstalled } from './helpers';

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

setup('seed a mechanical counter sensor', async ({ request }) => {
  const created = await request.post('api/v1/sensors', {
    data: {
      name: COUNTER_SENSOR_NAME,
      kind: 'reading',
      source_type: 'http',
      source: COUNTER_URL(89939.5), // the last two wheels are half way to the next digit
      roi: COUNTER_BOX,
      reading: { mode: 'counter', display: 'counter', digits: 7, decimals: 3, unit: 'm³', device_class: 'water' },
      interval_s: 5,
      debounce: 1,
    },
  });
  expect(created.ok()).toBeTruthy();
  const sensor = await created.json();
  // The last wheel stands exactly half way from 9 to 0: either neighbour is right.
  await expect
    .poll(async () => (await (await request.get(`api/v1/sensors/${sensor.id}`)).json()).reading.value, { timeout: 60_000 })
    .toMatch(/^89\.9(39|40)$/);
});

setup('seed a sensor that recognises faces', async ({ request }) => {
  const created = await request.post('api/v1/sensors', {
    data: {
      name: FACE_SENSOR_NAME,
      kind: 'objects',
      source_type: 'http',
      source: PHOTO_URL('face_crew'),
      objects: { classes: ['person'], faces: { enabled: true, ask_unknown: false } },
      interval_s: 5,
    },
  });
  expect(created.ok()).toBeTruthy();
  const sensor = await created.json();
  type Box = { key: string; score: number; box: number[]; face?: { box?: number[]; problem: string | null } };
  // The face model loads in the background (it is in the bundled models folder, as in CI: no download).
  let frameId = '';
  let people: Box[] = [];
  await expect
    .poll(
      async () => {
        const view = await (await request.get(`api/v1/sensors/${sensor.id}`)).json();
        frameId = view.live.frame_id;
        people = (view.objects.detections as Box[]).filter((d) => d.key === 'person' && d.face?.box && !d.face.problem);
        return people.length;
      },
      { timeout: 120_000 },
    )
    .toBe(2);
  // Suni Williams stands on the left; the test teaches the other one by hand.
  const left = people.sort((a, b) => a.box[0] - b.box[0])[0];
  const taught = await request.post(`api/v1/sensors/${sensor.id}/faces`, {
    data: { frame_id: frameId, box: left.box, score: left.score, new_person: 'Suni' },
  });
  expect(taught.ok(), await taught.text()).toBeTruthy();
});

setup('seed a sound sensor on a camera with sound', async ({ request }) => {
  setup.skip(!(await soundModelInstalled(request)), 'the sound model is not on disk (tools/soundmodel/README.md)');
  setup.setTimeout(120_000);
  const created = await request.post('api/v1/sensors', {
    data: {
      name: SOUND_SENSOR_NAME,
      kind: 'sound',
      source_type: 'rtsp',
      source: SOUND_CAMERA_URL,
      // A siren counts from 0.85: the recording's 0.9 is only just heard, so it is asked about in Review.
      sound: { sounds: ['siren', 'dog_bark', 'smoke_alarm'], clips: true, clear_after_s: 3, thresholds: { siren: 0.85 } },
      review: { cooldown_s: 0 },
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const sensor = await created.json();
  await expect
    .poll(async () => (await (await request.get(`api/v1/sensors/${sensor.id}`)).json()).sound.line, { timeout: 30_000 })
    .not.toBeNull();
  await request.get(PLAY_URL('siren'));
  // Heard, with a clip (with the camera's video), and asked about in Review once it stopped.
  await expect
    .poll(
      async () => {
        const rows = (await (await request.get(`api/v1/history?sensor=${sensor.id}&event=heard`)).json()).items;
        return rows.some((r: { clip_video: boolean }) => r.clip_video);
      },
      { timeout: 60_000 },
    )
    .toBe(true);
  await expect
    .poll(async () => (await (await request.get(`api/v1/review?sensor=${sensor.id}`)).json()).total, { timeout: 60_000 })
    .toBeGreaterThan(0);
  // An own sound taught from that clip.
  const rows = (await (await request.get(`api/v1/history?sensor=${sensor.id}&event=heard`)).json()).items;
  const taught = await request.post(`api/v1/sensors/${sensor.id}/sounds/teach`, {
    data: { history_id: rows[0].id, new_name: 'Our siren' },
  });
  expect(taught.ok(), await taught.text()).toBeTruthy();
});
