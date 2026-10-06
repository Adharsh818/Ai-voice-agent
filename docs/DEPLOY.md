# Deploying Emma on a Linux machine

Emma runs on Windows for development. For a server (a cloud VM, a clinic PC running Ubuntu, or WSL for a test) the `deploy/` folder has everything:

| File | What it does |
|---|---|
| `deploy/install.sh` | Installs or updates Emma in `/opt/emma`: packages, a system user, the Python environment, the services, Caddy |
| `deploy/emma.service` | systemd unit: restarts on failure, hardened (no new privileges, read-only system, write access only to Emma's own folders), loopback only |
| `deploy/Caddyfile` | HTTPS front door: automatic certificates, WebSockets and the dashboard's live events passed through unbuffered, `/telephony/*` refused from outside |
| `deploy/emma-backup.service` + `.timer` | Daily database backup at 02:30 (`tools/backup.py`), a missed run catches up at boot |

## Install

On Ubuntu 22.04 or 24.04, from a checkout of the repo with your `.env` (API keys, `DASHBOARD_PASSWORD_HASH`, `DASHBOARD_SESSION_SECRET`) and `secrets/` (the Google service-account key) in it:

```bash
sudo bash deploy/install.sh emma.example.com
```

Give it a domain whose DNS points at the machine for a real certificate, or nothing for `localhost` (Caddy's local certificate; fine for WSL or a lab VM). It's safe to run again to update: code, packages and services are refreshed; `.env`, the database, logs and backups in `/opt/emma` are kept.

Then:
- **Firewall:** only 443 (and 80, for certificate renewal) open to the internet: `sudo ufw allow 80,443/tcp && sudo ufw enable`. Emma (8000), AudioSocket (9092), the Asterisk manager (5038) and SIP stay on loopback.
- **Check:** `systemctl status emma`, then `https://<domain>/dashboard` (log in) and the talk page `https://<domain>/` (browsers allow the microphone only over HTTPS).
- **Calendar sync:** works unchanged (the service-account key is in `/opt/emma/secrets`).
- **Phone calls (optional):** Asterisk on the same machine with `telephony/install_asterisk.sh` and `TELEPHONY_ENABLED=true` (see [TELEPHONY.md](TELEPHONY.md)).

## Day-to-day

| Task | Command |
|---|---|
| Logs | `journalctl -u emma -f` (phone numbers are masked by `logredact`) |
| Restart | `sudo systemctl restart emma` (a call in progress is cut; a booking being written finishes first) |
| Update | `git pull && sudo bash deploy/install.sh <domain>` |
| Backups | `ls /opt/emma/backups` · run one now: `sudo systemctl start emma-backup` |
| Health of the database | `sudo -u emma /opt/emma/.venv/bin/python /opt/emma/tools/backup.py --check` |
| Restore | `sudo systemctl stop emma && sudo -u emma /opt/emma/.venv/bin/python /opt/emma/tools/backup.py --restore /opt/emma/backups/emma-YYYYMMDD-HHMM.db && sudo systemctl start emma` |

## Backups and logs

- **Database:** `tools/backup.py` uses SQLite's online backup, so it's safe while Emma runs. Each copy is checked (`integrity_check`, foreign keys, no doctor booked twice, every booking holding its slot claims) before it's kept; the newest 14 are kept (`BACKUP_KEEP`). Copy `/opt/emma/backups` off the machine as well (for example a nightly `rclone` to your own storage): a backup on the same disk doesn't survive the disk.
- **On Windows:** the same tool works; schedule it with Task Scheduler (`.venv\Scripts\python.exe tools\backup.py`, start in the project folder).
- **Turn timings:** `logs/turns.jsonl` rolls over at `LOG_ROTATE_MB` (5 MB), keeping `LOG_KEEP` files.
- **App log:** under systemd it goes to the journal. Elsewhere set `LOG_FILE=logs/emma.log` for a daily-rotating file (`LOG_KEEP` days).
- **Transcripts:** in the database only, blanked after `TRANSCRIPT_RETENTION_DAYS` (30). No call audio is ever stored.

## Before real patients

- Move Gemini, Deepgram and ElevenLabs to paid plans whose terms don't train on your data, and review each vendor's data-use terms.
- Set `LOG_CALLER_TEXT=false` so callers' words never reach the log.
- Set a strong dashboard password (`tools/hash_password.py`) and `DASHBOARD_SESSION_SECRET`.
- Read [THREAT_MODEL.md](THREAT_MODEL.md).
