import * as THREE from 'three';
import { Sky } from 'three/examples/jsm/objects/Sky.js';

/*
 * Golden-hour atmospheric environment:
 *  - Rayleigh/Mie scattering Sky dome
 *  - key directional "sun" light with high-res soft shadows
 *  - warm fill + cool sky ambient (hemisphere)
 *  - PMREM-generated environment map for physically based reflections
 *  - exponential height fog matched to the horizon color
 */
export function buildSky(scene, renderer, opts = {}) {
  const elevation = opts.elevation ?? 13.0;    // degrees above horizon (low = dramatic golden hour)
  const azimuth = opts.azimuth ?? 328;         // over the player's left shoulder, out of frame

  const sky = new Sky();
  sky.scale.setScalar(45000);
  const u = sky.material.uniforms;
  u['turbidity'].value = 5.0;
  u['rayleigh'].value = 2.2;
  u['mieCoefficient'].value = 0.004;           // smaller, tighter sun halo
  u['mieDirectionalG'].value = 0.8;

  const phi = THREE.MathUtils.degToRad(90 - elevation);
  const theta = THREE.MathUtils.degToRad(azimuth);
  const sunPos = new THREE.Vector3().setFromSphericalCoords(1, phi, theta);
  u['sunPosition'].value.copy(sunPos);
  scene.add(sky);

  // ---- Sun (key light) ----
  const sun = new THREE.DirectionalLight(0xfff0d8, 3.4);
  sun.color.setHSL(0.09, 0.62, 0.62); // warm gold
  sun.position.copy(sunPos).multiplyScalar(220);
  sun.castShadow = true;
  sun.shadow.mapSize.set(opts.shadowMap ?? 3072, opts.shadowMap ?? 3072);
  sun.shadow.camera.near = 1;
  sun.shadow.camera.far = 620;
  const s = 150;
  sun.shadow.camera.left = -s; sun.shadow.camera.right = s;
  sun.shadow.camera.top = s; sun.shadow.camera.bottom = -s;
  sun.shadow.bias = -0.00015;
  sun.shadow.normalBias = 0.6;
  sun.shadow.radius = 5.0;         // VSM blur
  sun.shadow.blurSamples = 8;
  scene.add(sun);
  scene.add(sun.target);

  // ---- Ambient / fill ----
  const hemi = new THREE.HemisphereLight(0xbcd3ff, 0x4a3a28, 0.55);
  scene.add(hemi);
  // subtle warm bounce from the ground toward camera
  const fill = new THREE.DirectionalLight(0xffd9a8, 0.35);
  fill.position.set(-sunPos.x, 0.4, -sunPos.z).multiplyScalar(100);
  scene.add(fill);

  // ---- Fog matched to warm horizon ----
  const fogColor = new THREE.Color(0xcbb894);
  scene.fog = new THREE.FogExp2(fogColor, 0.0024);
  scene.background = null; // sky dome provides the background

  // ---- PMREM environment for IBL reflections ----
  const pmrem = new THREE.PMREMGenerator(renderer);
  pmrem.compileEquirectangularShader();
  const envRT = pmrem.fromScene(sky, 0, 0.1, 1000);
  scene.environment = envRT.texture;
  scene.environmentIntensity = 0.85;

  return {
    sky, sun, hemi, fill,
    sunDirection: sunPos.clone(),
    dispose() { pmrem.dispose(); envRT.dispose(); },
  };
}
