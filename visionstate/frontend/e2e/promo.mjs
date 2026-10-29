// The README screenshots (docs/promo/), from an app seeded with scripts/seed_demo.py.
//   node e2e/promo.mjs [app URL, default http://127.0.0.1:8099] [output folder, default ../../docs/promo]
// 1440 px wide at device scale 1.2. With a sound sensor (seed_demo.py --sound), sound.png shows its Live
// tab right after VS_PROMO_PLAY (a URL, e.g. the fake camera's /play?sound=smoke_alarm) played a sound.
import { chromium } from '@playwright/test';
import path from 'node:path';

const base = (process.argv[2] ?? 'http://127.0.0.1:8099').replace(/\/$/, '');
const out = process.argv[3] ?? path.resolve(import.meta.dirname, '..', '..', '..', 'docs', 'promo');

const sensors = await (await fetch(`${base}/api/v1/sensors`)).json();
const id = (name) => {
  const sensor = sensors.find((s) => s.name === name);
  if (!sensor) throw new Error(`No sensor called ${name}: run scripts/seed_demo.py first`);
  return sensor.id;
};
// [page, file, height in CSS pixels]
const shots = [
  ['', 'dashboard', 640],
  [`sensors/${id('Driveway')}`, 'objects', 890],
  [`sensors/${id('Water meter')}`, 'reading', 790],
  ['people', 'people', 790],
];
const hallway = sensors.find((s) => s.name === 'Hallway' && s.kind === 'sound');
if (hallway) shots.push([`sensors/${hallway.id}/live`, 'sound', 885]);

// A fresh check of every sensor, so none shows "unknown" from before it was trained.
for (const s of sensors.filter((s) => s.kind !== 'sound')) await fetch(`${base}/api/v1/sensors/${s.id}/classify`, { method: 'POST' });
await new Promise((resolve) => setTimeout(resolve, 5000));

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1.2 });
const errors = [];
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
for (const [hash, name, height] of shots) {
  if (name === 'sound' && process.env.VS_PROMO_PLAY) {
    await fetch(process.env.VS_PROMO_PLAY);
    await new Promise((resolve) => setTimeout(resolve, 1500)); // heard in the next windows
  }
  await page.goto(`${base}/#/${hash}`);
  await page.waitForTimeout(4000); // the live frame and its boxes
  await page.waitForFunction(() => [...document.images].every((img) => img.complete));
  await page.screenshot({ path: path.join(out, `${name}.png`), clip: { x: 0, y: 0, width: 1440, height } });
  console.log(`${name}.png`);
}
await browser.close();
if (errors.length) console.log('Console errors:', errors);
