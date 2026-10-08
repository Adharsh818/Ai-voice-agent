"""
Replay a recorded caller (captures/*.wav from DEV_CAPTURE_AUDIO) through the
live listening pipeline, in real time, and score where the turns ended:

    python tools/replay.py captures/20261006-092434-fee33507.wav [more.wav ...]
    python tools/replay.py --flat-watchdog 1.0 captures/....wav     # the pre-6 Oct watchdog

The audio goes to Deepgram exactly as on a call (16 kHz PCM, 20 ms frames,
real-time pacing) and through call_session's turn detector; nothing is said
back. For a recording of the 30-line test set (docs/STT_TEST_SET.md) it reports:

    cut lines   script lines whose words ended up in two or more turns
                (the caller was cut off: Emma would have answered half a line)
    merged      turns that contain words of two or more lines
    end -> turn ms from the caller's last word to the turn being committed
    word errors a rough word error rate against the script

It needs DEEPGRAM_API_KEY and spends a few minutes of Deepgram audio per run.
"""

import argparse
import asyncio
import difflib
import json
import os
import re
import statistics
import sys
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
import stt_deepgram  # noqa: E402
from call_session import CallSession, Services  # noqa: E402

FRAME_BYTES = 640                     # 20 ms at 16 kHz mono PCM16


class _Quiet:
    """A transport that discards everything (Emma never speaks in a replay)."""

    async def send_audio(self, *_):
        pass

    async def send_event(self, *_):
        pass

    async def flush(self, *_):
        pass

    async def close(self):
        pass


def _norm(text: str) -> list:
    text = (text or "").lower().replace("-", " ")
    return re.sub(r"[^a-z0-9' ]", " ", text).split()


def _script() -> list:
    data = json.load(open(os.path.join(ROOT, "tests", "data", "stt_script.json"), encoding="utf-8"))
    return [line["say"] for line in data["lines"]]


def _clinic_terms() -> list:
    """The doctor, branch and service names a real call loads (call_session.clinic_keyterms)."""
    import db
    from call_session import clinic_keyterms
    return db.get_db().run_sync(clinic_keyterms)


async def replay(path: str, flat_watchdog: float = None, model: str = None, language: str = None,
                 provider: str = "deepgram") -> dict:
    with wave.open(path) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "16 kHz mono PCM16 only"
        pcm = w.readframes(w.getnframes())

    def stt_factory(**callbacks):
        if provider == "sarvam":
            import stt_sarvam
            return stt_sarvam.SarvamSTT(api_key=os.getenv("SARVAM_API_KEY", ""), model=model or "saaras:v4",
                                        language=language or "en-IN", keyterms=config.DEEPGRAM_KEYTERMS,
                                        **callbacks)
        if flat_watchdog is not None:
            callbacks.pop("watchdog_for", None)
            callbacks.pop("quiet_for", None)
        return stt_deepgram.DeepgramSTT(
            api_key=config.DEEPGRAM_API_KEY, model=model or config.DEEPGRAM_MODEL,
            language=language or config.DEEPGRAM_LANGUAGE,
            endpointing_ms=config.DEEPGRAM_ENDPOINTING_MS, utterance_end_ms=config.DEEPGRAM_UTTERANCE_END_MS,
            keyterms=config.DEEPGRAM_KEYTERMS, watchdog_s=flat_watchdog or stt_deepgram.WATCHDOG_S, **callbacks)

    saved_capture, config.DEV_CAPTURE_AUDIO = config.DEV_CAPTURE_AUDIO, False
    try:
        terms = _clinic_terms()
        session = CallSession(_Quiet(), Services(stt_factory=stt_factory, tts=None, keyterms=lambda: terms),
                              call_id="replay", listen_only=True)
    finally:
        config.DEV_CAPTURE_AUDIO = saved_capture
    turns, events = [], []
    start = None

    async def on_turn(text, end_wall, source="text", received=None, verdict=None):
        turns.append({"text": text, "at": time.perf_counter() - start, "source": source,
                      "verdict": verdict.label if verdict is not None else None,
                      "word_end": (end_wall - start) if end_wall else None})

    session._start_turn = on_turn
    original_end = session._on_utterance_end

    async def on_end(text, end_sec, source="speech_final", **kw):
        events.append({"text": text, "end_sec": end_sec, "source": source, "at": time.perf_counter() - start})
        return await original_end(text, end_sec, source, **kw)

    session._on_utterance_end = on_end
    await session.start()
    session.stt.on_utterance_end = on_end
    start = time.perf_counter()
    silence = b"\x00" * FRAME_BYTES
    frames = [pcm[i:i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES)] + [silence] * 150
    for i, frame in enumerate(frames):
        target = start + i * 0.02
        delay = target - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        await session.on_audio(frame)
    await asyncio.sleep(3.0)
    await session.close()
    return {"turns": turns, "events": events}


def score(turns: list, lines: list) -> dict:
    ref, ref_line = [], []
    for i, line in enumerate(lines):
        for w in _norm(line):
            ref.append(w)
            ref_line.append(i)
    hyp, hyp_turn = [], []
    for t_index, t in enumerate(turns):
        for w in _norm(t["text"]):
            hyp.append(w)
            hyp_turn.append(t_index)
    sm = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    turns_of_line: dict = {}
    lines_of_turn: dict = {}
    errors = 0
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal":
            for k in range(a2 - a1):
                turns_of_line.setdefault(ref_line[a1 + k], set()).add(hyp_turn[b1 + k])
                lines_of_turn.setdefault(hyp_turn[b1 + k], set()).add(ref_line[a1 + k])
        else:
            errors += max(a2 - a1, b2 - b1)
    cut = {i: sorted(ts) for i, ts in turns_of_line.items() if len(ts) > 1}
    merged = {t: sorted(ls) for t, ls in lines_of_turn.items() if len(ls) > 1}
    lags = [1000 * (t["at"] - t["word_end"]) for t in turns if t.get("word_end") is not None]
    return {"cut": cut, "merged": merged, "lags": sorted(lags), "errors": errors, "ref_words": len(ref)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("wav", nargs="+")
    parser.add_argument("--flat-watchdog", type=float, default=None,
                        help="use a fixed STT watchdog (seconds) instead of the per-words estimate")
    parser.add_argument("--show", action="store_true", help="print every turn")
    parser.add_argument("--provider", choices=("deepgram", "sarvam"), default="deepgram",
                        help="speech recogniser to replay through (sarvam needs SARVAM_API_KEY)")
    parser.add_argument("--model", help="model (Deepgram: default DEEPGRAM_MODEL, e.g. nova-2; Sarvam: saaras:v4)")
    parser.add_argument("--language", help="Deepgram language (default DEEPGRAM_LANGUAGE), e.g. en-IN")
    args = parser.parse_args()
    if args.provider == "deepgram" and not config.DEEPGRAM_API_KEY:
        sys.exit("DEEPGRAM_API_KEY is not set")
    if args.provider == "sarvam" and not os.getenv("SARVAM_API_KEY"):
        sys.exit("SARVAM_API_KEY is not set in .env")
    lines = _script()
    for path in args.wav:
        out = asyncio.run(replay(path, args.flat_watchdog, args.model, args.language, args.provider))
        s = score(out["turns"], lines)
        lags = s["lags"]
        p = lambda q: f"{lags[int(q * (len(lags) - 1))]:.0f}" if lags else "-"
        mode = f"flat watchdog {args.flat_watchdog:.2f}s" if args.flat_watchdog else "voice-gated end of turn"
        name = args.model or ("saaras:v4" if args.provider == "sarvam" else config.DEEPGRAM_MODEL)
        print(f"\n{os.path.basename(path)}  ({args.provider} {name}, {mode})")
        print(f"  turns {len(out['turns'])} for {len(lines)} lines; cut lines {len(s['cut'])}; merged turns {len(s['merged'])}; "
              f"word errors ~{100 * s['errors'] / s['ref_words']:.0f}%")
        print(f"  last word -> turn committed: p50 {p(.5)} ms, p90 {p(.9)} ms")
        for i, ts in sorted(s["cut"].items()):
            print(f"    cut: line {i + 1} {lines[i]!r} -> " + " | ".join(repr(out['turns'][t]['text']) for t in ts))
        if args.show:
            for t in out["turns"]:
                print(f"    {t['at']:6.1f}s {t['source']:13} {t['verdict'] or '':28} {t['text']}")


if __name__ == "__main__":
    main()
