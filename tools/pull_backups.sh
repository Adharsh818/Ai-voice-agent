#!/usr/bin/env bash
# Copy the hosted Emma's database backups to this machine (docs/DEPLOY.md, "Tested on AWS"):
# the server's daily backups (deploy/emma-backup.timer) otherwise live only on that one disk.
#
#   bash tools/pull_backups.sh                     # defaults: the AWS server of 17 Oct
#   EMMA_HOST=ubuntu@1.2.3.4 bash tools/pull_backups.sh
#
# Run it from the project folder on the owner's PC (Git Bash), by hand or from Task Scheduler.
# Only backups not already here are copied; each is checked with tools/backup.py --check, and
# the newest BACKUP_KEEP (14) are kept in backups/server/ (git-ignored: it holds patient data).
set -euo pipefail

HOST="${EMMA_HOST:-ubuntu@3.27.254.11}"
KEY="${EMMA_KEY:-$HOME/.ssh/emma_oracle}"
KEEP="${BACKUP_KEEP:-14}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/backups/server"
if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PY="$ROOT/.venv/Scripts/python.exe"; else PY="$ROOT/.venv/bin/python"; fi

mkdir -p "$DEST"
names="$(ssh -i "$KEY" "$HOST" "sudo ls -1 /opt/emma/backups" | tr -d '\r' | grep -E '^emma-[0-9]{8}-[0-9]{4}\.db$' | sort | tail -n "$KEEP" || true)"
if [ -z "$names" ]; then
    echo "No backups on the server yet." >&2
    exit 1
fi
copied=0
for name in $names; do
    [ -f "$DEST/$name" ] && continue
    ssh -i "$KEY" "$HOST" "sudo cat /opt/emma/backups/$name" > "$DEST/$name.partial"
    if ! "$PY" "$ROOT/tools/backup.py" --check "$DEST/$name.partial" > /dev/null; then
        rm -f "$DEST/$name.partial"
        echo "$name failed its check; not kept." >&2
        exit 1
    fi
    mv "$DEST/$name.partial" "$DEST/$name"
    copied=$((copied + 1))
done
ls -1 "$DEST"/emma-*.db | sort -r | tail -n +"$((KEEP + 1))" | xargs -r rm -f
echo "== $copied new, $(ls -1 "$DEST"/emma-*.db | wc -l) kept in backups/server (newest: $(ls -1 "$DEST"/emma-*.db | sort | tail -1 | xargs basename))"
