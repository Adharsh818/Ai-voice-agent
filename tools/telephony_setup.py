"""
Set up phone calls through Asterisk (docs/TELEPHONY.md), the Windows half.

    python tools/telephony_setup.py            add the telephony settings to .env (only the ones
                                               missing; nothing already there is changed) and
                                               render the Asterisk configs into telephony/build/
    python tools/telephony_setup.py --dry-run  show what it would add, change nothing
    python tools/telephony_setup.py --lan      also let a softphone app on a mobile on this Wi-Fi
                                               call Emma: SIP bound to this PC's Wi-Fi address,
                                               only that subnet admitted (acl.conf); --lan-subnet
                                               10.0.0.0/24 overrides the guessed /24
    python tools/telephony_setup.py --local    back to loopback only (the default)

Secrets (the dialplan's key, the manager password, the two SIP passwords) are
generated once and kept in .env, so running it again renders the same configs.
Then, in Ubuntu (WSL2):

    sudo bash /mnt/a/Voice-Agent/telephony/install_asterisk.sh

and restart Emma. The SIP passwords for the softphone accounts are printed at the end.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import secrets
import socket
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


def lan_address() -> str:
    """This PC's address on the network it reaches others through (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))           # TEST-NET: only picks the outgoing interface
        return s.getsockname()[0]


def set_env(path: Path, updates: dict):
    """Change (or add) only these keys in .env, keeping every other line as it is."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    for i, line in enumerate(lines):
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in pending and not line.lstrip().startswith("#"):
            lines[i] = f"{m.group(1)}={pending.pop(m.group(1))}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


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
        "SIP_BIND": values.get("TELEPHONY_SIP_BIND") or "127.0.0.1",
        "LAN_PERMIT": "",
    }
    if values.get("TELEPHONY_SIP_SUBNET"):
        net = ipaddress.ip_network(values["TELEPHONY_SIP_SUBNET"], strict=False)
        mapping["LAN_PERMIT"] = f"permit = {net.network_address}/{net.netmask}"
    out = {}
    for template in sorted(TEMPLATES.glob("*.conf")):
        text = template.read_text(encoding="utf-8")
        text = re.sub(r"\{\{([A-Z0-9_]+)\}\}", lambda m: mapping[m.group(1)], text)
        out[template.name] = text
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tools/telephony_setup.py", description=__doc__.splitlines()[1])
    parser.add_argument("--dry-run", action="store_true", help="show what would be added; change nothing")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--lan", action="store_true", help="admit softphones on this Wi-Fi (not the internet)")
    where.add_argument("--local", action="store_true", help="loopback only (the default)")
    parser.add_argument("--lan-subnet", help="the Wi-Fi subnet to admit, e.g. 192.168.1.0/24 (default: a /24 guess)")
    args = parser.parse_args(argv)

    env = read_env(ENV)
    network = {}
    if args.lan:
        ip = lan_address()
        subnet = args.lan_subnet or str(ipaddress.ip_network(f"{ip}/24", strict=False))
        if ipaddress.ip_address(ip).is_loopback or not ipaddress.ip_address(ip).is_private:
            print(f"{ip} isn't a private Wi-Fi address; refusing to expose SIP on it.", file=sys.stderr)
            return 1
        network = {"TELEPHONY_SIP_BIND": ip, "TELEPHONY_SIP_SUBNET": subnet}
    elif args.local:
        network = {"TELEPHONY_SIP_BIND": "127.0.0.1", "TELEPHONY_SIP_SUBNET": ""}
    env.update(network)
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
    if network:
        print("Network: " + (f"SIP on {network['TELEPHONY_SIP_BIND']}, admitting {network['TELEPHONY_SIP_SUBNET']}"
                             if network["TELEPHONY_SIP_SUBNET"] else "loopback only"))
        set_env(ENV, network)
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
    server = values.get("TELEPHONY_SIP_BIND") or "127.0.0.1"
    print(f"\nSoftphone accounts (server {server}, port 5060, UDP):")
    print(f"  1001 (caller / patient)  password {values['TELEPHONY_SIP_PASSWORD_1001']}")
    print(f"  1002 (front desk)        password {values['TELEPHONY_SIP_PASSWORD_1002']}")
    print("\nNext, in Ubuntu (WSL2):  sudo bash /mnt/a/Voice-Agent/telephony/install_asterisk.sh")
    print("Then restart Emma, and dial 100 from 1001.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
