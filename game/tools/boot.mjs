import { chromium } from 'playwright';
import { writeFileSync } from 'fs';

const out = process.argv[2] || 'shots/boot.png';
const browser = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium',
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    '--ignore-gpu-blocklist', '--enable-webgl', '--no-sandbox', '--disable-dev-shm-usage',
    '--autoplay-policy=no-user-gesture-required', '--mute-audio'],
});
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
const errs = [];
page.on('console', m => { if (m.type() === 'error') errs.push('[console] ' + m.text()); });
page.on('pageerror', e => errs.push('[pageerror] ' + e.message));

try {
  await page.goto('http://localhost:5173/index.html', { waitUntil: 'commit', timeout: 30000 });
  await page.waitForFunction(() => window.__game && window.__game.R, { timeout: 90000 });
  // start the loop without real pointer lock, then simulate combat
  await page.evaluate(() => {
    const g = window.__game;
    try { g.audio.init(); } catch (e) {}
    g.running = true; g.clock.start(); g.player.enabled = true;
    g._loop();
    // aim at first enemy and fire a few rounds
    g._setScoped(true);
  });
  await page.waitForTimeout(500);
  const combat = await page.evaluate(() => {
    const g = window.__game;
    const before = g.enemies.alive.length;
    // point camera at an enemy
    const e = g.enemies.enemies[0];
    const dir = e.root.position.clone(); dir.y += 1.4;
    g.camera.lookAt(dir);
    g.player.yaw = Math.atan2(-(dir.x - g.camera.position.x), -(dir.z - g.camera.position.z));
    g.player.pitch = 0;
    for (let i = 0; i < 6; i++) { g.mag = 5; g.fireCooldown = 0; g._fire(); }
    return { before, after: g.enemies.alive.length, score: g.score };
  });
  await page.waitForTimeout(400);
  const dataUrl = await page.evaluate(() => document.getElementById('scene').toDataURL('image/png'));
  writeFileSync(out, Buffer.from(dataUrl.split(',')[1], 'base64'));
  console.log('BOOT_OK combat=', JSON.stringify(combat));
  console.log('SHOT_SAVED:', out);
} catch (e) {
  console.log('BOOT_ERROR:', e.message);
} finally {
  if (errs.length) console.log('PAGE_ERRORS:\n' + errs.slice(0, 20).join('\n'));
  else console.log('NO_PAGE_ERRORS');
  await browser.close();
}
