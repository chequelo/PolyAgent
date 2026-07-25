import * as THREE from 'three';
import './ui/style.css';
import { Renderer } from './engine/renderer.js';
import { buildSky } from './engine/sky.js';
import { Level } from './world/level.js';
import { EnemyManager } from './enemy/enemy.js';
import { Rifle } from './weapon/rifle.js';
import { Player } from './player/controller.js';
import { FX } from './fx/particles.js';
import { Audio } from './audio/audio.js';
import { HUD } from './ui/hud.js';

class Game {
  constructor() {
    this.canvas = document.getElementById('scene');
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(58, innerWidth / innerHeight, 0.05, 2000);
    this.scene.add(this.camera);

    this.R = new Renderer(this.canvas, { preserveDrawingBuffer: true });
    this.R.width = innerWidth; this.R.height = innerHeight;
    this.env = buildSky(this.scene, this.R.renderer);

    this.level = new Level(this.scene);
    this.info = this.level.build();

    this.enemies = new EnemyManager(this.scene, this.level);
    this.enemies.spawnAll(this.info.enemySpawns);

    this.player = new Player(this.camera, this.level, this.info.playerStart);
    this.rifle = new Rifle(this.camera);
    this.fx = new FX(this.scene);
    this.audio = new Audio();
    this.hud = new HUD();

    this.R.build(this.scene, this.camera);

    // weapon config
    this.baseFov = 58;
    this.scopeFov = 11;
    this.scoped = false;
    this.magSize = 5; this.mag = 5; this.reserve = 95;
    this.reloading = false;
    this.fireCooldown = 0;
    this.score = 0;
    this.raycaster = new THREE.Raycaster();
    this.raycaster.camera = this.camera; // required when scene contains sprites

    // ---- Silent Scope arcade state ----
    this.timeLimit = 90; this.timeLeft = this.timeLimit;
    this.timeScale = 1; this.critRecover = 0; this.critZoom = 0;
    this.combo = 0; this.comboTimer = 0;
    this.shotsFired = 0; this.shotsHit = 0;
    this.totalTargets = this.enemies.enemies.length;
    this.gameOver = false;

    this.clock = new THREE.Clock();
    this.running = false;
    this._bindInput();
    window.addEventListener('resize', () => this._resize());

    // first render so the menu shows a live backdrop
    this.player.enabled = false;
    this.rifle.snap();
    this.R.render();
    document.getElementById('loading').classList.add('hidden');
  }

  _resize() {
    this.R.resize();
    this.camera.aspect = innerWidth / innerHeight;
    this.camera.updateProjectionMatrix();
  }

  _bindInput() {
    const start = () => this.start();
    document.getElementById('startBtn').addEventListener('click', start);
    document.getElementById('restartBtn').addEventListener('click', () => this._restart());

    document.addEventListener('pointerlockchange', () => {
      const locked = document.pointerLockElement === this.canvas;
      this.player.enabled = locked;
      if (!locked && this.running) this._pause();
    });

    this.canvas.addEventListener('mousedown', (e) => {
      if (!this.player.enabled) return;
      if (e.button === 0) this._fire();
      if (e.button === 2) this._setScoped(true);
    });
    window.addEventListener('mouseup', (e) => {
      if (e.button === 2) this._setScoped(false);
    });
    window.addEventListener('contextmenu', (e) => e.preventDefault());
    window.addEventListener('keydown', (e) => {
      if (e.code === 'Escape' && this.running) this._pause();
      if (e.code === 'KeyR') this._reload();
    });
  }

  start() {
    this.audio.init(); this.audio.resume();
    document.getElementById('menu').classList.add('hidden');
    this.canvas.requestPointerLock();
    this.hud.setTimer(this.timeLeft);
    this.hud.setTargets(this.enemies.alive.length);
    this.hud.setAmmo(this.mag, this.reserve);
    if (!this.running) { this.running = true; this.clock.start(); this._loop(); }
  }
  _pause() {
    document.getElementById('menu').classList.remove('hidden');
    document.querySelector('#menu h1').innerHTML = 'PAUSED';
    document.getElementById('startBtn').textContent = 'RESUME';
  }

  _setScoped(s) {
    if (this.reloading) s = false;
    this.scoped = s;
    this.player.setScoped(s);
    this.rifle.setScoped(s);
    this.R.setScoped(s, this._focusDist());
    document.getElementById('scope').classList.toggle('hidden', !s);
    document.getElementById('crosshair').style.opacity = s ? '0' : '1';
    document.getElementById('breath').classList.toggle('hidden', !s);
  }

  _focusDist() {
    this.raycaster.set(this.camera.position, this.player.forward());
    const hit = this.enemies.raycast(this.raycaster);
    if (hit) return hit.distance;
    // fallback: intersect terrain roughly
    const p = this.camera.position, d = this.player.forward();
    for (let t = 5; t < 200; t += 5) {
      const x = p.x + d.x * t, y = p.y + d.y * t, z = p.z + d.z * t;
      if (y <= this.level.heightAt(x, z)) return t;
    }
    return 80;
  }

  _fire() {
    if (this.reloading || this.fireCooldown > 0 || this.gameOver) return;
    if (this.mag <= 0) { this.audio.dryFire(); return; }
    this.mag--;
    this.shotsFired++;
    this.fireCooldown = 0.9; // bolt-action cadence
    this.rifle.fire();
    this.audio.gunshot();

    // recoil kick to view
    this.player.pitch += 0.028 * (this.scoped ? 0.5 : 1);
    this.player.yaw += (Math.random() - 0.5) * 0.01;

    // hitscan
    this.raycaster.set(this.camera.position, this.player.forward());
    const muzzleWorld = new THREE.Vector3(); this.rifle.muzzle.getWorldPosition(muzzleWorld);
    const hit = this.enemies.raycast(this.raycaster);
    const colliderHit = this._raycastWorld(this.raycaster, hit ? hit.distance : Infinity);

    if (hit && (!colliderHit || hit.distance < colliderHit.distance)) {
      this.shotsHit++;
      this.fx.tracer(muzzleWorld, hit.point);
      this.fx.impact(hit.point, this.player.forward().clone().negate(), 'blood');
      const res = hit.enemy.damage(hit.part);
      this.audio.hit(hit.part === 'head');
      this.hud.hitmarker(res.killed);
      if (res.killed) {
        this.combo++;
        this.comboTimer = 3.5;
        const head = hit.part === 'head';
        const base = head ? 150 : 100;
        const gained = base * this.combo;
        this.score += gained;
        if (head) this._criticalShot(hit.point, gained);
        else this.hud.killfeed(this.combo > 1 ? `ELIMINATED  x${this.combo}` : 'ELIMINATED');
        if (this.combo >= 2) this.hud.combo(this.combo);
        this.audio.impact();
        this.hud.setTargets(this.enemies.alive.length);
        if (this.enemies.alive.length === 0) this._endMission(true);
      }
      this.hud.setScore(this.score);
    } else if (colliderHit) {
      this.fx.tracer(muzzleWorld, colliderHit.point);
      this.fx.impact(colliderHit.point, colliderHit.normal, 'dust');
      this.audio.impact();
    } else {
      const far = this.camera.position.clone().addScaledVector(this.player.forward(), 300);
      this.fx.tracer(muzzleWorld, far);
    }
    this.hud.setAmmo(this.mag, this.reserve);
    if (this.mag === 0) setTimeout(() => this._reload(), 300);
  }

  _raycastWorld(ray, maxDist) {
    // intersect the static level meshes (terrain + props)
    const hits = ray.intersectObjects(this.scene.children, true).filter(h =>
      h.distance < maxDist && h.object !== this.rifle.gun &&
      !this.rifle.group.getObjectById(h.object.id) && h.object.type !== 'Points' &&
      h.object.type !== 'Sprite');
    if (!hits.length) return null;
    const h = hits[0];
    const n = h.face ? h.face.normal.clone().transformDirection(h.object.matrixWorld) : new THREE.Vector3(0, 1, 0);
    return { point: h.point, normal: n, distance: h.distance };
  }

  _reload() {
    if (this.reloading || this.mag === this.magSize || this.reserve <= 0) return;
    this.reloading = true;
    if (this.scoped) this._setScoped(false);
    this.audio.bolt();
    this.hud.setReloading(true);
    setTimeout(() => {
      const need = this.magSize - this.mag;
      const take = Math.min(need, this.reserve);
      this.mag += take; this.reserve -= take;
      this.reloading = false;
      this.hud.setReloading(false);
      this.hud.setAmmo(this.mag, this.reserve);
    }, 1600);
  }

  _criticalShot(point, bonus) {
    // Silent Scope signature: X-ray flash + slow-mo + banner + camera punch
    this.timeScale = 0.18;
    this.critRecover = 1.15;
    this.critZoom = 1;
    this.hud.criticalShot(bonus);
    this.hud.killfeed('CRITICAL SHOT');
    this.audio.hit(true);
  }

  _endMission(win) {
    if (this.gameOver) return;
    this.gameOver = true;
    const acc = this.shotsFired ? Math.round((this.shotsHit / this.shotsFired) * 100) : 0;
    const timeUsed = Math.round(this.timeLimit - this.timeLeft);
    // rank from score + accuracy + time remaining
    let rank = 'D';
    const grade = this.score + acc * 20 + Math.max(0, this.timeLeft) * 30 + (win ? 3000 : 0);
    if (grade > 12000) rank = 'S'; else if (grade > 9000) rank = 'A';
    else if (grade > 6000) rank = 'B'; else if (grade > 3500) rank = 'C';
    if (document.pointerLockElement) document.exitPointerLock();
    this._setScoped(false);
    setTimeout(() => this.hud.showResult({
      title: win ? 'MISSION COMPLETE' : 'MISSION FAILED',
      score: this.score, acc, time: timeUsed, rank: win ? rank : 'D',
    }), 900);
  }

  _restart() {
    window.location.reload();
  }

  _loop() {
    if (!this.running) return;
    requestAnimationFrame(() => this._loop());
    const rawDt = Math.min(this.clock.getDelta(), 0.05);

    // slow-mo recovery
    if (this.critRecover > 0) {
      this.critRecover -= rawDt;
      this.timeScale = THREE.MathUtils.damp(this.timeScale, 1, 3, rawDt);
      this.critZoom = THREE.MathUtils.damp(this.critZoom, 0, 4, rawDt);
      if (this.critRecover <= 0) { this.timeScale = 1; this.critZoom = 0; }
    }
    const dt = rawDt * this.timeScale;
    this.fireCooldown = Math.max(0, this.fireCooldown - rawDt);

    // mission timer + combo decay (real time)
    if (!this.gameOver && this.player.enabled) {
      this.timeLeft = Math.max(0, this.timeLeft - rawDt);
      this.hud.setTimer(this.timeLeft);
      if (this.timeLeft <= 0) this._endMission(false);
      if (this.comboTimer > 0) { this.comboTimer -= rawDt; if (this.comboTimer <= 0) this.combo = 0; }
    }

    if (this.player.enabled && !this.gameOver) this.player.update(dt);

    // smooth FOV for scope zoom (+ critical-shot punch-in), driven by real time
    const targetFov = (this.scoped ? this.scopeFov : this.baseFov) * (1 - this.critZoom * 0.16);
    this.camera.fov = THREE.MathUtils.damp(this.camera.fov, targetFov, 14, rawDt);
    this.camera.updateProjectionMatrix();

    this.rifle.update(dt, {
      moving: this.player.moving, breath: this.player.breathing,
      lookDelta: this.player.lookDelta,
    });
    this.enemies.update(dt, this.camera.position);
    this.fx.update(dt, this.camera.position);
    this.R.setFocus(this._focusDist());
    this.R.update(dt);

    // HUD live values
    this.hud.setHealth(1);
    this.hud.setStamina(this.player.stamina);
    if (this.scoped) this.hud.setScopeZoom(this.baseFov / this.camera.fov);

    this.R.render();
  }
}

window.addEventListener('DOMContentLoaded', () => { window.__game = new Game(); });
