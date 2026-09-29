/**
 * Emma's voice: 16 kHz Int16 PCM in, device-rate audio out.
 *
 * A ring buffer plays one turn at a time. Audio for a newer turn replaces
 * whatever is left of an older one, and `flush` empties the buffer within one
 * render block (~3 ms) — that is what makes barge-in feel instant.
 *
 * Messages in:  {type:'pcm', turn, pcm: Int16Array} | {type:'end', turn}
 *               {type:'flush'} | {type:'duck', gain}
 * Messages out: {type:'started', turn} | {type:'ended', turn, playedMs}
 *               {type:'flushed', turn, playedMs} | {type:'level', value}
 */
const SOURCE_RATE = 16000;
const PREBUFFER_SECONDS = 0.06;   // absorb network jitter before starting a turn
const RING_SECONDS = 120;

class PCMPlayer extends AudioWorkletProcessor {
    constructor() {
        super();
        this.ring = new Float32Array(Math.ceil(sampleRate * RING_SECONDS));
        this.read = 0;
        this.write = 0;
        this.step = SOURCE_RATE / sampleRate;
        this.gain = 1;
        this.targetGain = 1;
        this.levelAcc = 0;
        this.levelCount = 0;
        this._resetTurn(0);
        this.port.onmessage = (e) => this._onMessage(e.data);
    }

    _resetTurn(turn) {
        this.turn = turn;
        this.read = this.write = 0;
        this.started = false;
        this.endMarked = false;
        this.played = 0;
        this.phase = 0;      // resampler position between input samples
        this.prev = 0;       // last input sample of the previous chunk
    }

    get buffered() {
        return (this.write - this.read + this.ring.length) % this.ring.length;
    }

    _push(pcm) {
        // Linear-interpolating resampler over [prev, ...pcm], continuous across chunks.
        const n = pcm.length;
        let pos = this.phase;
        while (pos < n) {
            const i = Math.floor(pos);
            const f = pos - i;
            const a = i === 0 ? this.prev : pcm[i - 1] / 32768;
            const b = pcm[i] / 32768;
            this.ring[this.write] = a + (b - a) * f;
            this.write = (this.write + 1) % this.ring.length;
            pos += this.step;
        }
        this.phase = pos - n;
        this.prev = pcm[n - 1] / 32768;
    }

    _onMessage(msg) {
        if (msg.type === 'pcm') {
            if (msg.turn < this.turn) return;               // stale audio
            if (msg.turn > this.turn) this._resetTurn(msg.turn);
            if (msg.pcm.length) this._push(msg.pcm);
        } else if (msg.type === 'end') {
            if (msg.turn === this.turn) this.endMarked = true;
        } else if (msg.type === 'flush') {
            const playedMs = Math.round(this.played / sampleRate * 1000);
            const turn = this.turn;
            const hadAudio = this.started || this.buffered > 0;
            this.read = this.write = 0;
            this.started = false;
            this.endMarked = false;
            this.targetGain = this.gain = 1;
            if (hadAudio) this.port.postMessage({ type: 'flushed', turn, playedMs });
        } else if (msg.type === 'duck') {
            this.targetGain = msg.gain;
        }
    }

    process(inputs, outputs) {
        const out = outputs[0][0];
        const ready = this.started || this.endMarked
            || this.buffered >= sampleRate * PREBUFFER_SECONDS;
        for (let k = 0; k < out.length; k++) {
            this.gain += (this.targetGain - this.gain) * 0.01;
            if (ready && this.buffered > 0) {
                if (!this.started) {
                    this.started = true;
                    this.port.postMessage({ type: 'started', turn: this.turn });
                }
                const s = this.ring[this.read] * this.gain;
                this.read = (this.read + 1) % this.ring.length;
                this.played++;
                out[k] = s;
                this.levelAcc += s * s;
            } else {
                out[k] = 0;
            }
            this.levelCount++;
        }
        if (this.started && this.endMarked && this.buffered === 0) {
            this.port.postMessage({
                type: 'ended', turn: this.turn,
                playedMs: Math.round(this.played / sampleRate * 1000),
            });
            this.started = false;
            this.endMarked = false;
        }
        if (this.levelCount >= sampleRate / 30) {           // ~30 level updates per second
            this.port.postMessage({ type: 'level', value: Math.sqrt(this.levelAcc / this.levelCount) });
            this.levelAcc = 0;
            this.levelCount = 0;
        }
        return true;
    }
}

registerProcessor('pcm-player', PCMPlayer);
