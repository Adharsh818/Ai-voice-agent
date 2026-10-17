"""
Which voice sounds more like a real receptionist, and how fast does it start?

    .\\.venv\\Scripts\\python.exe tools\\tts_compare.py
    .\\.venv\\Scripts\\python.exe tools\\tts_compare.py --sarvam-model bulbul:v4-flash --speakers ritu_en_conversational
    .\\.venv\\Scripts\\python.exe tools\\tts_compare.py --skip-elevenlabs

The owner (17 Oct) asked whether Sarvam's Bulbul voices are better than
ElevenLabs. Each voice speaks the same Emma lines (a greeting, an Indian name,
a phone number read back in groups, a date and time, a branch name); every line
is timed and saved as a WAV, and tts_compare/<time>/index.html plays them side
by side. Emma need not be running.

ElevenLabs streams, so it has a first-audio time as well as a whole-line time.
Sarvam is called through its REST endpoint, which returns the whole line, so
only its whole-line time is measured; its WebSocket streaming would start
sooner (Sarvam says under 250 ms), which a later trial can measure.

Uses ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID / ELEVENLABS_MODEL and
SARVAM_API_KEY from .env. ElevenLabs spends about 600 characters per run
(the free plan has 10,000 a month); --skip-elevenlabs spends none.
"""

import argparse
import asyncio
import base64
import html
import io
import os
import statistics
import sys
import time
import wave
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from tts_elevenlabs import ElevenLabsTTS  # noqa: E402

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
RATE = 16000

LINES = [
    f"Hi, this is Emma at {config.CLINIC_SHORT_NAME}, how can I help?",
    "Sure, I can help with that. Can I get your name first?",
    "Thanks, Priya. And what's the best mobile number to reach you on?",
    "That's nine eight four five zero, one two three four five. Is that right?",
    "Let me just check that for you.",
    "Good news, Monday the 12th at 10:30 is free with Dr Rao at Jayanagar.",
    "That's a cleaning for Sriranjani on Monday at 10:30. Shall I book it?",
    "Lovely, you're all set. We'll see you on Monday. Take care!",
]


def _wav(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def elevenlabs_line(tts: ElevenLabsTTS, text: str) -> dict:
    started = time.monotonic()
    first, chunks = None, []
    async for chunk in tts.synthesize_stream(text):
        first = first or time.monotonic()
        chunks.append(chunk)
    pcm = b"".join(chunks)
    pcm = pcm[: len(pcm) - len(pcm) % 2]
    if not pcm:
        return {"error": "no audio (see the log above)"}
    return {"first_s": first - started, "whole_s": time.monotonic() - started, "wav": _wav(pcm)}


async def sarvam_line(client: httpx.AsyncClient, key: str, model: str, speaker: str, text: str) -> dict:
    body = {"text": text, "target_language_code": "en-IN", "speaker": speaker, "model": model,
            "speech_sample_rate": RATE}
    started = time.monotonic()
    try:
        response = await client.post(SARVAM_TTS_URL, json=body, headers={"api-subscription-key": key})
    except httpx.HTTPError as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    whole = time.monotonic() - started
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}: {response.text[:300]}"}
    data = response.json()
    audios = data.get("audios") or ([data["audio"]] if data.get("audio") else [])
    if not audios:
        return {"error": f"no audio in the answer: {str(data)[:200]}"}
    return {"whole_s": whole, "wav": base64.b64decode(audios[0])}


def _median(results: list, key: str):
    values = [r[key] for r in results if r.get(key) is not None]
    return statistics.median(values) if values else None


def _page(voices: dict, out: Path) -> Path:
    rows = []
    for i, line in enumerate(LINES):
        cells = []
        for name, results in voices.items():
            r = results[i]
            if r.get("error"):
                cells.append(f"<td class=err>{html.escape(r['error'])}</td>")
            else:
                timing = f"{r['first_s']:.2f} s to start, " if r.get("first_s") is not None else ""
                cells.append(f"<td><audio controls preload=none src='{html.escape(r['file'])}'></audio>"
                             f"<div class=t>{timing}{r['whole_s']:.2f} s whole line</div></td>")
        rows.append(f"<tr><th>{html.escape(line)}</th>{''.join(cells)}</tr>")
    heads = "".join(f"<th>{html.escape(name)}</th>" for name in voices)
    page = out / "index.html"
    page.write_text(
        "<!doctype html><meta charset=utf-8><title>Emma voice comparison</title>"
        "<style>body{font:15px system-ui,sans-serif;margin:24px;color:#1d1d1f}table{border-collapse:collapse}"
        "th,td{border:1px solid #d2d2d7;padding:8px;vertical-align:top;text-align:left}"
        "th{max-width:320px;font-weight:500}.t{color:#6e6e73;font-size:13px;margin-top:4px}"
        ".err{color:#b00020;font-size:13px;max-width:260px}</style>"
        "<h1>Emma: which voice sounds like a real receptionist?</h1>"
        "<p>Same lines, same order. Listen for warmth, the name, the number read in groups, and the dates.</p>"
        f"<table><tr><th>Line</th>{heads}</tr>{''.join(rows)}</table>",
        encoding="utf-8", newline="\n")
    return page


async def compare(args, out: Path, client: httpx.AsyncClient) -> dict:
    voices = {}
    if not args.skip_elevenlabs:
        if not config.ELEVENLABS_API_KEY:
            print("No ELEVENLABS_API_KEY in .env: skipping ElevenLabs.")
        else:
            tts = ElevenLabsTTS(api_key=config.ELEVENLABS_API_KEY, voice_id=config.ELEVENLABS_VOICE_ID,
                                model_id=config.ELEVENLABS_MODEL, output_format=f"pcm_{RATE}", client=client)
            voices[f"ElevenLabs {config.ELEVENLABS_MODEL}"] = [await elevenlabs_line(tts, t) for t in LINES]
    key = os.getenv("SARVAM_API_KEY", "")
    if not key:
        print("No SARVAM_API_KEY in .env: skipping Sarvam.")
    else:
        for speaker in args.speakers:
            voices[f"Sarvam {args.sarvam_model} {speaker}"] = [
                await sarvam_line(client, key, args.sarvam_model, speaker, t) for t in LINES]
    for name, results in voices.items():
        slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
        for i, r in enumerate(results):
            if r.get("wav"):
                r["file"] = f"{slug}-{i + 1}.wav"
                (out / r["file"]).write_bytes(r.pop("wav"))
    return voices


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sarvam-model", default="bulbul:v3", help="bulbul:v3 (default) or bulbul:v4-flash")
    parser.add_argument("--speakers", nargs="+", default=["priya", "ritu"],
                        help="Sarvam speakers to try (female bulbul:v3 voices by default)")
    parser.add_argument("--skip-elevenlabs", action="store_true", help="spend no ElevenLabs characters")
    parser.add_argument("--out", default=str(ROOT / "tts_compare"), help="where the WAVs and page go")
    args = parser.parse_args(argv)

    out = Path(args.out) / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    async def run():
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            return await compare(args, out, client)

    voices = asyncio.run(run())
    if not voices:
        print("Nothing to compare: add the API keys to .env.")
        return 1
    print(f"\n  {'voice':<38} {'start p50':>9} {'whole p50':>9} {'failed':>6}")
    for name, results in voices.items():
        first, whole = _median(results, "first_s"), _median(results, "whole_s")
        failed = sum(1 for r in results if r.get("error"))
        print(f"  {name:<38} {'-' if first is None else f'{first:.2f}s':>9} "
              f"{'-' if whole is None else f'{whole:.2f}s':>9} {failed:>6}")
        for r in results:
            if r.get("error"):
                print(f"      {r['error']}")
                break
    print(f"\nListen: {_page(voices, out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
