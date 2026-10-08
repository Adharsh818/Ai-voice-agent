#!/usr/bin/env bash
# Install Asterisk in Ubuntu (WSL2) and load Emma's configs (docs/TELEPHONY.md).
#
#   sudo bash /mnt/a/Voice-Agent/telephony/install_asterisk.sh
#
# Run tools/telephony_setup.py on Windows first: it renders telephony/build/.
# The package's original configs are kept in /etc/asterisk/orig-<date>/.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$HERE/build"
if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo." >&2
    exit 1
fi
if [ ! -f "$BUILD/extensions.conf" ]; then
    echo "No rendered configs in $BUILD: run 'python tools/telephony_setup.py' on Windows first." >&2
    exit 1
fi

if ! command -v asterisk >/dev/null 2>&1; then
    echo "Installing Asterisk..."
    apt-get update -q
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q asterisk asterisk-core-sounds-en
fi
asterisk -V

MODDIR="$(dirname "$(find /usr/lib -name 'app_audiosocket.so' -print -quit 2>/dev/null || true)")"
for mod in app_audiosocket.so res_audiosocket.so func_curl.so func_uri.so res_pjsip.so chan_pjsip.so; do
    if [ -z "$MODDIR" ] || [ ! -f "$MODDIR/$mod" ]; then
        echo "Missing Asterisk module: $mod (this package can't run Emma's dialplan)." >&2
        exit 2
    fi
done

BACKUP="/etc/asterisk/orig-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
for f in "$BUILD"/*.conf; do
    name="$(basename "$f")"
    [ -f "/etc/asterisk/$name" ] && cp "/etc/asterisk/$name" "$BACKUP/$name"
    install -m 640 -o asterisk -g asterisk "$f" "/etc/asterisk/$name"
done
echo "Configs installed (originals in $BACKUP)."

if command -v systemctl >/dev/null 2>&1 && systemctl is-system-running >/dev/null 2>&1; then
    systemctl enable asterisk >/dev/null 2>&1 || true
    systemctl restart asterisk
else
    service asterisk restart
fi
sleep 3
asterisk -rx "module show like audiosocket"
asterisk -rx "pjsip show endpoints" | grep -E "Endpoint:|^ *1001|^ *1002" || true
asterisk -rx "manager show settings" | grep -E "Manager \(AMI\)|Port|Bind" || true
echo "Asterisk is ready. Register 1002 (MicroSIP, source port 5070) at 127.0.0.1:5060 and 1001 at the"
echo "address tools/telephony_setup.py printed (127.0.0.1, or the Wi-Fi address with --lan), then dial 100."
