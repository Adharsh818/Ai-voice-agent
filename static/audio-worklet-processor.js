/**
 * Audio Worklet Processor — PCM capture with 16kHz downsampling.
 *
 * Converts incoming Float32 mic audio to 16kHz Int16 PCM chunks (~100ms)
 * for real-time WebSocket streaming to Deepgram STT.
 */
class PCMCaptureProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();
        const processorOptions = options.processorOptions || {};
        this._nativeSampleRate = processorOptions.nativeSampleRate || 16000;
        this._targetSampleRate = 16000;

        // Downsample factor (e.g., 44100 / 16000 = 2.75625)
        this._ratio = this._nativeSampleRate / this._targetSampleRate;

        // Buffers preserve resampling phase between 128-sample render blocks.
        // Resetting the phase every block changes the effective sample rate on
        // common 44.1 kHz devices and makes speech recognition less accurate.
        this._inputBuffer = [];
        this._nextInputPosition = 0;
        this._pcmBuffer = [];

        // 100ms of 16kHz audio = 1600 Int16 samples
        this._chunkSize = 1600;

    }

    /**
     * Convert Float32 sample [-1.0, 1.0] to Int16 [-32768, 32767]
     */
    _toInt16(s) {
        const clamped = Math.max(-1, Math.min(1, s));
        return clamped < 0 ? clamped * 0x8000 : clamped * 0x7FFF;
    }

    process(inputs, outputs, parameters) {
        const input = inputs[0];
        if (!input || input.length === 0) return true;

        const channelData = input[0];
        if (!channelData || channelData.length === 0) return true;

        for (let i = 0; i < channelData.length; i++) {
            this._inputBuffer.push(channelData[i]);
        }

        // Linear interpolation is sufficient here and, unlike the previous
        // per-block decimation, keeps timing continuous across callbacks.
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

        // Send every ~100ms frame.  Silence is meaningful to the recognizer's
        // endpoint detector; suppressing quiet frames loses soft speech and
        // makes pauses appear longer than they really are.
        while (this._pcmBuffer.length >= this._chunkSize) {
            const chunk = this._pcmBuffer.splice(0, this._chunkSize);
            const int16Array = new Int16Array(chunk);

            // Compute RMS energy for VAD
            let sumSq = 0;
            for (let k = 0; k < int16Array.length; k++) {
                const norm = int16Array[k] / 32768.0;
                sumSq += norm * norm;
            }
            const rms = Math.sqrt(sumSq / int16Array.length);

            this.port.postMessage(
                { type: 'audio', buffer: int16Array.buffer, rms: rms },
                [int16Array.buffer]
            );
        }

        return true;
    }
}

registerProcessor('pcm-capture-processor', PCMCaptureProcessor);
