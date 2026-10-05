/**
 * Mic capture: Float32 at the context's rate -> 16 kHz Int16 PCM in 20 ms frames.
 *
 * 20 ms (320 samples) matches telephony framing and gets audio to the
 * recognizer ~80 ms sooner than 100 ms frames. Each frame also carries its RMS
 * level for the orb animation and the local barge-in cue.
 *
 * Resampling (plan 4.5, R3.3). Where the browser allows it, app.js runs the
 * AudioContext at 16 kHz and the browser's own resampler does the work: this
 * processor then passes samples straight through. Otherwise (Firefox, or a
 * device that refuses), it converts here with band-limited interpolation: a
 * Blackman-windowed sinc low-pass at 7.2 kHz evaluated at each output
 * position. The old linear interpolation had no low-pass, so everything the
 * mic picked up between 8 and 24 kHz (sibilants, fan and keyboard hiss) folded
 * back into the speech band as noise: exactly the "s", "f" and "th" sounds
 * that tell "Rao" from "Rau", or "fifty" from "fifteen".
 */
const TARGET_RATE = 16000;
const HALF_TAPS = 24;             // kernel half-width, in input samples (0.5 ms at 48 kHz)
const TAPS = HALF_TAPS * 2;
const PHASES = 256;               // fractional positions precomputed (1/256 sample apart)
const CUTOFF_HZ = 7200;           // 0.9 x the 8 kHz Nyquist of 16 kHz audio
const FRAME = 320;                // 20 ms at 16 kHz

function sinc(x) {
    if (Math.abs(x) < 1e-9) return 1;
    const px = Math.PI * x;
    return Math.sin(px) / px;
}

/** PHASES rows of TAPS weights; row p is the kernel for an output p/PHASES past a sample. */
function buildKernel(inputRate) {
    // Cutoff in cycles per input sample. Upsampling (an 8 kHz headset) needs no
    // extra filtering beyond the interpolation itself.
    const cutoff = Math.min(CUTOFF_HZ, 0.45 * inputRate) / inputRate;
    const table = new Float32Array(PHASES * TAPS);
    for (let p = 0; p < PHASES; p++) {
        const frac = p / PHASES;
        let sum = 0;
        for (let k = 0; k < TAPS; k++) {
            const t = (k - HALF_TAPS + 1) - frac;           // distance from the output position
            const w = 0.42 + 0.5 * Math.cos(Math.PI * t / HALF_TAPS)
                + 0.08 * Math.cos(2 * Math.PI * t / HALF_TAPS);   // Blackman, centred on the output
            const h = Math.abs(t) >= HALF_TAPS ? 0 : 2 * cutoff * sinc(2 * cutoff * t) * w;
            table[p * TAPS + k] = h;
            sum += h;
        }
        for (let k = 0; k < TAPS; k++) table[p * TAPS + k] /= sum;   // unity gain at DC
    }
    return table;
}

class PCMCaptureProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        // `sampleRate` is the AudioContext's rate (a worklet global).
        this._step = sampleRate / TARGET_RATE;               // input samples per output sample
        this._passthrough = Math.abs(this._step - 1) < 1e-6;
        this._kernel = this._passthrough ? null : buildKernel(sampleRate);
        // Linear input buffer with HALF_TAPS samples of history before the read position.
        this._buf = new Float32Array(4096);
        this._len = HALF_TAPS;                               // leading zeros: history for the first output
        this._pos = HALF_TAPS;                               // next output position, in input samples
        this._frame = new Int16Array(FRAME);
        this._fill = 0;
        this._sumSq = 0;
    }

    _append(samples) {
        if (this._len + samples.length > this._buf.length) {
            const bigger = new Float32Array(Math.max(this._buf.length * 2, this._len + samples.length));
            bigger.set(this._buf.subarray(0, this._len));
            this._buf = bigger;
        }
        this._buf.set(samples, this._len);
        this._len += samples.length;
    }

    _emit(sample) {
        const s = Math.max(-1, Math.min(1, sample));
        this._frame[this._fill++] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        this._sumSq += s * s;
        if (this._fill === FRAME) {
            const rms = Math.sqrt(this._sumSq / FRAME);
            const out = this._frame;
            this.port.postMessage({ type: 'audio', buffer: out.buffer, rms }, [out.buffer]);
            this._frame = new Int16Array(FRAME);
            this._fill = 0;
            this._sumSq = 0;
        }
    }

    process(inputs) {
        const input = inputs[0];
        const channel = input && input[0];
        if (!channel || channel.length === 0) return true;

        // Silence is sent too: the recognizer's endpointer needs it.
        if (this._passthrough) {
            for (let i = 0; i < channel.length; i++) this._emit(channel[i]);
            return true;
        }

        this._append(channel);
        const buf = this._buf, kernel = this._kernel;
        while (true) {
            let base = Math.floor(this._pos);
            let phase = Math.round((this._pos - base) * PHASES);
            if (phase === PHASES) { phase = 0; base += 1; }
            if (base + HALF_TAPS >= this._len) break;            // wait for more input
            const start = base - HALF_TAPS + 1, row = phase * TAPS;
            let acc = 0;
            for (let k = 0; k < TAPS; k++) acc += buf[start + k] * kernel[row + k];
            this._emit(acc);
            this._pos += this._step;
        }
        // Keep only the history the next outputs need.
        const drop = Math.floor(this._pos) - HALF_TAPS;
        if (drop > 0) {
            buf.copyWithin(0, drop, this._len);
            this._len -= drop;
            this._pos -= drop;
        }
        return true;
    }
}

registerProcessor('pcm-capture-processor', PCMCaptureProcessor);
