#!/usr/bin/env bash
# Put the committed code (HEAD) on the hosted Emma and restart her (docs/RESUME_HERE.md, START HERE).
#
#   bash tools/deploy_remote.sh                    # defaults: the AWS server of 17 Oct
#   EMMA_HOST=ubuntu@1.2.3.4 EMMA_DOMAIN=x.duckdns.org bash tools/deploy_remote.sh
#
# Needs the SSH key ~/.ssh/emma_oracle (on the owner's PC only). Only committed files go
# up (git archive), so commit first. On the server ~/emma-src/.env and ~/emma-src/models
# stay; deploy/install.sh keeps /opt/emma/.env, the database, logs and backups.
set -euo pipefail

HOST="${EMMA_HOST:-ubuntu@3.27.254.11}"
DOMAIN="${EMMA_DOMAIN:-pearl-emma.duckdns.org}"
KEY="${EMMA_KEY:-$HOME/.ssh/emma_oracle}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAR="$(mktemp -d)/emma.tar.gz"

if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]; then
    echo "Uncommitted changes would not be deployed; commit them first." >&2
    exit 1
fi
echo "== Packing $(git -C "$ROOT" log --oneline -1)"
git -C "$ROOT" archive --format=tar.gz -o "$TAR" HEAD
scp -q -i "$KEY" "$TAR" "$HOST":~/emma.tar.gz
ssh -i "$KEY" "$HOST" "set -e
cd ~/emma-src
find . -mindepth 1 -maxdepth 1 ! -name .env ! -name models -exec rm -rf {} +
tar -xzf ~/emma.tar.gz
sudo bash deploy/install.sh '$DOMAIN' | tail -4"
echo "== Live: $(curl -sS -o /dev/null -w '%{http_code}' "https://$DOMAIN/") from https://$DOMAIN/"
