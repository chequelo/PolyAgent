import { chromium } from 'playwright';
import { writeFileSync } from 'fs';

const url = process.argv[2] || 'http://localhost:5173/capture.html';
const out = process.argv[3] || 'shots/probe.png';
const settle = parseInt(process.argv[4] || '600', 10);

const browser = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium',
  args: [
    '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    '--ignore-gpu-blocklist', '--enable-webgl', '--no-sandbox',
    '--disable-dev-shm-usage',
  ],
});
const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
const errs = [];
page.on('console', m => { if (m.type() === 'error') errs.push('[console] ' + m.text()); });
page.on('pageerror', e => errs.push('[pageerror] ' + e.message));

try {
  await page.goto(url, { waitUntil: 'commit', timeout: 30000 });
  await page.waitForFunction(() => window.__ready === true, { timeout: 90000 });
  await page.waitForTimeout(settle);
  const dataUrl = await page.evaluate(() => {
    const c = document.getElementById('scene');
    return c ? c.toDataURL('image/png') : null;
  });
  if (!dataUrl) throw new Error('no canvas');
  writeFileSync(out, Buffer.from(dataUrl.split(',')[1], 'base64'));
  console.log('SHOT_SAVED:', out);
} catch (e) {
  console.log('CAPTURE_ERROR:', e.message);
} finally {
  if (errs.length) console.log('PAGE_ERRORS:\n' + errs.slice(0, 12).join('\n'));
  await browser.close();
}
