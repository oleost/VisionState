// UI tests, run once on desktop (mouse) and once on a phone (touch). See playwright.config.ts.
import { expect, test, type APIRequestContext, type Locator, type Page, type TestInfo } from '@playwright/test';
import path from 'node:path';
import { CAMERA_URL, COUNTER_BOX, COUNTER_URL, DISPLAY_URL, PHOTO_URL, PLAY_URL, READING_SENSOR_NAME, SOUND_WAV_URL } from './env';
import {
  apiRequestsDuring,
  center,
  doubleTap,
  drag,
  expectNoClipping,
  expectNoHorizontalOverflow,
  hold,
  hasTouchScreen,
  isTouch,
  press,
  seededCounterSensorId,
  seededFaceSensorId,
  seededObjectSensorId,
  seededReadingSensorId,
  seededSensorId,
  seededSoundSensorId,
  setVisibility,
  soundModelInstalled,
  watchErrors,
} from './helpers';

/** Scrolls to the middle of the screen first, so a tap never lands under the sticky top bar. */
async function pressCentered(locator: Locator, info: TestInfo) {
  await locator.evaluate((el) => el.scrollIntoView({ block: 'center' }));
  await press(locator, info);
}

/** Every page: [screenshot name, route, text that shows it is ready]. */
async function allPages(request: APIRequestContext): Promise<[string, string, RegExp | string][]> {
  const id = await seededSensorId(request);
  const objectId = await seededObjectSensorId(request);
  const readingId = await seededReadingSensorId(request);
  const counterId = await seededCounterSensorId(request);
  const faceId = await seededFaceSensorId(request);
  const soundId = (await soundModelInstalled(request)) ? await seededSoundSensorId(request) : null;
  const sound: [string, string, RegExp | string][] = soundId
    ? [
        ['sound-live', `sensors/${soundId}/live`, 'Listening'],
        ['sound-history', `sensors/${soundId}/history`, 'When each sound was heard'],
        ['sound-own', `sensors/${soundId}/quality`, 'Teach the sensor sounds of your own'],
        ['sound-settings', `sensors/${soundId}/settings`, 'Sounds to listen for'],
      ]
    : [];
  return [
    ...sound,
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
    ['faces-live', `sensors/${faceId}/live`, 'Unknown person'],
    ['faces-settings', `sensors/${faceId}/settings`, 'Ask me about faces I do not know'],
    ['faces-quality', `sensors/${faceId}/quality`, 'Shared by every camera that recognises faces'],
    ['people', 'people', 'The people you named by their face'],
    ['counter-settings', `sensors/${counterId}/settings`, 'Number of digits'],
    ['review', 'review', 'Frames the AI was unsure about'],
    ['history-all', 'history', 'What every sensor recorded'],
    ['settings', 'settings', 'AI model'],
  ];
}

test('every page renders without errors and fits the screen', async ({ page, request }, info) => {
  test.setTimeout(180_000); // every page, with a full-page screenshot each
  const pages = await allPages(request);
  // Native parts of controls are drawn dark (Safari shows light select menus with light text otherwise).
  await page.goto('#/');
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe('dark');
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
      // A history page that filled up during the run can be taller than a screenshot may be
      // (32767 device pixels: on an iPhone at scale 3 that is under 11000 CSS px; WebKit renders
      // the full height even with a clip): then only the screen at the top. The checks above
      // cover the whole page.
      const pixels = await page.evaluate(() => document.documentElement.scrollHeight * devicePixelRatio);
      await page.screenshot({
        path: path.join('test-results', 'pages', info.project.name, `${name}.png`),
        fullPage: pixels <= 30_000,
      });
      errors.expectNone();
    });
  }
});

// The UI polls nothing faster than every 2 s (POLL in ui.ts): 4 s on a page may see each path
// three times, one more for a refresh after a new check. A loop shows up as dozens.
const POLL_WINDOW_MS = 4_000;
const MAX_PER_PATH = 4;
const expectCalm = (counts: Record<string, number>) =>
  expect.soft(Object.entries(counts).filter(([, n]) => n > MAX_PER_PATH), JSON.stringify(counts)).toEqual([]);

test('no page asks the server more often than it polls', async ({ page, request }) => {
  test.setTimeout(180_000); // every page, 4 s each
  for (const [name, route, ready] of await allPages(request)) {
    await test.step(name, async () => {
      await page.goto(`#/${route}`);
      await expect(page.getByText(ready).first()).toBeVisible();
      expectCalm(await apiRequestsDuring(page, POLL_WINDOW_MS));
    });
  }
});

test('a hidden page asks nothing, and hiding and showing it does not start extra polling', async ({ page, request }, info) => {
  const id = await seededSensorId(request);
  // Slow answers, so the page is hidden and shown while a request is on its way.
  await page.route('**/api/v1/sensors/*/frame*', async (route) => {
    await new Promise((r) => setTimeout(r, 400));
    await route.continue();
  });
  await page.route('**/api/v1/lights/hold', async (route) => {
    await new Promise((r) => setTimeout(r, 400));
    await route.continue();
  });
  await page.goto(`#/sensors/${id}/label`);
  await expect(page.locator('.roi img')).toBeVisible();
  for (let i = 0; i < 5; i++) {
    await setVisibility(page, 'hidden');
    await setVisibility(page, 'visible');
  }
  expectCalm(await apiRequestsDuring(page, POLL_WINDOW_MS));

  // While hidden, nothing is fetched at all (frames, the light's lease, the sensor and the
  // status wait for the page); shown again, the sensor is brought up to date at once.
  await setVisibility(page, 'hidden');
  await page.waitForTimeout(1_000); // answers already on their way
  const hidden = await apiRequestsDuring(page, POLL_WINDOW_MS);
  expect(Object.keys(hidden), JSON.stringify(hidden)).toEqual([]);
  const refreshed = page.waitForRequest((req) => new URL(req.url()).pathname === `/api/v1/sensors/${id}`, {
    timeout: 1_500,
  });
  await setVisibility(page, 'visible');
  await refreshed;

  // The light's lease (renewed every 10 s) is not taken more than once either.
  await page.goto('#/sensors/new');
  await page.getByPlaceholder('Garage door').fill('Light poll test');
  await press(page.getByText('HTTP snapshot URL'), info);
  await page.getByLabel('Source URL').fill(`${CAMERA_URL}/snapshot.jpg`);
  const light = page.getByLabel('Light to switch on');
  await light.fill('light.meter_flash');
  await light.press('Enter');
  await press(page.getByRole('button', { name: 'Next' }), info);
  await expect(page.getByTestId('light-hold')).toContainText('light.meter_flash');
  for (let i = 0; i < 5; i++) {
    await setVisibility(page, 'hidden');
    await setVisibility(page, 'visible');
  }
  const counts = await apiRequestsDuring(page, 11_000);
  expect(counts['/api/v1/lights/hold'] ?? 0, JSON.stringify(counts)).toBeLessThanOrEqual(2);
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

test('long camera names stay inside their boxes', async ({ page }) => {
  const long = 'garasje_bod_1_etg_esp32cam_watermeter_esp32_watermeter_camera';
  await page.route('**/api/v1/cameras', (route) =>
    route.fulfill({
      json: [
        { entity_id: `camera.${long}`, name: `esp32cam-watermeter ${long}`, state: 'idle' },
        { entity_id: 'camera.reolink', name: 'Reolink', state: 'unavailable' },
      ],
    }),
  );
  await page.goto('#/sensors/new');
  await expect(page.getByText(`camera.${long}`)).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.cam');
});

test('the buttons of the sensor cards line up along the bottom', async ({ page }) => {
  await page.goto('#/');
  const actions = page.locator('.sensor .actions');
  await expect(actions.first()).toBeVisible();
  const buttons = await actions.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().bottom));
  const cards = await page.locator('.sensor').evaluateAll((els) => els.map((el) => el.getBoundingClientRect().top));
  // Cards side by side (the same top) have their buttons at the same height.
  for (let i = 0; i < cards.length; i++)
    for (let j = i + 1; j < cards.length; j++)
      if (Math.abs(cards[i] - cards[j]) < 1) expect(Math.abs(buttons[i] - buttons[j])).toBeLessThan(1);
});

test('a light for the camera: picked with the camera, held on while framing', async ({ page }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/sensors/new');
  await page.getByPlaceholder('Garage door').fill('Light test');
  await press(page.getByText('HTTP snapshot URL'), info);
  await page.getByLabel('Source URL').fill(`${CAMERA_URL}/snapshot.jpg`);
  // The light is chosen right with the camera.
  const light = page.getByLabel('Light to switch on');
  await light.fill('light.meter_flash');
  await light.press('Enter');
  await expect(page.getByText('Wait before taking the frame')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await press(page.getByRole('button', { name: 'Next' }), info);

  // While the region shows live frames the light is held on; without Home Assistant (as here) it says why not.
  const hold = page.getByTestId('light-hold');
  await expect(hold).toContainText('light.meter_flash');
  await expect(hold).toContainText('Home Assistant API is not configured');
  await expect(page.locator('.roi img')).toBeVisible(); // the frame still comes
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.btn, .chip, [data-testid="light-hold"]');
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'wizard-light.png'), fullPage: true });
  // Its switch keeps the light off for this view (and is remembered); on again for the next tests.
  const toggle = page.getByLabel('Light on while this view is open');
  await press(toggle, info);
  await expect(hold).toContainText('stays off while you look');
  await press(toggle, info);
  await expect(hold).toContainText('Home Assistant API is not configured');
  // Still held on the next step (one hold for region and test: the light does not blink in between).
  await press(page.getByRole('button', { name: 'Next' }), info);
  await expect(page.getByText('What should this sensor detect?')).toBeVisible();
  await expect(hold).toBeVisible();
  errors.expectNone();
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
  await expect(page.getByTestId('low-stream')).toHaveCount(0); // 1280 × 720: no warning
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
  if (hasTouchScreen(info)) await expect(page.getByText('Keys 1–9 label them later.')).toBeHidden();
  await press(page.getByRole('button', { name: 'Next' }), info);
  await expect(page.getByText('When should it check the camera?')).toBeVisible();
  // A new sensor is sent to Home Assistant unless switched off here.
  await expect(page.getByRole('checkbox', { name: 'Send to Home Assistant' })).toBeChecked();
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
  // A small picture: maybe the camera's low-resolution stream.
  await expect(page.getByTestId('low-stream')).toContainText('640×421');
  await expectNoClipping(page, '.notice');
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
  await expect(page.getByText('binary_sensor.driveway_test_car')).toBeVisible();
  // With Person: recognising faces can be turned on here, after the same confirmation.
  const faces = page.getByTestId('wizard-faces').getByRole('checkbox');
  await expect(faces).not.toBeChecked();
  await pressCentered(faces, info);
  const sheet = page.getByRole('dialog', { name: 'Recognise faces' });
  await expect(sheet.getByText(/It is not a lock/)).toBeVisible();
  await press(sheet.getByRole('button', { name: 'Cancel' }), info);
  await expect(faces).not.toBeChecked();
  await pressCentered(faces, info);
  await press(sheet.getByRole('button', { name: /^(Turn on|Download and turn on)$/ }), info);
  await expect(faces).toBeChecked();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'wizard-objects.png'), fullPage: true });
  await expectNoClipping(page, '.btn, .chip-btn, .kind, .card');

  await press(page.getByRole('button', { name: 'Next' }), info);
  await press(page.getByRole('button', { name: 'Create sensor' }), info);
  await expect(page).toHaveURL(/\/live$/);
  await expect(page.getByText('Right now')).toBeVisible();
  await expect(page.locator('.count.on').first()).toBeVisible({ timeout: 60_000 });
  const sensorId = Number(page.url().match(/sensors\/(\d+)\//)![1]);
  const made = await (await request.get(`api/v1/sensors/${sensorId}`)).json();
  expect(made.objects.faces).toEqual({ enabled: true, ask_unknown: false });
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
  // Read by the wheel reader (the default), which reads every field; the text reader instead
  // reads the digits it sees: told there are eight wheels, the seven digits it reads are not accepted.
  const readWith = page.getByLabel('Read with');
  await expect(readWith).toHaveValue('wheels:wheels-v2');
  await readWith.selectOption('ocr');
  await expect(page.getByText(/a turning wheel can be misread/)).toBeVisible();
  await page.getByLabel('Number of digits').fill('8');
  await expect(page.getByText(/digits, not 8/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel('Number of digits').fill('7');
  await readWith.selectOption('wheels:wheels-v2');
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

/** Open a filter of the history (a list below it, a sheet on phones), tap options, close it. */
async function pick(page: Page, info: TestInfo, filter: string, options: RegExp[]) {
  await press(page.getByRole('button', { name: new RegExp(`^${filter}`) }), info);
  for (const option of options) await press(page.getByRole('option', { name: option }), info);
  await expectNoHorizontalOverflow(page);
  await press(page.getByRole('button', { name: 'Done' }), info);
  await expect(page.getByRole('listbox')).toHaveCount(0);
}

test('the history page filters by sensor, object and time, and keeps the filter in its URL', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const objectId = await seededObjectSensorId(request);
  await page.goto('#/history');
  await expect(page.getByTestId('history-total')).toContainText('entries');
  // Rows of several sensors, each with its sensor's name; then one sensor taken out again.
  await pick(page, info, 'Sensors', [/^Beach/, /^Power meter/]);
  await expect(page.getByRole('button', { name: /^Sensors\s*2 selected/ })).toBeVisible();
  await expect.poll(async () => [...new Set(await page.locator('.list .sensor').allTextContents())].sort()).toEqual(['Beach', 'Power meter']);
  await pick(page, info, 'Sensors', [/^Power meter/]);
  await expect(page).toHaveURL(new RegExp(`#/history\\?sensor=${objectId}$`));
  await expect(page.getByRole('button', { name: /^Sensors\s*Beach/ })).toBeVisible();
  await expect.poll(async () => [...new Set(await page.locator('.list .sensor').allTextContents())]).toEqual(['Beach']);

  // A class: only its rows.
  await pick(page, info, 'What', [/^Dog/]);
  await expect(page.getByRole('button', { name: /Dog detected/ }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: /Person detected/ })).toHaveCount(0);

  // The filter is in the URL: a reload keeps it.
  await page.reload();
  await expect(page.getByRole('button', { name: /^What\s*Dog/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Dog detected/ }).first()).toBeVisible();

  // A time window with nothing in it.
  await page.getByLabel('Time').selectOption('custom');
  await page.getByLabel('From', { exact: true }).fill('2001-01-01T00:00');
  await page.getByLabel('To', { exact: true }).fill('2001-01-02T00:00');
  await expect(page.getByText('Nothing matches these filters.')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await press(page.getByRole('button', { name: 'Clear filters' }).first(), info);
  await expect(page).toHaveURL(/#\/history$/);
  await expect(page.getByRole('button', { name: /^Sensors\s*All/ })).toBeVisible();
  errors.expectNone();
});

test("a sensor's History tab is the history of that sensor, and opens in the History page", async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const objectId = await seededObjectSensorId(request);
  await page.goto(`#/sensors/${objectId}/history`);
  await expect(page.getByText('When each object appeared and cleared')).toBeVisible();
  await expect(page.getByRole('button', { name: /^Sensors/ })).toHaveCount(0); // the sensor is fixed
  await pick(page, info, 'What', [/^Person/]);
  await expect(page).toHaveURL(new RegExp(`#/sensors/${objectId}/history\\?key=person$`));
  await expect(page.getByRole('button', { name: /Person detected/ }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: /Dog detected/ })).toHaveCount(0);

  await press(page.getByRole('link', { name: 'Open in all history' }), info);
  await expect(page).toHaveURL(new RegExp(`#/history\\?sensor=${objectId}&key=person$`));
  await expect(page.getByRole('button', { name: /^Sensors\s*Beach/ })).toBeVisible();
  await expect(page.getByRole('link', { name: 'History', exact: true })).toHaveAttribute('aria-current', 'page');
  errors.expectNone();
});

test('new history rows wait behind a button instead of moving the list', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededSensorId(request); // checks every 2 s and flags every frame for review
  await page.goto(`#/history?sensor=${id}`);
  await expect(page.getByTestId('history-total')).toContainText('entries');
  const total = page.getByTestId('history-total');
  const before = await total.textContent();
  const first = await page.locator('.list .item').first().textContent();
  const fresh = page.getByRole('button', { name: /^Show \d+ new$/ });
  await expect(fresh).toBeVisible({ timeout: 30_000 });
  // Nothing moved: the same count and the same first row.
  expect(await total.textContent()).toBe(before);
  expect(await page.locator('.list .item').first().textContent()).toBe(first);
  await press(fresh, info);
  await expect(fresh).toHaveCount(0);
  await expect(total).not.toHaveText(before ?? '');
  errors.expectNone();
});

test("a state sensor's card on the dashboard leads to its history", async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededSensorId(request);
  await page.goto('#/');
  const history = page.locator(`a[href="#/sensors/${id}/history"]`);
  await expect(history).toHaveText('History');
  await press(history, info);
  await expect(page.getByText('State changes and frames flagged for review')).toBeVisible();
  errors.expectNone();
});

test('a red cell of the confusion matrix shows the images behind it', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededSensorId(request); // one open frame was labelled closed when seeded
  await page.goto(`#/sensors/${id}/quality`);
  const red = page.getByRole('button', { name: /labelled .*, guessed .*: show them/ }).first();
  await expect(red).toBeVisible({ timeout: 30_000 });
  const [, label, guess] = /labelled (.*), guessed (.*): show them/.exec((await red.getAttribute('aria-label')) ?? '') ?? [];
  await press(red, info);
  await expect(red).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('heading', { name: `Labelled ${label}, the AI guessed ${guess}` })).toBeVisible();
  const listed = page.locator('#cell-samples');
  await expect(listed.locator('.item').first()).toContainText(`Labelled ${label}, the AI thinks ${guess}`);
  await expect(listed).toBeInViewport(); // right under the matrix, not at the bottom of the page
  await expectNoHorizontalOverflow(page);
  await press(listed.getByRole('button', { name: 'Close', exact: true }), info);
  await expect(listed).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Possibly mislabelled' })).toBeVisible();

  // Images already said to be right are no longer "possibly mislabelled", but still behind their cell.
  await page.route('**/api/v1/sensors/*/quality', async (route) => {
    const body = await (await route.fetch()).json();
    body.suspects = body.suspects.map((s: object) => ({ ...s, verified: true }));
    await route.fulfill({ json: body });
  });
  await page.reload();
  await expect(page.getByText('Nothing suspicious')).toBeVisible();
  await press(page.getByRole('button', { name: /labelled .*, guessed .*: show them/ }).first(), info);
  await expect(page.locator('#cell-samples').getByText(/You said .* is right/).first()).toBeVisible();
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

test('the live page of an object sensor does not jump when a new check comes in', async ({ page, request }) => {
  const errors = watchErrors(page);
  const id = await seededObjectSensorId(request);
  // Frames arrive slowly, as through Home Assistant on a phone: the check is announced well
  // before its picture is there.
  await page.route('**/frame?frame_id=*', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 600));
    await route.continue();
  });
  await page.goto(`#/sensors/${id}/live`);
  await expect(page.locator('button.found').first()).toBeVisible({ timeout: 30_000 });
  // Every animation frame for 12 s (two or three checks at 5 s): where "Check now" is, and
  // whether the list of boxes under the frame is there.
  const seen = await page.evaluate(async () => {
    const result = { tops: new Set<number>(), emptyList: 0, frames: 0, checks: new Set<string>() };
    const end = performance.now() + 12_000;
    while (performance.now() < end) {
      await new Promise(requestAnimationFrame);
      const button = [...document.querySelectorAll('button')].find((b) => b.textContent?.includes('Check now'));
      if (button) result.tops.add(Math.round(button.getBoundingClientRect().top + window.scrollY));
      if (!document.querySelector('button.found')) result.emptyList++;
      result.checks.add(document.querySelector('.roi img')?.getAttribute('src') ?? '');
      result.frames++;
    }
    return { tops: [...result.tops], emptyList: result.emptyList, frames: result.frames, checks: result.checks.size };
  });
  expect(seen.checks, 'no new check came in while watching').toBeGreaterThan(1);
  expect(seen.emptyList, 'the list under the frame disappeared for a moment').toBe(0);
  expect(seen.tops, 'the page moved').toHaveLength(1);
  errors.expectNone();
});

test('teaching an object sensor: correct a box, draw a missed one, forget it all', async ({ page, request }, info) => {
  test.setTimeout(180_000); // waits for the detector's checks several times
  const errors = watchErrors(page);
  // Its own sensor (per project), so the seeded one stays untaught for the other tests.
  const created = await request.post('api/v1/sensors', {
    data: {
      name: `Teach ${info.project.name}`,
      kind: 'objects',
      source_type: 'http',
      source: PHOTO_URL('beach'),
      objects: { classes: ['dog', 'person'] },
      interval_s: 3,
    },
  });
  expect(created.ok()).toBeTruthy();
  const id = (await created.json()).id;
  try {
    await page.goto(`#/sensors/${id}/live`);
    await expect(page.getByText('Wrong? Tap a box to correct it:')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole('link', { name: 'Quality' })).toHaveCount(0); // nothing taught yet

    // Tap the person in the list under the frame: not a person (asked once whether to teach).
    await press(page.locator('button.found', { hasText: 'Person' }), info);
    const sheet = page.getByRole('dialog', { name: 'Teach this box' });
    await expect(sheet.getByText(/Person .*sure/)).toBeVisible();
    await press(sheet.getByRole('button', { name: 'Not a person' }), info);
    await expect(sheet.getByText('Teach this sensor?')).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'objects-teach-sheet.png'), fullPage: true });
    await press(sheet.getByRole('button', { name: 'Teach' }), info);
    await expect(page.getByText(/no longer count as person/)).toBeVisible();
    // The next check shows the person dashed and filtered, and the Quality tab appears.
    await expect(page.locator('button.found.filtered', { hasText: 'Person' })).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.roi .box.filtered')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Quality' })).toBeVisible();
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'objects-teach-live.png'), fullPage: true });

    // Draw a box around something the AI missed, then pick what it is.
    await press(page.getByRole('button', { name: 'Missed something?' }), info);
    const draw = page.locator('.roi .draw');
    // Into the middle of the screen, so the drag does not start under the sticky top bar.
    await draw.evaluate((el) => el.scrollIntoView({ block: 'center' }));
    const area = await draw.boundingBox();
    if (!area) throw new Error('no drawing area');
    await drag(page, info, { x: area.x + area.width * 0.3, y: area.y + area.height * 0.35 }, { x: area.x + area.width * 0.5, y: area.y + area.height * 0.6 });
    await expect(page.locator('.roi .drawn')).toBeVisible();
    await press(page.getByRole('button', { name: 'Next' }), info);
    await expect(sheet.getByText('What did the AI miss?')).toBeVisible();
    await press(sheet.getByRole('button', { name: 'Dog' }), info);
    await expect(page.getByText('Saved as Dog.')).toBeVisible();

    // The Quality tab lists both, and the filtered frame.
    await page.goto(`#/sensors/${id}/quality`);
    await expect(page.getByRole('heading', { name: /Not a person/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /^Dog/ })).toBeVisible();
    await expect(page.getByText('Person filtered away').first()).toBeVisible({ timeout: 15_000 });
    await page.waitForFunction(() => [...document.images].every((img) => img.complete || img.loading === 'lazy'));
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.btn, .chip, .card');
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'objects-quality.png'), fullPage: true });

    // Taught from a history frame: its entry says so from then on.
    await page.goto(`#/sensors/${id}/history`);
    const dogRow = page.getByRole('button', { name: /Dog detected/ }).first();
    await expect(dogRow).toBeVisible({ timeout: 15_000 });
    // The thumbnails load first, or the list moves under the tap on a phone; and the toast from
    // teaching the missed box covers the bottom of a phone screen for a while.
    await page.waitForFunction(() => [...document.images].every((img) => img.complete || img.loading === 'lazy'));
    await expect(page.getByRole('button', { name: 'Dismiss' })).toHaveCount(0, { timeout: 20_000 });
    await press(dogRow, info);
    await expect(page.locator('.full .box').first()).toBeVisible();
    await press(page.locator('.full button.found', { hasText: 'Dog' }).first(), info);
    await press(sheet.getByRole('button', { name: 'Correct' }), info);
    await expect(page.getByTestId('history-taught').first()).toContainText('Taught: Dog');
    await expectNoHorizontalOverflow(page);

    // Settings: its own teaching limits. Until set, a field shows the global value in grey.
    await page.goto(`#/sensors/${id}/settings`);
    await expect(page.getByText('Use what you taught')).toBeVisible();
    // The region's camera frame loads above: wait for it, or the page moves under the tap.
    await expect(page.locator('.roi img')).toBeVisible();
    await page.waitForFunction(() => [...document.images].every((img) => img.complete));
    const global = await (await request.get('api/v1/teach-limits')).json();
    const limits = page.getByTestId('sensor-teach-limits');
    const match = limits.getByRole('spinbutton', { name: 'Recognised as a box you taught from' });
    await expect(match).toHaveValue('');
    await expect(match).toHaveAttribute('placeholder', String(Math.round(global.effective.match_similarity * 100)));
    await expect(limits.getByRole('button', { name: /^Use the global value/ }).first()).toBeHidden();
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.btn, .card');
    await match.fill('80');
    // The toast from teaching the missed box still covers the bottom of a phone screen: let it go.
    await expect(page.getByRole('button', { name: 'Dismiss' })).toHaveCount(0, { timeout: 20_000 });
    await pressCentered(page.getByRole('button', { name: 'Save changes' }), info);
    await expect(page.getByText('Settings saved')).toBeVisible();
    expect((await (await request.get(`api/v1/sensors/${id}`)).json()).objects.teach).toEqual({ match_similarity: 0.8 });
    await page.reload();
    await expect(match).toHaveValue('80');
    await match.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'objects-teach-limits.png'), fullPage: true });
    await pressCentered(limits.getByRole('button', { name: /^Use the global value/ }), info);
    await expect(match).toHaveValue('');
    await pressCentered(page.getByRole('button', { name: 'Save changes' }), info);
    await expect.poll(async () => (await (await request.get(`api/v1/sensors/${id}`)).json()).objects.teach).toEqual({});

    // Turn it off or forget it all; then the Quality tab goes away again.
    await press(page.getByRole('button', { name: 'Forget all' }), info);
    await press(page.getByRole('button', { name: 'Tap again to forget everything taught' }), info);
    await expect(page.getByText('Everything taught is forgotten')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Quality' })).toHaveCount(0);
    errors.expectNone();
  } finally {
    await request.delete(`api/v1/sensors/${id}`);
  }
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
  if (hasTouchScreen(info)) await expect(page.locator('.kbd-only').first()).toBeHidden();
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

test('a sensor can be kept from Home Assistant while it is tuned', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededReadingSensorId(request);
  try {
    await page.goto(`#/sensors/${id}/settings`);
    const send = page.getByRole('checkbox', { name: 'Send to Home Assistant' });
    await expect(send).toBeChecked();
    await press(send, info);
    await expect(page.getByText('stay unavailable')).toBeVisible();
    await press(page.getByRole('button', { name: 'Save changes' }), info);
    await expect(page.getByText('Settings saved')).toBeVisible();
    expect((await (await request.get(`api/v1/sensors/${id}`)).json()).publish).toBe(false);
    // Marked on the sensor page and on the dashboard, so it is not forgotten.
    await expect(page.locator('header .chip', { hasText: 'Not sent to Home Assistant' })).toBeVisible();
    await page.goto('#/');
    const card = page.locator('.card', { hasText: READING_SENSOR_NAME }).first();
    await expect(card.getByText('Not sent to Home Assistant')).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.btn, .chip, .card');
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'dashboard-not-sent.png'), fullPage: true });
    errors.expectNone();
  } finally {
    await request.patch(`api/v1/sensors/${id}`, { data: { publish: true } });
  }
});

test('a counter has a rate window for its rate entity', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededReadingSensorId(request);
  await page.goto(`#/sensors/${id}/settings`);
  const field = page.getByLabel('Rate over the last minutes');
  await expect(field).toHaveValue('15');
  await field.fill('30');
  await press(page.getByRole('button', { name: 'Save changes' }), info);
  await expect(page.getByText('Settings saved')).toBeVisible();
  try {
    const sensor = await (await request.get(`api/v1/sensors/${id}`)).json();
    expect(sensor.reading.rate_window_min).toBe(30);
    await expectNoHorizontalOverflow(page);
    errors.expectNone();
  } finally {
    const sensor = await (await request.get(`api/v1/sensors/${id}`)).json();
    const { value: _v, last: _l, has_image: _h, limit: _lim, camera_changed: _cc, ...reading } = sensor.reading;
    await request.patch(`api/v1/sensors/${id}`, { data: { reading: { ...reading, rate_window_min: 15 } } });
  }
});

test('a counter learns how fast it may go up, or takes a fixed rate or change per reading', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededReadingSensorId(request);
  await page.goto(`#/sensors/${id}/settings`);
  const choice = page.getByLabel('Limit how fast the counter may go up');
  await expect(choice).toHaveValue('auto');
  await expect(page.getByText(/Learning from its readings: \d+ of \d+ days/)).toBeVisible();
  // Until it learned, the standard rate applies, shown and open to change.
  const config = await (await request.get('api/v1/config')).json();
  const before = (await (await request.get(`api/v1/sensors/${id}`)).json()).reading;
  const standard = Number((config.counter_standard_steps_per_min * 10 ** -before.decimals * 60).toPrecision(6));
  await expect(page.getByLabel('Largest rise per hour')).toHaveValue(String(standard));
  await expect(page.getByLabel('Largest rise per hour')).toBeEnabled();
  await choice.selectOption('step');
  await expect(page.getByLabel('Largest rise per hour')).toHaveCount(0);
  await expect(page.getByLabel('Largest rise per reading')).not.toHaveValue('0');
  await choice.selectOption('fixed');
  await page.getByLabel('Largest rise per hour').fill('2.4');
  await press(page.getByRole('button', { name: 'Save changes' }), info);
  await expect(page.getByText('Settings saved')).toBeVisible();
  try {
    const sensor = await (await request.get(`api/v1/sensors/${id}`)).json();
    expect([sensor.reading.change_limit, sensor.reading.max_rate]).toEqual(['fixed', 2.4]);
    expect(sensor.reading.limit.allowed).not.toBeNull();
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.btn, .chip, .card');
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'reading-limit.png'), fullPage: true });
    errors.expectNone();
  } finally {
    const sensor = await (await request.get(`api/v1/sensors/${id}`)).json();
    const { value: _v, last: _l, has_image: _h, limit: _lim, camera_changed: _cc, ...reading } = sensor.reading;
    await request.patch(`api/v1/sensors/${id}`, { data: { reading: { ...reading, change_limit: 'auto', max_rate: 0, max_step: 0 } } });
  }
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
    // Only the region is shown, large; the whole frame is one tap away.
    const region = page.locator('.main img.region');
    await expect.poll(() => region.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth)).toBeGreaterThan(0);
    await press(page.getByRole('button', { name: 'Whole frame' }), info);
    await expect(page.locator('.main .frame')).toBeVisible();
    await press(page.getByRole('button', { name: 'Only the region' }), info);
    await expect(region).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'review-reading.png'), fullPage: true });
    await press(page.getByRole('button', { name: 'Misread' }), info);
    const right = page.getByLabel('The right value');
    await expect(right).toHaveValue('400'); // starts as what was read: usually one digit is changed
    await right.fill('501');
    await expect(page.getByText('Saved as 501 m³')).toBeVisible();
    await right.fill('5x1');
    await expect(page.getByText('Not a number')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save misread' })).toBeDisabled();
    await right.fill('501');
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

    // The readings can be exported to share (the checked ones and the accepted one nobody checked):
    // a ZIP with the region of each and a licence, and what the meter is.
    await expect(page.getByText('The reader does not learn from your answers')).toBeVisible();
    const exportLink = page.locator('.share').getByRole('link', { name: 'Export readings' });
    await page.getByLabel(/What meter is this/).fill('Gas meter, white on black');
    await expect(exportLink).toHaveAttribute('href', /meter=Gas\+meter%2C\+white\+on\+black/);
    const zip = await request.get((await exportLink.getAttribute('href'))!);
    expect(zip.ok()).toBeTruthy();
    expect(zip.headers()['content-disposition']).toContain('visionstate-readings-gas_meter.zip');
    expect((await zip.body()).subarray(0, 2).toString()).toBe('PK');
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.btn, .chip, .card');
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'reading-quality-export.png'), fullPage: true });

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
    // A tap on a sensor shows only its items, and the page keeps that filter in its address.
    await press(row.locator('.pick'), info);
    await expect(page).toHaveURL(new RegExp(`#/review\\?sensor=${id}$`));
    await expect(row.locator('.pick')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.main')).toContainText(/Did it read/);
    await expect(page.locator('.main .mono').first()).toHaveText('1 / 1');
    await page.reload();
    await expect(row.locator('.pick')).toHaveAttribute('aria-pressed', 'true');
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'review-dismiss.png'), fullPage: true });
    await press(row.getByRole('button', { name: 'Dismiss all' }), info);
    await press(row.getByRole('button', { name: 'Press again' }), info);
    await expect(page.getByText('Dismissed 1 item of Gas meter')).toBeVisible();
    // Nothing left for this sensor, while others still wait: it stays chosen, one tap shows them all.
    await expect(row).toContainText('0 waiting');
    await expect(row.getByRole('button', { name: 'Dismiss all' })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'All caught up for Gas meter' })).toBeVisible();
    await expectNoClipping(page, '.btn, .card');
    // On a phone the toast lies over the button: a finger waits for it too.
    await expect(page.getByText('Dismissed 1 item of Gas meter')).toBeHidden({ timeout: 10_000 });
    await press(page.getByRole('button', { name: /Show all sensors · \d+ waiting/ }), info);
    await expect(page).toHaveURL(/#\/review$/);
    await expect(page.getByRole('button', { name: /All sensors/ })).toHaveAttribute('aria-pressed', 'true');
    await expect(row).toHaveCount(0);
    expect(await waiting()).toBe(0);
    errors.expectNone();
  } finally {
    await page.goto('about:blank');
    await request.delete(`api/v1/sensors/${id}`);
    await request.patch(`api/v1/sensors/${garage}`, { data: { enabled: true } });
  }
});

test('a rejected reading keeps the region it was read in', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const half = { ...COUNTER_BOX, w: COUNTER_BOX.w / 2 };
  const created = await request.post('api/v1/sensors', {
    data: {
      name: 'Gas counter',
      kind: 'reading',
      source_type: 'http',
      source: COUNTER_URL(45730),
      roi: half, // half the wheels: the text reader reads the wrong number of digits, so rejected
      reading: { mode: 'counter', display: 'counter', digits: 7, decimals: 3, unit: 'm³', counter_reader: 'ocr' },
      interval_s: 3600,
      debounce: 1,
    },
  });
  const id = (await created.json()).id;
  try {
    await expect.poll(async () => (await (await request.get(`api/v1/sensors/${id}`)).json()).reading.last?.reason, { timeout: 60_000 }).toBe('wrong digit count');
    // The history row is written after the rejection is published.
    const rows = async () => (await (await request.get(`api/v1/history?sensor=${id}`)).json()).items as unknown[];
    await expect.poll(async () => (await rows()).length, { timeout: 30_000 }).toBeGreaterThan(0);
    await request.patch(`api/v1/sensors/${id}`, { data: { roi: COUNTER_BOX, enabled: false } });

    await page.goto(`#/sensors/${id}/history`);
    const row = page.locator('.item').filter({ hasText: 'not the number of digits the counter has' }).first();
    await press(row.locator('button.head'), info);
    const frame = row.locator('.full img');
    await expect.poll(() => frame.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    // The outline is the half region the reading was made in, not the region the sensor has now.
    const outline = await row.locator('.full polygon.outline').boundingBox();
    const image = await frame.boundingBox();
    expect(outline && image ? outline.width / image.width : 0).toBeCloseTo(half.w, 1);
    // The long reason wraps instead of running out of the card on a phone.
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.chip, .card');
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'history-reading-region.png'), fullPage: true });
    errors.expectNone();
  } finally {
    await page.goto('about:blank');
    await request.delete(`api/v1/sensors/${id}`);
  }
});

test('the wheel reader shows in Settings and is offered to counters read as text or with an older one', async ({
  page,
  request,
}, info) => {
  const errors = watchErrors(page);
  await page.goto('#/settings');
  const wheels = page.getByTestId('wheel-reader');
  await expect(wheels).toContainText('VisionState wheel reader v2');
  await expect(wheels.getByRole('link', { name: 'source' })).toHaveAttribute('href', /huggingface\.co/);
  await expect(page.getByText('Wheel reader', { exact: true })).toBeVisible(); // its status row
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.card');
  const created = await request.post('api/v1/sensors', {
    data: {
      name: 'Old water meter',
      kind: 'reading',
      source_type: 'http',
      source: COUNTER_URL(45730),
      roi: COUNTER_BOX,
      reading: { mode: 'counter', display: 'counter', digits: 7, decimals: 3, unit: 'm³', counter_reader: 'ocr' },
      interval_s: 3600,
    },
  });
  const id = (await created.json()).id;
  try {
    await page.goto(`#/sensors/${id}/live`);
    const notice = page.getByTestId('try-wheel-reader');
    await expect(notice).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await press(notice.getByRole('link'), info);
    await expect(page.getByLabel('Read with')).toHaveValue('ocr');
    errors.expectNone();
  } finally {
    await page.goto('about:blank');
    await request.delete(`api/v1/sensors/${id}`);
  }
  // A counter set up with wheels-v1 keeps it, and its Live tab offers the newer one.
  const older = await request.post('api/v1/sensors', {
    data: {
      name: 'Older water meter',
      kind: 'reading',
      source_type: 'http',
      source: COUNTER_URL(45730),
      roi: COUNTER_BOX,
      reading: { mode: 'counter', display: 'counter', digits: 7, decimals: 3, unit: 'm³', wheel_reader: 'wheels-v1' },
      interval_s: 3600,
    },
  });
  const olderId = (await older.json()).id;
  try {
    await page.goto(`#/sensors/${olderId}/live`);
    const newer = page.getByTestId('try-newer-wheel-reader');
    await expect(newer).toContainText('VisionState wheel reader v2');
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.notice');
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'live-newer-wheel-reader.png'), fullPage: true });
    await press(newer.getByRole('link'), info);
    await expect(page.getByLabel('Read with')).toHaveValue('wheels:wheels-v1');
    errors.expectNone();
  } finally {
    await page.goto('about:blank');
    await request.delete(`api/v1/sensors/${olderId}`);
  }
});

test('a backup of everything is downloaded and restored', async ({ page, request }, info) => {
  test.setTimeout(180_000); // the restore stops and starts every sensor
  const errors = watchErrors(page);
  await page.goto('#/settings');
  const card = page.getByTestId('backup');
  await card.scrollIntoViewIfNeeded();
  // Everything ticked, the sensors always; camera passwords only when ticked.
  await expect(card.getByRole('checkbox', { name: /Sensors and settings/ })).toBeChecked();
  await expect(card.getByRole('checkbox', { name: /Sensors and settings/ })).toBeDisabled();
  await expect(card.getByRole('checkbox', { name: /History/ })).toBeChecked();
  await expect(card.getByRole('checkbox', { name: /Camera passwords/ })).not.toBeChecked();
  const download = card.getByRole('link', { name: /Download backup/ });
  await press(card.getByRole('checkbox', { name: /Downloaded AI models/ }), info);
  await expect(download).toHaveAttribute('href', /leave_out=models/);
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '[data-testid=backup] .btn, [data-testid=backup] .check');
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'settings-backup.png'), fullPage: true });
  test.skip(info.project.name !== 'desktop', 'the restore itself once (it restarts every sensor)');

  const before = (await (await request.get('api/v1/sensors')).json()).map((s: { name: string }) => s.name).sort();
  const [file] = await Promise.all([page.waitForEvent('download'), download.click()]);
  expect(file.suggestedFilename()).toMatch(/^visionstate-backup-.*\.zip$/);
  const saved = info.outputPath('backup.zip');
  await file.saveAs(saved);
  await card.locator('#backup-restore').setInputFiles(saved);
  await expect(card.getByText(/Replace everything here with backup\.zip/)).toBeVisible();
  const replace = card.getByRole('button', { name: 'Replace everything' });
  await press(replace, info);
  await press(card.getByRole('button', { name: 'Click again to replace everything' }), info);
  await expect(card.getByText(new RegExp(`Restored ${before.length} sensors`))).toBeVisible({ timeout: 120_000 });
  const after = (await (await request.get('api/v1/sensors')).json()).map((s: { name: string }) => s.name).sort();
  expect(after).toEqual(before);
  errors.expectNone();
});

test('the AI models can be compared, and the teaching and face limits set for all object sensors', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  try {
    await page.goto('#/settings');
    await press(page.getByRole('button', { name: 'Compare and manage the models' }), info);
    const compare = page.getByTestId('model-compare');
    await expect(compare.getByText('D-FINE L (most accurate)')).toBeVisible();
    await expect(compare.getByText('DINOv2 base · fp32 (most accurate)')).toBeVisible();
    await expect(compare.getByText('AuraFace v1 (accurate)')).toBeVisible();
    await expect(compare.getByText(/· in use$/)).toHaveCount(5); // one per kind
    // From the smallest to the largest, in every kind.
    const settings = await (await request.get('api/v1/settings')).json();
    const smallest = [...settings.detectors].sort((a, b) => a.size - b.size)[0].name;
    await expect(compare.locator('.kind').nth(1).locator('.name').first()).toContainText(smallest);
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.btn, .card');

    const view = await (await request.get('api/v1/teach-limits')).json();
    const card = page.getByTestId('teach-limits');
    const ask = card.getByRole('spinbutton', { name: 'Asks you in the review queue from' });
    await expect(ask).toHaveValue('');
    await expect(ask).toHaveAttribute('placeholder', String(Math.round(view.defaults.ask_similarity * 100)));
    const save = card.getByRole('button', { name: 'Save teaching limits' });
    await expect(save).toBeDisabled(); // nothing changed yet
    await ask.fill('95'); // above "Recognised as…": not allowed
    await expect(card.getByText('must not be above the first')).toBeVisible();
    await ask.fill('70');
    await expect(card.getByText('must not be above the first')).toBeHidden();
    await pressCentered(save, info);
    await expect(page.getByText('Teaching limits saved')).toBeVisible();
    expect((await (await request.get('api/v1/teach-limits')).json()).values.ask_similarity).toBe(0.7);
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'settings-models.png'), fullPage: true });
    await pressCentered(card.getByRole('button', { name: /^Use the default of/ }), info);
    await expect(ask).toHaveValue('');
    await pressCentered(save, info);
    await expect.poll(async () => (await (await request.get('api/v1/teach-limits')).json()).values.ask_similarity).toBeNull();

    // Faces: the face model's defaults, a smallest face for every sensor, nonsense refused.
    const faces = page.getByTestId('face-limits');
    const smallestFace = faces.getByRole('spinbutton', { name: 'Smallest face' });
    const faceView = await (await request.get('api/v1/face-limits')).json();
    await expect(smallestFace).toHaveAttribute('placeholder', String(faceView.defaults.min_face_px));
    await faces.getByRole('spinbutton', { name: 'Asks you in the review queue from' }).fill('95');
    await expect(faces.getByText(/must not be above “Recognised as a person”/)).toBeVisible();
    await faces.getByRole('spinbutton', { name: 'Asks you in the review queue from' }).fill('');
    await smallestFace.fill('40');
    await pressCentered(faces.getByRole('button', { name: 'Save face limits' }), info);
    await expect(page.getByText('Face limits saved')).toBeVisible();
    expect((await (await request.get('api/v1/face-limits')).json()).values.min_face_px).toBe(40);
    await expectNoHorizontalOverflow(page);
    errors.expectNone();
  } finally {
    await request.put('api/v1/teach-limits', { data: {} });
    await request.put('api/v1/face-limits', { data: {} });
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

test('the review reminder can be set', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  try {
    await page.goto('#/settings');
    const card = page.locator('section', { has: page.getByRole('heading', { name: 'Review reminder' }) });
    await expect(card.getByLabel('Remind me in Home Assistant')).toBeChecked(); // on by default
    await expect(card.getByLabel('Remind again while they still wait')).not.toBeChecked();
    await expect(card.getByRole('combobox')).toHaveValue(''); // no push by default
    await expect(card.getByRole('button', { name: 'Send a test' })).toBeDisabled(); // no Home Assistant here
    const save = card.getByRole('button', { name: 'Save reminder' });
    await expect(save).toBeDisabled();

    // Longer than frames waiting for review are kept (twice the 7 history days): it says so.
    const days = card.getByText('When the oldest frame has waited').locator('xpath=..').locator('input');
    await days.fill('20');
    await expect(card.getByText('would never come')).toBeVisible();
    await days.fill('3');
    await expect(card.getByText('would never come')).toBeHidden();
    await press(card.getByLabel('Remind again while they still wait'), info);
    await card.getByText('Every').locator('xpath=..').locator('input').fill('2');
    await expectNoHorizontalOverflow(page);
    await press(save, info);
    await expect(page.getByText('Reminder saved')).toBeVisible();
    const saved = await (await request.get('api/v1/review-reminder')).json();
    expect(saved).toMatchObject({ enabled: true, after_days: 3, repeat: true, repeat_days: 2, min_items: 1 });
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'settings-reminder.png'), fullPage: true });
    errors.expectNone();
  } finally {
    const config = await (await request.get('api/v1/config')).json();
    await request.put('api/v1/review-reminder', { data: config.reminder_defaults });
  }
});

test('review queue can be answered', async ({ page }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/review');
  const position = page.locator('.main .mono').first();
  await expect(position).toHaveText(/^1 \/ \d+/);
  // The count per sensor follows each answer, and the sensor goes when nothing of it is left.
  const name = (await page.locator('.main strong').first().textContent())!;
  const row = page.locator('.wait-row').filter({ hasText: name });
  const before = Number((await row.locator('.xsmall').textContent())!.match(/\d+/)![0]);
  await press(page.getByRole('button', { name: 'Skip' }), info);
  if (before > 1) await expect(row).toContainText(`${before - 1} waiting`);
  else await expect(row).toHaveCount(0);
  await expect(page.getByText(/^(2 \/ \d+|All caught up)$/).first()).toBeVisible();
  errors.expectNone();
});

test('review asks whether an object is one of your own labels', async ({ page, request }, info) => {
  // A real question needs a box that looks only half like a taught one; the queue is given one
  // here, on a real photo, so the page itself is what is tested.
  const errors = watchErrors(page);
  const photo = await (await request.get(PHOTO_URL('beach'))).body();
  const item = {
    id: 99001,
    sensor_id: 1,
    created_at: new Date().toISOString(),
    state_key: 'rex',
    published_key: 'ask',
    confidence: 0.93,
    probs: { ask: { label: 'rex', similarity: 0.81, box: [0.62, 0.48, 0.78, 0.8], detected: 'dog' } },
    is_change: false,
    review_reason: 'ask',
    reviewed: false,
    has_frame: true,
    detections: [],
    read_ok: null,
    correct_value: null,
    sensor: { id: 1, name: 'Beach', kind: 'objects', roi: null, states: [], reading: null, labels: [{ key: 'rex', name: 'Rex', parent: 'dog' }] },
  };
  await page.route('**/api/v1/review', (route) =>
    route.fulfill({ json: { total: 1, items: [item], sensors: [{ id: 1, name: 'Beach', count: 1 }] } }),
  );
  await page.route('**/api/v1/history/99001/image*', (route) => route.fulfill({ body: photo, contentType: 'image/jpeg' }));
  const answers: unknown[] = [];
  await page.route('**/api/v1/review/99001', async (route) => {
    answers.push(route.request().postDataJSON());
    await route.fulfill({ json: { ok: true } });
  });
  await page.goto('#/review');
  await expect(page.getByText('Is this Rex?')).toBeVisible();
  await expect(page.locator('.main .box')).toHaveCount(1); // the box it asks about
  await expect(page.locator('.main .chip')).toHaveText('Is it yours?');
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.main .btn');
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'review-question.png'), fullPage: true });
  await press(page.getByRole('button', { name: 'Yes, Rex' }), info);
  await expect.poll(() => answers).toEqual([{ action: 'yes' }]);
  await expect(page.getByText('All caught up')).toBeVisible();
  // The answer in the list (shown on wide screens only).
  if (await page.locator('aside ol').isVisible()) await expect(page.getByText('✓ Rex')).toBeVisible();
  errors.expectNone();
});

test('an answer in the review queue can be changed from the list', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededSensorId(request);
  const reviewSamples = async () =>
    ((await (await request.get(`api/v1/sensors/${id}/samples?limit=1000`)).json()).items as { origin: string; labels: string[] }[]).filter(
      (x) => x.origin === 'review',
    );
  await page.goto('#/review');
  await expect(page.locator('.main .mono').first()).toHaveText(/^1 \/ \d+/);
  test.skip(!(await page.locator('aside ol').isVisible()), 'the list is only shown on wide screens');
  // Get past items of other sensors to one of the seeded state sensor.
  const main = page.locator('.main');
  for (let i = 0; i < 10 && !(await main.getByText('Is this').isVisible()); i++) await press(main.getByRole('button', { name: 'Skip' }), info);
  const before = (await reviewSamples()).length;
  const position = main.locator('.mono').first();
  const at = (await position.textContent())!;

  // Confirmed by mistake ...
  const yes = main.getByRole('button', { name: /^Yes, / });
  const predicted = (await yes.textContent())!.replace('Yes,', '').trim();
  await press(yes, info);
  await expect(position).not.toHaveText(at);
  const entry = page.locator('aside li').filter({ hasText: `✓ Confirmed ${predicted}` }).first();
  await expect(entry).toBeVisible();
  // ... opened again from the list: the answer given is marked, and another state is picked.
  await press(entry.getByRole('button'), info);
  await expect(position).toHaveText(at);
  await expect(main.getByText('answer again to change it')).toBeVisible();
  await expect(main.getByRole('button', { name: /^Yes, / })).toHaveClass(/chosen/);
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'review-change.png'), fullPage: true });
  const other = main.locator('.btn.state').first();
  const corrected = (await other.textContent())!.trim();
  await press(other, info);
  // Back in the queue where it was; the list shows the new answer.
  await expect(position).not.toHaveText(at);
  await expect(main.getByText('answer again to change it')).toHaveCount(0);
  await expect(page.locator('aside li').filter({ hasText: `✓ Corrected to ${corrected}` }).first()).toBeVisible();
  // The dataset got one sample from it, labelled with the corrected state.
  await expect.poll(async () => (await reviewSamples()).length).toBe(before + 1);
  expect((await reviewSamples())[0].labels).toEqual([corrected.toLowerCase()]);
  await expectNoHorizontalOverflow(page);
  errors.expectNone();
});

test('faces: a new face model learning the taught faces shows on the cards, the Faces card and People', async ({ page, request }) => {
  const errors = watchErrors(page);
  const faceId = await seededFaceSensorId(request);
  // Switching the face model embeds every taught face again; the status says how far it is.
  await page.route('**/api/v1/status', async (route) => {
    const status = await (await route.fetch()).json();
    status.faces = { ...status.faces, loaded: false, loading: true, embedding: { done: 3, total: 8 } };
    await route.fulfill({ json: status });
  });
  await page.goto('#/');
  const card = page.locator('.card', { has: page.getByRole('link', { name: 'Crew', exact: true }) });
  await expect(card.getByText('Learning faces… 3/8')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.goto(`#/sensors/${faceId}/settings`);
  await expect(page.locator('h1 ~ .chip', { hasText: 'Learning faces… 3/8' })).toBeVisible();
  await expect(page.getByTestId('faces-card').getByText(/Learning the taught faces .* 3 of 8/)).toBeVisible();
  await page.goto('#/people');
  await expect(page.getByTestId('faces-embedding')).toContainText('3 of 8');
  await expectNoHorizontalOverflow(page);
  errors.expectNone();
});

test('faces: turned on with a confirmation, a person taught from the Live tab, shown under Quality', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const faceId = await seededFaceSensorId(request);
  // Each device runs this against the same backend: start without the person it teaches.
  for (const key of ['butch', 'kari']) {
    const del = await request.delete(`api/v1/people/${key}`);
    expect([204, 404]).toContain(del.status());
  }

  // Turning it on asks first, and says what is stored and downloaded.
  const beach = await seededObjectSensorId(request);
  await page.goto(`#/sensors/${beach}/settings`);
  const card = page.getByTestId('faces-card');
  await expect(card.getByText('Recognise people by their face')).toBeVisible();
  await pressCentered(card.getByRole('button', { name: 'Turn on…' }), info);
  const sheet = page.getByRole('dialog', { name: 'Recognise faces' });
  await expect(sheet.getByText(/not the media folder/)).toBeVisible();
  await expect(sheet.getByText(/It is not a lock/)).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await press(sheet.getByRole('button', { name: /^(Turn on|Download and turn on)$/ }), info);
  await expect(card.getByText('Ask me about faces I do not know')).toBeVisible();
  // Its own limits follow the global ones until set.
  const own = card.getByTestId('sensor-face-limits').getByRole('spinbutton', { name: 'Smallest face' });
  await expect(own).toHaveValue('');
  const global = await (await request.get('api/v1/face-limits')).json();
  await expect(own).toHaveAttribute('placeholder', String(global.effective.min_face_px));
  await expectNoClipping(page, '.btn, .card');
  await pressCentered(card.getByRole('checkbox', { name: /Recognise faces/ }), info);
  await expect(card.getByRole('button', { name: 'Turn on…' })).toBeVisible();
  // Its toast sits at the bottom of a phone's screen, where the teach sheet's buttons come below.
  await expect(page.getByText('Face recognition is off')).toHaveCount(0, { timeout: 15_000 });

  // The Live tab: faces drawn in the person boxes, the known one named, the other unknown.
  await page.goto(`#/sensors/${faceId}/live`);
  await expect(page.locator('.roi .face').first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('Person · Suni').first()).toBeVisible({ timeout: 30_000 });
  // Tap the person nobody named (an unknown person after two checks): "Who is this?" with a new person.
  await pressCentered(page.locator('button.found', { hasText: /Unknown person|^\s*Person/ }).first(), info);
  const teach = page.getByRole('dialog', { name: 'Teach this box' });
  await expect(teach.getByText('Who is this?')).toBeVisible();
  await press(teach.getByRole('button', { name: 'New person…' }), info);
  await teach.getByPlaceholder('Name').fill('Butch');
  await press(teach.getByRole('button', { name: 'Add person' }), info);
  await expect(page.getByText(/Got it: this is Butch \(1 face\)/)).toBeVisible();
  await expect(page.getByText('Person · Butch').first()).toBeVisible({ timeout: 30_000 });

  // Quality: one card per person, with their faces.
  await page.goto(`#/sensors/${faceId}/quality`);
  const people = page.getByTestId('people');
  await expect(people).toBeVisible();
  await expect(page.getByRole('heading', { name: /^Butch/ })).toBeVisible();
  await expect(page.getByRole('img', { name: 'A face of Butch' })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.btn, .butch');

  // People: shared by every camera, renamed there (the entity IDs stay).
  await press(page.getByRole('link', { name: 'People' }), info);
  await expect(page).toHaveURL(/#\/people$/);
  const butch = page.locator('section.person', { has: page.getByRole('heading', { name: /^Butch/ }) });
  await expect(butch.getByText(/Seen now on Crew|Last seen .* on Crew/)).toBeVisible({ timeout: 30_000 });
  await expect(butch.getByText(/· Crew/).first()).toBeVisible(); // the camera each face was taught on
  const rename = page.locator('form.rename'); // the heading becomes the name field
  await expect(async () => {
    await pressCentered(butch.getByRole('button', { name: 'Rename' }), info); // a tap after scrolling can be lost
    await expect(rename).toBeVisible({ timeout: 2_000 });
  }).toPass();
  await rename.getByLabel('Name').fill('Butch W');
  await press(rename.getByRole('button', { name: 'Save' }), info);
  await expect(page.getByText('Butch is now Butch W')).toBeVisible();
  await expect(page.getByRole('heading', { name: /^Butch W/ })).toBeVisible();
  expect((await (await request.get('api/v1/people')).json()).people.map((p: { key: string }) => p.key)).toContain('butch');

  // Photos from the phone: more of a person, and a new person (the photo picker is a file input).
  const assets = path.join('..', 'backend', 'tests', 'assets');
  await page.locator('#photos-butch').setInputFiles(path.join(assets, 'face_wilmore.jpg'));
  await expect(page.getByText('1 face added to Butch W')).toBeVisible({ timeout: 30_000 });
  await expect(butch.getByText(/· photo/)).toBeVisible();
  const add = page.getByTestId('add-person');
  await add.getByLabel('Name of the new person').fill('Kari');
  await add.locator('input[type=file]').setInputFiles(path.join(assets, 'face_williams_2018.jpg'));
  await expect(page.getByText('1 face added to Kari')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('heading', { name: /^Kari/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.btn, .butch');
  errors.expectNone();
});

test('a camera that changed its picture size holds the readings until the region is checked', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const camera = `size-${info.project.name}`; // its own picture size: the projects run at the same time
  const created = await request.post('api/v1/sensors', {
    data: {
      name: `Resized ${info.project.name}`,
      kind: 'reading',
      source_type: 'http',
      source: `${COUNTER_URL(89931)}&camera=${camera}`,
      roi: COUNTER_BOX,
      reading: { mode: 'counter', display: 'counter', digits: 7, decimals: 3, change_limit: 'off' },
      interval_s: 3600,
      debounce: 1,
    },
  });
  expect(created.ok()).toBeTruthy();
  const id = (await created.json()).id;
  const reading = async () => (await (await request.get(`api/v1/sensors/${id}`)).json()).reading;
  try {
    await expect.poll(async () => (await reading()).value, { timeout: 60_000 }).toBe('89.931');
    await request.get(`${CAMERA_URL}/set?wider=400&camera=${camera}`);
    await request.post(`api/v1/sensors/${id}/classify`);
    await expect.poll(async () => (await reading()).camera_changed?.to, { timeout: 30_000 }).toEqual([1200, 360]);

    await page.goto('#/');
    await expect(page.locator('.sensor', { hasText: `Resized ${info.project.name}` }).getByText('Camera changed')).toBeVisible();
    await page.goto(`#/sensors/${id}/live`);
    const notice = page.getByTestId('camera-changed');
    await expect(notice).toContainText('from 800×360 to 1200×360 pixels');
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.btn, .chip, .notice');
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'camera-changed.png'), fullPage: true });
    await expect(notice.getByRole('link', { name: 'Adjust the region' })).toHaveAttribute('href', `#/sensors/${id}/settings`);

    await press(notice.getByRole('button', { name: 'The region is right' }), info);
    await expect(notice).toHaveCount(0, { timeout: 30_000 });
    expect((await reading()).camera_changed).toBeNull();
    errors.expectNone();
  } finally {
    await request.get(`${CAMERA_URL}/set?wider=0&camera=${camera}`);
    await request.delete(`api/v1/sensors/${id}`);
  }
});


// --- sound sensors (skipped until the sound model is on disk: tools/soundmodel/README.md) --------

test('new sound sensor through the wizard: a microphone stream is tried, sounds picked', async ({ page, request }, info) => {
  test.skip(!(await soundModelInstalled(request)), 'no sound model');
  const errors = watchErrors(page);
  await page.goto('#/sensors/new');
  await press(page.getByRole('radio', { name: /Listen/ }), info);
  await expect(page.getByText('Name it and pick a stream with sound')).toBeVisible();
  await page.getByPlaceholder('Hallway').fill('Wizard sound');
  await page.getByLabel('Stream with sound').fill(SOUND_WAV_URL);
  await press(page.getByRole('button', { name: 'Next' }), info);
  // The stream is listened to: sound only (an ESP32 microphone sends WAV).
  await expect(page.getByTestId('sound-test')).toContainText('16 kHz', { timeout: 20_000 });
  await expect(page.getByTestId('sound-test')).not.toContainText('video');
  // The same picker as for objects: popular sounds first, every other one behind "Show all".
  await press(page.getByRole('button', { name: 'Siren', exact: true }), info);
  await expect(page.getByRole('button', { name: 'Siren', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await press(page.getByRole('button', { name: /Show all \d+ sounds/ }), info);
  await page.getByPlaceholder(/Search \d+ sounds/).fill('kettle');
  await press(page.getByRole('button', { name: 'Whistle or kettle' }), info);
  await press(page.getByRole('button', { name: 'Show fewer' }), info);
  await expect(page.getByRole('button', { name: 'Whistle or kettle' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('spinbutton', { name: 'Siren: heard from' })).toBeVisible();
  await expect(page.getByText('binary_sensor.wizard_sound_whistle_or_kettle')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expect(page.getByRole('checkbox', { name: 'Send to Home Assistant' })).toBeChecked();
  await press(page.getByRole('button', { name: 'Create sensor' }), info);
  await expect(page).toHaveURL(/\/live$/);
  await expect(page.getByTestId('bar-siren')).toBeVisible();
  await expect(page.getByText('Analysed above')).toBeVisible({ timeout: 20_000 });
  const sensorId = Number(page.url().match(/sensors\/(\d+)\//)![1]);
  errors.expectNone();
  await page.goto('about:blank');
  await request.delete(`api/v1/sensors/${sensorId}`);
});

test('a sound heard on a camera: its video clip in the history, the question in Review', async ({ page, request }, info) => {
  test.skip(!(await soundModelInstalled(request)), 'no sound model');
  test.setTimeout(150_000);
  const id = await seededSoundSensorId(request);
  const errors = watchErrors(page);
  await page.goto(`#/sensors/${id}/history`);
  // The heading exactly: "Our siren heard" (the own sound, once heard) is not this row.
  const row = page.locator('.card.item').filter({ has: page.getByText('Siren heard', { exact: true }) }).first();
  await expect(row).toContainText('video clip');
  await press(row.getByRole('button').first(), info);
  const play = row.getByRole('button', { name: 'Play the clip' });
  await expect(play).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await press(play, info);
  await expect(row.locator('video')).toBeVisible();
  await expect(row.getByText('Teach this clip as')).toBeVisible();
  await expect(row.getByRole('button', { name: 'Our siren', exact: true })).toBeVisible();

  // Every project answers one question: a new siren asks again (the sensor's review has no cooldown).
  const waiting = async () => (await (await request.get(`api/v1/review?sensor=${id}`)).json()).total;
  if (!(await waiting())) {
    await request.get(PLAY_URL('siren', 0.1));
    await expect.poll(waiting, { timeout: 60_000 }).toBeGreaterThan(0);
  }
  await page.goto(`#/review?sensor=${id}`);
  await expect(page.getByText('Was this')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Play the clip' })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  // The question may name another sound (a siren can score as a smoke alarm too, and be asked about as both):
  // answer yes to it, and it leaves the queue.
  const before = await waiting();
  await press(page.getByRole('button', { name: /^Yes, / }), info);
  await expect.poll(waiting).toBe(before - 1);
  errors.expectNone();
});

test('own sounds: a recording taught as a new sound, played back, and forgotten', async ({ page, request }, info) => {
  test.skip(!(await soundModelInstalled(request)), 'no sound model');
  const id = await seededSoundSensorId(request);
  const errors = watchErrors(page);
  await page.goto(`#/sensors/${id}/quality`);
  await expect(page.getByTestId('own-sound-our_siren')).toBeVisible();
  const name = `Bark ${info.project.name}`;
  await page.getByLabel('Name of the new own sound').fill(name);
  const recording = path.resolve(import.meta.dirname, '..', '..', 'backend', 'tests', 'assets', 'sounds', 'dog_bark.m4a');
  await page.locator('#sound-files').setInputFiles(recording);
  const card = page.getByTestId(`own-sound-bark_${info.project.name}`);
  await expect(card).toContainText('1 clip');
  await press(card.getByRole('button', { name: 'Play the clip' }), info);
  await expect(card.locator('audio')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  const forget = card.getByRole('button', { name: /Forget/ }).first();
  await press(forget, info);
  await press(card.getByRole('button', { name: /Tap again/ }), info);
  await expect(card).toHaveCount(0);
  errors.expectNone();
});
