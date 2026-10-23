#!/usr/bin/env bash
# tools/call_report.py on the hosted Emma, from the owner's PC (the SSH key is there):
#
#   bash tools/call_report_remote.sh               today's calls on the server
#   bash tools/call_report_remote.sh --last 3      the last 3 calls
#
# Then the voice service's own complaints from today's server log (why a call fell back
# to the second voice). Nothing here prints what callers said.
set -euo pipefail

HOST="${EMMA_HOST:-ubuntu@3.27.254.11}"
KEY="${EMMA_KEY:-$HOME/.ssh/emma_oracle}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARGS="$*"

ssh -i "$KEY" "$HOST" "cd /opt/emma && sudo -u emma .venv/bin/python - --logs /opt/emma/logs $ARGS" \
    < "$ROOT/tools/call_report.py" || true
echo
echo "== voice problems in today's server log"
ssh -i "$KEY" "$HOST" "sudo journalctl -u emma --since today --no-pager -o cat" \
    | grep -E "ElevenLabs|TTS|backup voice|quota" | tail -n 25 || echo "(none)"
