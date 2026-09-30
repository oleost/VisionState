// UI tests, run once on desktop (mouse) and once on a phone (touch). See playwright.config.ts.
import { expect, test } from '@playwright/test';
import path from 'node:path';
import { CAMERA_URL } from './env';
import {
  center,
  doubleTap,
  drag,
  expectNoClipping,
  expectNoHorizontalOverflow,
  hold,
  isTouch,
  press,
  seededSensorId,
  watchErrors,
} from './helpers';

test('every page renders without errors and fits the screen', async ({ page, request }, info) => {
  const id = await seededSensorId(request);
  const pages: [string, string, RegExp | string][] = [
    ['dashboard', '', 'Sensors'],
    ['new-sensor', 'sensors/new', 'Name it and pick a camera'],
    ['label', `sensors/${id}/label`, 'What state is this?'],
    ['upload', `sensors/${id}/upload`, 'Drop images, ZIP archives or video here'],
    ['dataset', `sensors/${id}/dataset`, /\d+ images/],
    ['quality', `sensors/${id}/quality`, 'What gets mixed up'],
    ['history', `sensors/${id}/history`, 'State changes and frames flagged for review'],
    ['sensor-settings', `sensors/${id}/settings`, 'When to check'],
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
      await expectNoClipping(page, '.btn, .state-btn, .chip, .pill');
      await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, `${name}.png`), fullPage: true });
      errors.expectNone();
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
