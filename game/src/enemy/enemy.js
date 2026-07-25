import * as THREE from 'three';

/*
 * Enemy soldiers built from primitives with PBR materials.
 * Each soldier is a small skeleton of grouped parts so we can pose / animate
 * (idle sway, aim, stagger, death fall) without an external rig.
 */

const CAMO = 0x5a5c42;      // olive drab
const CAMO_DARK = 0x3f4230;
const SKIN = 0x9c6b4a;
const GEAR = 0x2b2b26;
const HELMET = 0x4a4c3a;

function mat(color, rough = 0.85, metal = 0.0) {
  return new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: metal });
}

function makeSoldier() {
  const g = new THREE.Group();
  const camo = mat(CAMO, 0.9);
  const camoD = mat(CAMO_DARK, 0.9);
  const skin = mat(SKIN, 0.7);
  const gear = mat(GEAR, 0.6, 0.1);
  const helmetMat = mat(HELMET, 0.55, 0.15);

  // pelvis/root at y=0; total height ~1.8
  const pelvis = new THREE.Group(); pelvis.position.y = 0.95; g.add(pelvis);

  // legs
  const legGeo = new THREE.CapsuleGeometry(0.11, 0.42, 4, 8);
  for (const side of [-1, 1]) {
    const hip = new THREE.Group(); hip.position.set(side * 0.12, 0, 0); pelvis.add(hip);
    const thigh = new THREE.Mesh(legGeo, camoD); thigh.position.y = -0.28; hip.add(thigh);
    const knee = new THREE.Group(); knee.position.y = -0.52; hip.add(knee);
    const shin = new THREE.Mesh(legGeo, camoD); shin.position.y = -0.26; knee.add(shin);
    const boot = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.12, 0.3), gear);
    boot.position.set(0, -0.5, 0.06); knee.add(boot);
    g.userData[`hip${side}`] = hip; g.userData[`knee${side}`] = knee;
    thigh.castShadow = shin.castShadow = boot.castShadow = true;
  }

  // torso + vest
  const torso = new THREE.Group(); torso.position.y = 0.02; pelvis.add(torso);
  const chest = new THREE.Mesh(new THREE.CapsuleGeometry(0.2, 0.34, 6, 12), camo);
  chest.position.y = 0.28; chest.scale.set(1.15, 1, 0.75); torso.add(chest);
  const vest = new THREE.Mesh(new THREE.BoxGeometry(0.46, 0.5, 0.34), gear);
  vest.position.y = 0.3; torso.add(vest);
  // pouches
  for (const dx of [-0.12, 0.12]) {
    const p = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.12, 0.08), mat(0x33342c, 0.8));
    p.position.set(dx, 0.16, 0.2); torso.add(p);
  }
  chest.castShadow = vest.castShadow = true;

  // head + helmet
  const neck = new THREE.Group(); neck.position.y = 0.56; torso.add(neck);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.13, 16, 16), skin);
  head.scale.set(0.9, 1.05, 0.95); head.position.y = 0.08; neck.add(head);
  const helmet = new THREE.Mesh(new THREE.SphereGeometry(0.15, 16, 12, 0, Math.PI * 2, 0, Math.PI * 0.62), helmetMat);
  helmet.position.y = 0.12; neck.add(helmet);
  const brim = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, 0.03, 16), helmetMat);
  brim.position.y = 0.09; neck.add(brim);
  head.castShadow = helmet.castShadow = true;
  g.userData.neck = neck;

  // arms + rifle held at low-ready
  const armGeo = new THREE.CapsuleGeometry(0.075, 0.34, 4, 8);
  const shoulderL = new THREE.Group(); shoulderL.position.set(-0.26, 0.42, 0); torso.add(shoulderL);
  const shoulderR = new THREE.Group(); shoulderR.position.set(0.26, 0.42, 0); torso.add(shoulderR);
  const upperL = new THREE.Mesh(armGeo, camo); upperL.position.y = -0.18; shoulderL.add(upperL);
  const upperR = new THREE.Mesh(armGeo, camo); upperR.position.y = -0.18; shoulderR.add(upperR);
  const foreL = new THREE.Group(); foreL.position.y = -0.34; shoulderL.add(foreL);
  const foreR = new THREE.Group(); foreR.position.y = -0.34; shoulderR.add(foreR);
  const flL = new THREE.Mesh(armGeo, skin); flL.position.y = -0.16; flL.scale.setScalar(0.85); foreL.add(flL);
  const flR = new THREE.Mesh(armGeo, skin); flR.position.y = -0.16; flR.scale.setScalar(0.85); foreR.add(flR);
  upperL.castShadow = upperR.castShadow = flL.castShadow = flR.castShadow = true;
  // pose arms holding rifle across body
  shoulderL.rotation.set(-0.9, 0.2, 0.35); foreL.rotation.x = -1.1;
  shoulderR.rotation.set(-0.8, -0.2, -0.2); foreR.rotation.x = -1.0;

  // rifle
  const rifle = new THREE.Group();
  const rmetal = mat(0x26271f, 0.5, 0.6);
  const rbody = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.1, 0.7), rmetal); rifle.add(rbody);
  const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.5, 10), rmetal);
  barrel.rotation.x = Math.PI / 2; barrel.position.z = -0.55; rifle.add(barrel);
  const mag = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.18, 0.1), mat(0x1c1d16, 0.7)); mag.position.set(0, -0.12, 0.05); rifle.add(mag);
  const stock = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.12, 0.22), mat(0x2a2b22, 0.7)); stock.position.z = 0.42; rifle.add(stock);
  rifle.position.set(0.16, 0.16, -0.16); rifle.rotation.set(0.1, 0.5, 0);
  rbody.castShadow = barrel.castShadow = true;
  torso.add(rifle);

  g.userData.pelvis = pelvis;
  g.userData.torso = torso;
  g.userData.shoulderL = shoulderL; g.userData.shoulderR = shoulderR;
  return g;
}

export class Enemy {
  constructor(scene, pos, seed = 0) {
    this.scene = scene;
    this.root = makeSoldier();
    this.root.position.copy(pos);
    this.root.rotation.y = Math.PI + (seed % 3 - 1) * 0.4; // roughly face +Z toward player
    this.home = pos.clone();
    this.seed = seed;
    this.alive = true;
    this.hp = 100;
    this.t = seed * 1.3;
    this.deathTime = 0;
    // hit volumes (local offsets from root): head, chest
    scene.add(this.root);
  }

  // returns { hit, part, point } for a ray
  raycastHit(raycaster) {
    if (!this.alive) return null;
    const hits = raycaster.intersectObject(this.root, true);
    if (hits.length === 0) return null;
    const p = hits[0].point;
    const localY = p.y - this.root.position.y;
    let part = 'body';
    if (localY > 1.55) part = 'head';
    else if (localY > 1.0) part = 'chest';
    else if (localY < 0.55) part = 'legs';
    return { hit: true, part, point: p.clone(), distance: hits[0].distance };
  }

  damage(part) {
    if (!this.alive) return { killed: false, part };
    const dmg = part === 'head' ? 200 : part === 'chest' ? 85 : part === 'legs' ? 35 : 55;
    this.hp -= dmg;
    if (this.hp <= 0) { this.kill(part); return { killed: true, part }; }
    // stagger
    this._stagger = 0.25;
    return { killed: false, part };
  }

  kill(part) {
    this.alive = false;
    this.deathTime = 0;
    this._deathDir = (Math.random() - 0.5);
    this._headshot = part === 'head';
  }

  update(dt, playerPos) {
    this.t += dt;
    const u = this.root.userData;
    if (this.alive) {
      // idle breathing + subtle weapon sway, face the player
      const breathe = Math.sin(this.t * 1.6) * 0.02;
      u.torso.rotation.x = breathe;
      u.neck.rotation.x = Math.sin(this.t * 1.6 + 1) * 0.03;
      // slow scan turn
      this.root.rotation.y = Math.PI + Math.sin(this.t * 0.4 + this.seed) * 0.5;
      if (this._stagger > 0) {
        this._stagger -= dt;
        u.torso.rotation.x -= this._stagger * 0.8;
      }
      // weight shift
      u.pelvis.position.y = 0.95 + Math.sin(this.t * 1.6) * 0.01;
    } else {
      // death fall: rotate about base, collapse
      this.deathTime += dt;
      const t = Math.min(this.deathTime / 0.8, 1);
      const ease = 1 - Math.pow(1 - t, 3);
      this.root.rotation.z = ease * (this._deathDir > 0 ? 1.45 : -1.45);
      this.root.rotation.x = ease * 0.3;
      u.pelvis.position.y = 0.95 - ease * 0.35;
      u.torso.rotation.x = ease * (this._headshot ? -0.6 : 0.4);
      // sink slightly after settling
      if (t >= 1) this.root.position.y = this.home.y - 0.05;
    }
  }

  dispose() { this.scene.remove(this.root); }
}

export class EnemyManager {
  constructor(scene, level) {
    this.scene = scene; this.level = level;
    this.enemies = [];
  }
  spawnAll(spawns) {
    spawns.forEach((p, i) => {
      const pos = p.clone(); // feet on terrain
      this.enemies.push(new Enemy(this.scene, pos, i));
    });
  }
  get alive() { return this.enemies.filter(e => e.alive); }
  update(dt, playerPos) { for (const e of this.enemies) e.update(dt, playerPos); }

  // ray from camera; returns closest enemy hit
  raycast(raycaster) {
    let best = null;
    for (const e of this.enemies) {
      const h = e.raycastHit(raycaster);
      if (h && (!best || h.distance < best.distance)) best = { enemy: e, ...h };
    }
    return best;
  }
}
