import * as THREE from 'three';

/*
 * First-person player controller.
 *  - pointer-lock mouse look (yaw on player, pitch on camera)
 *  - accelerated WASD movement, sprint w/ stamina, jump + gravity
 *  - AABB collision against level colliders + terrain height clamp
 *  - breath-hold to steady scope sway
 */
const EYE = 1.7;
const RADIUS = 0.4;

export class Player {
  constructor(camera, level, start) {
    this.camera = camera;
    this.level = level;
    this.pos = start.clone();
    this.vel = new THREE.Vector3();
    this.yaw = Math.PI;      // face -Z (into compound)
    this.pitch = -0.05;
    this.onGround = true;

    this.baseSens = 0.0022;
    this.scopedSensMul = 0.35;
    this.scoped = false;

    this.stamina = 1;        // 0..1
    this.breathing = false;  // holding breath
    this.moving = false;
    this.speed = 0;

    this.keys = {};
    this.lookDelta = { x: 0, y: 0 };
    this._bind();
  }

  _bind() {
    this._onKey = (e, down) => {
      const k = e.code;
      this.keys[k] = down;
      if (down && k === 'KeyR') this.wantReload = true;
    };
    this._kd = (e) => this._onKey(e, true);
    this._ku = (e) => this._onKey(e, false);
    window.addEventListener('keydown', this._kd);
    window.addEventListener('keyup', this._ku);

    this._mm = (e) => {
      if (!this.enabled) return;
      const mul = this.scoped ? this.scopedSensMul : 1;
      const s = this.baseSens * mul;
      this.yaw -= e.movementX * s;
      this.pitch -= e.movementY * s;
      this.pitch = THREE.MathUtils.clamp(this.pitch, -Math.PI / 2 + 0.05, Math.PI / 2 - 0.05);
      this.lookDelta.x = e.movementX; this.lookDelta.y = e.movementY;
    };
    window.addEventListener('mousemove', this._mm);
  }

  setScoped(s) { this.scoped = s; }

  _collide(next) {
    // resolve against AABB colliders on XZ (simple push-out)
    for (const c of this.level.colliders) {
      if (next.x + RADIUS > c.min.x && next.x - RADIUS < c.max.x &&
          next.z + RADIUS > c.min.z && next.z - RADIUS < c.max.z &&
          next.y < c.max.y + 0.2 && next.y + EYE > c.min.y) {
        // find least-penetration axis on XZ
        const penX = Math.min(next.x + RADIUS - c.min.x, c.max.x - (next.x - RADIUS));
        const penZ = Math.min(next.z + RADIUS - c.min.z, c.max.z - (next.z - RADIUS));
        if (penX < penZ) {
          next.x = (this.pos.x < (c.min.x + c.max.x) / 2) ? c.min.x - RADIUS : c.max.x + RADIUS;
        } else {
          next.z = (this.pos.z < (c.min.z + c.max.z) / 2) ? c.min.z - RADIUS : c.max.z + RADIUS;
        }
      }
    }
    return next;
  }

  update(dt) {
    // ---- desired movement (camera-relative on XZ) ----
    const fwd = new THREE.Vector3(-Math.sin(this.yaw), 0, -Math.cos(this.yaw));
    const right = new THREE.Vector3(Math.cos(this.yaw), 0, -Math.sin(this.yaw));
    const wish = new THREE.Vector3();
    if (this.keys['KeyW']) wish.add(fwd);
    if (this.keys['KeyS']) wish.sub(fwd);
    if (this.keys['KeyD']) wish.add(right);
    if (this.keys['KeyA']) wish.sub(right);
    const wishing = wish.lengthSq() > 0;
    if (wishing) wish.normalize();

    const sprint = this.keys['ShiftLeft'] && !this.scoped && this.stamina > 0.02 && wishing;
    const maxSpeed = this.scoped ? 1.7 : sprint ? 7.2 : 4.4;
    const accel = this.onGround ? 55 : 12;

    // horizontal velocity toward wish
    const hv = new THREE.Vector3(this.vel.x, 0, this.vel.z);
    const target = wish.multiplyScalar(maxSpeed);
    hv.x = THREE.MathUtils.damp(hv.x, target.x, accel / 6, dt);
    hv.z = THREE.MathUtils.damp(hv.z, target.z, accel / 6, dt);
    this.vel.x = hv.x; this.vel.z = hv.z;
    this.speed = Math.hypot(hv.x, hv.z);
    this.moving = this.speed > 0.6;

    // stamina: drain on sprint, regen otherwise; breath-hold uses stamina
    this.breathing = !!this.keys['ShiftLeft'] && this.scoped && this.stamina > 0;
    if (sprint) this.stamina = Math.max(0, this.stamina - dt * 0.28);
    else if (this.breathing) this.stamina = Math.max(0, this.stamina - dt * 0.33);
    else this.stamina = Math.min(1, this.stamina + dt * 0.22);

    // ---- gravity + jump ----
    this.vel.y -= 22 * dt;
    if (this.keys['Space'] && this.onGround) { this.vel.y = 7.2; this.onGround = false; }

    // integrate
    const next = this.pos.clone().addScaledVector(this.vel, dt);
    this._collide(next);

    // terrain clamp
    const groundY = this.level.heightAt(next.x, next.z) + EYE;
    if (next.y <= groundY) { next.y = groundY; this.vel.y = 0; this.onGround = true; }
    else this.onGround = false;

    this.pos.copy(next);

    // ---- apply to camera ----
    this.camera.position.copy(this.pos);
    const cyaw = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), this.yaw);
    const cpitch = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), this.pitch);
    this.camera.quaternion.copy(cyaw).multiply(cpitch);

    // decay look delta (for weapon sway)
    this.lookDelta.x *= Math.exp(-12 * dt);
    this.lookDelta.y *= Math.exp(-12 * dt);
  }

  forward() { return new THREE.Vector3(0, 0, -1).applyQuaternion(this.camera.quaternion); }

  dispose() {
    window.removeEventListener('keydown', this._kd);
    window.removeEventListener('keyup', this._ku);
    window.removeEventListener('mousemove', this._mm);
  }
}
