// Shared helpers for the UI tests: error collection, layout checks and real touch/mouse gestures.
import { expect, type APIRequestContext, type Locator, type Page, type TestInfo } from '@playwright/test';
import { OBJECT_SENSOR_NAME, READING_SENSOR_NAME, SENSOR_NAME } from './env';

export type Point = { x: number; y: number };

export const isTouch = (info: TestInfo) => info.project.name === 'mobile';

/** Collects console errors and uncaught exceptions; call `expectNone()` at the end of a test. */
export function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(`console: ${msg.text()} (${msg.location().url})`);
  });
  page.on('pageerror', (err) => errors.push(`page: ${err.message}`));
  return { errors, expectNone: () => expect(errors, errors.join('\n')).toEqual([]) };
}

/** The page must not scroll sideways (content wider than the screen). */
export async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, 'page is wider than the screen').toBeLessThanOrEqual(1);
}

/** No element matching `selector` may have content wider than itself (text running out of a button). */
export async function expectNoClipping(page: Page, selector: string) {
  const clipped = await page.$$eval(selector, (els) =>
    els.filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent?.trim()),
  );
  expect(clipped, `content runs out of ${selector}`).toEqual([]);
}

export async function center(locator: Locator): Promise<Point> {
  const box = await locator.boundingBox();
  if (!box) throw new Error('element not visible');
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/**
 * Real touch events via Chrome DevTools. Taps go through the same channel as drags so a test
 * uses one consistent touch stream.
 */
async function touch(page: Page, from: Point, to: Point, holdMs: number) {
  const steps = from.x === to.x && from.y === to.y ? 0 : 8;
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [from] });
  if (holdMs) await page.waitForTimeout(holdMs);
  for (let i = 1; i <= steps; i++) {
    const p = { x: from.x + ((to.x - from.x) * i) / steps, y: from.y + ((to.y - from.y) * i) / steps };
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [p] });
  }
  // Rest before lifting: a fast release starts a fling, and Chrome then spends the next tap on stopping it.
  if (steps) await page.waitForTimeout(150);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await cdp.detach();
}

/** Drag with a finger or the mouse, depending on the project. */
export async function drag(page: Page, info: TestInfo, from: Point, to: Point, holdMs = 0) {
  const steps = 8;
  if (isTouch(info)) {
    await touch(page, from, to, holdMs);
  } else {
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    if (holdMs) await page.waitForTimeout(holdMs);
    await page.mouse.move(to.x, to.y, { steps });
    await page.mouse.up();
  }
}

/** Press and hold without moving (long-press). */
export async function hold(page: Page, info: TestInfo, at: Point, ms: number) {
  await drag(page, info, at, at, ms);
}

/** Double-click with the mouse, or double-tap with a finger. */
export async function doubleTap(page: Page, info: TestInfo, at: Point) {
  if (isTouch(info)) {
    await touch(page, at, at, 0);
    await page.waitForTimeout(80);
    await touch(page, at, at, 0);
  } else {
    await page.mouse.dblclick(at.x, at.y);
  }
}

/** Tap or click an element. */
export async function press(locator: Locator, info: TestInfo) {
  if (isTouch(info)) {
    await locator.scrollIntoViewIfNeeded();
    await expect(locator).toBeEnabled();
    await touch(locator.page(), await center(locator), await center(locator), 0);
  } else await locator.click();
}

export async function seededSensorId(request: APIRequestContext, name = SENSOR_NAME): Promise<number> {
  const sensors: { id: number; name: string }[] = await (await request.get('api/v1/sensors')).json();
  const sensor = sensors.find((s) => s.name === name);
  if (!sensor) throw new Error(`seeded sensor ${name} missing`);
  return sensor.id;
}

export const seededObjectSensorId = (request: APIRequestContext) => seededSensorId(request, OBJECT_SENSOR_NAME);
export const seededReadingSensorId = (request: APIRequestContext) => seededSensorId(request, READING_SENSOR_NAME);
