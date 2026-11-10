// UI tests, run once on desktop (mouse) and once on a phone (touch). See playwright.config.ts.
import { expect, test, type APIRequestContext, type Locator, type Page, type TestInfo } from '@playwright/test';
import path from 'node:path';
import {
  CAMERA_URL,
  COUNTER_BOX,
  COUNTER_URL,
  DISPLAY_URL,
  METER_REGION,
  METER_URL,
  PHOTO_URL,
  PLAY_URL,
  READING_SENSOR_NAME,
  SOUND_WAV_URL,
  TURNED_COUNTER_REGION,
  TURNED_COUNTER_URL,
} from './env';
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
  playSirenUntilAsked,
  press,
  seededCounterSensorId,
  seededFaceSensorId,
  seededObjectSensorId,
  seededReadingSensorId,
  seededSensorId,
  sensorTabs,
  settingsSection,
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
        ['sound-dataset', `sensors/${soundId}/dataset`, 'Teach the sensor sounds of your own'],
        ['sound-settings-listening', `sensors/${soundId}/settings/detect`, 'Sounds to listen for'],
        ['sound-settings-review', `sensors/${soundId}/settings/checks`, 'a sound that was only just heard is asked about'],
        ['sound-home-assistant', `sensors/${soundId}/settings/home-assistant`, 'Send values'],
      ]
    : [];
  return [
    ...sound,
    ['dashboard', '', 'Sensors'],
    ['new-sensor', 'sensors/new', 'Name it and pick a camera'],
    ['esp32', 'esp32', 'An ESP32 microphone or camera of your own'],
    ['label', `sensors/${id}/label`, 'What state is this?'],
    ['dataset', `sensors/${id}/dataset`, 'Drop images, ZIP archives or video here'],
    ['quality', `sensors/${id}/quality`, 'What gets mixed up'],
    ['history', `sensors/${id}/history`, 'State changes and frames flagged for review'],
    ['sensor-settings', `sensors/${id}/settings`, 'Export or delete'],
    ['sensor-settings-general', `sensors/${id}/settings/general`, 'Image source'],
    ['sensor-settings-region', `sensors/${id}/settings/region`, 'Changing it retrains the model'],
    ['sensor-settings-states', `sensors/${id}/settings/detect`, 'Add state'],
    ['sensor-settings-checks', `sensors/${id}/settings/checks`, 'When to check'],
    ['sensor-settings-export', `sensors/${id}/settings/export`, 'Delete sensor'],
    ['objects-live', `sensors/${objectId}/live`, 'Right now'],
    ['objects-history', `sensors/${objectId}/history`, 'When each object appeared and cleared'],
    ['objects-dataset', `sensors/${objectId}/dataset`, 'What you taught'],
    ['objects-settings', `sensors/${objectId}/settings/detect`, 'Each object gets an on/off sensor'],
    ['objects-settings-checks', `sensors/${objectId}/settings/checks`, 'Sensor output'],
    ['objects-home-assistant', `sensors/${objectId}/settings/home-assistant`, 'Send values'],
    ['sensor-home-assistant', `sensors/${id}/settings/home-assistant`, 'Last frame'],
    ['reading-home-assistant', `sensors/${readingId}/settings/home-assistant`, 'Raw reading'],
    ['reading-live', `sensors/${readingId}/live`, 'What the reader sees'],
    ['reading-history', `sensors/${readingId}/history`, 'Every new value and every rejected reading'],
    ['reading-settings', `sensors/${readingId}/settings/detect`, 'Digits after the decimal point'],
    ['reading-settings-general', `sensors/${readingId}/settings`, 'Region and reading'],
    ['reading-settings-checks', `sensors/${readingId}/settings/checks`, 'Sensor output'],
    ['reading-quality', `sensors/${readingId}/quality`, 'Readings per day'],
    ['counter-live', `sensors/${counterId}/live`, 'One wheel per field'],
    ['faces-live', `sensors/${faceId}/live`, 'Unknown person'],
    ['faces-settings', `sensors/${faceId}/settings/detect`, 'Ask me about faces I do not know'],
    ['faces-dataset', `sensors/${faceId}/dataset`, 'Shared by every camera that recognises faces'],
    ['people', 'people', 'The people you named by their face'],
    ['counter-settings', `sensors/${counterId}/settings/detect`, 'Number of digits'],
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
  await toDetect(page, info);
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

test("a sensor's tabs, and its Settings in sections beside Pause, with one Save for them all", async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededSensorId(request);
  const phone = info.project.name !== 'desktop';
  await page.goto(`#/sensors/${id}/label`);
  const nav = page.getByRole('navigation', { name: 'Sensor sections' });
  await expect(nav.getByRole('link', { name: 'Label' })).toHaveAttribute('aria-current', 'page');
  expect(await sensorTabs(page)).toEqual(['Label', 'History', 'Dataset', 'Quality']);
  await expect(nav.getByRole('button', { name: /^More/ })).toHaveCount(0); // four fit on a phone too
  await expectNoHorizontalOverflow(page);
  if (phone) {
    // A very narrow phone: what does not fit goes under More, and the open tab stays in the row.
    const size = page.viewportSize()!;
    await page.setViewportSize({ width: 300, height: size.height });
    await page.goto(`#/sensors/${id}/history`);
    await expect(nav.getByRole('button', { name: /^More/ })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'History' })).toHaveAttribute('aria-current', 'page');
    await expectNoHorizontalOverflow(page);
    expect(await sensorTabs(page)).toEqual(expect.arrayContaining(['Label', 'Dataset', 'Quality', 'History']));
    await page.setViewportSize(size);
    await page.goto(`#/sensors/${id}/label`);
  }

  // Settings is the button beside Pause.
  const settingsButton = page.locator('header').getByRole('link', { name: 'Settings' });
  await press(settingsButton, info);
  await expect(page).toHaveURL(new RegExp(`#/sensors/${id}/settings$`));
  const sections = page.getByRole('navigation', { name: 'Settings sections' });
  const heading = page.locator('.settings .content h2');
  if (phone) {
    // A phone lists the sections, each saying what is set; one opens with a tap.
    await expect(sections.getByRole('link', { name: /Checks and output.*Every \d+ s/ })).toBeVisible();
    await expect(heading).toBeHidden();
  } else {
    await expect(heading).toHaveText('General'); // a wider screen opens the first beside the menu
  }
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'sensor-settings-sections.png') });
  await press(sections.getByRole('link', { name: /Checks and output/ }), info);
  await expect(page).toHaveURL(new RegExp(`#/sensors/${id}/settings/checks$`));
  await expect(heading).toHaveText('Checks and output');

  // A change waits for Save at the bottom of the screen, and is kept while another section is open.
  const every = page.getByLabel('Seconds between regular checks');
  const before = await every.inputValue();
  await every.fill(String(Number(before) + 7));
  const bar = page.locator('.savebar');
  await expect(bar.getByText('Unsaved changes')).toBeVisible();
  await expect(bar).toBeInViewport();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'sensor-settings-unsaved.png') });
  if (phone) {
    await press(page.locator('.settings .back'), info); // back to the list
    await expect(sections.getByRole('link', { name: /Checks and output/ })).toBeVisible();
  }
  await press(sections.getByRole('link', { name: /General/ }), info);
  await expect(heading).toHaveText('General');
  await expect(bar.getByText('Unsaved changes')).toBeVisible();
  // Undo puts back what is saved.
  await press(bar.getByRole('button', { name: 'Undo' }), info);
  await expect(bar).toHaveCount(0);
  await page.goto(`#/sensors/${id}/settings/checks`);
  await expect(page.getByLabel('Seconds between regular checks')).toHaveValue(before);

  // Export and Delete have their own section.
  await page.goto(`#/sensors/${id}/settings/export`);
  await expect(page.getByRole('button', { name: 'Delete sensor' })).toBeVisible();
  const content = page.locator('.settings .content');
  await expect(content.getByRole('link', { name: 'Export', exact: true }).or(content.getByRole('button', { name: 'Export', exact: true }))).toBeVisible();

  // A phone keeps the import options behind a button, so the images start on the first screen.
  await page.goto(`#/sensors/${id}/dataset`);
  const labelAll = page.getByLabel('Label everything as');
  if (phone) {
    await expect(labelAll).toBeHidden();
    await press(page.getByRole('button', { name: /^Import options/ }), info);
  }
  await expect(labelAll).toBeVisible();

  // Links of earlier versions open where those tabs went.
  await page.goto(`#/sensors/${id}/home-assistant`);
  await expect(heading).toHaveText('Home Assistant');
  await page.goto(`#/sensors/${id}/upload`);
  await expect(nav.getByRole('link', { name: 'Dataset' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByText('Drop images, ZIP archives or video here')).toBeVisible();
  errors.expectNone();
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

/** From the wizard's first step to its second; a small picture asks for a second Next (its warning). */
async function toDetect(page: Page, info: TestInfo) {
  const detect = page.getByText('What should this sensor detect?');
  await expect(async () => {
    if (await detect.isVisible()) return;
    await press(page.getByRole('button', { name: 'Next' }), info);
    await expect(detect).toBeVisible({ timeout: 2000 });
  }).toPass();
}

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

  // Held on from the moment both are chosen; without Home Assistant (as here) it says why not.
  const hold = page.getByTestId('light-hold');
  await expect(hold).toContainText('light.meter_flash');
  await expect(hold).toContainText('Home Assistant API is not configured');
  // The same hold on the next step, so the light does not blink in between; and the frame still comes.
  await page.getByRole('button', { name: 'Next' }).scrollIntoViewIfNeeded();
  await toDetect(page, info);
  await expect(hold).toBeVisible();
  await expect(page.locator('.roi img')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.btn, .chip, [data-testid="light-hold"]');
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'wizard-light.png'), fullPage: true });
  // Its switch keeps the light off for this view (and is remembered); on again for the next tests.
  const toggle = page.getByLabel('Light on while this view is open');
  await press(toggle, info);
  await expect(hold).toContainText('stays off while you look');
  await press(toggle, info);
  await expect(hold).toContainText('Home Assistant API is not configured');
  errors.expectNone();
});

test('the wizard shows the whole list of entities for a light', async ({ page }, info) => {
  const errors = watchErrors(page);
  const lights = Array.from({ length: 12 }, (_, i) => ({ entity_id: `light.lamp_${i}`, name: `Lamp ${i}`, domain: 'light', state: 'off' }));
  await page.route('**/api/v1/entities*', (route) => route.fulfill({ json: lights }));
  await page.goto('#/sensors/new');
  await press(page.getByText('HTTP snapshot URL'), info);
  await page.getByLabel('Light to switch on').focus();
  const list = page.getByRole('listbox');
  await expect(list).toBeVisible();
  if (info.project.name === 'desktop') {
    // Holding the scrollbar leaves the list open (the field keeps the focus).
    const box = (await list.boundingBox())!;
    await page.mouse.move(box.x + box.width - 4, box.y + 20);
    await page.mouse.down();
    await page.waitForTimeout(300);
    await page.mouse.up();
    await expect(list).toBeVisible();
  }
  // The last one is reached by scrolling, not cut off by the wizard.
  const last = list.getByRole('button', { name: /Lamp 11/ });
  await last.scrollIntoViewIfNeeded();
  await press(last, info);
  await expect(page.getByText('light.lamp_11')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  errors.expectNone();
});

test('new sensor wizard creates a sensor', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/sensors/new');
  await page.getByPlaceholder('Garage door').fill('Wizard test');
  await press(page.getByText('HTTP snapshot URL'), info);
  await page.getByLabel('Source URL').fill(`${CAMERA_URL}/snapshot.jpg`);
  // Leaving the field brings the camera's picture on this step; a URL half typed is not tried.
  await expect(page.getByTestId('camera-preview').locator('img')).toHaveCount(0);
  await page.getByLabel('Source URL').blur();
  await expect(page.getByTestId('camera-preview').locator('img')).toBeVisible();
  await expect(page.getByTestId('low-stream')).toHaveCount(0); // 1280 × 720: no warning
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'wizard-camera.png') }); // a full page would drop touch
  await press(page.getByRole('button', { name: 'Next' }), info);

  // The region is drawn with the states, the only step without a test.
  await expect(page.getByText('Which states can it be in?')).toBeVisible();
  const image = page.locator('.roi img');
  await expect(image).toBeVisible();
  await image.scrollIntoViewIfNeeded();
  const box = (await image.boundingBox())!;
  await drag(page, info, { x: box.x + box.width * 0.2, y: box.y + box.height * 0.2 }, { x: box.x + box.width * 0.8, y: box.y + box.height * 0.9 });
  await expect(page.locator('.handle.corner')).toHaveCount(4);

  // "Add state" puts the cursor in the new field; an empty name is not shown as an option.
  await press(page.getByRole('button', { name: 'Add state' }), info);
  await expect(page.getByText('options: open, closed, unknown')).toBeVisible();
  await page.keyboard.type('Partial');
  await expect(page.getByLabel('Name of state 3')).toHaveValue('Partial');
  await expect(page.getByText('options: open, closed, partial, unknown')).toBeVisible();
  if (hasTouchScreen(info)) await expect(page.getByText('Keys 1–9 label them later.')).toBeHidden();
  await press(page.getByRole('button', { name: 'Next' }), info);
  await expect(page.getByText('When should it check the camera?')).toBeVisible();
  // A new sensor is in Home Assistant unless kept out of it here.
  const notInHa = page.getByRole('checkbox', { name: 'Not in Home Assistant' });
  await expect(notInHa).not.toBeChecked();
  await press(notInHa, info);
  await expect(notInHa).toBeChecked();
  await press(notInHa, info);
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
  // A small picture: maybe the camera's low-resolution stream. Next stays to say so; the next one goes on.
  await expect(page.getByTestId('low-stream')).toContainText('640×421');
  await expect(page.getByText('What should this sensor detect?')).toHaveCount(0);
  await expectNoClipping(page, '.notice');
  await toDetect(page, info);
  await expect(page.locator('.roi img')).toBeVisible();

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
  await toDetect(page, info);

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
  await page.getByLabel('Source URL').fill(COUNTER_URL(45730.4)); // the last wheel four tenths on: rounded
  await toDetect(page, info);

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
  // A new reading sensor corrects rotation and perspective; the switch is with the region it changes.
  await expect(page.locator('.read-images').getByLabel('Correct rotation and perspective')).toBeChecked();
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
  await page.goto(`#/sensors/${sensorId}/settings/detect`);
  await expect(page.getByTestId('digit-cells').locator('.cell')).toHaveCount(7);
  await expect(page.getByLabel('Number of digits')).toHaveValue('7');
  errors.expectNone();
  await page.goto('about:blank');
  await request.delete(`api/v1/sensors/${sensorId}`);
});

test('a counter the camera sees turned is read with its region straightened', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  // The camera hangs askew: the counter is turned by 125° (issue #58). The region's four corners sit on the
  // window with the wheels, its top edge at the wheels' top.
  const roi = await (await request.get(TURNED_COUNTER_REGION(125))).json();
  const made = await request.post('api/v1/sensors', {
    data: {
      name: 'Turned meter',
      kind: 'reading',
      source_type: 'http',
      source: TURNED_COUNTER_URL(89932, 125),
      roi,
      reading: { mode: 'counter', display: 'counter', digits: 7, decimals: 3, unit: 'm³' },
    },
  });
  expect(made.ok(), await made.text()).toBeTruthy();
  const sensor = await made.json();
  expect(sensor.reading.straighten).toBe(true); // a new reading sensor straightens its region
  await page.goto(`#/sensors/${sensor.id}/live`);
  await expect(page.locator('.value-card .value')).toHaveText('89.932 m³', { timeout: 60_000 });

  // Settings: the region has four corners and no "+" to add one, its top marked; Turn moves the top.
  await page.goto(`#/sensors/${sensor.id}/settings/detect`);
  await expect(page.getByLabel('Correct rotation and perspective')).toBeChecked();
  const frame = page.locator('.frame .roi');
  await expect(frame.locator('.handle.corner')).toHaveCount(4);
  await expect(frame.locator('.handle.add')).toHaveCount(0);
  // The fields follow the corners in perspective, and stay inside them.
  const fields = frame.getByTestId('digit-cells');
  await expect(fields.locator('.cell')).toHaveCount(7);
  await expect(fields.locator('.divider')).toHaveCount(6);
  const insideRegion = await fields.evaluate((svg) => {
    const corners = (svg.parentElement!.querySelector('polygon.outline') as SVGPolygonElement).points;
    const inside = (x: number, y: number) => {
      let sign = 0;
      for (let i = 0; i < 4; i++) {
        const [a, b] = [corners[i], corners[(i + 1) % 4]];
        const side = Math.sign((b.x - a.x) * (y - a.y) - (b.y - a.y) * (x - a.x));
        if (side && sign && side !== sign) return false;
        sign = side || sign;
      }
      return true;
    };
    return [...svg.querySelectorAll('line')].every((l) => {
      const near = (t: number) => [l.x1.baseVal.value * (1 - t) + l.x2.baseVal.value * t, l.y1.baseVal.value * (1 - t) + l.y2.baseVal.value * t];
      return inside(...(near(0.02) as [number, number])) && inside(...(near(0.98) as [number, number]));
    });
  });
  expect(insideRegion).toBe(true);
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'settings-straightened.png'), fullPage: true });
  const top = frame.getByTestId('roi-top');
  await expect(top).toBeVisible();
  const before = (await top.boundingBox())!;
  await press(page.getByTestId('roi-turn'), info);
  await expect.poll(async () => Math.round((await top.boundingBox())!.x)).not.toBe(Math.round(before.x));
  await expectNoHorizontalOverflow(page);
  errors.expectNone();
  await page.goto('about:blank');
  await request.delete(`api/v1/sensors/${sensor.id}`);
});

test('a water meter with pointer dials: placed on its face in Settings, read as one more digit each', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  // The meter of issue #59: five wheels for whole m³ and four dials, seen turned and at an angle.
  const { region, dials } = await (await request.get(METER_REGION(20, 0.2))).json();
  const made = await request.post('api/v1/sensors', {
    data: {
      name: 'Dial meter',
      kind: 'reading',
      source_type: 'http',
      source: METER_URL(187.34565, 20, 0.2),
      roi: region,
      reading: { mode: 'counter', display: 'counter', digits: 5, decimals: 0, unit: 'm³' },
      interval_s: 3600,
    },
  });
  expect(made.ok(), await made.text()).toBeTruthy();
  const sensor = await made.json();
  try {
    await page.goto(`#/sensors/${sensor.id}/settings/detect`);
    // The same switch as in the wizard: the picture edits the counter, or the dials.
    await expect(page.locator('.frame .roi')).toBeVisible();
    await press(page.getByTestId('edit-dials'), info);
    await expect(page.locator('.frame .roi')).toHaveCount(0);
    const card = page.getByTestId('dials-card');
    const editor = card.getByTestId('dials-editor');
    await expect(editor.locator('img')).toBeVisible({ timeout: 60_000 });
    // Add a dial: a circle labelled ×0.1 below the counter; dragged it moves, its handle sizes it.
    await card.scrollIntoViewIfNeeded();
    await press(card.getByTestId('dial-add'), info);
    await expect(card.getByTestId('dial-label')).toHaveText(['×0.1']);
    const circle = editor.locator('circle').first();
    // Into the middle of the screen: Save at the bottom (a change is waiting) would take the drag.
    await circle.evaluate((el) => el.scrollIntoView({ block: 'center' }));
    const before = (await circle.boundingBox())!;
    const centre = { x: before.x + before.width / 2, y: before.y + before.height / 2 };
    await drag(page, info, centre, { x: centre.x - before.width * 0.6, y: centre.y + before.height * 0.3 });
    await expect.poll(async () => Math.round((await circle.boundingBox())!.x)).toBeLessThan(Math.round(before.x));
    const handle = card.getByTestId('dial-size');
    await handle.evaluate((el) => el.scrollIntoView({ block: 'center' }));
    const h = (await handle.boundingBox())!;
    const width = (await circle.boundingBox())!.width;
    await drag(page, info, { x: h.x + h.width / 2, y: h.y + h.height / 2 }, { x: h.x + h.width / 2 + width * 0.3, y: h.y + h.height / 2 });
    await expect.poll(async () => (await circle.boundingBox())!.width).toBeGreaterThan(width * 1.1);
    // A second one is ×0.01; its direction can be turned, and it can be removed.
    await press(card.getByTestId('dial-add'), info);
    await expect(card.getByTestId('dial-label')).toHaveText(['×0.1', '×0.01']);
    const second = card.getByTestId('dial-row').nth(1);
    await press(second.getByRole('button', { name: 'Clockwise' }), info);
    await expect(second.getByRole('button', { name: 'Anticlockwise' })).toBeVisible();
    await press(second.getByRole('button', { name: 'Remove the ×0.01 dial' }), info);
    await expect(card.getByTestId('dial-row')).toHaveCount(1);
    await press(page.getByRole('button', { name: 'Read now' }), info);
    await expect(card.getByTestId('dial-row').first()).toContainText(/Needle at|No needle found/, { timeout: 30_000 });
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.btn, .card');
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'settings-dials.png'), fullPage: true });

    // The dials where they are on this meter: one more digit each, read with the wheels.
    const saved = await request.patch(`api/v1/sensors/${sensor.id}`, { data: { reading: { ...sensor.reading, dials } } });
    expect(saved.ok(), await saved.text()).toBeTruthy();
    await request.post(`api/v1/sensors/${sensor.id}/classify`);
    await page.goto(`#/sensors/${sensor.id}/live`);
    await expect(page.locator('.value-card .value')).toHaveText('187.3456 m³', { timeout: 60_000 });
    await page.goto(`#/sensors/${sensor.id}/settings/detect`);
    await press(page.getByTestId('edit-dials'), info);
    await expect(card.getByTestId('dial-label')).toHaveText(['×0.1', '×0.01', '×0.001', '×0.0001']);
    await expect(card.getByTestId('dial-row').first()).toContainText('Needle at 3.', { timeout: 60_000 });

    // A reading rejected as too low waits in Review, with the dials to check it against.
    await request.patch(`api/v1/sensors/${sensor.id}`, { data: { source: METER_URL(150.12345, 20, 0.2) } });
    await request.post(`api/v1/sensors/${sensor.id}/classify`);
    await expect.poll(async () => (await (await request.get(`api/v1/review?sensor=${sensor.id}`)).json()).items.length, { timeout: 60_000 }).toBeGreaterThan(0);
    await page.goto(`#/review?sensor=${sensor.id}`);
    const seen = page.getByTestId('reading-seen').locator('img');
    await expect(seen).toBeVisible({ timeout: 30_000 });
    await expect.poll(() => seen.evaluate((img: HTMLImageElement) => img.naturalWidth), { timeout: 30_000 }).toBeGreaterThan(0);
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'review-dials.png'), fullPage: true });
    errors.expectNone();
  } finally {
    await page.goto('about:blank');
    await request.delete(`api/v1/sensors/${sensor.id}`);
  }
});

test('reading wizard suggests the decimals the display shows', async ({ page }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/sensors/new');
  await page.getByPlaceholder('Garage door').fill('Meter');
  await press(page.getByText('HTTP snapshot URL'), info);
  await page.getByLabel('Source URL').fill(DISPLAY_URL('1234.5'));
  await toDetect(page, info);
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
    expect(await sensorTabs(page)).toEqual(['Live', 'History', 'Dataset']);

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
    // The next check shows the person dashed and filtered.
    await expect(page.locator('button.found.filtered', { hasText: 'Person' })).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.roi .box.filtered')).toBeVisible();
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

    // The Dataset tab lists both, and the filtered frame.
    await page.goto(`#/sensors/${id}/dataset`);
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
    await page.goto(`#/sensors/${id}/settings/detect`);
    await expect(page.getByText('Use what you taught')).toBeVisible();
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

    // Turn it off or forget it all; then its Dataset says how to teach it again.
    await press(page.getByRole('button', { name: 'Forget all' }), info);
    await press(page.getByRole('button', { name: 'Tap again to forget everything taught' }), info);
    await expect(page.getByText('Everything taught is forgotten')).toBeVisible();
    await page.goto(`#/sensors/${id}/dataset`);
    await expect(page.getByTestId('nothing-taught')).toBeVisible();
    errors.expectNone();
  } finally {
    await request.delete(`api/v1/sensors/${id}`);
  }
});

test('region editor: move, add and remove corners', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededSensorId(request);
  await page.goto(`#/sensors/${id}/settings/region`);
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
  await page.goto(`#/sensors/${id}/settings/checks`);
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

  // A light (under General: the change above waits for Save meanwhile): only lights and switches are accepted.
  await settingsSection(page, info, 'General');
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
  await expect(page.getByRole('button', { name: 'Remove light.meter_flash' })).toBeVisible(); // General
  await page.goto(`#/sensors/${id}/settings/checks`);
  await expect(page.getByRole('checkbox', { name: 'Regular check' })).not.toBeChecked();
  await expect(page.getByLabel('Only when sensor.watermeter_status becomes')).toHaveValue('Flow finished');
  errors.expectNone();
  expect((await request.patch(`api/v1/sensors/${id}`, { data: { triggers: before } })).ok()).toBe(true);
});

test('the live page says when the regular check is off', async ({ page, request }) => {
  const errors = watchErrors(page);
  const before = (await (await request.get('api/v1/config')).json()).trigger_defaults;
  // An own object sensor: the seeded one may have been triggered by another test, and then the page
  // shows that trigger instead of this line.
  const seeded = await (await request.get(`api/v1/sensors/${await seededObjectSensorId(request)}`)).json();
  const made = await request.post('api/v1/sensors', {
    data: { name: 'Regular off', kind: 'objects', source_type: seeded.source_type, source: seeded.source, objects: { classes: ['person'] } },
  });
  expect(made.ok()).toBe(true);
  const own = (await made.json()).id;
  try {
    for (const [id, every, only] of [
      [own, /Checks every \d/, 'Only checks when triggered.'],
      [await seededReadingSensorId(request), /Reads every \d/, 'Only reads when triggered.'],
    ] as const) {
      const off = { triggers: { ...before, regular: false } };
      expect((await request.patch(`api/v1/sensors/${id}`, { data: off })).ok()).toBe(true);
      try {
        await page.goto(`#/sensors/${id}/live`);
        await page.reload();
        await expect(page.getByText(only)).toBeVisible();
        await expect(page.getByText(every)).toHaveCount(0);
      } finally {
        // Also after a failure: the next tests need this sensor checking.
        expect((await request.patch(`api/v1/sensors/${id}`, { data: { triggers: before } })).ok()).toBe(true);
      }
      await page.reload();
      await expect(page.getByText(every)).toBeVisible();
      await expect(page.getByText(only)).toHaveCount(0);
    }
  } finally {
    await request.delete(`api/v1/sensors/${own}`);
  }
  errors.expectNone();
});

test('the Home Assistant tab chooses what a sensor has there', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededObjectSensorId(request);
  const entity = async (name: string) =>
    (await (await request.get(`api/v1/sensors/${id}`)).json()).ha_entities.find((e: { name: string }) => e.name === name);
  // The first tap only asks, and nothing is removed until the second. A tap right after the page
  // scrolled can be spent on stopping the scroll (see the pitfalls in CLAUDE.md).
  const saveTwice = async () => {
    const again = page.getByRole('button', { name: 'Tap again to remove' });
    await expect(async () => {
      await press(page.getByRole('button', { name: 'Save changes' }), info);
      await expect(again).toBeVisible({ timeout: 1000 });
    }).toPass();
    await press(again, info);
  };
  await request.patch(`api/v1/sensors/${id}`, { data: { in_ha: true, publish: true, ha_entities: {} } });
  try {
    await page.goto(`#/sensors/${id}/settings/home-assistant`);
    const options: { object?: string }[] = (await (await request.get(`api/v1/sensors/${id}`)).json()).ha_entities;
    const first = options.find((e) => e.object)!.object!;
    // A snapshot is off until chosen; choosing one removes nothing, so one tap saves it.
    const snapshot = page.getByRole('checkbox', { name: `${first} snapshot` });
    await expect(snapshot).not.toBeChecked();
    await press(snapshot, info);
    await expect(page.getByTestId('ha-removing')).toHaveCount(0);
    await press(page.getByRole('button', { name: 'Save changes' }), info);
    await expect.poll(async () => (await entity(`${first} snapshot`)).on).toBe(true);
    await expect(page.getByText('Saved', { exact: true })).toHaveCount(0); // it covers the button on a phone

    // A chosen snapshot has a shape and a share of movement; a shape that is none says so.
    const shape = page.getByRole('textbox', { name: 'Snapshot shape' });
    await shape.fill('wide');
    const refused = page.getByText(/Snapshot shape: free, or width:height such as 16:9/);
    await expect(async () => {
      await press(page.getByRole('button', { name: 'Save changes' }), info);
      await expect(refused).toBeVisible({ timeout: 1000 });
    }).toPass();
    errors.errors.length = 0; // the refusal (422) is what was asked for here
    // It covers the button on a phone; a retried tap above may have left two.
    await expect(async () => {
      const dismiss = page.getByRole('button', { name: 'Dismiss' });
      if (await dismiss.count()) await press(dismiss.first(), info);
      await expect(dismiss).toHaveCount(0, { timeout: 500 });
    }).toPass();
    await shape.fill('16:9');
    await page.getByRole('spinbutton', { name: 'New snapshot when the box has changed by' }).fill('40');
    await expect(async () => {
      await press(page.getByRole('button', { name: 'Save changes' }), info);
      await expect
        .poll(async () => (await (await request.get(`api/v1/sensors/${id}`)).json()).objects.snapshot_aspect, { timeout: 1500 })
        .toBe('16:9');
    }).toPass();
    expect((await (await request.get(`api/v1/sensors/${id}`)).json()).objects.snapshot_move).toBe(0.4);
    await expectNoHorizontalOverflow(page);
    await expect(page.getByText('Saved', { exact: true })).toHaveCount(0);

    // Taking one away says what happens, and asks twice.
    await press(page.getByRole('checkbox', { name: `${first} count` }), info);
    await expect(page.getByTestId('ha-removing')).toContainText(`${first} count`);
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.btn, .card');
    await saveTwice();
    await expect.poll(async () => (await entity(`${first} count`)).on).toBe(false);
    await expect(page.getByText('Saved', { exact: true })).toHaveCount(0); // it covers the button on a phone

    // Not in Home Assistant: the table goes, and it is marked like a sensor not sent.
    await press(page.getByRole('radio', { name: 'Not in Home Assistant' }), info);
    await expect(page.getByTestId('ha-entities')).toHaveCount(0);
    await expect(page.getByTestId('ha-removing')).toContainText('all of this sensor');
    await saveTwice();
    await expect(page.locator('header .chip', { hasText: 'Not in Home Assistant' })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    // Last: a full-page screenshot drops touch emulation.
    await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'home-assistant-removing.png'), fullPage: true });
    errors.expectNone();
  } finally {
    const objects = { snapshot_aspect: 'free', snapshot_move: 0.25 };
    await request.patch(`api/v1/sensors/${id}`, { data: { in_ha: true, publish: true, ha_entities: {}, objects } });
  }
});

test('a sensor can be kept from Home Assistant while it is tuned', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const id = await seededReadingSensorId(request);
  try {
    await page.goto(`#/sensors/${id}/settings/home-assistant`);
    await expect(page.getByRole('radio', { name: 'Send values', exact: true })).toBeChecked();
    await press(page.getByRole('radio', { name: 'Don’t send values' }), info);
    await press(page.getByRole('button', { name: 'Save changes' }), info);
    await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    expect((await (await request.get(`api/v1/sensors/${id}`)).json()).publish).toBe(false);
    // Marked on the sensor page and on the dashboard, so it is not forgotten.
    await expect(page.locator('header .chip', { hasText: 'Values not sent' })).toBeVisible();
    await page.goto('#/');
    const card = page.locator('.card', { hasText: READING_SENSOR_NAME }).first();
    await expect(card.getByText('Values not sent')).toBeVisible();
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
  await page.goto(`#/sensors/${id}/settings/checks`);
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
  await page.goto(`#/sensors/${id}/settings/checks`);
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
  // The sizes come a moment later and make the lines longer: on a phone that moves the boxes, and
  // a tap before then can land beside the one meant.
  await expect(download).toContainText('about');
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
    await expect(compare.getByText(/· in use$/)).toHaveCount(6); // one per kind
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
  await page.goto(`#/sensors/${faceId}/settings/detect`);
  await expect(page.locator('h1 ~ .chip', { hasText: 'Learning faces… 3/8' })).toBeVisible();
  await expect(page.getByTestId('faces-card').getByText(/Learning the taught faces .* 3 of 8/)).toBeVisible();
  await page.goto('#/people');
  await expect(page.getByTestId('faces-embedding')).toContainText('3 of 8');
  await expectNoHorizontalOverflow(page);
  errors.expectNone();
});

test('faces: turned on with a confirmation, a person taught from the Live tab, shown on its Dataset tab', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  const faceId = await seededFaceSensorId(request);
  // Each device runs this against the same backend: start without the person it teaches.
  for (const key of ['butch', 'kari']) {
    const del = await request.delete(`api/v1/people/${key}`);
    expect([204, 404]).toContain(del.status());
  }

  // Turning it on asks first, and says what is stored and downloaded.
  const beach = await seededObjectSensorId(request);
  await page.goto(`#/sensors/${beach}/settings/detect`);
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
  await page.goto(`#/sensors/${faceId}/dataset`);
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
    await expect(notice.getByRole('link', { name: 'Adjust the region' })).toHaveAttribute('href', `#/sensors/${id}/settings/detect`);

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
  await expect(page.getByRole('checkbox', { name: 'Not in Home Assistant' })).not.toBeChecked();
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
  if (!(await waiting())) await playSirenUntilAsked(request, id);
  await page.goto(`#/review?sensor=${id}`);
  await expect(page.getByText('Was this')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Play the clip' })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  // The question may name another sound (a siren can score as a smoke alarm too, and be asked about as both):
  // answer yes to it, and it leaves the queue.
  const before = await waiting();
  // The clips taught so far: the answer adds one, to whichever sound the question named — a
  // built-in one, or the own sound "Our siren" when that was almost heard (both can be asked).
  const clipIds = async () =>
    ((await (await request.get(`api/v1/sensors/${id}/sounds/taught`)).json()).clips as { id: number }[]).map((c) => c.id);
  const known = new Set(await clipIds());
  // Played once, the clip goes round until the next question, whose clip then plays by itself in
  // the same player (a phone lets an element it played play again, never a new one).
  await press(page.getByRole('button', { name: 'Play the clip' }), info);
  const media = page.locator('section.main').locator('video, audio').first();
  await expect(media).toHaveAttribute('loop', '');
  const player = await media.elementHandle();
  const firstSrc = await media.getAttribute('src');
  await press(page.getByRole('button', { name: /^Yes, / }), info);
  await expect.poll(waiting).toBe(before - 1);
  if (before > 1 && (await page.getByText('Was this').count())) {
    await expect.poll(() => media.getAttribute('src')).not.toBe(firstSrc);
    // The same element, unless the next clip is of the other kind (sound only, or with video).
    if (await player!.evaluate((el) => el.isConnected)) {
      await expect.poll(() => player!.evaluate((el) => !(el as HTMLMediaElement).paused)).toBe(true);
    }
  }
  // The answer taught its clip to that sound, which then hears it surely and asks no more: forget
  // that clip (only it), so the next project is asked too.
  const added = async () => (await clipIds()).filter((c) => !known.has(c));
  await expect.poll(async () => (await added()).length, { timeout: 30_000 }).toBeGreaterThan(0);
  for (const c of await added()) await request.delete(`api/v1/sound-clips/${c}`);
  errors.expectNone();
});

test('a sound sensor on an ESP32: its microphone set from the Live tab, and a newer firmware said', async ({ page, request }, info) => {
  test.skip(!(await soundModelInstalled(request)), 'no sound model');
  const id = await seededSoundSensorId(request);
  const errors = watchErrors(page);
  // The e2e backend has no Home Assistant: the device it would find there is answered here.
  const device = {
    name: 'hall',
    address: '10.0.0.9',
    ours: true,
    firmware: '1.0.0-dev',
    latest: '1.1.0',
    outdated: true,
    controls: [
      { entity_id: 'switch.hall_microphone', kind: 'switch', name: 'Microphone', state: 'on' },
      { entity_id: 'number.hall_microphone_gain', kind: 'number', name: 'Microphone gain', state: '4.0', min: 1, max: 64, step: 1 },
    ],
  };
  const sent: unknown[] = [];
  await page.route(`**/api/v1/sensors/${id}/device`, async (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as { entity_id: string; value: boolean | number };
      sent.push(body);
      const c = device.controls.find((x) => x.entity_id === body.entity_id)!;
      c.state = typeof body.value === 'boolean' ? (body.value ? 'on' : 'off') : `${body.value}.0`;
      return route.fulfill({ json: { ok: true } });
    }
    return route.fulfill({ json: { device, error: null } });
  });
  await page.goto(`#/sensors/${id}/live`);
  const card = page.getByTestId('sound-device');
  await expect(card).toBeVisible();
  await expect(card.getByTestId('firmware-outdated')).toContainText('1.1.0');
  const mic = card.getByLabel('Microphone', { exact: true });
  await expect(mic).toBeChecked();
  await press(mic, info);
  await expect(mic).not.toBeChecked();
  const gain = card.getByLabel('Microphone gain');
  await gain.fill('8'); // a range input: set and let go
  await expect(card.getByText('8', { exact: true })).toBeVisible();
  expect(sent).toEqual([
    { entity_id: 'switch.hall_microphone', value: false },
    { entity_id: 'number.hall_microphone_gain', value: 8 },
  ]);
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.btn, .card');
  errors.expectNone();
});

test('own sounds: a recording taught as a new sound, played back, and forgotten', async ({ page, request }, info) => {
  test.skip(!(await soundModelInstalled(request)), 'no sound model');
  const id = await seededSoundSensorId(request);
  const errors = watchErrors(page);
  await page.goto(`#/sensors/${id}/dataset`);
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

test('own sounds: recorded from the stream, trimmed by its ends, taught and forgotten', async ({ page, request }, info) => {
  test.skip(!(await soundModelInstalled(request)), 'no sound model');
  test.setTimeout(90_000);
  const id = await seededSoundSensorId(request);
  const errors = watchErrors(page);
  await page.goto(`#/sensors/${id}/dataset`);
  const name = `Chime ${info.project.name}`;
  await page.getByLabel('Name of the new own sound').fill(name);
  await press(page.getByTestId('record-new'), info);
  const recorder = page.getByTestId('sound-recorder');
  const stop = recorder.getByTestId('record-stop');
  await expect(stop).toBeEnabled();
  await expect(recorder.getByTestId('record-clock')).not.toHaveText('0:00'); // the clock runs
  await request.get(PLAY_URL('chime'));
  await page.waitForTimeout(1500);
  await press(stop, info);
  await expect(recorder.getByText(/Listening \d+ s more/)).toBeVisible();
  // Then the trimmer: from Start to Stop kept, the seconds after there to take in.
  const range = recorder.getByTestId('trim-range');
  await expect(range).toBeVisible({ timeout: 30_000 });
  await expect(recorder.getByTestId('clip-trimmer').getByText('Stop', { exact: true })).toBeVisible(); // where Stop was
  const before = await range.textContent();
  const end = recorder.getByRole('slider', { name: 'End of the clip' });
  const from = await center(end);
  await drag(page, info, from, { x: from.x + 60, y: from.y });
  await expect(range).not.toHaveText(before ?? '');
  const startHandle = recorder.getByRole('slider', { name: 'Start of the clip' });
  const at = await center(startHandle);
  await drag(page, info, at, { x: at.x + 20, y: at.y });
  await expect(range).not.toHaveText(/^0\.0–/);
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '[data-testid=sound-recorder] .btn');
  await press(recorder.getByTestId('trim-play'), info);
  await press(recorder.getByTestId('record-teach'), info);
  const card = page.getByTestId(`own-sound-chime_${info.project.name}`);
  await expect(card).toContainText('1 clip');
  await expect(card.locator('.when').first()).toHaveAttribute('title', /^Recorded from the stream · /); // where it came from, on hover
  await expect(recorder).toHaveCount(0);
  await press(card.getByRole('button', { name: /Forget/ }).first(), info);
  await press(card.getByRole('button', { name: /Tap again/ }), info);
  await expect(card).toHaveCount(0);
  errors.expectNone();
});

test('a built-in sound taught with a clip of what it is not, recorded from the stream, then forgotten', async ({ page, request }, info) => {
  test.skip(!(await soundModelInstalled(request)), 'no sound model');
  test.setTimeout(90_000);
  const id = await seededSoundSensorId(request);
  const errors = watchErrors(page);
  await page.goto(`#/sensors/${id}/dataset`);
  // Not the siren: the other tests need it heard.
  const card = page.getByTestId('builtin-sound-dog_bark');
  await expect(card).toContainText('built-in');
  await expect(page.getByTestId('builtin-sound-siren')).toBeVisible();
  await press(card.getByTestId('record-not:dog_bark'), info);
  const recorder = card.getByTestId('sound-recorder');
  await expect(recorder.getByTestId('record-stop')).toBeEnabled();
  await request.get(PLAY_URL('chime'));
  await page.waitForTimeout(1500);
  await press(recorder.getByTestId('record-stop'), info);
  await expect(recorder.getByTestId('trim-range')).toBeVisible({ timeout: 30_000 });
  await expect(recorder.getByTestId('record-teach')).toHaveText(/Teach as not Dog/);
  await press(recorder.getByTestId('record-teach'), info);
  await expect(card.getByText('It is not this 1')).toBeVisible();
  await expect(card.locator('.when').first()).toHaveAttribute('title', /^Recorded from the stream · /); // where it came from, on hover
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '[data-testid^=builtin-sound] .btn');
  await press(card.getByRole('button', { name: 'Forget this clip' }), info);
  await expect(card.getByText('It is not this 0')).toBeVisible();
  errors.expectNone();
});

test('thresholds from the answers: the card on Settings, for a state sensor and a sound sensor', async ({ page, request }) => {
  const errors = watchErrors(page);
  const ids = [await seededSensorId(request)];
  if (await soundModelInstalled(request)) ids.push(await seededSoundSensorId(request));
  for (const id of ids) {
    const advice = (await (await request.get(`api/v1/sensors/${id}/advice`)).json()).items as { key: string; advice: { yes: number; no: number } }[];
    expect(advice.length).toBeGreaterThan(0);
    await page.goto(`#/sensors/${id}/settings/checks`);
    const card = page.getByTestId('advice-card');
    await expect(card).toContainText('From your answers');
    const answered = advice.filter((i) => i.advice.yes + i.advice.no > 0);
    if (answered.length) await expect(card.getByTestId(`advice-${answered[0].key}`)).toBeVisible();
    else await expect(card).toContainText('are counted here');
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '[data-testid=advice-card] .btn');
  }
  errors.expectNone();
});

test('the threads per AI model are chosen with the models, applied without a restart', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  await page.goto('#/settings');
  const field = page.getByTestId('inference-threads');
  const select = field.getByRole('combobox', { name: 'Threads per AI model' });
  await expect(select).toHaveValue('0');
  await expect(select.locator('option').first()).toHaveText(/^Automatic( \(\d+\))?$/); // no number on Windows: ONNX Runtime counts
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.card');
  try {
    await select.selectOption('1');
    await press(page.getByRole('button', { name: 'Apply' }), info);
    await expect.poll(async () => (await (await request.get('api/v1/settings')).json()).inference_threads).toBe(1);
    await expect(page.getByRole('button', { name: 'Apply' })).toBeDisabled(); // saved: nothing left to apply
  } finally {
    const now = await (await request.get('api/v1/settings')).json();
    await request.put('api/v1/settings', { data: { backbone: now.backbone, inference_threads: 0 } });
  }
  errors.expectNone();
});

test('the speed test: a model measured on a sensor, the sensors paused meanwhile', async ({ page, request }, info) => {
  test.setTimeout(120_000);
  const errors = watchErrors(page);
  const sensorId = await seededSensorId(request);
  await page.goto('#/settings');
  await press(page.getByTestId('speed-test-link').getByRole('link', { name: 'Open the speed test' }), info);
  const form = page.getByTestId('speed-test');
  await form.getByRole('combobox', { name: 'Sensor to test with' }).selectOption(String(sensorId));
  await expect(form.getByRole('checkbox', { checked: true })).toHaveCount(1); // the model in use, ready to test
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '[data-testid=speed-test] .btn, [data-testid=speed-test] .chip');
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'speed-test.png'), fullPage: true });
  await press(form.getByRole('button', { name: 'Test 1 model' }), info);
  const results = page.getByTestId('speed-test-result');
  await expect(results).toHaveCount(1, { timeout: 90_000 });
  await expect(results.first()).toContainText('Whole check');
  await expect(results.first()).toContainText('In use');
  await expect(page.getByTestId('speed-test-results')).toContainText('threads per model (automatic)');
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '[data-testid=speed-test-results] .btn, [data-testid=speed-test-results] .chip');
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'speed-test-results.png'), fullPage: true });
  const [file] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: 'Download report' }).click()]);
  expect(file.suggestedFilename()).toBe('visionstate-speed-test.txt');
  // Over: the sensors go on.
  await expect.poll(async () => (await (await request.get('api/v1/status')).json()).benchmark_running).toBe(false);
  errors.expectNone();
});

test('the ESP32 guide: a board and what it is to an ESPHome configuration, and a sensor from the device', async ({
  page,
}, info) => {
  const errors = watchErrors(page);
  await page.goto('#/sensors/new');
  await press(page.getByTestId('esp32-link').getByRole('link', { name: 'Set one up with ESPHome' }), info);
  const guide = page.getByTestId('esp32-guide');
  await expect(guide.getByRole('heading', { name: 'An ESP32 microphone or camera of your own' })).toBeVisible();
  const yaml = guide.locator('textarea[aria-label="ESPHome configuration"]'); // under "What is in it" (closed)
  await expect(yaml).toHaveValue(/board-esp32-devkit\.yaml/);

  // A XIAO ESP32-S3 Sense: its own microphone (PDM), nothing to wire.
  await press(guide.getByText('XIAO ESP32-S3 Sense', { exact: true }), info);
  await press(guide.getByText('Camera and microphone', { exact: true }), info);
  await expect(yaml).toHaveValue(/camera-xiao-esp32s3-sense\.yaml[\s\S]*microphone-pdm\.yaml/);
  await expect(guide.getByText('Nothing to wire: the camera and the microphone are on the board.')).toBeVisible();

  // A Waveshare ESP32-S3-Touch-LCD-2: a microphone only, wired to its headers, with no mute pin; its display is the light.
  await press(guide.getByText('Waveshare ESP32-S3-Touch-LCD-2', { exact: true }), info);
  await expect(yaml).toHaveValue(/board-waveshare-esp32s3-lcd2\.yaml[\s\S]*microphone\.yaml/);
  await expect(yaml).not.toHaveValue(/mute/);
  await expect(guide.getByText(/A mute switch on a pin/)).toHaveCount(0);
  await expect(guide.locator('table')).toContainText('GPIO43');
  await expect(guide.getByText(/With a camera in the socket, SD goes to GPIO18/)).toBeVisible();
  await expect(guide.getByText('Its display (a red dot) lights while VisionState listens or looks.')).toBeVisible();
  await expect(guide.getByText(/The Wi-Fi is the 2\.4 GHz network/)).toBeVisible();

  // A Waveshare ESP32-C5-WIFI6-KIT: a microphone with a mute switch, and Wi-Fi on 5 GHz too.
  await press(guide.getByText('Waveshare ESP32-C5-WIFI6-KIT', { exact: true }), info);
  await expect(yaml).toHaveValue(/board-waveshare-esp32c5-kit\.yaml[\s\S]*microphone\.yaml/);
  await expect(guide.locator('table')).toContainText('GPIO10');
  await expect(guide.getByText(/The Wi-Fi is the 2\.4 or 5 GHz network/)).toBeVisible();

  // An ESP32-CAM with a microphone and a mute switch: the camera's packages, the SD card's pins.
  await press(guide.getByText('ESP32-CAM', { exact: true }), info);
  await press(guide.getByText('Camera and microphone', { exact: true }), info);
  await guide.getByRole('textbox', { name: 'Device name' }).fill('Front door');
  await press(guide.getByText(/A mute switch on a pin/), info);
  await expect(yaml).toHaveValue(/esp32-cam-microphone\.yaml[\s\S]*microphone\.yaml[\s\S]*mute-pin\.yaml/);
  // What the camera looks at: written for the device's Camera use.
  await expect(yaml).toHaveValue(/camera_use: "States"/);
  await press(guide.getByText('Meters and displays', { exact: true }), info);
  await expect(yaml).toHaveValue(/camera_use: "Meters and displays"/);
  await expect(yaml).toHaveValue(/name: front-door/);
  await expect(guide.locator('table')).toContainText('GPIO14');
  await expect(guide.locator('table')).toContainText('GPIO12 to GND');
  // The Wi-Fi and the device's password are fields of the page, filled into the file in the browser.
  const download = guide.getByRole('button', { name: /^Download/ });
  await expect(download).toBeDisabled();
  await guide.getByRole('textbox', { name: 'Wi-Fi name' }).fill('Our Wi-Fi');
  await guide.getByLabel('Wi-Fi password', { exact: true }).fill('very secret');
  await expect(download).toBeEnabled();
  const password = await guide.getByRole('textbox', { name: 'Device password' }).inputValue();
  expect(password).toHaveLength(20);
  await expect(yaml).toHaveValue(new RegExp(`wifi_ssid: "Our Wi-Fi"[\\s\\S]*stream_password: "${password}"`));
  await expect(yaml).not.toHaveValue(/very secret/); // shown masked
  const [file] = await Promise.all([page.waitForEvent('download'), press(download, info)]);
  expect(file.suggestedFilename()).toBe('front-door.yaml');
  const saved = await (await import('node:fs/promises')).readFile(await file.path(), 'utf8');
  expect(saved).toContain('wifi_password: "very secret"'); // the file itself has it
  await expect(yaml).not.toHaveValue(/\{\{/);
  await expectNoClipping(page, '[data-testid=esp32-guide] .btn');
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: path.join('test-results', 'pages', info.project.name, 'esp32-guide.png'), fullPage: true });

  // Home Assistant finds the device: first waiting under Discovered (with the key it asks for), then
  // added, with its address filled in. No board here: Home Assistant's answers are made up.
  let devices: unknown = { devices: [], waiting: ['front-door'], error: null };
  await page.route('**/api/v1/esphome/devices', (route) => route.fulfill({ json: devices }));
  const waiting = guide.getByTestId('esp32-waiting');
  await expect(waiting).toContainText('Home Assistant has found front-door: add it.', { timeout: 15_000 });
  await press(waiting.getByRole('button', { name: 'Show' }), info);
  await expect(waiting.locator('.key')).toHaveText(/^[A-Za-z0-9+/]{43}=$/);
  devices = {
    devices: [{ entity_id: 'sensor.front_door_ip_address', name: 'Front door', address: '10.0.0.7' }],
    waiting: [],
    error: null,
  };
  await expect(guide.getByTestId('esp32-found')).toContainText('10.0.0.7', { timeout: 15_000 });
  await expect(guide.getByRole('textbox', { name: 'IP address' })).toHaveValue('10.0.0.7');

  // The device answers (no board here: the check is answered for it) and becomes a sound sensor.
  const sound = `http://visionstate:${password}@10.0.0.7:8081/audio.wav`;
  await page.route('**/api/v1/esphome/check', (route) =>
    route.fulfill({
      json: {
        addresses: { sound, picture: 'http://visionstate:x@10.0.0.7:8081/snapshot.jpg' },
        sound: { ok: true, status: 200 },
        picture: { ok: false, status: 401 },
      },
    }),
  );
  await press(guide.getByRole('button', { name: 'Check' }), info);
  await expect(guide.getByTestId('esp32-check')).toContainText('Picture: Wrong password');
  await press(guide.getByRole('button', { name: 'Make a sound sensor' }), info);
  await expect(page.getByRole('heading', { name: 'Name it and pick a stream with sound' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Stream with sound' })).toHaveValue(sound);
  await expect(page.getByLabel('Sensor name')).toHaveValue('Front door sound'); // not the device's own name
  expect(page.url()).not.toContain(password); // the password never goes into the address bar
  errors.expectNone();
});

test('an ESP32 camera of ours: its set-up in the sensor settings, and the camera follows', async ({ page, request }, info) => {
  const errors = watchErrors(page);
  // The fake camera's /esp32/ answers as our firmware does (an X-VisionState-Camera header).
  const made = await request.post('api/v1/sensors', {
    data: {
      name: `ESP32 door ${info.project.name}`,
      source_type: 'http',
      source: `${CAMERA_URL}/esp32/snapshot.jpg`,
      interval_s: 2,
      states: [{ name: 'Open' }, { name: 'Closed' }],
    },
  });
  const id = (await made.json()).id as number;
  const cameraUse = async () => (await (await request.get(`${CAMERA_URL}/esp32/state`)).json()).use as string;
  try {
    await expect
      .poll(async () => (await (await request.get(`api/v1/sensors/${id}`)).json()).camera?.now, { timeout: 30_000 })
      .toBe('states'); // a state sensor: still things
    await page.goto(`#/sensors/${id}/settings/general`); // on a phone Settings opens as a list of its sections
    const setup = page.getByLabel('Camera set-up');
    await expect(setup).toBeVisible();
    await expect(setup.locator('option').first()).toHaveText('Automatic: States');
    await setup.selectOption('meter');
    await press(page.getByRole('button', { name: 'Save changes' }), info);
    await expect(page.getByText('Settings saved')).toBeVisible();
    await expect.poll(cameraUse, { timeout: 30_000 }).toBe('meter');
    await expectNoHorizontalOverflow(page);
    await expectNoClipping(page, '.btn, .card');
    // Any other camera has no such choice.
    await page.goto(`#/sensors/${await seededSensorId(request)}/settings/general`);
    await expect(page.getByRole('heading', { name: 'General' })).toBeVisible();
    await expect(page.getByLabel('Camera set-up')).toHaveCount(0);
    errors.expectNone();
  } finally {
    await request.delete(`api/v1/sensors/${id}`);
    await request.get(`${CAMERA_URL}/esp32/snapshot.jpg?use=states`); // as it was, for the next project
  }
});

test('licence plates: turned on after a sheet, a car added, named by its plate on the Live tab and in the history', async ({
  page,
  request,
}, info) => {
  // Every device runs this against the same backend: its own sensor, car and plate.
  const plate = { desktop: 'VS 12345', mobile: 'VS 23456', iphone: 'VS 34567' }[info.project.name] ?? 'VS 45678';
  const name = `Plates ${info.project.name}`;
  const carName = `Car ${info.project.name}`;
  for (const s of await (await request.get('api/v1/sensors')).json()) {
    if (s.name === name) await request.delete(`api/v1/sensors/${s.id}`);
  }
  for (const c of await (await request.get('api/v1/cars')).json()) {
    if (c.name.startsWith(carName)) await request.delete(`api/v1/cars/${c.key}`);
  }
  const created = await request.post('api/v1/sensors', {
    data: {
      name,
      kind: 'objects',
      source_type: 'http',
      source: `${CAMERA_URL}/plate.jpg?text=${encodeURIComponent(plate)}`,
      objects: { classes: ['car'], clear_after_s: 0 },
      interval_s: 2,
    },
  });
  expect(created.ok()).toBeTruthy();
  const id = (await created.json()).id;

  // Off by default; turning it on asks first and says what it means.
  await page.goto(`#/sensors/${id}/settings/detect`);
  const card = page.getByTestId('plates-card');
  await pressCentered(card.getByRole('button', { name: 'Turn on…' }), info);
  const sheet = page.getByRole('dialog', { name: `Read licence plates on ${name}?` });
  await expect(sheet.getByText(/It is not a key/)).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await press(sheet.getByRole('button', { name: /^(Turn on|Download and turn on)$/ }), info);
  await expect(card.getByText('Report plates of cars I do not know')).toBeVisible();

  // A car of the household, with its plate.
  await pressCentered(card.getByRole('button', { name: 'Add a car' }), info);
  await card.getByPlaceholder('Our car').fill(carName);
  await card.getByPlaceholder('AB 12345').fill(plate);
  await press(card.getByRole('button', { name: 'Add car' }), info);
  await expect(card.getByTestId('car').filter({ hasText: carName })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoClipping(page, '.btn, .card');

  // Reporting unknown plates asks first too.
  await pressCentered(card.getByRole('checkbox', { name: /Report plates of cars I do not know/ }), info);
  await expect(page.getByRole('dialog', { name: 'Report plates of cars you do not know?' })).toBeVisible();
  await press(page.getByRole('button', { name: 'Cancel' }), info);
  await expect(card.getByRole('checkbox', { name: /Report plates of cars I do not know/ })).not.toBeChecked();

  // How far off a plate may be when the car's look agrees: saved at once.
  await card.getByTestId('look-distance').selectOption('2');
  await expect
    .poll(async () => (await (await request.get(`api/v1/sensors/${id}`)).json()).objects.plates.look_distance)
    .toBe(2);
  await expectNoClipping(page, '.btn, .card');

  // The Live tab: the plate read and whose car it is; the car is an entity of the sensor.
  await page.goto(`#/sensors/${id}/live`);
  const text = plate.replace(' ', '');
  await expect(page.getByTestId('plate-notes').getByText(`Car · ${text} · ${carName}`)).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(/^event\..*licence_plate$/)).toBeVisible();
  // ... and how it was known, there and as an entity that can be chosen on the Home Assistant tab.
  await expect(page.getByText(/^Detected by its plate/).first()).toBeVisible();
  await page.goto(`#/sensors/${id}/settings/home-assistant`);
  await expect(page.getByRole('columnheader', { name: 'Identified by' })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: `${carName} identified by` })).toBeChecked();
  await expectNoHorizontalOverflow(page);

  // The history: the car's coming is a plate row.
  await page.goto(`#/sensors/${id}/history`);
  await expect(page.getByText(`${carName} · plate ${text}`).first()).toBeVisible({ timeout: 30_000 });
  await expectNoHorizontalOverflow(page);

  // A second car of the same look: the first stands for the look, its plate moves to a car under it.
  await page.goto(`#/sensors/${id}/settings/detect`);
  await pressCentered(card.getByRole('button', { name: 'Add a car' }), info);
  await card.getByPlaceholder('Our car').fill(`${carName} 2`);
  await card.getByPlaceholder('AB 12345').fill(plate.replace('VS', 'EL')); // its own on every device
  await card.getByTestId('car-kind').selectOption({ label: carName });
  await expect(card.getByText(/moves to a car of its own under it/)).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await pressCentered(card.getByRole('button', { name: 'Add car' }), info);
  await expect(card.getByTestId('car').filter({ hasText: `${carName} ${plate}` })).toBeVisible();
  await expect(card.getByTestId('car').filter({ hasText: `${carName} 2` })).toContainText(`looks like ${carName}`);
  await expectNoClipping(page, '.btn, .card');

  await request.delete(`api/v1/sensors/${id}`);
  for (const c of await (await request.get('api/v1/cars')).json()) {
    if (c.name.startsWith(carName)) await request.delete(`api/v1/cars/${c.key}`);
  }
});

test('a plate model that states no licence is chosen only after accepting that', async ({ page }, info) => {
  await page.goto('#/settings');
  const select = page.getByRole('combobox', { name: 'Licence plates (object sensors)' });
  await select.scrollIntoViewIfNeeded();
  const options = await select.locator('option').allTextContents();
  await select.selectOption({ label: options.find((o) => o.startsWith('Plate model, fast'))! });
  const accept = page.getByTestId('plate-accept');
  await expect(accept).toBeVisible();
  const save = page.getByRole('button', { name: 'Apply', exact: true });
  await expect(save).toBeDisabled();
  await expectNoHorizontalOverflow(page);
  await press(accept.getByRole('checkbox'), info);
  await expect(save).toBeEnabled();
  // Not saved: the open model stays for the other tests.
  await select.selectOption({ label: options.find((o) => o.startsWith('PP-OCR small'))! });
  await expect(accept).toHaveCount(0);
});
