#!/usr/bin/env bash
# Install or update Emma on an Ubuntu 22.04/24.04 machine (docs/DEPLOY.md).
#
#   sudo bash deploy/install.sh [domain]       # run from a checkout of the repo
#
# domain: a DNS name pointing at this machine (Caddy gets a real certificate);
# omit it for "localhost" (a local certificate, for a lab VM or WSL).
# Safe to run again: it updates the code and restarts Emma, keeping .env, the
# database, logs and backups in /opt/emma.
set -euo pipefail

DOMAIN="${1:-localhost}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST=/opt/emma
if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo." >&2
    exit 1
fi

echo "== Packages"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3 python3-venv python3-pip rsync ffmpeg caddy

echo "== User and folders"
id emma >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin emma
mkdir -p "$DEST"/{data,logs,cache,backups}
rsync -a --delete \
    --exclude .git --exclude .venv --exclude .env --exclude data --exclude logs --exclude cache \
    --exclude backups --exclude captures --exclude harness_runs --exclude secrets --exclude telephony/build \
    "$SRC"/ "$DEST"/
if [ -d "$SRC/secrets" ] && [ ! -d "$DEST/secrets" ]; then
    cp -r "$SRC/secrets" "$DEST/secrets"
fi
if [ ! -f "$DEST/.env" ]; then
    if [ -f "$SRC/.env" ]; then
        cp "$SRC/.env" "$DEST/.env"
    else
        cp "$DEST/.env.example" "$DEST/.env"
        echo "!! $DEST/.env was created from .env.example: add the API keys and DASHBOARD_PASSWORD_HASH, then rerun."
    fi
fi
# Behind HTTPS the dashboard cookie must be Secure; Emma itself stays on loopback.
grep -q '^DASHBOARD_COOKIE_SECURE=' "$DEST/.env" || echo 'DASHBOARD_COOKIE_SECURE=true' >> "$DEST/.env"
grep -q '^SERVER_HOST=' "$DEST/.env" && sed -i 's/^SERVER_HOST=.*/SERVER_HOST=127.0.0.1/' "$DEST/.env"
chown -R emma:emma "$DEST"
chmod 600 "$DEST/.env"
[ -d "$DEST/secrets" ] && chmod -R go-rwx "$DEST/secrets"

echo "== Python environment"
sudo -u emma python3 -m venv "$DEST/.venv"
sudo -u emma "$DEST/.venv/bin/pip" install -q --upgrade pip
sudo -u emma "$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"

echo "== Services"
install -m 644 "$DEST/deploy/emma.service" /etc/systemd/system/emma.service
install -m 644 "$DEST/deploy/emma-backup.service" /etc/systemd/system/emma-backup.service
install -m 644 "$DEST/deploy/emma-backup.timer" /etc/systemd/system/emma-backup.timer
install -m 644 "$DEST/deploy/Caddyfile" /etc/caddy/Caddyfile
mkdir -p /etc/systemd/system/caddy.service.d /var/log/caddy
chown caddy:caddy /var/log/caddy 2>/dev/null || true
printf '[Service]\nEnvironment=EMMA_DOMAIN=%s\n' "$DOMAIN" > /etc/systemd/system/caddy.service.d/emma.conf
systemctl daemon-reload
systemctl enable --now emma.service emma-backup.timer
systemctl restart emma.service caddy.service

echo "== Check"
for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then break; fi
    sleep 1
done
systemctl --no-pager --lines=0 status emma.service | head -3
curl -fsS http://127.0.0.1:8000/health | head -c 200; echo
echo "Emma is behind https://$DOMAIN/ (dashboard: https://$DOMAIN/dashboard)."
echo "Firewall: allow only 443 (and 80 for certificate renewal), e.g. 'ufw allow 80,443/tcp && ufw enable'."
