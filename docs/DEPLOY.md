# Deploying Emma on a Linux machine

Emma runs on Windows for development. For a server (a cloud VM, a clinic PC running Ubuntu, or WSL for a test) the `deploy/` folder has everything:

| File | What it does |
|---|---|
| `deploy/install.sh` | Installs or updates Emma in `/opt/emma`: packages, a system user, the Python environment, the services, Caddy |
| `deploy/emma.service` | systemd unit: restarts on failure, hardened (no new privileges, read-only system, write access only to Emma's own folders), loopback only |
| `deploy/Caddyfile` | HTTPS front door: automatic certificates, WebSockets and the dashboard's live events passed through unbuffered, `/telephony/*`, `/health` and `/metrics` refused from outside (12 Oct; check them on the machine at `http://127.0.0.1:8000/health`) |
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
| Crash drill on a copy of the live database (never writes the live file) | `sudo -u emma /opt/emma/.venv/bin/python /opt/emma/tools/crash_drill.py --from /opt/emma/data/emma.db` |
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

## Tested in WSL (11 Oct 2026)

`deploy/install.sh` was run in WSL Ubuntu 24.04 (the same distro as Asterisk) from a `git archive` copy of the repo **without `.env` or `secrets/`**, so it used `.env.example`: no API keys and no Google key, so a second Emma could never sync the real calendars.

| Check | Result |
|---|---|
| Install (packages, `emma` user, venv, services, Caddy; ~5 min) | OK; `/health` answers `degraded` (no keys, as expected) |
| `systemctl status emma`, `caddy`, `emma-backup.timer` | all active and enabled; next backup 02:30 |
| Caddy on `https://localhost/` | 200 over HTTP/2 with HSTS; `/dashboard` redirects to login; `/telephony/*` refused (404) |
| Ports | Emma on `127.0.0.1:8000` only; Caddy on 80/443 |
| Backup now (`systemctl start emma-backup`) and `backup.py --check` | written and verified healthy |
| Calendar sync without a key | switches itself off: "no service-account key" |
| Running the installer again (update) | OK; `.env`, database and backups kept |
| `wsl --terminate Ubuntu-24.04`, then start again | Emma, Caddy and the timer came back by themselves; HTTPS 200 |

Two bugs found and fixed (`f314a0b`): the backup inherited the database's WAL mode and left `.partial-wal`/`.partial-shm` files beside each copy (copies are now one self-contained file), and python-dotenv couldn't parse `TELEPHONY_CLINIC_CID` in `.env.example`, logging a warning on every start (now single-quoted, which systemd and python-dotenv both read as `"Pearl Dental" <08041234567>`). Tests in `tests/test_operations.py`.

Afterwards the test copy was switched off so it never takes port 8000 from Emma on Windows (WSL forwards its ports to Windows `localhost`):

```bash
sudo systemctl disable --now emma caddy emma-backup.timer
```

It stays installed in `/opt/emma` (switched off). Asterisk in the same distro was not touched.

## Tested on AWS (live since 17 Oct 2026)

Emma runs at **https://pearl-emma.duckdns.org** for the owner's resume (sign-in needed; recruiters use the `demo` login).

**The setup:**
- **Account:** AWS **Free plan** (no card), project "Emma", region **Sydney `ap-southeast-2`**.
  - The Free plan pins a project to one region. Mumbai needs "advanced features", and activating them leaves the protected Free plan, so don't.
  - $100 credit at about $21/month. The "earn credits" tasks on the AWS home page add up to $100 more, which takes it to about 9-10 months. The Free plan itself ends **17 Apr 2027**; after that AWS suspends the account and deletes it 90 days later.
- **Server:** EC2 t4g.small (ARM, 2 vCPU, 2 GB) with a 2 GB swapfile, Ubuntu 24.04, 20 GB gp3, time zone Asia/Kolkata. Security group: 22, 80 and 443 open.
  - The public IP (3.27.254.11) is ephemeral: after a stop and start it changes, and the DuckDNS record must be updated by hand.
- **Install:** a `git archive` of the repo, then `sudo bash deploy/install.sh pearl-emma.duckdns.org`. Caddy gets the Let's Encrypt certificate by itself.
- **`.env` on the server** (`/opt/emma/.env`): the PC's keys, with `CALENDAR_SYNC_ENABLED=false` (the Calendar stays on the PC), `TELEPHONY_ENABLED=false`, `TALK_PAGE_PUBLIC=false`, `DASHBOARD_COOKIE_SECURE=true` and `LOG_CALLER_TEXT=false`. No `secrets/` folder.
- **The demo login:** a `demo` staff user (role `demo`, migration 007). It can look around and talk to Emma, but every change is refused. It sees phone numbers masked to their last four digits, and can start `DEMO_DAILY_CALL_LIMIT` (20) talk-page calls a day, counted across all demo logins.

**Updating it** (from the owner's PC, where the SSH key `~/.ssh/emma_oracle` is):
```bash
bash tools/deploy_remote.sh
```
It deploys the committed code (HEAD), keeps the server's `.env`, models, database, logs and backups, restarts Emma and prints the live status code. It refuses to run with uncommitted changes.

**Or from GitHub, with no PC needed** (`.github/workflows/deploy.yml`): Actions → **deploy** → Run workflow, on `main`. It runs the tests and both scenario runs first, then the same `tools/deploy_remote.sh`. It never runs by itself. One-time setup by the owner, under Settings → Secrets and variables → Actions:
- `EMMA_SSH_KEY`: the contents of `~/.ssh/emma_oracle`;
- `EMMA_KNOWN_HOSTS`: the output of `ssh-keyscan -t ed25519 3.27.254.11`. Update it if the IP changes.

Until both are set, the job stops at "Check secrets". Adding the key lets GitHub's runners log in to the server, so keep the repository's write access to the owner only.

**Backups off the server:** the server backs itself up daily at 02:30 (`emma-backup.timer`) onto its own disk. To keep copies on the PC as well:
```bash
bash tools/pull_backups.sh
```
It copies backups the PC doesn't have yet into `backups/server/` (git-ignored), checks each one with `tools/backup.py --check`, and keeps the newest 14. Run it by hand every few days, or add it to Windows Task Scheduler (program `C:\Program Files\Git\bin\bash.exe`, arguments `tools/pull_backups.sh`, start in `A:\Voice-Agent`). To restore one on the server: copy it up with `scp`, stop Emma, then run `tools/backup.py --restore FILE` there.
