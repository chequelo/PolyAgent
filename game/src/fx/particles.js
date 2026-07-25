import * as THREE from 'three';

/*
 * Pooled effects: bullet tracers, impact dust bursts, blood mist,
 * lingering bullet-hole decals, and ambient drifting dust motes.
 * All GPU-cheap (Points + a small mesh pool) and updated each frame.
 */

function softDot(color) {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, color.hi); grad.addColorStop(0.4, color.mid); grad.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grad; g.fillRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}

class Burst {
  constructor(scene, tex, max, size, blending = THREE.NormalBlending) {
    this.max = max;
    this.pos = new Float32Array(max * 3);
    this.vel = new Float32Array(max * 3);
    this.life = new Float32Array(max);
    this.ttl = new Float32Array(max);
    this.head = 0;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(this.pos, 3));
    this.aAlpha = new Float32Array(max);
    g.setAttribute('aAlpha', new THREE.BufferAttribute(this.aAlpha, 1));
    this.aSize = new Float32Array(max);
    g.setAttribute('aSize', new THREE.BufferAttribute(this.aSize, 1));
    const mat = new THREE.PointsMaterial({ size, map: tex, transparent: true, depthWrite: false, blending, sizeAttenuation: true });
    mat.onBeforeCompile = (sh) => {
      sh.vertexShader = sh.vertexShader
        .replace('uniform float size;', 'uniform float size;\nattribute float aAlpha;\nattribute float aSize;\nvarying float vA;')
        .replace('gl_PointSize = size * ( scale / - mvPosition.z );', 'vA = aAlpha;\ngl_PointSize = size * aSize * ( scale / - mvPosition.z );');
      sh.fragmentShader = sh.fragmentShader
        .replace('#include <common>', '#include <common>\nvarying float vA;')
        .replace('vec4 diffuseColor = vec4( diffuse, opacity );', 'vec4 diffuseColor = vec4( diffuse, opacity * vA );');
    };
    this.points = new THREE.Points(g, mat);
    this.points.frustumCulled = false;
    scene.add(this.points);
    this.geo = g;
  }
  emit(p, v, ttl, size) {
    const i = this.head; this.head = (this.head + 1) % this.max;
    this.pos[i * 3] = p.x; this.pos[i * 3 + 1] = p.y; this.pos[i * 3 + 2] = p.z;
    this.vel[i * 3] = v.x; this.vel[i * 3 + 1] = v.y; this.vel[i * 3 + 2] = v.z;
    this.life[i] = ttl; this.ttl[i] = ttl; this.aSize[i] = size;
  }
  update(dt, gravity) {
    for (let i = 0; i < this.max; i++) {
      if (this.life[i] <= 0) { this.aAlpha[i] = 0; continue; }
      this.life[i] -= dt;
      const k = i * 3;
      this.vel[k + 1] += gravity * dt;
      this.vel[k] *= 0.96; this.vel[k + 2] *= 0.96;
      this.pos[k] += this.vel[k] * dt;
      this.pos[k + 1] += this.vel[k + 1] * dt;
      this.pos[k + 2] += this.vel[k + 2] * dt;
      this.aAlpha[i] = Math.max(0, this.life[i] / this.ttl[i]);
    }
    this.geo.attributes.position.needsUpdate = true;
    this.geo.attributes.aAlpha.needsUpdate = true;
    this.geo.attributes.aSize.needsUpdate = true;
  }
}

export class FX {
  constructor(scene) {
    this.scene = scene;
    this.dust = new Burst(scene, softDot({ hi: 'rgba(200,180,150,0.9)', mid: 'rgba(170,150,120,0.5)' }), 400, 0.9);
    this.blood = new Burst(scene, softDot({ hi: 'rgba(150,20,15,0.95)', mid: 'rgba(90,10,10,0.6)' }), 300, 0.5);
    this.spark = new Burst(scene, softDot({ hi: 'rgba(255,220,150,1)', mid: 'rgba(255,150,60,0.7)' }), 200, 0.25, THREE.AdditiveBlending);

    // tracer pool (stretched glowing segments)
    this.tracers = [];
    const tmat = new THREE.MeshBasicMaterial({ color: 0xffddaa, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false });
    for (let i = 0; i < 8; i++) {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 1, 6), tmat.clone());
      m.visible = false; scene.add(m); this.tracers.push({ mesh: m, t: -1 });
    }

    // decals (bullet holes)
    this.decals = [];
    this.decalGeo = new THREE.CircleGeometry(0.08, 10);
    this.decalMat = new THREE.MeshStandardMaterial({ color: 0x111008, roughness: 1, transparent: true, opacity: 0.9, polygonOffset: true, polygonOffsetFactor: -4 });

    this._ambientDust(scene);
  }

  _ambientDust(scene) {
    const N = 240;
    const pos = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      pos[i * 3] = (Math.random() - 0.5) * 120;
      pos[i * 3 + 1] = Math.random() * 18;
      pos[i * 3 + 2] = (Math.random() - 0.5) * 120 - 10;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const m = new THREE.PointsMaterial({ size: 0.06, color: 0xd9c9a8, transparent: true, opacity: 0.35, depthWrite: false, blending: THREE.AdditiveBlending });
    this.motes = new THREE.Points(g, m); this.motes.frustumCulled = false;
    scene.add(this.motes);
    this._motePos = pos;
  }

  tracer(from, to) {
    const slot = this.tracers.find(t => t.t < 0) || this.tracers[0];
    const m = slot.mesh;
    const dir = new THREE.Vector3().subVectors(to, from);
    const len = dir.length();
    m.scale.set(1, len, 1);
    m.position.copy(from).addScaledVector(dir, 0.5);
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
    m.material.opacity = 0.9;
    m.visible = true; slot.t = 0;
  }

  impact(point, normal, kind = 'dust') {
    const src = kind === 'blood' ? this.blood : this.dust;
    const n = normal || new THREE.Vector3(0, 1, 0);
    const count = kind === 'blood' ? 22 : 16;
    for (let i = 0; i < count; i++) {
      const v = n.clone().multiplyScalar(kind === 'blood' ? 2.5 : 1.8)
        .add(new THREE.Vector3((Math.random() - 0.5) * 3, Math.random() * 2, (Math.random() - 0.5) * 3));
      src.emit(point, v, 0.5 + Math.random() * 0.6, 0.6 + Math.random() * 1.2);
    }
    if (kind !== 'blood') {
      for (let i = 0; i < 8; i++) {
        const v = n.clone().multiplyScalar(3).add(new THREE.Vector3((Math.random() - 0.5) * 4, Math.random() * 3, (Math.random() - 0.5) * 4));
        this.spark.emit(point, v, 0.18 + Math.random() * 0.12, 0.4 + Math.random() * 0.5);
      }
      this._decal(point, n);
    }
  }

  _decal(point, normal) {
    const m = new THREE.Mesh(this.decalGeo, this.decalMat.clone());
    m.position.copy(point).addScaledVector(normal, 0.01);
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
    m.scale.setScalar(0.7 + Math.random() * 0.8);
    this.scene.add(m);
    this.decals.push({ mesh: m, t: 0 });
    if (this.decals.length > 40) { const d = this.decals.shift(); this.scene.remove(d.mesh); d.mesh.material.dispose(); }
  }

  update(dt, camPos) {
    this.dust.update(dt, -1.5);
    this.blood.update(dt, -6);
    this.spark.update(dt, -9);
    for (const t of this.tracers) {
      if (t.t < 0) continue;
      t.t += dt;
      t.mesh.material.opacity = Math.max(0, 0.9 - t.t * 9);
      if (t.t > 0.1) { t.mesh.visible = false; t.t = -1; }
    }
    // drift motes
    const p = this._motePos;
    for (let i = 0; i < p.length; i += 3) {
      p[i] += Math.sin((i + performance.now() * 0.0002)) * dt * 0.15;
      p[i + 1] += dt * 0.08;
      if (p[i + 1] > 18) p[i + 1] = 0;
    }
    this.motes.geometry.attributes.position.needsUpdate = true;
    if (camPos) this.motes.position.set(camPos.x, 0, camPos.z);
  }
}
