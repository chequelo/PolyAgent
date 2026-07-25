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
  u['turbidity'].value = 4.2;
  u['rayleigh'].value = 1.7;
  u['mieCoefficient'].value = 0.004;           // smaller, tighter sun halo
  u['mieDirectionalG'].value = 0.8;

  const phi = THREE.MathUtils.degToRad(90 - elevation);
  const theta = THREE.MathUtils.degToRad(azimuth);
  const sunPos = new THREE.Vector3().setFromSphericalCoords(1, phi, theta);
  u['sunPosition'].value.copy(sunPos);
  scene.add(sky);

  // ---- Sun (key light) ----
  const sun = new THREE.DirectionalLight(0xfff0d8, 2.7);
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
  // NOTE: PMREM-ing the raw atmospheric Sky shader can emit non-finite (Inf/NaN)
  // radiance that blackens every PBR material. Instead we build IBL from a safe,
  // finite procedural gradient equirect tuned to the golden-hour palette.
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envTex = makeGradientEnv(sunPos);
  const envRT = pmrem.fromEquirectangular(envTex);
  scene.environment = envRT.texture;
  scene.environmentIntensity = 1.0;
  envTex.dispose();

  return {
    sky, sun, hemi, fill,
    sunDirection: sunPos.clone(),
    dispose() { pmrem.dispose(); envRT.dispose(); },
  };
}

// Procedural equirectangular sky gradient (zenith -> horizon -> ground) with a
// warm sun bloom, used only as a finite IBL source for reflections/ambient.
function makeGradientEnv(sunPos) {
  const w = 512, h = 256;
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const g = c.getContext('2d');
  const grad = g.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0.0, '#3f74b0');   // zenith
  grad.addColorStop(0.42, '#93b4d4');
  grad.addColorStop(0.5, '#e8dcc2');   // horizon haze
  grad.addColorStop(0.5, '#caa877');   // ground line
  grad.addColorStop(1.0, '#6b5636');   // ground
  g.fillStyle = grad; g.fillRect(0, 0, w, h);
  // warm sun glow placed by azimuth/elevation
  const az = Math.atan2(sunPos.x, sunPos.z);
  const sx = ((az / (Math.PI * 2)) + 0.5) * w;
  const sy = (1 - Math.max(0, sunPos.y)) * 0.5 * h;
  const sun = g.createRadialGradient(sx, sy, 0, sx, sy, w * 0.22);
  sun.addColorStop(0, 'rgba(255,240,210,0.95)');
  sun.addColorStop(0.35, 'rgba(255,220,170,0.45)');
  sun.addColorStop(1, 'rgba(255,220,170,0)');
  g.fillStyle = sun; g.fillRect(0, 0, w, h);
  const t = new THREE.CanvasTexture(c);
  t.mapping = THREE.EquirectangularReflectionMapping;
  t.colorSpace = THREE.SRGBColorSpace;
  t.needsUpdate = true;
  return t;
}
