// UI tests, run once on desktop (mouse) and once on a phone (touch). See playwright.config.ts.
import { expect, test } from '@playwright/test';
import path from 'node:path';
import { CAMERA_URL, DISPLAY_URL, PHOTO_URL } from './env';
import {
  center,
  doubleTap,
  drag,
  expectNoClipping,
  expectNoHorizontalOverflow,
  hold,
  isTouch,
  press,
  seededObjectSensorId,
  seededReadingSensorId,
  seededSensorId,
  watchErrors,
} from './helpers';

test('every page renders without errors and fits the screen', async ({ page, request }, info) => {
  const id = await seededSensorId(request);
  const objectId = await seededObjectSensorId(request);
  const readingId = await seededReadingSensorId(request);
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
    ['reading-history', `sensors/${readingId}/history`, 'Every new value and the readings that were rejected'],
    ['reading-settings', `sensors/${readingId}/settings`, 'Digits after the decimal point'],
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

test('review queue can be answered', async ({ page }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/review');
  const position = page.locator('.main .mono').first();
  await expect(position).toHaveText(/^1 \/ \d+/);
  await press(page.getByRole('button', { name: 'Skip' }), info);
  await expect(page.getByText(/^(2 \/ \d+|All caught up)$/).first()).toBeVisible();
  errors.expectNone();
});
