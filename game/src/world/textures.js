import * as THREE from 'three';

/*
 * Procedural PBR texture factory.
 * Generates albedo + normal + roughness (+ optional AO) maps on canvases using
 * fractal value noise. No external assets => fully offline / CSP-safe.
 * Everything is cached by key so materials can share GPU textures.
 */

const _cache = new Map();

// ---- deterministic hash noise ---------------------------------------------
function hash2(x, y, seed) {
  let h = x * 374761393 + y * 668265263 + seed * 2246822519;
  h = (h ^ (h >>> 13)) >>> 0;
  h = Math.imul(h, 1274126177) >>> 0;
  return (h & 0xffffff) / 0x1000000;
}
function smooth(t) { return t * t * (3 - 2 * t); }
function valueNoise(x, y, seed) {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = x - xi, yf = y - yi;
  const tl = hash2(xi, yi, seed), tr = hash2(xi + 1, yi, seed);
  const bl = hash2(xi, yi + 1, seed), br = hash2(xi + 1, yi + 1, seed);
  const u = smooth(xf), v = smooth(yf);
  return THREE.MathUtils.lerp(THREE.MathUtils.lerp(tl, tr, u), THREE.MathUtils.lerp(bl, br, u), v);
}
function fbm(x, y, seed, octaves = 5, lac = 2.0, gain = 0.5) {
  let amp = 0.5, freq = 1, sum = 0, norm = 0;
  for (let o = 0; o < octaves; o++) {
    sum += amp * valueNoise(x * freq, y * freq, seed + o * 17);
    norm += amp; amp *= gain; freq *= lac;
  }
  return sum / norm;
}

function makeCanvas(size) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  return c;
}

// Build a normal map from a height function by finite differences.
function heightToNormal(heightData, size, strength) {
  const out = new Uint8ClampedArray(size * size * 4);
  const at = (x, y) => heightData[((y + size) % size) * size + ((x + size) % size)];
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const hl = at(x - 1, y), hr = at(x + 1, y);
      const hd = at(x, y - 1), hu = at(x, y + 1);
      let nx = (hl - hr) * strength;
      let ny = (hd - hu) * strength;
      const nz = 1.0;
      const len = Math.hypot(nx, ny, nz);
      nx /= len; ny /= len;
      const i = (y * size + x) * 4;
      out[i] = (nx * 0.5 + 0.5) * 255;
      out[i + 1] = (ny * 0.5 + 0.5) * 255;
      out[i + 2] = (nz / len * 0.5 + 0.5) * 255;
      out[i + 3] = 255;
    }
  }
  return out;
}

function texFromCanvas(canvas, { srgb = false, repeat = 1, aniso = 8 } = {}) {
  const t = new THREE.CanvasTexture(canvas);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(repeat, repeat);
  t.anisotropy = aniso;
  t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  t.needsUpdate = true;
  return t;
}

function lerpColor(a, b, t) {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/*
 * Generic surface generator.
 * spec: { size, seed, scale, base:[r,g,b], base2:[r,g,b], octaves,
 *         rough:[min,max], normalStrength, grain, cracks }
 * returns { map, normalMap, roughnessMap, height }
 */
function generateSurface(spec) {
  const size = spec.size || 512;
  const seed = spec.seed || 1;
  const scale = spec.scale || 6;
  const oct = spec.octaves || 5;
  const albedo = makeCanvas(size), aimg = albedo.getContext('2d').createImageData(size, size);
  const rough = makeCanvas(size), rimg = rough.getContext('2d').createImageData(size, size);
  const height = new Float32Array(size * size);
  const grain = spec.grain ?? 0.06;

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const u = x / size * scale, v = y / size * scale;
      let n = fbm(u, v, seed, oct);
      // fine grain
      const g = (hash2(x, y, seed + 99) - 0.5) * grain;
      let h = n + g;

      // cracks / veins via ridged noise
      if (spec.cracks) {
        const ridge = 1 - Math.abs(fbm(u * 1.7, v * 1.7, seed + 200, 4) * 2 - 1);
        const crack = Math.pow(ridge, spec.crackSharp || 8) * (spec.cracks);
        h -= crack;
      }
      // vertical weathering streaks (rain/dirt running down)
      let streakDark = 0;
      if (spec.streak) {
        const sN = fbm(u * spec.streakFreq || u * 3, v * 0.25, seed + 300, 4);
        const runs = Math.pow(THREE.MathUtils.clamp(sN, 0, 1), 3);
        streakDark = runs * spec.streak * (0.4 + 0.6 * (v / scale)); // stronger lower down
        h -= streakDark * 0.5;
      }
      h = THREE.MathUtils.clamp(h, 0, 1);
      height[y * size + x] = h;

      const col = lerpColor(spec.base, spec.base2, THREE.MathUtils.clamp(n + g, 0, 1));
      if (streakDark > 0) { col[0] *= (1 - streakDark * 0.55); col[1] *= (1 - streakDark * 0.5); col[2] *= (1 - streakDark * 0.45); }
      // patchy dirt splotches
      if (spec.splotch) {
        const s = fbm(u * 0.5 + 40, v * 0.5 + 40, seed + 7, 3);
        const f = smooth(THREE.MathUtils.clamp((s - 0.45) * 3, 0, 1)) * spec.splotch;
        col[0] = col[0] * (1 - f) + (spec.splotchColor[0]) * f;
        col[1] = col[1] * (1 - f) + (spec.splotchColor[1]) * f;
        col[2] = col[2] * (1 - f) + (spec.splotchColor[2]) * f;
      }
      const ai = (y * size + x) * 4;
      aimg.data[ai] = col[0]; aimg.data[ai + 1] = col[1]; aimg.data[ai + 2] = col[2]; aimg.data[ai + 3] = 255;

      const rr = THREE.MathUtils.lerp(spec.rough[0], spec.rough[1], n) * 255;
      rimg.data[ai] = rimg.data[ai + 1] = rimg.data[ai + 2] = rr; rimg.data[ai + 3] = 255;
    }
  }
  albedo.getContext('2d').putImageData(aimg, 0, 0);
  rough.getContext('2d').putImageData(rimg, 0, 0);

  const nData = heightToNormal(height, size, spec.normalStrength || 2.2);
  const nCanvas = makeCanvas(size);
  nCanvas.getContext('2d').putImageData(new ImageData(nData, size, size), 0, 0);

  return {
    map: texFromCanvas(albedo, { srgb: true, repeat: spec.repeat || 1 }),
    normalMap: texFromCanvas(nCanvas, { repeat: spec.repeat || 1 }),
    roughnessMap: texFromCanvas(rough, { repeat: spec.repeat || 1 }),
  };
}

export const Textures = {
  get(key, spec) {
    if (_cache.has(key)) return _cache.get(key);
    const t = generateSurface(spec);
    _cache.set(key, t);
    return t;
  },

  sand() {
    return this.get('sand', {
      size: 512, seed: 11, scale: 5, octaves: 6,
      base: [156, 132, 92], base2: [201, 178, 132],
      rough: [0.78, 0.95], normalStrength: 1.6, repeat: 26,
      splotch: 0.35, splotchColor: [120, 100, 70], grain: 0.09,
    });
  },
  concrete() {
    return this.get('concrete', {
      size: 1024, seed: 31, scale: 10, octaves: 6,
      base: [116, 112, 104], base2: [158, 154, 146],
      rough: [0.72, 0.92], normalStrength: 0.9, repeat: 2,
      cracks: 0.32, crackSharp: 26,           // thin hairline cracks, not cells
      streak: 0.55, streakFreq: 7,            // vertical rain/dirt weathering
      splotch: 0.42, splotchColor: [92, 86, 78], grain: 0.045,
    });
  },
  rock() {
    return this.get('rock', {
      size: 512, seed: 53, scale: 3.5, octaves: 6,
      base: [92, 84, 74], base2: [148, 138, 122],
      rough: [0.7, 0.92], normalStrength: 3.4, repeat: 2,
      cracks: 0.6, crackSharp: 6, grain: 0.08,
    });
  },
  metal() {
    return this.get('metal', {
      size: 512, seed: 71, scale: 8, octaves: 4,
      base: [70, 72, 78], base2: [120, 124, 132],
      rough: [0.35, 0.7], normalStrength: 1.2, repeat: 2,
      splotch: 0.4, splotchColor: [120, 70, 40], grain: 0.04, // rust splotches
    });
  },
  wood() {
    return this.get('wood', {
      size: 512, seed: 91, scale: 2, octaves: 4,
      base: [104, 84, 56], base2: [150, 126, 92],   // weathered, less red
      rough: [0.6, 0.82], normalStrength: 2.0, repeat: 2,
      grain: 0.03,
    });
  },
  sandbag() {
    return this.get('sandbag', {
      size: 256, seed: 41, scale: 10, octaves: 4,
      base: [120, 108, 78], base2: [156, 142, 104],
      rough: [0.85, 0.98], normalStrength: 2.6, repeat: 1,
      grain: 0.12,
    });
  },

  dispose() {
    for (const s of _cache.values()) {
      s.map?.dispose(); s.normalMap?.dispose(); s.roughnessMap?.dispose();
    }
    _cache.clear();
  },
};
