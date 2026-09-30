"""
Development-only capture of what the caller said (DEV_CAPTURE_AUDIO=true).

Per call, two files in captures/:
    <stamp>-<call_id>.wav     caller audio exactly as sent to STT (16 kHz mono PCM16)
    <stamp>-<call_id>.jsonl   one line per utterance STT reported: where it ended
                              in the audio, which event ended it, and the text

These feed the Deepgram Nova-3 vs Flux comparison and tools/replay.py. They
contain people's voices, so capture is off by default and the folder is
git-ignored.
"""

import json
import logging
import os
import wave

import clock

logger = logging.getLogger(__name__)


class CallCapture:
    def __init__(self, directory: str, call_id: str, sample_rate: int = 16000):
        os.makedirs(directory, exist_ok=True)
        stem = f"{clock.now().strftime('%Y%m%d-%H%M%S')}-{call_id}"
        self.path = os.path.join(directory, stem)
        self.sample_rate = sample_rate
        self._bytes = 0
        self._wav = wave.open(self.path + ".wav", "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(sample_rate)
        self._events = open(self.path + ".jsonl", "a", encoding="utf-8")
        logger.info("Capturing caller audio to %s.wav", self.path)

    @property
    def seconds(self) -> float:
        return self._bytes / (self.sample_rate * 2)

    def audio(self, pcm: bytes):
        if self._wav is None or not pcm:
            return
        # writeframes (not writeframesraw) keeps the header valid after each
        # chunk, so a crash still leaves a playable file.
        self._wav.writeframes(pcm)
        self._bytes += len(pcm)

    def utterance(self, text: str, end_sec, source: str):
        if self._events is None:
            return
        self._events.write(json.dumps({
            "received_at_sec": round(self.seconds, 3),
            "end_sec": end_sec,
            "source": source,
            "text": text,
        }) + "\n")
        self._events.flush()

    def close(self):
        for handle in (self._wav, self._events):
            try:
                if handle is not None:
                    handle.close()
            except Exception as exc:
                logger.debug("capture close failed: %s", exc)
        self._wav = self._events = None
