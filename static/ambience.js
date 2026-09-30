/**
 * Clinic sound that follows the call (docs/NORTH_STAR.md, decisions R4-R6, and
 * the owner's note: "don't play anything constantly, play according to the call").
 *
 * Nothing plays on its own. The clinic is heard only while Emma's line is
 * active, i.e. while she speaks or types, the way a headset with a noise gate
 * sounds on a real phone call:
 *
 *   murmur    waiting-room recordings running silently behind a gate that
 *             opens in about 80 ms when Emma's line opens and closes over about
 *             350 ms after a short hold. Played as 40-110 s segments from random
 *             offsets, crossfaded, so the clinic never sounds looped.
 *   movement  an occasional door, chair or footsteps: at most one every 25 s,
 *             only while the line is open (a random 3-6 s slice of long files).
 *   typing    when Emma writes down what the caller just said, or checks the
 *             diary. Triggered by the server; stops when she starts speaking.
 *
 * While the caller talks, Emma's side is silent, so nothing leaks back into
 * their microphone. Files come from static/ambience/manifest.json (sounds the
 * owner approved; see tools/ambience_sources.json). Levels are normalised on
 * load: murmur to a target RMS, events and typing to a target peak.
 */

const dbToGain = (db) => Math.pow(10, db / 20);
const rand = (a, b) => a + Math.random() * (b - a);
const pick = (list, not) => {
  const pool = list.length > 1 ? list.filter((x) => x !== not) : list;
  return pool[Math.floor(Math.random() * pool.length)];
};

const GATE = { open: 0.03, hold: 0.25, close: 0.12 };   // time constants and hold, seconds
const MOVEMENT = { chance: 0.35, cooldown: 25 };        // per line-open, seconds

export class Ambience {
  constructor(ctx, output, cfg = {}) {
    this.ctx = ctx;
    this.cfg = { murmurDb: -38, eventDb: -34, ...cfg };
    this.out = output;
    this.gate = ctx.createGain();
    this.gate.gain.value = 0;
    this.gate.connect(output);
    this.buffers = { murmur: [], movement: [], typing: [] };
    this.timers = [];
    this.sources = new Set();
    this.running = false;
    this.speakingNow = false;
    this.typingUntil = 0;
    this.lineOpen = false;
    this.closeTimer = null;
    this.lastMovement = -Infinity;
    this.typist = null;            // { src, gain, turn, untilSpeech }
  }

  // ------------------------------------------------------------------ loading
  /**
   * Level of a recording as heard: `rms` is the median of half-second windows of
   * the mono mix, so a few loud moments (a door, a cough) don't make the typical
   * stretch play too quietly; `peak` is the loudest sample of the mono mix.
   */
  static _stats(buffer) {
    const n = buffer.length, chans = buffer.numberOfChannels;
    const data = Array.from({ length: chans }, (_, c) => buffer.getChannelData(c));
    const win = Math.max(1, Math.floor(buffer.sampleRate / 2));
    const levels = [];
    let peak = 0;
    for (let s = 0; s < n; s += win) {
      let e = 0, m = 0;
      for (let i = s; i < Math.min(n, s + win); i += 2) {
        let v = 0;
        for (let c = 0; c < chans; c++) v += data[c][i];
        v /= chans;
        e += v * v; m++;
        if (Math.abs(v) > peak) peak = Math.abs(v);
      }
      levels.push(Math.sqrt(e / Math.max(1, m)));
    }
    levels.sort((a, b) => a - b);
    return { rms: levels[Math.floor(levels.length / 2)] || 1e-6, peak: peak || 1e-6 };
  }

  async load() {
    let manifest = {};
    try {
      const res = await fetch('/static/ambience/manifest.json', { cache: 'no-cache' });
      if (res.ok) manifest = await res.json();
    } catch (_) { /* no recordings: typing falls back to synthesis, the rest stays silent */ }
    await Promise.all(Object.keys(this.buffers).map(async (group) => {
      for (const file of manifest[group] || []) {
        try {
          const res = await fetch(`/static/ambience/${file}`);
          if (!res.ok) throw new Error(res.status);
          const buffer = await this.ctx.decodeAudioData(await res.arrayBuffer());
          this.buffers[group].push({ buffer, ...Ambience._stats(buffer) });
        } catch (err) {
          console.warn('ambience: could not load', file, err);
        }
      }
    }));
  }

  // ------------------------------------------------------------------ the line gate
  /** Emma's audio started (true) or finished / was interrupted (false). */
  speaking(on) {
    this.speakingNow = on;
    this._refresh();
  }

  /** Emma's audio for `turn` started: typing that was waiting for her reply stops. */
  speechStarted(turn) {
    if (this.typist && this.typist.untilSpeech && turn >= this.typist.turn) this.stopTyping(160);
    this.speaking(true);
  }

  _active() {
    return this.speakingNow || this.ctx.currentTime < this.typingUntil;
  }

  _refresh() {
    if (!this.running) return;
    const now = this.ctx.currentTime;
    const g = this.gate.gain;
    if (this._active()) {
      if (this.closeTimer) { clearTimeout(this.closeTimer); this.closeTimer = null; }
      if (!this.lineOpen) {
        this.lineOpen = true;
        g.cancelScheduledValues(now);
        g.setTargetAtTime(1, now, GATE.open);
        this._maybeMovement();
      }
    } else if (this.lineOpen && !this.closeTimer) {
      this.closeTimer = setTimeout(() => {
        this.closeTimer = null;
        if (this._active()) return;
        this.lineOpen = false;
        const t = this.ctx.currentTime;
        g.cancelScheduledValues(t);
        g.setTargetAtTime(0, t, GATE.close);
      }, GATE.hold * 1000);
    }
  }

  // ------------------------------------------------------------------ murmur
  _startMurmur() {
    const items = this.buffers.murmur;
    if (!items.length) return;
    const bus = this.ctx.createGain();
    bus.connect(this.gate);
    const fade = 3;
    let previous = null;

    const segment = (when) => {
      if (!this.running) return;
      const item = pick(items, previous);
      previous = item;
      const length = rand(40, 110);
      // A recording shorter than the segment loops inside it; longer ones play a random stretch.
      const loop = item.buffer.duration < length + 1;
      const offset = loop ? rand(0, item.buffer.duration) : rand(0, item.buffer.duration - length);
      const src = this.ctx.createBufferSource();
      src.buffer = item.buffer;
      src.loop = loop;
      const g = this.ctx.createGain();
      const level = dbToGain(this.cfg.murmurDb) / item.rms;
      g.gain.setValueAtTime(0, when);
      g.gain.linearRampToValueAtTime(level, when + fade);
      g.gain.setValueAtTime(level, when + length - fade);
      g.gain.linearRampToValueAtTime(0, when + length);
      src.connect(g).connect(bus);
      src.start(when, offset, length);
      this.sources.add(src);
      src.onended = () => this.sources.delete(src);
      const next = when + length - fade;      // crossfade into the next segment
      this._later((next - this.ctx.currentTime - 1) * 1000, () => segment(next));
    };

    // Slow, small level drift (about +/-1.5 dB), like a real room.
    const drift = () => {
      if (!this.running) return;
      bus.gain.linearRampToValueAtTime(dbToGain(rand(-1.5, 1.5)), this.ctx.currentTime + rand(6, 12));
      this._later(rand(8000, 15000), drift);
    };
    segment(this.ctx.currentTime + 0.05);
    drift();
  }

  // ------------------------------------------------------------------ movement
  _maybeMovement() {
    const items = this.buffers.movement;
    const now = this.ctx.currentTime;
    if (!items.length || now - this.lastMovement < MOVEMENT.cooldown || Math.random() > MOVEMENT.chance) return;
    this.lastMovement = now;
    this._later(rand(400, 2000), () => {
      if (!this.running || !this.lineOpen) return;
      this._playMovement(pick(items));
    });
  }

  _playMovement(item) {
    const t = this.ctx.currentTime;
    const src = this.ctx.createBufferSource();
    src.buffer = item.buffer;
    const g = this.ctx.createGain();
    const level = dbToGain(this.cfg.eventDb + rand(-5, 0)) / item.peak;
    const slice = item.buffer.duration > 8 ? rand(3, 6) : item.buffer.duration;
    const offset = item.buffer.duration > 8 ? rand(0, item.buffer.duration - slice) : 0;
    const edge = Math.min(0.3, slice / 4);
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(level, t + edge);
    g.gain.setValueAtTime(level, t + slice - edge);
    g.gain.linearRampToValueAtTime(0, t + slice);
    const pan = this.ctx.createStereoPanner ? this.ctx.createStereoPanner() : null;
    if (pan) { pan.pan.value = rand(-0.6, 0.6); src.connect(g).connect(pan).connect(this.gate); }
    else src.connect(g).connect(this.gate);
    src.start(t, offset, slice);
    this.sources.add(src);
    src.onended = () => this.sources.delete(src);
  }

  // ------------------------------------------------------------------ typing
  /**
   * Keyboard typing for up to `durationMs`, starting `afterMs` from now.
   * untilSpeech: stop as soon as Emma's reply for `turn` starts (she was writing
   * down what the caller said). Otherwise it runs its full length (checking).
   */
  typing(afterMs = 0, durationMs = 1500, { turn = 0, untilSpeech = false } = {}) {
    this.stopTyping(60);
    const when = this.ctx.currentTime + Math.max(0, afterMs) / 1000;
    const dur = Math.max(0.3, durationMs / 1000);
    const gain = this.ctx.createGain();
    gain.gain.setValueAtTime(0, when);
    gain.gain.linearRampToValueAtTime(1, when + 0.06);
    gain.gain.setValueAtTime(1, when + dur - 0.12);
    gain.gain.linearRampToValueAtTime(0, when + dur);
    gain.connect(this.out);
    let src;
    const items = this.buffers.typing;
    if (items.length) {
      const item = pick(items);
      src = this.ctx.createBufferSource();
      src.buffer = item.buffer;
      const level = this.ctx.createGain();
      // The keyboard is right by the phone: peaks about 10 dB above distant events.
      level.gain.value = dbToGain(this.cfg.eventDb + 10) / item.peak;
      src.connect(level).connect(gain);
      src.start(when, rand(0, Math.max(0, item.buffer.duration - dur)), dur);
    } else {
      src = this._synthClicks(when, dur, gain);
    }
    this.typist = { src, gain, turn, untilSpeech };
    this.typingUntil = when + dur;
    this._later(afterMs, () => this._refresh());
    this._later(afterMs + durationMs + 20, () => this._refresh());
  }

  _synthClicks(when, dur, dest) {
    // Fallback when no typing recording is available: filtered clicks at a typist's rhythm.
    const rate = this.ctx.sampleRate;
    const buffer = this.ctx.createBuffer(1, Math.ceil(dur * rate), rate);
    const data = buffer.getChannelData(0);
    let t = rand(0.02, 0.1);
    while (t < dur - 0.05) {
      const start = Math.floor(t * rate), len = Math.floor(0.018 * rate), amp = rand(0.25, 0.6);
      for (let i = 0; i < len && start + i < data.length; i++) {
        data[start + i] += amp * (Math.random() * 2 - 1) * Math.exp(-i / (len / 5));
      }
      t += Math.random() < 0.12 ? rand(0.25, 0.45) : rand(0.07, 0.17);
    }
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    const band = this.ctx.createBiquadFilter();
    band.type = 'bandpass'; band.frequency.value = 2800; band.Q.value = 0.9;
    const level = this.ctx.createGain();
    level.gain.value = dbToGain(this.cfg.eventDb + 20);   // clicks peak near -10 dBFS before this
    src.connect(band).connect(level).connect(dest);
    src.start(when);
    return src;
  }

  stopTyping(fadeMs = 120) {
    const typist = this.typist;
    if (!typist) return;
    this.typist = null;
    const t = this.ctx.currentTime;
    typist.gain.gain.cancelScheduledValues(t);
    typist.gain.gain.setValueAtTime(typist.gain.gain.value, t);
    typist.gain.gain.linearRampToValueAtTime(0, t + fadeMs / 1000);
    try { typist.src.stop(t + fadeMs / 1000 + 0.02); } catch (_) {}
    this.typingUntil = Math.min(this.typingUntil, t);
    this._refresh();
  }

  // ------------------------------------------------------------------ lifecycle
  _later(ms, fn) {
    this.timers.push(setTimeout(fn, Math.max(0, ms)));
  }

  async start() {
    if (this.running) return;
    await this.load();
    this.running = true;
    this._startMurmur();
    this._refresh();
  }

  stop() {
    this.running = false;
    this.timers.forEach(clearTimeout);
    this.timers = [];
    if (this.closeTimer) clearTimeout(this.closeTimer);
    this.stopTyping(20);
    for (const src of this.sources) { try { src.stop(); } catch (_) {} }
    this.sources.clear();
  }
}

/**
 * Phone-line character for the browser demo: 300-3400 Hz band-pass and gentle
 * compression, like a real call. Returns the node to connect sources into.
 */
export function phoneLine(ctx, destination) {
  const input = ctx.createGain();
  const hp = ctx.createBiquadFilter();
  hp.type = 'highpass'; hp.frequency.value = 300; hp.Q.value = 0.7;
  const lp = ctx.createBiquadFilter();
  lp.type = 'lowpass'; lp.frequency.value = 3400; lp.Q.value = 0.7;
  const presence = ctx.createBiquadFilter();
  presence.type = 'peaking'; presence.frequency.value = 1800; presence.gain.value = 2.5; presence.Q.value = 1;
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -22; comp.ratio.value = 3; comp.attack.value = 0.005; comp.release.value = 0.15;
  input.connect(hp).connect(lp).connect(presence).connect(comp).connect(destination);
  return input;
}
