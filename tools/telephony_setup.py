"""
Set up phone calls through Asterisk (docs/TELEPHONY.md), the Windows half.

    python tools/telephony_setup.py            add the telephony settings to .env (only the ones
                                               missing; nothing already there is changed) and
                                               render the Asterisk configs into telephony/build/
    python tools/telephony_setup.py --dry-run  show what it would add, change nothing

Secrets (the dialplan's key, the manager password, the two SIP passwords) are
generated once and kept in .env, so running it again renders the same configs.
Then, in Ubuntu (WSL2):

    sudo bash /mnt/a/Voice-Agent/telephony/install_asterisk.sh

and restart Emma. The SIP passwords for the softphone accounts are printed at the end.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
TEMPLATES = ROOT / "telephony" / "asterisk"
BUILD = ROOT / "telephony" / "build"

DEFAULTS = {
    "TELEPHONY_ENABLED": "true",
    "AUDIOSOCKET_PORT": "9092",
    "TELEPHONY_FRONT_DESK": "PJSIP/1002",
    "TELEPHONY_PATIENT_PHONE": "PJSIP/1001",
    "AMI_USER": "emma",
    # The demo caller's caller ID: Neha Kapoor's DEMO number from docs/DEMO_SCRIPT.md.
    "TELEPHONY_CALLER_NUMBER": "9845022222",
}
GENERATED = ("TELEPHONY_SECRET", "AMI_SECRET", "TELEPHONY_SIP_PASSWORD_1001", "TELEPHONY_SIP_PASSWORD_1002")


def read_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
            if m and not line.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def render(values: dict) -> dict:
    """{filename: text} with every {{NAME}} filled; fails loudly on one left over."""
    mapping = {
        "SERVER_PORT": values.get("SERVER_PORT", "8000"),
        "AUDIOSOCKET_PORT": values["AUDIOSOCKET_PORT"],
        "TELEPHONY_SECRET": values["TELEPHONY_SECRET"],
        "TELEPHONY_FRONT_DESK": values["TELEPHONY_FRONT_DESK"],
        "AMI_USER": values["AMI_USER"],
        "AMI_SECRET": values["AMI_SECRET"],
        "SIP_1001_PASSWORD": values["TELEPHONY_SIP_PASSWORD_1001"],
        "SIP_1002_PASSWORD": values["TELEPHONY_SIP_PASSWORD_1002"],
        "CALLER_NUMBER": values["TELEPHONY_CALLER_NUMBER"],
    }
    out = {}
    for template in sorted(TEMPLATES.glob("*.conf")):
        text = template.read_text(encoding="utf-8")
        text = re.sub(r"\{\{([A-Z0-9_]+)\}\}", lambda m: mapping[m.group(1)], text)
        out[template.name] = text
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tools/telephony_setup.py", description=__doc__.splitlines()[1])
    parser.add_argument("--dry-run", action="store_true", help="show what would be added; change nothing")
    args = parser.parse_args(argv)

    env = read_env(ENV)
    added = {k: v for k, v in DEFAULTS.items() if k not in env}
    for key in GENERATED:
        if not env.get(key):
            added[key] = secrets.token_urlsafe(18)
    values = {**DEFAULTS, **env, **added}
    configs = render(values)

    print("Adding to .env:" if added else ".env already has every telephony setting.")
    for key in added:
        shown = "(generated)" if key in GENERATED else added[key]
        print(f"  {key}={shown}")
    if args.dry_run:
        print(f"Would render {', '.join(configs)} into {BUILD.relative_to(ROOT)}/ (dry run: nothing written).")
        return 0
    if added:
        block = "\n# Phone calls through Asterisk (tools/telephony_setup.py, docs/TELEPHONY.md)\n" + \
            "".join(f"{k}={v}\n" for k, v in added.items())
        existing = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
        with open(ENV, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(existing.rstrip("\n") + "\n" + block)
    BUILD.mkdir(parents=True, exist_ok=True)
    for name, text in configs.items():
        with open(BUILD / name, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    print(f"Rendered {', '.join(configs)} into {BUILD.relative_to(ROOT)}/")
    print("\nSoftphone accounts (server 127.0.0.1, port 5060, UDP):")
    print(f"  1001 (caller / patient)  password {values['TELEPHONY_SIP_PASSWORD_1001']}")
    print(f"  1002 (front desk)        password {values['TELEPHONY_SIP_PASSWORD_1002']}")
    print("\nNext, in Ubuntu (WSL2):  sudo bash /mnt/a/Voice-Agent/telephony/install_asterisk.sh")
    print("Then restart Emma, and dial 100 from 1001.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
