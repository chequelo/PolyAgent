import { chromium } from 'playwright';

const mode = process.argv[2] || 'scoped';       // scoped | critical | hud
const out = process.argv[3] || `shots/scene_${mode}.png`;

const browser = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium',
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    '--ignore-gpu-blocklist', '--no-sandbox', '--disable-dev-shm-usage',
    '--autoplay-policy=no-user-gesture-required', '--mute-audio'],
});
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
page.on('pageerror', e => console.log('[PAGEERR]', e.message.slice(0, 200)));

try {
  await page.goto('http://localhost:5173/index.html', { waitUntil: 'commit', timeout: 30000 });
  await page.waitForFunction(() => window.__game && window.__game.R, { timeout: 90000 });

  await page.evaluate((mode) => {
    const g = window.__game;
    const THREE = g.THREE || window.THREE;
    try { g.audio.init(); } catch (e) {}
    document.getElementById('menu').classList.add('hidden');
    document.getElementById('loading').classList.add('hidden');
    g.running = true; g.clock.start(); g.player.enabled = true;

    // aim at the closest enemy's head
    const e = g.enemies.enemies.slice().sort((a, b) =>
      a.root.position.distanceTo(g.player.pos) - b.root.position.distanceTo(g.player.pos))[2];
    const head = e.root.position.clone(); head.y += 1.55;
    const dx = head.x - g.player.pos.x, dz = head.z - g.player.pos.z;
    g.player.yaw = Math.atan2(-dx, -dz);
    const dist = Math.hypot(dx, dz);
    g.player.pitch = Math.atan2(head.y - g.player.pos.y, dist);

    g._loop();
    if (mode === 'scoped' || mode === 'critical') {
      g._setScoped(true);
      g.camera.fov = g.scopeFov; g.camera.updateProjectionMatrix();
    }
    window.__aimHead = [head.x, head.y, head.z];
  }, mode);

  // let scope/FOV settle
  await page.waitForTimeout(700);

  if (mode === 'critical') {
    await page.evaluate(() => {
      const g = window.__game;
      g.mag = 5; g.fireCooldown = 0; g.gameOver = false;
      g.rifle.fire(); g.audio.gunshot();
      // guaranteed headshot on the centered target for the spectacle capture
      const e = g.enemies.enemies.slice().sort((a, b) =>
        a.root.position.distanceTo(g.player.pos) - b.root.position.distanceTo(g.player.pos))[2];
      const p = e.root.position.clone(); p.y += 1.55;
      const muzzle = p.clone(); g.rifle.muzzle.getWorldPosition(muzzle);
      g.fx.tracer(muzzle, p); g.fx.impact(p, g.player.forward().clone().negate(), 'blood');
      e.damage('head');
      g.combo = 3; g.score += 450;
      g._criticalShot(p, 450);
      g.hud.setTargets(g.enemies.alive.length);
      g.hud.setScore(g.score);
    });
    await page.waitForTimeout(80);
    // Stop the loop (avoids compositor hang) then force the overlay to its
    // visible end-state via inline styles (headless freezes the CSS anim clock).
    await page.evaluate(() => {
      const g = window.__game;
      g.running = false;
      g.camera.fov = g.scopeFov * 0.92; g.camera.updateProjectionMatrix();
      g.R.setFocus(g.player.pos.distanceTo({ x: 0, y: 0, z: -10 }) || 40);
      g.R.render();
      const cr = document.getElementById('critical');
      cr.classList.remove('hidden');
      cr.style.cssText += ';display:block!important;animation:none!important;opacity:1!important;transform:scale(1) skewX(-12deg)';
      const xr = document.getElementById('xray');
      xr.classList.remove('hidden');
      xr.style.cssText += ';display:block!important;animation:none!important;opacity:0.6!important';
      return { critCls: cr.className, disp: getComputedStyle(cr).display, op: getComputedStyle(cr).opacity };
    }).then(s => console.log('CRIT_STATE', JSON.stringify(s)));
    await page.waitForTimeout(80);
    await page.screenshot({ path: out, timeout: 25000 });
    console.log('SHOT_SAVED:', out);
    await browser.close();
    process.exit(0);
  }

  // static modes: stop the loop so the compositor isn't racing heavy GPU work
  await page.evaluate(() => { window.__game.running = false; });
  await page.waitForTimeout(120);
  await page.screenshot({ path: out, timeout: 25000 });
  console.log('SHOT_SAVED:', out);
} catch (e) {
  console.log('SCENE_ERROR:', e.message.slice(0, 300));
} finally {
  await browser.close();
}
