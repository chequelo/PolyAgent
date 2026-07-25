import * as THREE from 'three';

/*
 * First-person .50 sniper rifle viewmodel.
 * Built from primitives with PBR gunmetal/polymer materials, attached to the
 * camera. Handles ADS pose blend, procedural sway/bob, spring-based recoil,
 * and a muzzle flash.
 */

const HIP_POS = new THREE.Vector3(0.16, -0.19, -0.42);
const HIP_ROT = new THREE.Euler(0.02, -0.06, 0.0);
const ADS_POS = new THREE.Vector3(0.0, -0.045, -0.20);
const ADS_ROT = new THREE.Euler(0, 0, 0);

function mat(color, rough, metal) {
  return new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: metal });
}

export class Rifle {
  constructor(camera) {
    this.camera = camera;
    this.group = new THREE.Group();
    this.group.renderOrder = 10;
    camera.add(this.group);

    this.ads = 0; this.adsTarget = 0;
    this.recoil = new THREE.Vector3();       // positional kick
    this.recoilVel = new THREE.Vector3();
    this.recoilRot = 0; this.recoilRotVel = 0;
    this.swayT = 0;
    this.flashTime = -1;
    this.boltTime = -1;

    this._build();
  }

  _build() {
    const gun = new THREE.Group();
    const gunmetal = mat(0x17181d, 0.5, 0.65);
    const gunmetalWorn = mat(0x2a2c32, 0.62, 0.55);
    const polymer = mat(0x1a1c1a, 0.7, 0.05);
    const black = mat(0x101012, 0.55, 0.3);

    // receiver
    const receiver = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.09, 0.5), gunmetal);
    receiver.position.set(0, 0, -0.1); gun.add(receiver);

    // barrel + fluting
    const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.02, 0.62, 16), gunmetal);
    barrel.rotation.x = Math.PI / 2; barrel.position.set(0, 0.005, -0.5); gun.add(barrel);
    // muzzle brake
    const brake = new THREE.Mesh(new THREE.CylinderGeometry(0.032, 0.032, 0.12, 12), gunmetalWorn);
    brake.rotation.x = Math.PI / 2; brake.position.set(0, 0.005, -0.82); gun.add(brake);
    for (let i = 0; i < 3; i++) {
      const slot = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.012, 0.012), black);
      slot.position.set(0, 0.005, -0.78 - i * 0.03); gun.add(slot);
    }
    this.muzzle = new THREE.Object3D(); this.muzzle.position.set(0, 0.005, -0.9); gun.add(this.muzzle);

    // handguard w/ rail
    const guard = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.055, 0.3), polymer);
    guard.position.set(0, -0.01, -0.34); gun.add(guard);
    for (let i = 0; i < 6; i++) {
      const r = new THREE.Mesh(new THREE.BoxGeometry(0.052, 0.008, 0.012), black);
      r.position.set(0, 0.03, -0.24 - i * 0.035); gun.add(r);
    }

    // scope
    const scopeGroup = new THREE.Group(); scopeGroup.position.set(0, 0.075, -0.12);
    const tube = new THREE.Mesh(new THREE.CylinderGeometry(0.028, 0.028, 0.34, 20), black);
    tube.rotation.x = Math.PI / 2; scopeGroup.add(tube);
    const bell = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.032, 0.08, 20), black);
    bell.rotation.x = Math.PI / 2; bell.position.z = -0.19; scopeGroup.add(bell);
    const eyepiece = new THREE.Mesh(new THREE.CylinderGeometry(0.034, 0.03, 0.06, 20), black);
    eyepiece.rotation.x = Math.PI / 2; eyepiece.position.z = 0.18; scopeGroup.add(eyepiece);
    // lens (front + rear) with tinted glass
    const lensMat = new THREE.MeshPhysicalMaterial({ color: 0x224466, metalness: 0, roughness: 0.05, transmission: 0.0, clearcoat: 1, reflectivity: 0.9, emissive: 0x0a1a2a, emissiveIntensity: 0.2 });
    const lens = new THREE.Mesh(new THREE.CircleGeometry(0.036, 24), lensMat);
    lens.position.z = -0.229; lens.rotation.y = Math.PI; scopeGroup.add(lens);
    // rings
    for (const z of [-0.08, 0.08]) {
      const ring = new THREE.Mesh(new THREE.CylinderGeometry(0.032, 0.032, 0.02, 16), gunmetalWorn);
      ring.rotation.x = Math.PI / 2; ring.position.z = z; scopeGroup.add(ring);
    }
    gun.add(scopeGroup);
    this.scopeModel = scopeGroup;

    // magazine
    const mag = new THREE.Mesh(new THREE.BoxGeometry(0.045, 0.16, 0.09), polymer);
    mag.position.set(0, -0.12, 0.02); mag.rotation.x = -0.08; gun.add(mag);
    this.mag = mag;

    // pistol grip + trigger guard
    const grip = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.14, 0.05), polymer);
    grip.position.set(0, -0.11, 0.14); grip.rotation.x = 0.35; gun.add(grip);
    const tguard = new THREE.Mesh(new THREE.TorusGeometry(0.03, 0.008, 8, 16, Math.PI), black);
    tguard.position.set(0, -0.04, 0.1); tguard.rotation.x = Math.PI / 2; gun.add(tguard);

    // stock + cheek riser
    const stock = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.11, 0.26), polymer);
    stock.position.set(0, -0.02, 0.28); gun.add(stock);
    const cheek = new THREE.Mesh(new THREE.BoxGeometry(0.045, 0.035, 0.16), mat(0x141512, 0.8, 0)); cheek.position.set(0, 0.05, 0.24); gun.add(cheek);
    const pad = new THREE.Mesh(new THREE.BoxGeometry(0.055, 0.13, 0.04), mat(0x0d0d0d, 0.9, 0)); pad.position.set(0, -0.02, 0.42); gun.add(pad);

    // bipod (folded forward)
    for (const s of [-1, 1]) {
      const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.006, 0.22, 8), gunmetalWorn);
      leg.position.set(s * 0.02, -0.05, -0.44); leg.rotation.set(0.5, 0, s * 0.15); gun.add(leg);
    }

    // shadows
    gun.traverse(o => { if (o.isMesh) { o.castShadow = false; o.receiveShadow = false; } });

    // hands (gloved) — left supports handguard, right on grip
    const glove = mat(0x2a2a26, 0.8, 0);
    const handL = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.05, 0.11), glove);
    handL.position.set(0.005, -0.05, -0.34); handL.rotation.z = 0.2; gun.add(handL);
    const foreArmL = new THREE.Mesh(new THREE.CapsuleGeometry(0.035, 0.2, 4, 8), glove);
    foreArmL.position.set(0.12, -0.16, -0.28); foreArmL.rotation.set(0.4, 0, -0.9); gun.add(foreArmL);
    const handR = new THREE.Mesh(new THREE.BoxGeometry(0.055, 0.06, 0.09), glove);
    handR.position.set(0.01, -0.09, 0.13); handR.rotation.x = 0.3; gun.add(handR);
    const foreArmR = new THREE.Mesh(new THREE.CapsuleGeometry(0.04, 0.22, 4, 8), glove);
    foreArmR.position.set(0.14, -0.2, 0.2); foreArmR.rotation.set(-0.5, 0, -0.7); gun.add(foreArmR);
    this.hands = [handL, foreArmL, handR, foreArmR];

    // muzzle flash (hidden by default)
    this.flash = this._buildFlash();
    this.muzzle.add(this.flash);
    this.flash.visible = false;

    // muzzle smoke sprite
    const smokeTex = this._smokeTexture();
    this.smoke = new THREE.Sprite(new THREE.SpriteMaterial({ map: smokeTex, color: 0xbcae98, opacity: 0, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, fog: false }));
    this.smoke.scale.setScalar(0.4); this.smoke.position.set(0, 0.01, -0.1);
    this.smoke.visible = false;
    this.muzzle.add(this.smoke);

    this.gun = gun;
    this.group.add(gun);
    this._applyPose();
  }

  _buildFlash() {
    const grp = new THREE.Group();
    const flashMat = new THREE.MeshBasicMaterial({ color: 0xffd27a, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false });
    const star = new THREE.Mesh(new THREE.ConeGeometry(0.06, 0.22, 6), flashMat);
    star.rotation.x = -Math.PI / 2; star.position.z = -0.08; grp.add(star);
    const star2 = new THREE.Mesh(new THREE.ConeGeometry(0.09, 0.1, 6), flashMat);
    star2.rotation.x = Math.PI / 2; grp.add(star2);
    const core = new THREE.Mesh(new THREE.SphereGeometry(0.05, 12, 12), new THREE.MeshBasicMaterial({ color: 0xfff2c0, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false }));
    grp.add(core);
    // dynamic point light for muzzle
    this.flashLight = new THREE.PointLight(0xffcaa0, 0, 12, 2);
    grp.add(this.flashLight);
    return grp;
  }

  _smokeTexture() {
    const c = document.createElement('canvas'); c.width = c.height = 64;
    const g = c.getContext('2d');
    const grad = g.createRadialGradient(32, 32, 2, 32, 32, 32);
    grad.addColorStop(0, 'rgba(255,255,255,0.9)');
    grad.addColorStop(0.5, 'rgba(200,200,200,0.4)');
    grad.addColorStop(1, 'rgba(200,200,200,0)');
    g.fillStyle = grad; g.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(c);
  }

  setScoped(scoped) { this.adsTarget = scoped ? 1 : 0; }
  snap() { this.ads = this.adsTarget; this._applyPose(); }

  fire() {
    // recoil impulse (kick back + up + slight roll)
    this.recoilVel.z += 2.6;
    this.recoilVel.y += 0.9;
    this.recoilRotVel += 5.5;
    this.flashTime = 0;
    this.boltTime = 0;
    this.flash.visible = true;
    this.flash.rotation.z = Math.random() * Math.PI;
    this.flashLight.intensity = 9;
    this.smoke.visible = true;
    this.smoke.material.opacity = 0.5;
  }

  _applyPose() {
    const t = this.ads;
    const ease = t * t * (3 - 2 * t);
    this.group.position.lerpVectors(HIP_POS, ADS_POS, ease);
    this.group.rotation.x = THREE.MathUtils.lerp(HIP_ROT.x, ADS_ROT.x, ease);
    this.group.rotation.y = THREE.MathUtils.lerp(HIP_ROT.y, ADS_ROT.y, ease);
    // hide the model when fully scoped (looking through the scope overlay)
    const vis = t < 0.85;
    this.gun.visible = vis;
    this.hands.forEach(h => h.visible = vis);
  }

  update(dt, state = {}) {
    // ADS blend
    this.ads = THREE.MathUtils.damp(this.ads, this.adsTarget, 12, dt);

    // recoil spring (critically-ish damped)
    const k = 90, c = 15;
    this.recoilVel.addScaledVector(this.recoil, -k * dt);
    this.recoilVel.multiplyScalar(Math.exp(-c * dt));
    this.recoil.addScaledVector(this.recoilVel, dt);
    this.recoilRotVel += -k * this.recoilRot * dt;
    this.recoilRotVel *= Math.exp(-c * dt);
    this.recoilRot += this.recoilRotVel * dt;

    // sway + bob
    this.swayT += dt * (state.moving ? 9 : 1.4);
    const swayAmp = (1 - this.ads * 0.8) * (state.breath ? 0.25 : 1);
    const bob = state.moving ? (1 - this.ads * 0.6) : 0.25;
    const sx = Math.sin(this.swayT) * 0.006 * swayAmp * bob;
    const sy = Math.abs(Math.cos(this.swayT)) * 0.006 * swayAmp * bob;
    const lookSway = state.lookDelta || { x: 0, y: 0 };

    this._applyPose();
    this.group.position.z += this.recoil.z * 0.03;
    this.group.position.y += this.recoil.y * 0.02 + sy;
    this.group.position.x += sx - lookSway.x * 0.02;
    this.group.rotation.x += this.recoilRot * 0.04 + lookSway.y * 0.03;
    this.group.rotation.z = Math.sin(this.swayT * 0.5) * 0.01 * swayAmp;

    // flash / smoke lifetime
    if (this.flashTime >= 0) {
      this.flashTime += dt;
      const f = this.flashTime / 0.045;
      if (f >= 1) { this.flash.visible = false; this.flashLight.intensity = 0; this.flashTime = -1; }
      else { this.flash.scale.setScalar(1 - f * 0.4); this.flashLight.intensity = 9 * (1 - f); }
    }
    if (this.smoke.visible) {
      this.smoke.material.opacity = Math.max(0, this.smoke.material.opacity - dt * 0.8);
      this.smoke.scale.setScalar(0.4 + (0.5 - this.smoke.material.opacity) * 1.2);
      this.smoke.position.z -= dt * 0.3;
      if (this.smoke.material.opacity <= 0.001) this.smoke.visible = false;
    }
    if (this.boltTime >= 0) {
      this.boltTime += dt;
      if (this.boltTime > 0.5) { this.boltTime = -1; this.smoke.position.set(0, 0.01, -0.1); }
    }
  }
}
