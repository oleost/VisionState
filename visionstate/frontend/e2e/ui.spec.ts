// UI tests, run once on desktop (mouse) and once on a phone (touch). See playwright.config.ts.
import { expect, test } from '@playwright/test';
import path from 'node:path';
import { CAMERA_URL, COUNTER_BOX, COUNTER_URL, DISPLAY_URL, PHOTO_URL } from './env';
import {
  center,
  doubleTap,
  drag,
  expectNoClipping,
  expectNoHorizontalOverflow,
  hold,
  isTouch,
  press,
  seededCounterSensorId,
  seededObjectSensorId,
  seededReadingSensorId,
  seededSensorId,
  watchErrors,
} from './helpers';

test('every page renders without errors and fits the screen', async ({ page, request }, info) => {
  const id = await seededSensorId(request);
  const objectId = await seededObjectSensorId(request);
  const readingId = await seededReadingSensorId(request);
  const counterId = await seededCounterSensorId(request);
  const pages: [string, string, RegExp | string][] = [
    ['dashboard', '', 'Sensors'],
    ['new-sensor', 'sensors/new', 'Name it and pick a camera'],
    ['label', `sensors/${id}/label`, 'What state is this?'],
    ['upload', `sensors/${id}/upload`, 'Drop images, ZIP archives or video here'],
    ['dataset', `sensors/${id}/dataset`, /\d+ images/],
    ['quality', `sensors/${id}/quality`, 'What gets mixed up'],
    ['history', `sensors/${id}/history`, 'State changes and frames flagged for review'],
    ['sensor-settings', `sensors/${id}/settings`, 'When to check'],
    ['objects-live', `sensors/${objectId}/live`, 'Right now'],
    ['objects-history', `sensors/${objectId}/history`, 'When each object appeared and cleared'],
    ['objects-settings', `sensors/${objectId}/settings`, 'Each object gets an on/off sensor'],
    ['reading-live', `sensors/${readingId}/live`, 'What the reader sees'],
    ['reading-history', `sensors/${readingId}/history`, 'Every new value and every rejected reading'],
    ['reading-settings', `sensors/${readingId}/settings`, 'Digits after the decimal point'],
    ['reading-quality', `sensors/${readingId}/quality`, 'Readings per day'],
    ['counter-live', `sensors/${counterId}/live`, 'One wheel per field'],
    ['counter-settings', `sensors/${counterId}/settings`, 'Number of digits'],
    ['review', 'review', 'Frames the AI was unsure about'],
    ['settings', 'settings', 'AI model'],
  ];
  for (const [name, route, ready] of pages) {
    await test.step(name, async () => {
      const errors = watchErrors(page);
      await page.goto(`#/${route}`);
      await expect(page.getByText(ready).first()).toBeVisible();
      // The app polls live frames, so the network never goes idle: wait for images instead
      // (lazy ones off-screen never load, so they don't count).
      await page.waitForFunction(() => [...document.images].every((img) => img.complete || img.loading === 'lazy'));
      await expectNoHorizontalOverflow(page);
      await expectNoClipping(page, '.btn, .state-btn, .chip, .pill, .card');
      await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, `${name}.png`), fullPage: true });
      errors.expectNone();
    });
  }
});

test('swiping on a camera frame scrolls the page', async ({ page, request }, info) => {
  test.skip(!isTouch(info), 'touch only');
  const id = await seededSensorId(request);
  for (const route of ['', `sensors/${id}/label`, 'review']) {
    await test.step(route || 'dashboard', async () => {
      await page.goto(`#/${route}`);
      const frame = page.locator('.roi').first();
      await expect(frame.locator('img')).toBeVisible();
      await page.evaluate(() => window.scrollTo(0, 0));
      const room = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
      if (room < 100) return; // page too short to scroll (e.g. a short review queue)
      const at = await center(frame);
      await drag(page, info, at, { x: at.x, y: at.y - 250 });
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(50);
    });
  }
});

test('new sensor wizard creates a sensor', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/sensors/new');
  await page.getByPlaceholder('Garage door').fill('Wizard test');
  await press(page.getByText('HTTP snapshot URL'), info);
  await page.getByLabel('Source URL').fill(`${CAMERA_URL}/snapshot.jpg`);
  await press(page.getByRole('button', { name: 'Next' }), info);

  const image = page.locator('.roi img');
  await expect(image).toBeVisible();
  const box = (await image.boundingBox())!;
  await drag(page, info, { x: box.x + box.width * 0.2, y: box.y + box.height * 0.2 }, { x: box.x + box.width * 0.8, y: box.y + box.height * 0.9 });
  await expect(page.locator('.handle.corner')).toHaveCount(4);
  await press(page.getByRole('button', { name: 'Next' }), info);

  await expect(page.getByText('Which states can it be in?')).toBeVisible();
  // "Add state" puts the cursor in the new field; an empty name is not shown as an option.
  await press(page.getByRole('button', { name: 'Add state' }), info);
  await expect(page.getByText('options: open, closed, unknown')).toBeVisible();
  await page.keyboard.type('Partial');
  await expect(page.getByLabel('Name of state 3')).toHaveValue('Partial');
  await expect(page.getByText('options: open, closed, partial, unknown')).toBeVisible();
  if (isTouch(info)) await expect(page.getByText('Keys 1–9 label them later.')).toBeHidden();
  await press(page.getByRole('button', { name: 'Next' }), info);
  await expect(page.getByText('When should it check the camera?')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await press(page.getByRole('button', { name: 'Create sensor and start labelling' }), info);

  await expect(page).toHaveURL(/\/label$/);
  await expect(page.getByText('What state is this?')).toBeVisible();
  const sensorId = Number(page.url().match(/sensors\/(\d+)\//)![1]);
  errors.expectNone();
  await page.goto('about:blank'); // stop live-frame polling before the sensor disappears
  await request.delete(`api/v1/sensors/${sensorId}`);
});

test('new object sensor through the wizard', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/sensors/new');
  await page.getByPlaceholder('Garage door').fill('Driveway test');
  await press(page.getByText('HTTP snapshot URL'), info);
  await page.getByLabel('Source URL').fill(PHOTO_URL('driveway'));
  await press(page.getByRole('button', { name: 'Next' }), info);
  await expect(page.locator('.roi img')).toBeVisible();
  await press(page.getByRole('button', { name: 'Next' }), info); // whole frame

  await press(page.getByRole('radio', { name: /Objects/ }), info);
  // The test on a fresh frame finds the cars (not selected yet) and the person.
  await expect(page.getByText(/Found .*cars \(not selected\)/)).toBeVisible({ timeout: 60_000 });
  await press(page.getByRole('button', { name: 'Car', exact: true }), info);
  await expect(page.getByRole('button', { name: 'Car', exact: true })).toHaveAttribute('aria-pressed', 'true');
  // Everything else is behind "Show all", with search.
  await press(page.getByRole('button', { name: /Show all \d+ objects/ }), info);
  await page.getByPlaceholder(/Search \d+ objects/).fill('bicy');
  await expect(page.getByRole('button', { name: 'Bicycle' })).toHaveCount(1);
  await expect(page.getByText('binary_sensor.visionstate_driveway_test_car')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'wizard-objects.png'), fullPage: true });
  await expectNoClipping(page, '.btn, .chip-btn, .kind, .card');

  await press(page.getByRole('button', { name: 'Next' }), info);
  await press(page.getByRole('button', { name: 'Create sensor' }), info);
  await expect(page).toHaveURL(/\/live$/);
  await expect(page.getByText('Right now')).toBeVisible();
  await expect(page.locator('.count.on').first()).toBeVisible({ timeout: 60_000 });
  const sensorId = Number(page.url().match(/sensors\/(\d+)\//)![1]);
  errors.expectNone();
  await page.goto('about:blank');
  await request.delete(`api/v1/sensors/${sensorId}`);
});

test('new reading sensor through the wizard', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/sensors/new');
  await page.getByPlaceholder('Garage door').fill('Washer');
  await press(page.getByText('HTTP snapshot URL'), info);
  await page.getByLabel('Source URL').fill(DISPLAY_URL('1:05', 'led'));
  await press(page.getByRole('button', { name: 'Next' }), info);
  await expect(page.locator('.roi img')).toBeVisible();
  await press(page.getByRole('button', { name: 'Next' }), info); // whole frame

  await press(page.getByRole('radio', { name: /Reading/ }), info);
  await expect(page.getByText(/Read “1:05”/)).toBeVisible({ timeout: 60_000 });
  // Switching the mode re-tests: "1:05" is 65 minutes left.
  await press(page.getByRole('radio', { name: /Time left/ }), info);
  await expect(page.getByText('65 min')).toBeVisible({ timeout: 30_000 });
  // The region can be drawn right on the preview; it is read again.
  const preview = page.locator('.read-images .roi');
  await preview.scrollIntoViewIfNeeded(); // the mouse and the finger only reach what is on screen
  const box = (await preview.boundingBox())!;
  await drag(page, info, { x: box.x + box.width * 0.05, y: box.y + box.height * 0.1 }, { x: box.x + box.width * 0.95, y: box.y + box.height * 0.9 });
  await expect(preview.locator('.handle.corner')).toHaveCount(4);
  await expect(page.getByText('(reading…)')).toHaveCount(0, { timeout: 30_000 }); // the re-read finished
  await expect(page.getByText('65 min')).toBeVisible();
  await expect(page.getByAltText('The region as the number reader saw it')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.btn, .kind, .mode, .card');
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'wizard-reading.png'), fullPage: true });

  await press(page.getByRole('button', { name: 'Next' }), info);
  await press(page.getByRole('button', { name: 'Create sensor' }), info);
  await expect(page).toHaveURL(/\/live$/);
  await expect(page.locator('.value-card .value')).toHaveText('65 min', { timeout: 60_000 });
  const sensorId = Number(page.url().match(/sensors\/(\d+)\//)![1]);
  errors.expectNone();
  await page.goto('about:blank');
  await request.delete(`api/v1/sensors/${sensorId}`);
});

test('new mechanical counter sensor through the wizard', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/sensors/new');
  await page.getByPlaceholder('Garage door').fill('Gas meter');
  await press(page.getByText('HTTP snapshot URL'), info);
  await page.getByLabel('Source URL').fill(COUNTER_URL(45730));
  await press(page.getByRole('button', { name: 'Next' }), info);
  await expect(page.locator('.roi img')).toBeVisible();
  await press(page.getByRole('button', { name: 'Next' }), info); // whole frame for now

  await press(page.getByRole('radio', { name: /Reading/ }), info);
  await press(page.getByRole('radio', { name: /Mechanical counter/ }), info);
  await expect(page.getByRole('radio', { name: /Time left/ })).toHaveCount(0); // a counter shows no countdown
  await page.getByLabel('Number of digits').fill('7');
  // One field per wheel is drawn over the frame.
  const preview = page.locator('.read-images .roi');
  await expect(preview.getByTestId('digit-cells').locator('.cell')).toHaveCount(7, { timeout: 60_000 });
  // Drag a box around the window with the wheels; it is read again.
  await preview.scrollIntoViewIfNeeded(); // the mouse and the finger only reach what is on screen
  const box = (await preview.boundingBox())!;
  const at = (x: number, y: number) => ({ x: box.x + box.width * x, y: box.y + box.height * y });
  await drag(
    page,
    info,
    at(COUNTER_BOX.x, COUNTER_BOX.y),
    at(COUNTER_BOX.x + COUNTER_BOX.w, COUNTER_BOX.y + COUNTER_BOX.h),
  );
  await expect(preview.locator('.handle.corner')).toHaveCount(4);
  await expect(page.getByText(/Read “0045730”/)).toBeVisible({ timeout: 30_000 });
  // The cells follow the region.
  const cells = (await preview.getByTestId('digit-cells').boundingBox())!;
  expect(Math.abs(cells.width - box.width * COUNTER_BOX.w)).toBeLessThan(box.width * 0.03);
  await page.getByLabel('Digits after the decimal point').fill('3');
  await page.getByLabel('Unit').fill('m³');
  await expect(page.getByText('45.730 m³')).toBeVisible({ timeout: 30_000 });
  // Told there are eight wheels, the seven digits read are not accepted.
  await page.getByLabel('Number of digits').fill('8');
  await expect(page.getByText(/digits, not 8/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel('Number of digits').fill('7');
  await expect(page.getByText('45.730 m³')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('(reading…)')).toHaveCount(0, { timeout: 30_000 });
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.btn, .kind, .mode, .card');
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'wizard-counter.png'), fullPage: true });

  await press(page.getByRole('button', { name: 'Next' }), info);
  await press(page.getByRole('button', { name: 'Create sensor' }), info);
  await expect(page).toHaveURL(/\/live$/);
  await expect(page.locator('.value-card .value')).toHaveText('45.730 m³', { timeout: 60_000 });
  const sensorId = Number(page.url().match(/sensors\/(\d+)\//)![1]);
  // The settings page shows the same fields over the region.
  await page.goto(`#/sensors/${sensorId}/settings`);
  await expect(page.getByTestId('digit-cells').locator('.cell')).toHaveCount(7);
  await expect(page.getByLabel('Number of digits')).toHaveValue('7');
  errors.expectNone();
  await page.goto('about:blank');
  await request.delete(`api/v1/sensors/${sensorId}`);
});

test('reading wizard suggests the decimals the display shows', async ({ page }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/sensors/new');
  await page.getByPlaceholder('Garage door').fill('Meter');
  await press(page.getByText('HTTP snapshot URL'), info);
  await page.getByLabel('Source URL').fill(DISPLAY_URL('1234.5'));
  await press(page.getByRole('button', { name: 'Next' }), info);
  await expect(page.locator('.roi img')).toBeVisible();
  await press(page.getByRole('button', { name: 'Next' }), info);
  await press(page.getByRole('radio', { name: /Reading/ }), info);
  // 0 decimals would make it 12345; the editor offers the one digit after the point.
  await expect(page.getByText(/1 digit after the point\?/)).toBeVisible({ timeout: 60_000 });
  await press(page.getByRole('button', { name: 'Use 1' }), info);
  await expect(page.getByLabel('Digits after the decimal point')).toHaveValue('1');
  await expect(page.getByText(/1 digit after the point\?/)).toHaveCount(0);
  await expect(page.getByText(/→\s*1234\.5/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Create sensor', exact: true })).toHaveCount(0); // not on step 3
  await press(page.getByRole('button', { name: 'Next' }), info);
  await expect(page.getByRole('button', { name: 'Create sensor', exact: true })).toBeVisible(); // no labelling
  await expectNoHorizontalOverflow(page);
  errors.expectNone();
});

test('a history frame opens in full', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededSensorId(request);
  // Paused, so no new rows push the list down while it is tapped.
  expect((await request.patch(`api/v1/sensors/${id}`, { data: { enabled: false } })).ok()).toBe(true);
  await page.goto(`#/sensors/${id}/history`);
  await expect(page.getByRole('link', { name: 'Settings → Storage' })).toBeVisible();
  const thumb = page.getByRole('button', { name: 'Show the whole frame' }).first();
  await press(thumb, info);
  await expect(page.locator('img.full').first()).toBeVisible();
  await expect.poll(() => page.locator('img.full').first().evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await press(thumb, info);
  await expect(page.locator('img.full')).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
  errors.expectNone();
  await request.patch(`api/v1/sensors/${id}`, { data: { enabled: true } });
});

test('reading sensor shows its value everywhere', async ({ page, request }) => {
  const errors = watchErrors(page);
  const id = await seededReadingSensorId(request);
  await page.goto(`#/sensors/${id}/live`);
  await expect(page.locator('.value-card .value')).toHaveText('12345.6 kWh');
  await expect(page.getByText('Last reading accepted')).toBeVisible();
  await page.goto(`#/sensors/${id}/history`);
  await expect(page.getByText('12345.6 kWh').first()).toBeVisible();
  await page.goto('#/');
  await expect(page.getByText('12345.6 kWh').first()).toBeVisible();
  await expect(page.getByText('Reads a counter in kWh')).toBeVisible();
  errors.expectNone();
});

test('object sensor shows boxes and history', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededObjectSensorId(request);
  await page.goto(`#/sensors/${id}/live`);
  await expect(page.getByText(/The AI sees .*dogs/)).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.roi .box').first()).toBeVisible();
  await page.goto(`#/sensors/${id}/history`);
  const row = page.getByRole('button', { name: /Dog detected/ }).first();
  await press(row, info);
  await expect(page.locator('.full .box').first()).toBeVisible();
  await page.goto('#/');
  await expect(page.getByText(/\d+ dogs, 1 person|1 person, \d+ dogs/).first()).toBeVisible();
  await expectNoHorizontalOverflow(page);
  errors.expectNone();
});

test('region editor: move, add and remove corners', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededSensorId(request);
  await page.goto(`#/sensors/${id}/settings`);
  const roi = page.locator('.roi');
  await roi.scrollIntoViewIfNeeded();
  await expect(roi.locator('img')).toBeVisible();
  const corners = roi.locator('.handle.corner');
  await expect(corners).toHaveCount(4);

  // Drag a "+" outwards: a fifth corner appears.
  const plus = await center(roi.locator('.handle.add').nth(1));
  await drag(page, info, plus, { x: plus.x + 25, y: plus.y });
  await expect(corners).toHaveCount(5);
  await expect(page.getByRole('button', { name: 'Reset to rectangle' })).toBeVisible();

  // Drag one corner on its own.
  const before = await center(corners.nth(0));
  await drag(page, info, before, { x: before.x + 15, y: before.y + 15 });
  const after = await center(corners.nth(0));
  expect(Math.round(after.x - before.x)).toBeGreaterThan(8);

  // Double-click / double-tap removes a corner.
  await doubleTap(page, info, await center(corners.nth(2)));
  await expect(corners).toHaveCount(4);

  if (isTouch(info)) {
    // Long-press removes a corner (add one first so at least 3 remain).
    const add = await center(roi.locator('.handle.add').nth(0));
    await drag(page, info, add, { x: add.x, y: add.y - 20 });
    await expect(corners).toHaveCount(5);
    await hold(page, info, await center(corners.nth(1)), 800);
    await expect(corners).toHaveCount(4);
  }

  await press(page.getByRole('button', { name: 'Reset to rectangle' }), info);
  await expect(page.getByRole('button', { name: 'Reset to rectangle' })).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
  errors.expectNone();
});

test('labelling a frame shows a confirmation', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededSensorId(request);
  await page.goto(`#/sensors/${id}/label`);
  await expect(page.locator('.live img')).toBeVisible();
  // Keyboard shortcuts are hidden on touch screens and shown with a mouse.
  if (isTouch(info)) await expect(page.locator('.kbd-only').first()).toBeHidden();
  else await expect(page.locator('.kbd-only').first()).toBeVisible();
  await press(page.locator('.state-btn', { hasText: 'Partial' }), info);
  await expect(page.getByText('Saved as Partial')).toBeVisible();
  errors.expectNone();
});

test('when to check: no regular check, one state of an entity, and a light', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededReadingSensorId(request);
  // Start from the defaults, whatever an earlier (failed) run left behind.
  const before = (await (await request.get('api/v1/config')).json()).trigger_defaults;
  expect((await request.patch(`api/v1/sensors/${id}`, { data: { triggers: before } })).ok()).toBe(true);
  await page.goto(`#/sensors/${id}/settings`);
  await expect(page.getByText('When to check')).toBeVisible();

  // The regular check can be switched off; its interval then goes away.
  const regular = page.getByRole('checkbox', { name: 'Regular check' });
  await expect(regular).toBeChecked();
  await press(regular, info);
  await expect(page.getByLabel('Seconds between regular checks')).toHaveCount(0);
  await expect(page.getByText('Off: only checks when triggered')).toBeVisible();

  // A trigger entity can be limited to one of its states.
  const add = page.getByLabel('Add trigger entity');
  await add.fill('sensor.watermeter_status');
  await add.press('Enter');
  await page.getByLabel('Only when sensor.watermeter_status becomes').fill('Flow finished');

  // A light: only lights and switches are accepted.
  const light = page.getByLabel('Light to switch on');
  await light.fill('camera.meter');
  await light.press('Enter');
  await expect(page.getByText('Wait before taking the frame')).toHaveCount(0);
  await light.fill('light.meter_flash');
  await light.press('Enter');
  await expect(page.getByText('Wait before taking the frame')).toBeVisible();
  await expect(page.getByLabel('Light to switch on')).toHaveCount(0); // one light at most

  await press(page.getByRole('button', { name: 'Save changes' }), info);
  await expect(page.getByText('Settings saved')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.btn, .chip, .card, .chip-entity');
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'settings-triggers.png'), fullPage: true });

  const saved = (await (await request.get(`api/v1/sensors/${id}`)).json()).triggers;
  expect(saved.regular).toBe(false);
  expect(saved.only_states).toEqual({ 'sensor.watermeter_status': 'Flow finished' });
  expect(saved.light_entity).toBe('light.meter_flash');
  await page.reload();
  await expect(page.getByRole('checkbox', { name: 'Regular check' })).not.toBeChecked();
  await expect(page.getByLabel('Only when sensor.watermeter_status becomes')).toHaveValue('Flow finished');
  await expect(page.getByRole('button', { name: 'Remove light.meter_flash' })).toBeVisible();
  errors.expectNone();
  expect((await request.patch(`api/v1/sensors/${id}`, { data: { triggers: before } })).ok()).toBe(true);
});

test('rejected readings: review queue and quality tab', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const garage = await seededSensorId(request);
  // The seeded state sensor flags a frame for review on every check; pause it so ours is the newest.
  await request.patch(`api/v1/sensors/${garage}`, { data: { enabled: false } });
  const created = await request.post('api/v1/sensors', {
    data: {
      name: 'Gas meter',
      kind: 'reading',
      source_type: 'http',
      source: DISPLAY_URL('00500'),
      reading: { mode: 'counter', unit: 'm³' },
      interval_s: 3600,
      debounce: 1,
    },
  });
  const id = (await created.json()).id;
  try {
    await expect.poll(async () => (await (await request.get(`api/v1/sensors/${id}`)).json()).reading.value, { timeout: 60_000 }).toBe('500');
    // The display now shows less: a counter can not go down, so the reading is rejected.
    await request.patch(`api/v1/sensors/${id}`, { data: { source: DISPLAY_URL('00400') } });
    await request.post(`api/v1/sensors/${id}/classify`);
    await expect.poll(async () => (await (await request.get(`api/v1/sensors/${id}`)).json()).reading.last?.reason, { timeout: 30_000 }).toBe('went down');

    // In the review queue: say it misread, and what the meter showed.
    await page.goto('#/review');
    // A check of the paused sensor may still have been running: skip anything newer than ours.
    const ours = page.getByText(/Did it read “00400”/);
    const position = page.locator('.main .mono').first();
    for (let i = 0; i < 5; i++) {
      await expect(page.getByText(/Did it read “00400”|Is this /).first()).toBeVisible();
      if (await ours.isVisible()) break;
      // Wait for the next item before looking again, or a second Skip lands on ours.
      const before = (await position.textContent()) ?? '';
      await press(page.getByRole('button', { name: 'Skip' }), info);
      await expect(position).not.toHaveText(before);
    }
    await expect(ours).toBeVisible();
    await expect(page.getByText('Rejected: a counter can not go down')).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'review-reading.png'), fullPage: true });
    await press(page.getByRole('button', { name: 'Misread' }), info);
    await page.getByLabel('The right value').fill('501');
    await press(page.getByRole('button', { name: 'Save misread' }), info);
    await expect(page.getByText(/Did it read “00400”/)).toHaveCount(0);

    // The quality tab sums it up and keeps the answer, which can be changed.
    await page.goto(`#/sensors/${id}/quality`);
    await expect(page.getByText('1 of 2 readings accepted').first()).toBeVisible();
    await expect(page.getByText('50.0 %').first()).toBeVisible();
    await expect(page.locator('.reasons').getByText('a counter can not go down')).toBeVisible();
    await expect(page.getByText('Misread — was 501 m³')).toBeVisible();
    await expect(page.getByText('misread was caught by the checks.')).toBeVisible();
    await expect(page.locator('.chart .day')).toHaveCount(30);
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.btn, .chip, .card');
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'reading-quality-rejected.png'), fullPage: true });
    await press(page.getByRole('button', { name: 'Change' }), info);
    await press(page.getByRole('button', { name: 'Read correctly' }).first(), info);
    await expect(page.getByText(/correct reading was rejected\s+— is the change limit too low\?/)).toBeVisible();

    // Dismiss all takes a sensor's waiting items out of the queue; answers already given stay.
    const waiting = async () =>
      ((await (await request.get('api/v1/review')).json()).sensors as { id: number; count: number }[]).find((s) => s.id === id)?.count ?? 0;
    await request.post(`api/v1/sensors/${id}/classify`);
    await expect.poll(waiting, { timeout: 30_000 }).toBe(1);
    await page.reload();
    const bar = page.locator('.dismiss');
    await expect(bar).toContainText('1 waiting in the review queue.');
    await expectNoClipping(page, '.btn, .chip, .card');
    await press(bar.getByRole('button', { name: 'Dismiss all' }), info);
    await press(bar.getByRole('button', { name: 'Press again' }), info);
    await expect(page.getByText('Dismissed 1 item from the review queue')).toBeVisible();
    await expect(bar).toHaveCount(0);
    await expect(page.getByText(/correct reading was rejected/)).toBeVisible();

    // ... and the same per sensor on the review page.
    await request.post(`api/v1/sensors/${id}/classify`);
    await expect.poll(waiting, { timeout: 30_000 }).toBe(1);
    await page.goto('#/review');
    const row = page.locator('.wait-row').filter({ hasText: 'Gas meter' });
    await expect(row).toContainText('1 waiting');
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.btn, .card');
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'review-dismiss.png'), fullPage: true });
    await press(row.getByRole('button', { name: 'Dismiss all' }), info);
    await press(row.getByRole('button', { name: 'Press again' }), info);
    await expect(page.getByText('Dismissed 1 item of Gas meter')).toBeVisible();
    await expect(row).toHaveCount(0);
    expect(await waiting()).toBe(0);
    errors.expectNone();
  } finally {
    await page.goto('about:blank');
    await request.delete(`api/v1/sensors/${id}`);
    await request.patch(`api/v1/sensors/${garage}`, { data: { enabled: true } });
  }
});

test('storage limits can be changed', async ({ page }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/settings');
  await expect(page.getByText('History frames', { exact: true })).toBeVisible();
  await expect(page.getByText('Training images', { exact: true })).toBeVisible();
  const save = page.getByRole('button', { name: 'Save storage limits' });
  await expect(save).toBeDisabled(); // nothing changed yet
  const maxGb = page.getByText('… but at most').locator('xpath=..').locator('input');
  await maxGb.fill('1.5');
  await press(save, info);
  await expect(page.getByText('Storage limits saved')).toBeVisible();
  await maxGb.fill('2');
  await press(save, info);
  await expectNoHorizontalOverflow(page);
  errors.expectNone();
});

test('review queue can be answered', async ({ page }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/review');
  const position = page.locator('.main .mono').first();
  await expect(position).toHaveText(/^1 \/ \d+/);
  await press(page.getByRole('button', { name: 'Skip' }), info);
  await expect(page.getByText(/^(2 \/ \d+|All caught up)$/).first()).toBeVisible();
  errors.expectNone();
});
