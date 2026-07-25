import * as THREE from 'three';
import { Renderer } from './engine/renderer.js';
import { buildSky } from './engine/sky.js';
import { Level } from './world/level.js';
import { EnemyManager } from './enemy/enemy.js';
import { Rifle } from './weapon/rifle.js';

/*
 * Deterministic still-frame capture harness for the visual critic loop.
 * Reads the scene setup from URL query params, builds the world, renders one
 * (optionally accumulated) frame, then sets window.__ready.
 *
 *  ?cam=x,y,z & look=x,y,z & fov=55 & scope=0|1 & focus=auto|<m>
 *  &enemies=1 & weapon=1 & scene=<preset>
 */
const q = new URLSearchParams(location.search);
const num = (s, d) => (s == null ? d : parseFloat(s));
const vec = (s, d) => { if (!s) return d; const p = s.split(',').map(Number); return new THREE.Vector3(p[0], p[1], p[2]); };

const canvas = document.getElementById('scene');
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(num(q.get('fov'), 55), innerWidth / innerHeight, 0.05, 2000);

const R = new Renderer(canvas, { preserveDrawingBuffer: true });
R.renderer.setPixelRatio(1);
R.width = innerWidth; R.height = innerHeight;
const env = buildSky(scene, R.renderer);
const level = new Level(scene);
const noLevel = q.get('nolevel') === '1';
const info = noLevel
  ? { colliders: [], playerStart: new THREE.Vector3(0, 2, 6), enemySpawns: [] }
  : level.build();
if (noLevel) scene.background = new THREE.Color(0x2a3038);

let enemies = null;
if (q.get('enemies') !== '0') {
  enemies = new EnemyManager(scene, level);
  enemies.spawnAll(info.enemySpawns);
  const t = q.get('t');
  if (t) enemies.update(parseFloat(t), camera.position); // advance to a pose
}

let rifle = null;
const scoped = q.get('scope') === '1';

// ---- presets frame nice hero shots ----
const presets = {
  ridge: { cam: info.playerStart.clone().add(new THREE.Vector3(0, 0.1, 0)), look: new THREE.Vector3(0, level.heightAt(0, -18) + 1, -18) },
  compound: { cam: new THREE.Vector3(6, level.heightAt(6, 6) + 1.7, 6), look: new THREE.Vector3(0, level.heightAt(0, -16) + 1.4, -16) },
  buildingB: { cam: new THREE.Vector3(22, level.heightAt(22, -8) + 2, -8), look: new THREE.Vector3(22, level.heightAt(22, -30) + 3, -30) },
  tower: { cam: new THREE.Vector3(16, level.heightAt(16, -14) + 2, -14), look: new THREE.Vector3(16, level.heightAt(16, -24) + 5, -24) },
  enemy: { cam: new THREE.Vector3(-4, level.heightAt(-4, -2) + 1.7, -2), look: new THREE.Vector3(-6, level.heightAt(-6, -10) + 1.4, -10) },
};
const preset = presets[q.get('scene')] || presets.ridge;
const camPos = vec(q.get('cam'), preset.cam);
const lookAt = vec(q.get('look'), preset.look);

camera.position.copy(camPos);
camera.lookAt(lookAt);
env.sun.target.position.copy(lookAt); env.sun.target.updateMatrixWorld();

if (q.get('weapon') === '1') {
  rifle = new Rifle(camera);
  scene.add(camera); // camera must be in the graph for its child weapon to render
  rifle.setScoped(scoped);
  rifle.snap();
  rifle.update(0.016, {});
}

if (q.get('noshadow') === '1') R.renderer.shadowMap.enabled = false;
R.build(scene, camera);
R.setScoped(scoped, camPos.distanceTo(lookAt));
const focus = q.get('focus');
if (focus && focus !== 'auto') R.setFocus(parseFloat(focus));
else R.setFocus(camPos.distanceTo(lookAt));

// render a few frames so temporal passes (SMAA/GTAO) settle
if (q.get('raw') === '1') {
  R.renderer.render(scene, camera); // bypass composer/post
} else {
  for (let i = 0; i < 3; i++) R.render();
}
window.__scene = scene; window.__THREE = THREE; window.__cam = camera; window.__R = R;
window.__ready = true;
