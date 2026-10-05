/**
 * Clinic sound that follows the call (docs/NORTH_STAR.md, decisions R4-R6, and
 * the owner's notes: "don't play anything constantly, play according to the
 * call"; on 1 Oct the waiting-room murmur was removed because it still felt
 * constant).
 *
 * There is no background bed. Only two kinds of sound, both tied to the call:
 *
 *   movement  an occasional door, chair or footsteps, heard only while Emma's
 *             line is active (she is speaking or typing), like a headset with a
 *             noise gate: at most one every 25 s, a random 3-6 s slice of long
 *             files, faded out if the line closes.
 *   typing    when Emma writes down what the caller just said, or checks the
 *             diary. Triggered by the server; note-taking typing stops when she
 *             starts speaking.
 *
 * While the caller talks, Emma's side is silent, so nothing leaks back into
 * their microphone. Files come from static/ambience/manifest.json (sounds the
 * owner approved; see tools/ambience_sources.json), normalised to a target peak.
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
    this.cfg = { eventDb: -34, ...cfg };
    this.out = output;
    this.gate = ctx.createGain();
    this.gate.gain.value = 0;
    this.gate.connect(output);
    this.buffers = { movement: [], typing: [] };
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
  /** Loudest sample of the mono mix, used to set each sound's level. */
  static _peak(buffer) {
    const n = buffer.length, chans = buffer.numberOfChannels;
    const data = Array.from({ length: chans }, (_, c) => buffer.getChannelData(c));
    let peak = 0;
    for (let i = 0; i < n; i += 2) {
      let v = 0;
      for (let c = 0; c < chans; c++) v += data[c][i];
      v = Math.abs(v / chans);
      if (v > peak) peak = v;
    }
    return peak || 1e-6;
  }

  async load() {
    let manifest = {};
    try {
      const res = await fetch('/static/ambience/manifest.json', { cache: 'no-cache' });
      if (res.ok) manifest = await res.json();
    } catch (_) { /* no recordings: typing falls back to synthesis, nothing else plays */ }
    await Promise.all(Object.keys(this.buffers).map(async (group) => {
      for (const file of manifest[group] || []) {
        try {
          const res = await fetch(`/static/ambience/${file}`);
          if (!res.ok) throw new Error(res.status);
          const buffer = await this.ctx.decodeAudioData(await res.arrayBuffer());
          this.buffers[group].push({ buffer, peak: Ambience._peak(buffer) });
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
