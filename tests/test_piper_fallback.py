"""
The backup voice (plan 5.7): when ElevenLabs fails, or answers with silence as
it did when its quota ran out on 1 Oct, Piper says the sentence and the rest
of the call uses Piper.
"""

import asyncio
import os
import unittest

import config
from speech import Speaker
from tts_elevenlabs import PCMStream


class Transport:
    def __init__(self):
        self.audio = 0

    async def send_audio(self, turn_id, pcm):
        self.audio += len(pcm)


class SilentTTS:
    """ElevenLabs with no characters left: the stream opens, then ends with no audio."""

    def __init__(self):
        self.opened = 0

    async def open_stream(self, text):
        self.opened += 1
        stream = PCMStream()
        stream.finish()
        return stream


class BrokenTTS:
    async def open_stream(self, text):
        raise ConnectionError("ElevenLabs unreachable")


class BackupTTS:
    ready = True

    def __init__(self):
        self.said = []

    async def open_stream(self, text):
        self.said.append(text)
        stream = PCMStream()
        stream.feed(bytes(3200))
        stream.finish()
        return stream


class SilentOnceTTS(BackupTTS):
    """The live socket answers one sentence with silence, then works again."""

    async def open_stream(self, text):
        if not self.said:
            self.said.append(None)
            stream = PCMStream()
            stream.finish()
            return stream
        return await super().open_stream(text)


class FallbackTests(unittest.TestCase):
    def run_speaker(self, primary, backup, texts, http=None):
        transport = Transport()
        speaker = Speaker(transport, None, primary, http, backup)

        async def run():
            for i, text in enumerate(texts, start=1):
                await speaker.speak(i, text)

        asyncio.run(run())
        return speaker, transport

    def test_a_silent_live_voice_falls_back_and_stays_on_the_backup(self):
        primary, backup = SilentTTS(), BackupTTS()
        speaker, transport = self.run_speaker(primary, backup, ["We do braces at Indiranagar.", "Which suits you?"])
        self.assertEqual(backup.said, ["We do braces at Indiranagar.", "Which suits you?"])
        self.assertEqual(primary.opened, 1)          # not asked again once it had gone silent
        self.assertTrue(speaker.degraded)
        self.assertEqual(transport.audio, 6400)

    def test_one_silent_answer_is_retried_in_the_same_voice_before_the_backup(self):
        # Owner, 23 Oct: "it is switching between 2 voices". A sentence the live socket answered with
        # silence goes to ElevenLabs over HTTP (the same voice) first; the call stays in that voice.
        live, http, backup = SilentOnceTTS(), BackupTTS(), BackupTTS()
        speaker, transport = self.run_speaker(live, backup, ["Hmm.", "Which suits you?"], http=http)
        self.assertEqual(http.said, ["Hmm."])
        self.assertEqual(live.said, [None, "Which suits you?"])
        self.assertEqual(backup.said, [])
        self.assertFalse(speaker.degraded)
        self.assertEqual(transport.audio, 3200 * 2)            # the second sentence was the live voice's

    def test_silent_on_both_paths_falls_back_to_the_backup(self):
        http, backup = SilentTTS(), BackupTTS()
        speaker, _ = self.run_speaker(SilentTTS(), backup, ["Sure, the 2nd.", "Which suits you?"], http=http)
        self.assertEqual(backup.said, ["Sure, the 2nd.", "Which suits you?"])
        self.assertEqual(http.opened, 1)
        self.assertTrue(speaker.degraded)

    def test_an_unreachable_live_voice_falls_back(self):
        backup = BackupTTS()
        _, transport = self.run_speaker(BrokenTTS(), backup, ["Sure, the 2nd."])
        self.assertEqual(backup.said, ["Sure, the 2nd."])
        self.assertEqual(transport.audio, 3200)

    def test_a_backup_still_loading_is_not_used(self):
        backup = BackupTTS()
        backup.ready = False
        _, transport = self.run_speaker(SilentTTS(), backup, ["Hello."])
        self.assertEqual(backup.said, [])
        self.assertEqual(transport.audio, 0)


@unittest.skipUnless(os.path.exists(os.path.join(config.BASE_DIR, "models", "piper", f"{config.PIPER_VOICE}.onnx")),
                     "Piper voice model not downloaded")
class RealPiperTests(unittest.TestCase):
    def test_piper_speaks_16k_pcm(self):
        import tts_piper

        async def run():
            tts = tts_piper.from_config()
            await tts.warm()
            return await tts.synthesize("Sure, I can help with that.")

        pcm = asyncio.run(run())
        seconds = len(pcm) / 2 / config.TTS_SAMPLE_RATE
        self.assertGreater(seconds, 0.8)
        self.assertLess(seconds, 4.0)


if __name__ == "__main__":
    unittest.main()
