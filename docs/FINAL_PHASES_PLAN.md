# Final phases plan: softphone real calls + Phase E

Approved by the owner on 6 Oct 2026 (Twilio skipped for now). Kept here so every session can refer back to it; tick items off in docs/HANDOFF.md as they're done.


## Context
The final demo should end with a real call. Twilio is skipped for now. The route is a **SIP softphone app on your own mobile**, calling Emma over your Wi-Fi through Asterisk. Phase D already built the Emma side (`audiosocket.py`, `phone_audio.py`, `ami.py`, `telephony/`).

**Is it completely free? Yes.**
- **Software:** Asterisk (open source), WSL2 + Ubuntu (free with Windows) and the softphone apps (Linphone is fully free and open source; Zoiper's free version works too, with ads) all cost nothing.
- **Calls:** they travel over your own Wi-Fi, so no minutes are billed.
- **The only paid services are the ones Emma already uses on every call** (Deepgram, ElevenLabs, Gemini), same as the browser.
- **Limit:** there is no phone number. It works only while the phone is on the same Wi-Fi as the PC, which is fine for a live demo.

The same session also finishes **Phase E (evaluation hardening)** from plan section 13, plus the leftovers from today: regression tests for the conversation fixes, and a commit.

## Step 0: Close out today's uncommitted work
- **Tests:** add `tests/test_sim_fixes.py` covering:
  - the question detector ("is it possible to get…", "Hello? Yes…");
  - small talk ignored as a date;
  - "move my existing appointment" read as a reschedule;
  - "what do you suggest" → earliest times;
  - "which one" after a plain yes to two times, and the second yes taking the first;
  - `scheduling.suggest` skipping the patient's own appointments (`patient=`);
  - the asked time on a later day (`same_time_later=`) and at another branch;
  - same-day times in clock order;
  - the reworded lines ("at at", "{doctor} has").
- **Then:** the full suite, scenarios and the 4 simulations; update `docs/HANDOFF.md` and the plan; and, with your OK, commit and push to PR #5 (phase D, DEMO removal, these fixes).

## Part 1: Softphone real calls

### Your part (needs admin or your phone)
1. Install WSL: `wsl --install -d Ubuntu-24.04` (admin PowerShell), reboot, open Ubuntu once and create the user.
2. Say yes to me creating `%UserProfile%\.wslconfig` with `networkingMode=mirrored`, then run `wsl --shutdown`.
3. Add a firewall rule (I'll give the exact admin commands; you run them) allowing **UDP 5060 and 10000-10200 from your Wi-Fi subnet only**, in both Windows Defender Firewall and the Hyper-V firewall that mirrored mode uses.
4. Install **Linphone** (recommended) or Zoiper on your phone, and MicroSIP on the PC to act as the front desk.

### My part
- **LAN mode in `tools/telephony_setup.py`** (`--lan`):
  - finds the PC's Wi-Fi IP;
  - renders `pjsip.conf` with the transport bound to that IP (still loopback otherwise);
  - adds `acl.conf`, with endpoints accepting only that subnet;
  - generates long random SIP passwords and prints them with a QR-free text block to type into the app.
- **Install:** run `telephony/install_asterisk.sh` in WSL and confirm the modules (AudioSocket, CURL, PJSIP).
- **Emma:** set `TELEPHONY_ENABLED=true` and restart her.
- **Live checks, with you holding the phone:**
  - **Inbound:** dial 100. Check the greeting, a booking with the caller-ID question (extension 1001's caller ID is set to the demo number), interrupting Emma, and keying a number with `#`.
  - **Transfer:** "I want to talk to a person" twice. MicroSIP (1002) on the PC rings, and the callback task exists.
  - **Recovery:** block a doctor on the dashboard. The phone rings through AMI Originate; answer once, then reject once (task + NEEDS RESCHEDULE).
  - **The three open questions from TELEPHONY.md:** keypad frames through AudioSocket (if not, switch `dtmf_mode`), 16 kHz support, and the Reject reason code (adjust `ami.REASON_DECLINED`).
- **Docs:** `docs/TELEPHONY.md` (LAN setup and results) and `docs/DEMO_SCRIPT.md` (a final "real call" scene, with the browser as backup).

## Part 2: Phase E, evaluation hardening

### E1: Evaluation report (`tools/evaluate.py` → `docs/EVALUATION.md`)
- **Latency:** p50/p95 of perceived reply time, split into rule-handled turns (T1, target 0.9 / 1.8 s) and model turns (T2, target 1.8 / 3 s). Source: `logs/turns.jsonl` (653 real turns so far, fields `tier`, `perceived_ms`, `endpoint_ms`, `nlu_ms`, `first_audio_ms`).
- **Fallback and interruptions:** fallback rate (share of model turns answered without the model; tier and fallback counts from the same log), filler rate, and barge-in rate (`barge_in`).
- **Task success and safety:** from fresh runs of the 53 scenarios and 4 × 200-call simulations (model on and off; harness `summary.json`). Covers bookings completed, dead ends, loops, Z1-Z7, and turns per booking.
- **Speech recognition word error:** `tools/replay.py` scoring (`errors / ref_words`) on your two 30-line recordings in `captures/`, plus cut-off lines and the wait after the last word. This uses about 2 minutes of Deepgram; I'll ask before running it.
- **Report format:** each number appears next to its target from `docs/SUCCESS_CRITERIA.md`, with an honest pass/fail. The known gap, latency on simple turns at about 1.6 s against 0.9 s, is stated as such.

### E2: Booking-integrity report (in `docs/EVALUATION.md`)
- Run `tests/test_fault_drills.py` and the Gemini-off simulation, and summarise in plain words:
  - a hang-up mid-booking is finished and recorded;
  - nothing is half-written after a crash;
  - held slots are freed at hang-up;
  - zero double bookings and zero actions without a heard yes.
- Add a small drill tool to kill the server during a booking, then check the database with `PRAGMA integrity_check` and the slot claims, since that part is manual today.

### E3: Operations
- **Daily SQLite backup** (`tools/backup.py`): uses SQLite's online `.backup` API (safe while Emma runs). It writes `backups/emma-YYYYMMDD.db`, keeps 14, and verifies each copy with `integrity_check`. There's also `--restore <file>`, which requires Emma to be stopped.
  - Scheduled by a systemd timer on Linux.
  - On Windows, scheduling would be a Task Scheduler command for you to add; I won't create the scheduled task myself.
  - `backups/` is git-ignored.
- **Log rotation:**
  - `logs/turns.jsonl` rotates by size (for example 5 MB, keeping 5) inside `latency.LatencyLog`.
  - An optional `LOG_FILE` setting adds a daily-rotating, phone-masked app log (the `logredact` filter stays on).
  - On Linux, systemd's journal covers the console log.
- **Linux deployment kit** (`deploy/`, explained in `docs/DEPLOY.md`):
  - an `emma.service` systemd unit;
  - a `Caddyfile` for HTTPS with automatic certificates, proxying WebSockets and keeping Emma on 127.0.0.1;
  - the backup timer;
  - an `install.sh`, and notes for a cloud VM (ports, a firewall allowing only 443, secrets in `.env`).
  - **Verified in the WSL Ubuntu** we're installing anyway: start Emma under systemd, put Caddy in front with a local certificate, and open the dashboard through it.
- **Threat model** (`docs/THREAT_MODEL.md`): assets (patient data, transcripts, API keys, the booking database), trust boundaries (browser, phone line, the model, Google, the dashboard), and threats with their mitigations, each pointing to the code and tests that cover it:
  - spoofed origins and cross-site socket abuse;
  - the dashboard login and audit log;
  - prompt injection through caller speech (Python decides every action and spoken fact);
  - personal data in logs (`logredact`) and transcript retention;
  - SIP toll fraud and scanning (loopback or Wi-Fi-only binding, ACL, strong passwords, no outbound trunk);
  - denial of service through the single call gate;
  - leaked secrets.
  - Remaining risks are listed honestly.
- **Viva notes** (`docs/VIVA_NOTES.md`): a one-page architecture summary, the key design decisions and why (Python decides, a model fallback, Tier-0 rules, honesty boundary, holds and claims against double booking), likely examiner questions with short answers, numbers from the evaluation, limitations and future work (PSTN number, multiple calls, Hinglish).

## Critical files
- **New:** `tests/test_sim_fixes.py`, `tools/evaluate.py`, `tools/backup.py`, `deploy/` (`emma.service`, `Caddyfile`, `emma-backup.timer`, `install.sh`), `docs/EVALUATION.md`, `docs/DEPLOY.md`, `docs/THREAT_MODEL.md`, `docs/VIVA_NOTES.md`, `telephony/asterisk/acl.conf`.
- **Changed:**
  - `tools/telephony_setup.py` (`--lan`), `telephony/asterisk/pjsip.conf`;
  - `latency.py` (rotation), `server.py` (optional `LOG_FILE`), `config.py`, `.env.example`, `.gitignore` (`backups/`);
  - `docs/TELEPHONY.md`, `docs/DEMO_SCRIPT.md`, `docs/HANDOFF.md`, `docs/IMPLEMENTATION_PLAN.md`.
- **Reused:**
  - `tools/replay.py` (`score`), `harness` (`run sim/scenarios`, `summary.json`), `tests/test_fault_drills.py`;
  - `latency.LatencyLog`, `logredact`, `db.get_db`;
  - everything from Phase D (`audiosocket.py`, `ami.py`, `phone_audio.py`).

## Verification
1. Full suite (`.\.venv\Scripts\python.exe -m unittest discover -s tests`), 53 scenarios, 4 × 200-call simulations: all at or above today's results.
2. Softphone: every live check in Part 1 passes with your phone. The results go into TELEPHONY.md.
3. `tools/evaluate.py` regenerates `docs/EVALUATION.md` from the data, so the numbers can be reproduced.
4. Backup: take a copy while Emma runs, check its integrity, and restore it into a scratch path, comparing row counts.
5. Rotation: force a small size limit in a test and check the files roll over.
6. Deploy: in WSL, `systemctl status emma` is active, the dashboard loads through Caddy's HTTPS, and a reboot of the WSL distro brings it back up.

## Order
Step 0 → E1-E3 (none need WSL; they can start immediately) → Part 1 once WSL is installed (the deploy check in E3 also runs in WSL).
