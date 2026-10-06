"""vad.VoiceActivity and the voice-gated end of turn (6 Oct replay of the owner's test lines)."""

import array
import asyncio
import math
import time
import unittest

import stt_deepgram
import vad
from stt_deepgram import DeepgramSTT
from test_realtime_stt import Dialer, final, interim
from test_realtime_support import patched, settle


def tone(amplitude: int, ms: int = 20) -> bytes:
    n = 16 * ms
    return array.array("h", (int(amplitude * math.sin(2 * math.pi * 220 * i / 16000)) for i in range(n))).tobytes()


SILENCE = b"\x00" * 640


class VoiceActivityTests(unittest.TestCase):
    def test_speech_is_voiced_and_silence_is_not(self):
        clock = [0.0]
        v = vad.VoiceActivity(now=lambda: clock[0])
        for _ in range(10):
            self.assertFalse(v.feed(SILENCE))
        self.assertEqual(v.quiet_for(), 1e9)                 # never spoke yet
        results = [v.feed(tone(3000)) for _ in range(10)]
        self.assertFalse(results[0])                          # one loud frame is not speech yet
        self.assertTrue(all(results[1:]))
        self.assertAlmostEqual(v.quiet_for(1.5), 1.5)       # last voiced frame was at t = 0

    def test_a_single_click_is_not_speech(self):
        v = vad.VoiceActivity()
        v.feed(SILENCE)
        self.assertFalse(v.feed(tone(5000)))
        self.assertFalse(v.feed(SILENCE))
        self.assertIsNone(v.last_voice)

    def test_steady_noise_stops_counting_as_talk(self):
        v = vad.VoiceActivity()
        for _ in range(5):
            v.feed(tone(100))
        noise = tone(800)                                    # a fan starts up
        voiced = [v.feed(noise) for _ in range(3000)]
        self.assertTrue(voiced[10])
        self.assertFalse(voiced[-1])                          # the floor caught up within a minute
        self.assertTrue(v.feed(tone(8000)) or v.feed(tone(8000)))   # louder speech still counts


class VoiceGatedWatchdogTests(unittest.TestCase):
    def run_stt(self, messages, quiet, voice_until=None, wait=0.8):
        heard, dialer = [], Dialer()

        async def on_end(text, end_sec, source, start_sec=None):
            heard.append((text, source, time.monotonic()))

        async def run():
            stt = DeepgramSTT(api_key="k", on_utterance_end=on_end, watchdog_s=1.0,
                              watchdog_for=lambda t: 0.35, quiet_for=quiet, voice_until=voice_until)
            await stt.connect()
            start = time.monotonic()
            for m in messages:
                dialer.sockets[0].push(m)
            await settle(wait)
            await stt.close()
            return start

        with patched(stt_deepgram.websockets, connect=dialer):
            start = asyncio.run(run())
        return [(t, s, round(at - start, 2)) for t, s, at in heard]

    def test_still_talking_holds_the_turn_until_the_hard_limit(self):
        heard = self.run_stt([final("Can I move my cleaning", 0.1, 1.2, speech_final=False)],
                             quiet=lambda: 0.0, wait=0.8)
        self.assertEqual(heard, [])                           # voice on the line: not done
        with patched(stt_deepgram, WATCHDOG_MAX_S=0.5):
            heard = self.run_stt([final("Can I move my cleaning", 0.1, 1.2, speech_final=False)],
                                 quiet=lambda: 0.0, wait=0.9)
        self.assertEqual([(t, s) for t, s, _ in heard], [("Can I move my cleaning", "watchdog")])   # noise fallback

    def test_quiet_line_ends_the_turn_soon(self):
        heard = self.run_stt([final("yes", 0.1, 0.4, speech_final=False)], quiet=lambda: 5.0)
        self.assertEqual([(t, s) for t, s, _ in heard], [("yes", "watchdog")])
        self.assertLess(heard[0][2], 0.6)

    def test_waits_for_the_words_to_reach_the_end_of_the_voice(self):
        # The line went quiet at 2.0 s of audio but Deepgram's words only reach 1.2 s ("I need a root").
        heard = self.run_stt([final("I need a root", 0.1, 1.2, speech_final=False)], quiet=lambda: 5.0,
                             voice_until=lambda: 2.0, wait=0.8)
        self.assertEqual(heard, [])
        heard = self.run_stt([final("I need a root", 0.1, 1.2, speech_final=False),
                              final("canal.", 1.3, 1.9, speech_final=False)], quiet=lambda: 5.0,
                             voice_until=lambda: 2.0, wait=0.8)
        self.assertEqual([(t, s) for t, s, _ in heard], [("I need a root canal.", "watchdog")])

    def test_words_already_handed_over_are_not_repeated(self):
        heard = self.run_stt([interim("I need a root", 0.1, 1.2),
                              ], quiet=lambda: 5.0, wait=0.6)
        self.assertEqual([t for t, _, _ in heard], ["I need a root"])

        stt = DeepgramSTT(api_key="k")
        stt._last_emitted_end = 1.2
        words = [{"word": "i", "punctuated_word": "I", "start": 0.1, "end": 0.3},
                 {"word": "root", "punctuated_word": "root", "start": 0.9, "end": 1.2},
                 {"word": "canal", "punctuated_word": "canal.", "start": 1.3, "end": 1.9}]
        kept, text = stt._drop_reported_words(words, "I root canal.")
        self.assertEqual(text, "canal.")
        self.assertEqual(len(kept), 1)

    def test_a_repeated_last_word_is_dropped(self):
        stt = DeepgramSTT(api_key="k")
        stt._last_emitted = (stt_deepgram._term_key_words("Sorry. Can you repeat that?"), time.monotonic())
        self.assertTrue(stt._repeats_last("that?"))
        self.assertFalse(stt._repeats_last("this"))
        self.assertFalse(stt._repeats_last("hold on a second"))


if __name__ == "__main__":
    unittest.main()
