/*
 * Fully synthesized audio via WebAudio — no external sound files.
 * Big .50 cal report (noise crack + low body + tail), bolt cycle, dry-fire,
 * hit confirmation, distant impact, and a looping wind bed.
 */
export class Audio {
  constructor() {
    this.ctx = null;
    this.master = null;
    this.enabled = false;
  }

  init() {
    if (this.ctx) return;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.ctx = new Ctx();
    this.master = this.ctx.createGain();
    this.master.gain.value = 0.9;
    this.master.connect(this.ctx.destination);
    this.enabled = true;
    this._noiseBuf = this._makeNoise(1.0);
    this._wind();
  }
  resume() { if (this.ctx && this.ctx.state === 'suspended') this.ctx.resume(); }

  _makeNoise(sec) {
    const n = this.ctx.sampleRate * sec;
    const buf = this.ctx.createBuffer(1, n, this.ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
    return buf;
  }
  _noise(dur, dest, { type = 'bandpass', freq = 1000, q = 1, gain = 1 } = {}) {
    const src = this.ctx.createBufferSource(); src.buffer = this._noiseBuf;
    const f = this.ctx.createBiquadFilter(); f.type = type; f.frequency.value = freq; f.Q.value = q;
    const g = this.ctx.createGain(); g.gain.value = gain;
    src.connect(f); f.connect(g); g.connect(dest || this.master);
    return { src, f, g };
  }

  gunshot() {
    if (!this.enabled) return;
    const t = this.ctx.currentTime;
    // sharp crack
    const crack = this._noise(0.3, null, { type: 'highpass', freq: 1800, q: 0.7, gain: 1.1 });
    crack.g.gain.setValueAtTime(1.1, t);
    crack.g.gain.exponentialRampToValueAtTime(0.001, t + 0.14);
    crack.src.start(t); crack.src.stop(t + 0.16);
    // body (mid boom)
    const body = this._noise(0.4, null, { type: 'bandpass', freq: 380, q: 0.8, gain: 1.0 });
    body.g.gain.setValueAtTime(1.0, t);
    body.g.gain.exponentialRampToValueAtTime(0.001, t + 0.3);
    body.src.start(t); body.src.stop(t + 0.34);
    // low thump (sine drop)
    const osc = this.ctx.createOscillator(); const og = this.ctx.createGain();
    osc.type = 'sine'; osc.frequency.setValueAtTime(120, t); osc.frequency.exponentialRampToValueAtTime(45, t + 0.18);
    og.gain.setValueAtTime(0.9, t); og.gain.exponentialRampToValueAtTime(0.001, t + 0.25);
    osc.connect(og); og.connect(this.master); osc.start(t); osc.stop(t + 0.3);
    // tail (reverb-ish decaying noise)
    const tail = this._noise(0.8, null, { type: 'lowpass', freq: 900, q: 0.5, gain: 0.35 });
    tail.g.gain.setValueAtTime(0.0, t);
    tail.g.gain.linearRampToValueAtTime(0.35, t + 0.05);
    tail.g.gain.exponentialRampToValueAtTime(0.001, t + 0.7);
    tail.src.start(t); tail.src.stop(t + 0.8);
  }

  bolt() {
    if (!this.enabled) return;
    const t = this.ctx.currentTime + 0.12;
    for (const [dt, freq] of [[0, 2600], [0.13, 1800], [0.26, 2200]]) {
      const c = this._noise(0.06, null, { type: 'bandpass', freq, q: 6, gain: 0.4 });
      c.g.gain.setValueAtTime(0.4, t + dt); c.g.gain.exponentialRampToValueAtTime(0.001, t + dt + 0.05);
      c.src.start(t + dt); c.src.stop(t + dt + 0.06);
    }
  }

  dryFire() {
    if (!this.enabled) return;
    const t = this.ctx.currentTime;
    const c = this._noise(0.05, null, { type: 'bandpass', freq: 2500, q: 8, gain: 0.5 });
    c.g.gain.setValueAtTime(0.5, t); c.g.gain.exponentialRampToValueAtTime(0.001, t + 0.04);
    c.src.start(t); c.src.stop(t + 0.05);
  }

  hit(headshot = false) {
    if (!this.enabled) return;
    const t = this.ctx.currentTime;
    const osc = this.ctx.createOscillator(); const g = this.ctx.createGain();
    osc.type = 'square';
    osc.frequency.setValueAtTime(headshot ? 1400 : 900, t);
    osc.frequency.exponentialRampToValueAtTime(headshot ? 2000 : 1200, t + 0.05);
    g.gain.setValueAtTime(0.25, t); g.gain.exponentialRampToValueAtTime(0.001, t + 0.09);
    osc.connect(g); g.connect(this.master); osc.start(t); osc.stop(t + 0.1);
  }

  impact() {
    if (!this.enabled) return;
    const t = this.ctx.currentTime + 0.15; // slight delay = distance
    const c = this._noise(0.2, null, { type: 'lowpass', freq: 500, q: 1, gain: 0.4 });
    c.g.gain.setValueAtTime(0.4, t); c.g.gain.exponentialRampToValueAtTime(0.001, t + 0.15);
    c.src.start(t); c.src.stop(t + 0.2);
  }

  _wind() {
    const w = this._noise(1.0, null, { type: 'lowpass', freq: 420, q: 0.6, gain: 0.05 });
    w.src.loop = true;
    const lfo = this.ctx.createOscillator(); const lg = this.ctx.createGain();
    lfo.frequency.value = 0.15; lg.gain.value = 0.03;
    lfo.connect(lg); lg.connect(w.g.gain);
    w.src.start(); lfo.start();
  }
}
