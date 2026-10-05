"""
Download a Piper voice for the backup TTS into models/piper/ (git-ignored).

    python tools/fetch_piper_voice.py                       # config.PIPER_VOICE
    python tools/fetch_piper_voice.py en_US-kristin-medium  # another voice

Voices: https://huggingface.co/rhasspy/piper-voices (about 63 MB each).
"""

import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def fetch(name: str):
    lang, speaker, quality = name.split("-", 2)
    folder = os.path.join(config.BASE_DIR, "models", "piper")
    os.makedirs(folder, exist_ok=True)
    for ext in (".onnx", ".onnx.json"):
        dest = os.path.join(folder, name + ext)
        if os.path.exists(dest):
            print("have", dest)
            continue
        url = f"{BASE}/{lang.split('_')[0]}/{lang}/{speaker}/{quality}/{name}{ext}"
        with httpx.stream("GET", url, follow_redirects=True, timeout=300) as response:
            response.raise_for_status()
            with open(dest + ".part", "wb") as fh:
                for chunk in response.iter_bytes(1 << 20):
                    fh.write(chunk)
        os.replace(dest + ".part", dest)
        print("saved", dest)


if __name__ == "__main__":
    fetch(sys.argv[1] if len(sys.argv) > 1 else config.PIPER_VOICE)
