import * as THREE from 'three';
import { Renderer } from './engine/renderer.js';
import { buildSky } from './engine/sky.js';
import { Level } from './world/level.js';

const canvas = document.getElementById('scene');
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 2000);

const R = new Renderer(canvas);
R.width = innerWidth; R.height = innerHeight;
const env = buildSky(scene, R.renderer);

const level = new Level(scene);
const info = level.build();

// place camera at player deploy point looking into the compound
camera.position.copy(info.playerStart).add(new THREE.Vector3(0, 0.2, 0));
camera.lookAt(0, level.heightAt(0, -20) + 1, -20);
env.sun.target.position.set(0, 0, -20);
env.sun.target.updateMatrixWorld();

R.build(scene, camera);
R.setScoped(false);
R.setFocus(camera.position.distanceTo(new THREE.Vector3(0, level.heightAt(0, -20) + 1, -20)));
R.render();
window.__ready = true;
