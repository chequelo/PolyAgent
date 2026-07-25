import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/examples/jsm/geometries/RoundedBoxGeometry.js';
import { Textures } from './textures.js';

// ---- shared FBM (matches textures.js so terrain + placement agree) ----
function hash2(x, y, s) {
  let h = x * 374761393 + y * 668265263 + s * 2246822519;
  h = (h ^ (h >>> 13)) >>> 0; h = Math.imul(h, 1274126177) >>> 0;
  return (h & 0xffffff) / 0x1000000;
}
function sm(t) { return t * t * (3 - 2 * t); }
function vnoise(x, y, s) {
  const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
  const tl = hash2(xi, yi, s), tr = hash2(xi + 1, yi, s), bl = hash2(xi, yi + 1, s), br = hash2(xi + 1, yi + 1, s);
  const u = sm(xf), v = sm(yf);
  return THREE.MathUtils.lerp(THREE.MathUtils.lerp(tl, tr, u), THREE.MathUtils.lerp(bl, br, u), v);
}
function fbm2(x, y, s, o = 5) {
  let a = 0.5, f = 1, sum = 0, n = 0;
  for (let i = 0; i < o; i++) { sum += a * vnoise(x * f, y * f, s + i * 13); n += a; a *= 0.5; f *= 2; }
  return sum / n;
}

/* Height function for the terrain (metres). Gentle dunes + a raised sniper ridge. */
function terrainHeight(x, z) {
  const dune = (fbm2(x * 0.012 + 10, z * 0.012 + 10, 7, 5) - 0.5) * 10.0;
  const roll = Math.sin(x * 0.03) * Math.cos(z * 0.025) * 1.4;
  // sniper ridge along the +Z (south) edge where the player deploys
  const ridge = THREE.MathUtils.smoothstep(z, 44, 66) * 7.0;
  // flatten the combat compound area around origin
  const flat = 1 - THREE.MathUtils.smoothstep(Math.hypot(x, z + 6), 6, 40);
  let h = dune + roll + ridge;
  h = THREE.MathUtils.lerp(h, dune * 0.15, flat * 0.85);
  return h;
}

export class Level {
  constructor(scene) {
    this.scene = scene;
    this.colliders = [];   // { min:Vector3, max:Vector3 }
    this.meshes = [];
    this.height = terrainHeight;
  }

  heightAt(x, z) { return terrainHeight(x, z); }

  addCollider(cx, cy, cz, hx, hy, hz) {
    this.colliders.push({
      min: new THREE.Vector3(cx - hx, cy - hy, cz - hz),
      max: new THREE.Vector3(cx + hx, cy + hy, cz + hz),
    });
  }

  build() {
    this._terrain();
    this._buildings();
    this._sandbagRing(2, -10, 6);
    this._sandbagRing(-14, -18, 5);
    this._watchtower(16, -24);
    this._crates(-8, -4);
    this._crates(10, -14);
    this._barrels(6, -2);
    this._wreck(-4, -22);
    this._rocks();
    this._grass();
    return {
      colliders: this.colliders,
      playerStart: new THREE.Vector3(0, terrainHeight(0, 54) + 1.7, 54),
      enemySpawns: this._enemySpawns(),
    };
  }

  _terrain() {
    const S = 400, seg = 320;
    const geo = new THREE.PlaneGeometry(S, S, seg, seg);
    geo.rotateX(-Math.PI / 2);
    const pos = geo.attributes.position;
    const colors = [];
    const lo = new THREE.Color(0x93794f), hi = new THREE.Color(0xc4ac81), dark = new THREE.Color(0x6f5c3e);
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i), z = pos.getZ(i);
      const h = terrainHeight(x, z);
      pos.setY(i, h);
      // low-contrast macro tint (softer so triangle interpolation doesn't show as seams)
      const t = THREE.MathUtils.clamp((h + 4) / 14, 0, 1);
      const patch = fbm2(x * 0.03, z * 0.03, 3, 4);
      const c = lo.clone().lerp(hi, t * 0.7 + 0.15).lerp(dark, patch * 0.22);
      colors.push(c.r, c.g, c.b);
    }
    geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    geo.computeVertexNormals();

    const t = Textures.sand();
    const mat = new THREE.MeshStandardMaterial({
      map: t.map, normalMap: t.normalMap, roughnessMap: t.roughnessMap,
      roughness: 1, metalness: 0, vertexColors: true,
      normalScale: new THREE.Vector2(1.8, 1.8),
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.receiveShadow = true;
    mesh.castShadow = false;
    this.scene.add(mesh);
    this.terrainMesh = mesh;
  }

  _concreteMat() {
    const t = Textures.concrete();
    return new THREE.MeshStandardMaterial({
      map: t.map, normalMap: t.normalMap, roughnessMap: t.roughnessMap,
      roughness: 1, metalness: 0, color: 0xb2aca1,
      normalScale: new THREE.Vector2(0.55, 0.55),
    });
  }

  // A ruined concrete building: walls with window/door gaps, partially collapsed.
  _wall(x, y, z, w, h, d, mat, rotY = 0) {
    const r = Math.min(0.08, Math.min(w, h, d) * 0.4);
    const m = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 3, r), mat);
    m.position.set(x, y, z); m.rotation.y = rotY;
    m.castShadow = true; m.receiveShadow = true;
    this.scene.add(m);
    // collider (axis-aligned approx)
    const hw = Math.abs(Math.cos(rotY)) * w / 2 + Math.abs(Math.sin(rotY)) * d / 2;
    const hd = Math.abs(Math.sin(rotY)) * w / 2 + Math.abs(Math.cos(rotY)) * d / 2;
    this.addCollider(x, y, z, hw, h / 2, hd);
    return m;
  }

  _buildings() {
    const mat = this._concreteMat();
    const g = (x, z) => terrainHeight(x, z);
    // Building A — two-storey shell, left
    const ax = -20, az = -20, ah = g(ax, az);
    this._wall(ax - 5, ah + 3, az, 0.6, 6, 12, mat);           // left wall
    this._wall(ax + 5, ah + 4, az - 3, 0.6, 8, 6, mat);        // right wall (taller)
    this._wall(ax, ah + 3, az - 6, 10, 6, 0.6, mat);           // back wall
    this._wall(ax - 2.5, ah + 5.5, az, 5, 1, 12, mat);         // partial roof slab
    // window lintels on front (broken)
    this._wall(ax - 5, ah + 6.2, az + 2, 0.6, 0.5, 3, mat);
    // rubble pile
    this._rubble(ax + 3, g(ax + 3, az + 4), az + 4, 8);

    // Building B — long barracks, right/back
    const bx = 22, bz = -30, bh = g(bx, bz);
    this._wall(bx, bh + 3.5, bz - 6, 20, 7, 0.7, mat);         // back
    this._wall(bx - 10, bh + 3.5, bz, 0.7, 7, 12, mat);        // side
    this._wall(bx + 10, bh + 2.5, bz + 2, 0.7, 5, 8, mat);     // half-collapsed side
    this._wall(bx, bh + 7, bz, 20, 0.6, 12, mat);             // roof (partial)
    this._rubble(bx - 6, g(bx - 6, bz + 7), bz + 7, 10);

    // Central low ruin / cover
    const cx = 0, cz = -16, ch = g(cx, cz);
    this._wall(cx - 3, ch + 1.5, cz, 0.6, 3, 5, mat);
    this._wall(cx + 3, ch + 1.2, cz + 1, 0.6, 2.4, 4, mat, 0.3);
    this._wall(cx, ch + 1.5, cz - 2.5, 6, 3, 0.6, mat);
  }

  _rubble(x, y, z, count) {
    const t = Textures.concrete();
    const mat = new THREE.MeshStandardMaterial({ map: t.map, normalMap: t.normalMap, roughness: 1, color: 0xa39b8e });
    const geo = new THREE.DodecahedronGeometry(0.5, 0);
    const im = new THREE.InstancedMesh(geo, mat, count);
    im.castShadow = true; im.receiveShadow = true;
    const d = new THREE.Object3D();
    for (let i = 0; i < count; i++) {
      const a = (i / count) * Math.PI * 2 + i;
      const r = 0.6 + (i % 4) * 0.7;
      const px = x + Math.cos(a) * r, pz = z + Math.sin(a) * r;
      const s = 0.4 + hash2(i, 3, 5) * 0.9;
      d.position.set(px, terrainHeight(px, pz) + s * 0.35, pz);
      d.rotation.set(hash2(i, 1, 1) * 6, hash2(i, 2, 2) * 6, hash2(i, 3, 3) * 6);
      d.scale.setScalar(s);
      d.updateMatrix(); im.setMatrixAt(i, d.matrix);
    }
    this.scene.add(im);
  }

  _sandbagRing(cx, cz, count) {
    const t = Textures.sandbag();
    const mat = new THREE.MeshStandardMaterial({ map: t.map, normalMap: t.normalMap, roughnessMap: t.roughnessMap, roughness: 1, color: 0x9c8c62 });
    const geo = new THREE.CapsuleGeometry(0.28, 0.5, 4, 8);
    const total = count * 3 * 2; // rings x rows
    const im = new THREE.InstancedMesh(geo, mat, total);
    im.castShadow = true; im.receiveShadow = true;
    const d = new THREE.Object3D(); let n = 0;
    const baseH = terrainHeight(cx, cz);
    for (let row = 0; row < 2; row++) {
      for (let i = 0; i < count * 3; i++) {
        const a = (i / (count * 3)) * Math.PI * 1.15 - 0.6;
        const r = 2.6;
        const px = cx + Math.cos(a) * r, pz = cz + Math.sin(a) * r;
        d.position.set(px, baseH + 0.28 + row * 0.5, pz);
        d.rotation.set(Math.PI / 2, a + Math.PI / 2, (hash2(i, row, 9) - 0.5) * 0.3);
        d.scale.setScalar(0.95 + hash2(i, row, 2) * 0.15);
        d.updateMatrix(); im.setMatrixAt(n++, d.matrix);
      }
    }
    im.count = n;
    this.scene.add(im);
    this.addCollider(cx, baseH + 0.6, cz, 3, 0.8, 3);
  }

  _watchtower(x, z) {
    const wood = Textures.wood();
    const mat = new THREE.MeshStandardMaterial({ map: wood.map, normalMap: wood.normalMap, roughnessMap: wood.roughnessMap, roughness: 1, color: 0x8a6a44 });
    const base = terrainHeight(x, z);
    const legH = 6;
    for (const [dx, dz] of [[-1.4, -1.4], [1.4, -1.4], [-1.4, 1.4], [1.4, 1.4]]) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.35, legH, 0.35), mat);
      leg.position.set(x + dx, base + legH / 2, z + dz);
      leg.castShadow = true; this.scene.add(leg);
    }
    const platform = new THREE.Mesh(new THREE.BoxGeometry(3.6, 0.3, 3.6), mat);
    platform.position.set(x, base + legH, z); platform.castShadow = true; platform.receiveShadow = true;
    this.scene.add(platform);
    // rail
    for (const [dx, dz, w, dd] of [[0, -1.7, 3.6, 0.2], [0, 1.7, 3.6, 0.2], [-1.7, 0, 0.2, 3.6], [1.7, 0, 0.2, 3.6]]) {
      const rail = new THREE.Mesh(new THREE.BoxGeometry(w, 0.9, dd), mat);
      rail.position.set(x + dx, base + legH + 0.6, z + dz); rail.castShadow = true; this.scene.add(rail);
    }
    // roof — weathered corrugated metal, not wood
    const roofMat = new THREE.MeshStandardMaterial({ color: 0x4b4a42, roughness: 0.65, metalness: 0.35 });
    const roof = new THREE.Mesh(new THREE.ConeGeometry(3.0, 1.6, 4), roofMat);
    roof.position.set(x, base + legH + 2.4, z); roof.rotation.y = Math.PI / 4; roof.castShadow = true;
    this.scene.add(roof);
    this.addCollider(x, base + 3, z, 1.8, 3, 1.8);
  }

  _crates(x, z) {
    const wood = Textures.wood();
    const mat = new THREE.MeshStandardMaterial({ map: wood.map, normalMap: wood.normalMap, roughnessMap: wood.roughnessMap, roughness: 0.9, color: 0x8a7346 });
    const stack = [[0, 0, 0], [1.05, 0, 0.2], [0.5, 1.02, 0.1], [-0.1, 0, 1.0]];
    for (const [dx, dy, dz] of stack) {
      const s = 0.95;
      const m = new THREE.Mesh(new RoundedBoxGeometry(s, s, s, 2, 0.04), mat);
      const px = x + dx, pz = z + dz;
      m.position.set(px, terrainHeight(px, pz) + s / 2 + dy, pz);
      m.rotation.y = (dx + dz) * 0.4;
      m.castShadow = true; m.receiveShadow = true; this.scene.add(m);
    }
    this.addCollider(x + 0.4, terrainHeight(x, z) + 1, z + 0.3, 1.4, 1.2, 1.4);
  }

  _barrels(x, z) {
    const t = Textures.metal();
    const mat = new THREE.MeshStandardMaterial({ map: t.map, normalMap: t.normalMap, roughnessMap: t.roughnessMap, roughness: 0.6, metalness: 0.75, color: 0x6b7a55 });
    for (let i = 0; i < 3; i++) {
      const px = x + (i - 1) * 0.75 + (i === 1 ? 0.1 : 0), pz = z + (i % 2) * 0.7;
      const b = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.35, 1.1, 20), mat);
      b.position.set(px, terrainHeight(px, pz) + 0.55, pz);
      b.castShadow = true; b.receiveShadow = true; this.scene.add(b);
    }
    this.addCollider(x, terrainHeight(x, z) + 0.55, z, 1.6, 0.6, 1.0);
  }

  _wreck(x, z) {
    const t = Textures.metal();
    const mat = new THREE.MeshStandardMaterial({ map: t.map, normalMap: t.normalMap, roughness: 0.7, metalness: 0.6, color: 0x55503f });
    const base = terrainHeight(x, z);
    const body = new THREE.Mesh(new THREE.BoxGeometry(4.4, 1.3, 2.0), mat);
    body.position.set(x, base + 0.9, z); body.rotation.z = 0.06; body.castShadow = true; body.receiveShadow = true; this.scene.add(body);
    const cab = new THREE.Mesh(new THREE.BoxGeometry(1.8, 1.1, 1.9), mat);
    cab.position.set(x - 1.1, base + 1.9, z); cab.castShadow = true; this.scene.add(cab);
    // wheels (blown)
    for (const [dx, dz] of [[1.4, 0.9], [1.4, -0.9], [-1.3, 0.9]]) {
      const w = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.55, 0.4, 16), new THREE.MeshStandardMaterial({ color: 0x1a1a1a, roughness: 0.9 }));
      w.rotation.x = Math.PI / 2; w.position.set(x + dx, base + 0.45, z + dz); w.castShadow = true; this.scene.add(w);
    }
    this.addCollider(x, base + 1, z, 2.4, 1.4, 1.2);
  }

  _rocks() {
    const t = Textures.rock();
    const mat = new THREE.MeshStandardMaterial({ map: t.map, normalMap: t.normalMap, roughnessMap: t.roughnessMap, roughness: 1, color: 0x8f8474 });
    const geo = new THREE.DodecahedronGeometry(1, 1);
    // deform for organic look
    const p = geo.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const f = 0.75 + fbm2(p.getX(i) * 2, p.getZ(i) * 2, 12, 3) * 0.5;
      p.setXYZ(i, p.getX(i) * f, p.getY(i) * f * 0.8, p.getZ(i) * f);
    }
    geo.computeVertexNormals();
    const N = 40;
    const im = new THREE.InstancedMesh(geo, mat, N);
    im.castShadow = true; im.receiveShadow = true;
    const d = new THREE.Object3D();
    for (let i = 0; i < N; i++) {
      const a = hash2(i, 5, 1) * Math.PI * 2, r = 24 + hash2(i, 6, 2) * 150;
      const px = Math.cos(a) * r, pz = Math.sin(a) * r - 6;
      const s = 0.5 + hash2(i, 7, 3) * 2.4;
      d.position.set(px, terrainHeight(px, pz) + s * 0.3, pz);
      d.rotation.set(hash2(i, 1, 8) * 6, hash2(i, 2, 8) * 6, hash2(i, 3, 8) * 6);
      d.scale.set(s, s * (0.6 + hash2(i, 9, 1) * 0.5), s);
      d.updateMatrix(); im.setMatrixAt(i, d.matrix);
    }
    this.scene.add(im);
  }

  _grass() {
    // dry desert tufts — instanced crossed billboards, each quad holds a full
    // soft blade cluster (not sparse strokes) so it reads as a tuft, not an X.
    const blade = new THREE.PlaneGeometry(0.42, 0.34, 1, 3);
    blade.translate(0, 0.17, 0);
    // taper + base darkening via vertex colors + slight bend
    const p = blade.attributes.position; const cols = [];
    const baseC = new THREE.Color(0x5c5330), tipC = new THREE.Color(0x9a8c56);
    for (let i = 0; i < p.count; i++) {
      const yy = p.getY(i);
      const t = THREE.MathUtils.clamp(yy / 0.34, 0, 1);
      p.setX(i, p.getX(i) * (1 - t * 0.35));      // taper toward tip
      p.setZ(i, p.getZ(i) + Math.pow(t, 2) * 0.06); // gentle bend
      const c = baseC.clone().lerp(tipC, t);
      cols.push(c.r, c.g, c.b);
    }
    blade.setAttribute('color', new THREE.Float32BufferAttribute(cols, 3));
    blade.computeVertexNormals();

    const tex = this._grassTexture();
    const mat = new THREE.MeshStandardMaterial({
      map: tex, alphaTest: 0.5, side: THREE.DoubleSide, roughness: 1, metalness: 0,
      vertexColors: true, color: 0xffffff, envMapIntensity: 0.3,
    });
    const N = 3200;
    const im = new THREE.InstancedMesh(blade, mat, N * 2);
    im.receiveShadow = true; im.castShadow = false;
    const d = new THREE.Object3D(); let n = 0;
    for (let i = 0; i < N; i++) {
      const a = hash2(i, 1, 20) * Math.PI * 2, r = 5 + hash2(i, 2, 21) * 125;
      const px = Math.cos(a) * r, pz = Math.sin(a) * r - 6;
      const h = terrainHeight(px, pz);
      if (h > 5.0) continue;                         // no grass on high ridges
      // clumpier distribution — skip in dead zones
      if (fbm2(px * 0.08, pz * 0.08, 33, 3) < 0.42) continue;
      const s = 0.75 + hash2(i, 3, 22) * 1.1;
      const yaw = hash2(i, 4, 23) * Math.PI;
      for (let k = 0; k < 2; k++) {
        d.position.set(px + (hash2(i, 6, 25) - 0.5) * 0.6, h, pz + (hash2(i, 7, 26) - 0.5) * 0.6);
        d.rotation.set((hash2(i, 8, 27) - 0.5) * 0.15, k === 0 ? yaw : yaw + Math.PI / 2, 0);
        d.scale.set(s, s * (0.8 + hash2(i, 5, 24) * 0.7), s);
        d.updateMatrix(); im.setMatrixAt(n++, d.matrix);
      }
    }
    im.count = n;
    this.scene.add(im);
    this.grass = im;
  }

  _grassTexture() {
    const S = 128;
    const c = document.createElement('canvas'); c.width = c.height = S;
    const g = c.getContext('2d');
    g.clearRect(0, 0, S, S);
    // many soft thin blades filling the quad, muted dry tones
    const blades = 26;
    for (let i = 0; i < blades; i++) {
      const x = (i / blades) * S + (Math.sin(i * 2.3) * 3);
      const sway = (i % 2 ? 1 : -1) * (6 + (i % 5) * 4);
      const topY = 6 + (i % 4) * 8;
      const shade = 70 + (i % 6) * 12;
      g.strokeStyle = `rgba(${shade + 30},${shade + 18},${shade - 10},${0.55 + (i % 3) * 0.15})`;
      g.lineWidth = 1.4 + (i % 3) * 0.5;
      g.beginPath(); g.moveTo(x, S);
      g.quadraticCurveTo(x + sway * 0.5, S * 0.5, x + sway, topY);
      g.stroke();
    }
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = 4;
    return t;
  }

  _enemySpawns() {
    // positions on the compound floor, facing roughly toward the player ridge (+Z)
    const raw = [
      [-6, -10], [4, -12], [-14, -16], [12, -18], [0, -22],
      [20, -28], [-20, -24], [8, -26], [-2, -30], [16, -34],
    ];
    return raw.map(([x, z]) => new THREE.Vector3(x, terrainHeight(x, z), z));
  }
}
