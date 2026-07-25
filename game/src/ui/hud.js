/* Thin DOM HUD controller. */
export class HUD {
  constructor() {
    this.$ = (id) => document.getElementById(id);
    this.hm = this.$('hitmarker');
    this.kf = this.$('killfeed');
    this._hmTimer = null;
  }

  setAmmo(mag, reserve) {
    this.$('ammoMag').textContent = mag;
    this.$('ammoReserve').textContent = reserve;
    this.$('ammoMag').style.color = mag === 0 ? '#c0392b' : '#fff';
  }
  setScore(s) { this.$('score').textContent = s; }
  setHealth(f) { this.$('healthbar').style.width = Math.max(0, f * 100) + '%'; }
  setStamina(f) { this.$('staminabar').style.width = Math.max(0, f * 100) + '%'; }

  setReloading(on) {
    let el = this.$('reloadTag');
    if (on) {
      if (!el) {
        el = document.createElement('div'); el.id = 'reloadTag';
        el.style.cssText = 'position:absolute;left:50%;bottom:30%;transform:translateX(-50%);color:#e8ede8;letter-spacing:3px;font-size:14px;text-shadow:0 2px 6px #000';
        el.textContent = 'RELOADING…';
        this.$('hud').appendChild(el);
      }
    } else if (el) el.remove();
  }

  setScopeZoom(z) { this.$('scopeZoom').textContent = z.toFixed(1) + 'x'; }

  hitmarker(kill) {
    this.hm.classList.remove('show', 'kill');
    void this.hm.offsetWidth; // reflow to restart animation
    if (kill) this.hm.classList.add('kill');
    this.hm.classList.add('show');
  }

  killfeed(text) {
    const item = document.createElement('div');
    item.className = 'kf-item';
    item.textContent = text;
    this.kf.prepend(item);
    setTimeout(() => item.remove(), 3500);
    while (this.kf.children.length > 5) this.kf.lastChild.remove();
  }

  damageFlash() {
    const d = this.$('damage');
    d.style.opacity = '1';
    setTimeout(() => { d.style.opacity = '0'; }, 120);
  }
}
