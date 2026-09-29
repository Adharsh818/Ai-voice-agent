/**
 * Mic capture: native-rate Float32 -> 16 kHz Int16 PCM in 20 ms frames.
 *
 * 20 ms (320 samples) matches telephony framing and gets audio to the
 * recognizer ~80 ms sooner than the previous 100 ms frames. Each frame also
 * carries its RMS level for the orb animation and the local barge-in cue.
 */
class PCMCaptureProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();
        const processorOptions = options.processorOptions || {};
        this._nativeSampleRate = processorOptions.nativeSampleRate || sampleRate;
        this._targetSampleRate = 16000;
        this._ratio = this._nativeSampleRate / this._targetSampleRate;

        // Buffers preserve resampling phase between 128-sample render blocks;
        // resetting it per block would shift the effective sample rate.
        this._inputBuffer = [];
        this._nextInputPosition = 0;
        this._pcmBuffer = [];

        this._chunkSize = 320; // 20 ms at 16 kHz
    }

    _toInt16(s) {
        const clamped = Math.max(-1, Math.min(1, s));
        return clamped < 0 ? clamped * 0x8000 : clamped * 0x7FFF;
    }

    process(inputs) {
        const input = inputs[0];
        if (!input || input.length === 0) return true;
        const channelData = input[0];
        if (!channelData || channelData.length === 0) return true;

        for (let i = 0; i < channelData.length; i++) {
            this._inputBuffer.push(channelData[i]);
        }

        while (this._nextInputPosition + 1 < this._inputBuffer.length) {
            const i0 = Math.floor(this._nextInputPosition);
            const fraction = this._nextInputPosition - i0;
            const sample = this._inputBuffer[i0] * (1 - fraction)
                + this._inputBuffer[i0 + 1] * fraction;
            this._pcmBuffer.push(this._toInt16(sample));
            this._nextInputPosition += this._ratio;
        }

        const consumed = Math.floor(this._nextInputPosition);
        if (consumed > 0) {
            this._inputBuffer.splice(0, consumed);
            this._nextInputPosition -= consumed;
        }

        // Silence is sent too: the recognizer's endpointer needs it.
        while (this._pcmBuffer.length >= this._chunkSize) {
            const int16Array = new Int16Array(this._pcmBuffer.splice(0, this._chunkSize));
            let sumSq = 0;
            for (let k = 0; k < int16Array.length; k++) {
                const norm = int16Array[k] / 32768.0;
                sumSq += norm * norm;
            }
            const rms = Math.sqrt(sumSq / int16Array.length);
            this.port.postMessage({ type: 'audio', buffer: int16Array.buffer, rms }, [int16Array.buffer]);
        }
        return true;
    }
}

registerProcessor('pcm-capture-processor', PCMCaptureProcessor);
