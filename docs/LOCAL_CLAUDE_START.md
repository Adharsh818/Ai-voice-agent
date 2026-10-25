# Start here: briefing for the next Claude (written 25 Oct 2026)

**Owner:** "Read docs/LOCAL_CLAUDE_START.md and continue." This file is enough to pick the project up. It covers what Emma is, where everything runs, every change made in the 23 Oct cloud session, what the owner decided, and the tasks left in order with exact commands. The other docs hold the detail; links are given where needed.

---

## 1. Read first (10 minutes)

1. [NORTH_STAR.md](NORTH_STAR.md): the goal and the checklist **every** change must pass.
   - Emma must feel like a skilled human receptionist.
   - Python, never the model, decides every booking action and every spoken fact.
   - She never claims to be human.
2. This file.
3. Only when a task needs it:
   - [RESUME_HERE.md](RESUME_HERE.md) ("STATUS NOW"): the live list;
   - [ARCHITECTURE.md](ARCHITECTURE.md): how the code fits together;
   - [DEPLOY.md](DEPLOY.md) ("Tested on AWS"): hosting;
   - [HANDOFF.md](HANDOFF.md): history.

**Rules that apply to every change** (from CLAUDE.md and past sessions):
- Line endings are **LF**. Write files with `newline="\n"`.
- Tests: `.\.venv\Scripts\python.exe -m unittest discover -s tests` (Windows) or `.venv/bin/python -m unittest discover -s tests`.
- Scenarios: `python -m harness run scenarios --engine r2 --offline` and `... --nlu-down`.
- Sims: `python -m harness run sim --engine r2 --offline|--nlu-down --n 200 --seed 7|11 --concurrency 2`.
- Everything must pass before a push: 1061 tests, 53/53 scenarios both ways, sims with Z1-Z7 = 0.
- GitHub runs the tests and both scenario runs on every push (`.github/workflows/tests.yml`). A red ✗ there means fix before merging.
- Prefer the Edit tool for code with regexes. Python heredocs have mangled `\b` and `\x00` before.
- Never commit `.env`, `secrets/`, `data/`, `backups/` or `logs/`: they hold keys and patient data. Never paste API keys or the demo password anywhere.
- Ask the owner before anything hard to undo or outward-facing:
  - merging into `main`;
  - deploying;
  - changing the server's `.env`;
  - spending money;
  - changing how Emma talks (read new lines aloud against the north star first).
- Commit messages end with the attribution lines your harness gives you.

## 2. What Emma is, and where everything runs

Emma is Pearl Dental's phone receptionist: four branches in Bengaluru, fictional sample clinic data.
- **What she does:** books, moves and cancels appointments, answers clinic questions, handles urgent pain, and makes recovery calls when a doctor drops out.
- **Where she works:** the browser talk page, a softphone through Asterisk (WSL), and online.
- **Pipeline:**
  - listening: Deepgram Nova-3;
  - understanding: Gemini Flash-Lite, or Gemma 4 via Sarvam, with her own rules when the model is slow or down;
  - the dialogue engine: `dialogue/`;
  - scheduling: `scheduling.py` on SQLite;
  - voice: ElevenLabs Flash v2.5 ("Rachel", `EXAVITQu4vr4xnSDxMaL`), with a pre-rendered cache and Piper `en_GB-cori-medium` as the backup;
  - the staff dashboard, and a Google Calendar mirror (PC only).

| What | Where |
|---|---|
| PC checkout Emma runs from | `A:\Voice-Agent` (has `.env`, `data/`, `logs/`, `cache/`, `secrets/`); she runs in a Terminal tab "Emma" |
| Code on GitHub | `Adharsh818/Ai-voice-agent`. **`main`** = 2655be3. The 23 Oct work is on **`claude/optimistic-edison-kbglfn`** (5 commits ahead of `main`, not merged). `day2-r2-engine` is old: don't use it |
| Hosted Emma | https://pearl-emma.duckdns.org. AWS Free plan, Sydney `ap-southeast-2`, EC2 t4g.small `i-0bbb4d005a770fbb8`, public IP **3.27.254.11** (changes if the instance is stopped; then update DuckDNS by hand). Code in `/opt/emma`, settings in `/opt/emma/.env`, service `emma` (systemd), Caddy for HTTPS |
| SSH to the server | `ssh -i ~/.ssh/emma_oracle ubuntu@3.27.254.11`. The key exists only on the owner's PC |
| Logins | The owner's dashboard password, and a `demo` view-only login for recruiters (password in `A:\Voice-Agent\secrets\demo_login.txt`, never in git) |
| Server differences from the PC | Calendar sync off, telephony off, the talk page needs a sign-in, `LOG_CALLER_TEXT=false`. Its database is a copy from 17 Oct and is separate from the PC's |

**Keep these test bookings:**
- Jimmy, Sat 24 Oct 9:30, Dr Menon, Indiranagar;
- Adharsh, root canal, Wed 21 Oct 1 pm, Dr Shetty.

## 3. Everything the 23 Oct cloud session changed

All of it is on `claude/optimistic-edison-kbglfn`. The first part was also merged into `main` (up to 2655be3) with the owner's go-ahead.

**In `main` already:**

| Commit | Change | Files |
|---|---|---|
| dad4f0d | **Demo login hides phone numbers** everywhere it could see them: API answers, CSV, transcripts, the live-event stream. It's masked to the last 4 digits by one middleware, so no route can forget it. **20 talk-page calls a day** for all demo logins together (`DEMO_DAILY_CALL_LIMIT`, counted as `audit_events` rows with `action='demo_call'`). Staff are never limited | `server.py` (`mask_phones_for_demo`, `_count_demo_call`), `dashboard.py` (`sse_stream`), `logredact.mask_tree`, `config.py`, `.env.example`, `tests/test_dashboard.py` |
| 7fb5b56 | **Call fixes:** (1) staff ending a call with no number makes no uncallable callback task and promises no call back; (2) greeting "Pearl Dental, Emma speaking. Would you like to book an appointment?"; (3) "We're closed on Sundays, but we're open on Saturday and Monday."; (4) End call on a typed call: Emma says goodbye first (the page waits up to 8 s); (5) "ok bye" after a greeting question is a goodbye, not a yes | `call_session.py`, `config.py`, `prompts.py`, `dialogue/engine.py`, `static/app.js`, `tests/test_takeover.py` |
| 0b9a63d | **`tools/pull_backups.sh`** (copies the server's daily backups to `backups/server/` on the PC, checks each, keeps 14); DEPLOY.md "Tested on AWS"; NOTIFICATIONS.md "Is it free?"; HANDOFF final state; FINISH_PLAN Phase 7 | `tools/pull_backups.sh`, docs |
| 10c17fb, 2655be3 | Checked in a headless browser: the typed goodbye, the demo ribbon, masking and the limit. Once the limit is reached, the page shows only the message, with no "Try again" (`retry: false`) | `server.py`, `static/app.js`, `static/talk.css` |

**Only on `claude/optimistic-edison-kbglfn` (merge after the owner says so):**

| Commit | Change | Files |
|---|---|---|
| 2ee7d23 | **One voice per call while ElevenLabs works.** A sentence the live socket answers with silence is retried over ElevenLabs HTTP (same Rachel voice) before Piper. The call only switches to Piper when both are silent. Plus **`tools/call_report.py`** and **`tools/call_report_remote.sh`** | `speech.py` (`play`, `_open_on`), `tests/test_piper_fallback.py`, tools |
| 740ca52 | The report gets an `end` column: what ended the caller's turn (`final` = Deepgram's 300 ms silence, `utt` = its 1 s fallback, `text` = typed). Sentences Piper speaks are logged as `backup` (they were labelled `live`) | `speech.py`, `tools/call_report.py` |
| 8bef88d | RESUME_HERE: the improvement ideas moved to Future; the new ElevenLabs key | docs |
| 2b90a13 | **GitHub Actions** runs the tests and the scenarios on every push. **README** rewritten for recruiters (what Emma does, "the model talks, Python decides", a diagram, how it's checked). **`scipy`** added to `requirements.txt` (phone audio needs it) | `.github/workflows/tests.yml`, `README.md`, `requirements.txt` |

**Checks at the end of the session:**
- 1061 tests pass, on Python 3.12 and 3.13;
- 53/53 scenarios, offline and model-down;
- all four 200-call sims pass every check (Z1-Z7 = 0, M7 99-100%, M3 <= 0.5%, M10 <= 1%, T5 = 9);
- the crash drill passes;
- GitHub's first run passed.

## 4. What the owner's test calls showed (hosted, 23 Oct 17:55-18:05)

From `bash tools/call_report_remote.sh`, over 3 calls and 32 turns.

**The two voices:** ElevenLabs `quota_exceeded`, with 0 credits left. Live sentences went to Piper, while cached ones stayed Rachel. **The owner has since changed `ELEVENLABS_API_KEY` to one with about 9,800 characters**, on the PC and on the server. The PC Emma and the site share that allowance; a call uses about 30-75 live characters.

**The waits:** a reply was heard a median of 2.18 s after the caller stopped (90th percentile 3.68 s). 19 of 32 turns took 2 s or more.

| Part | Median / 90th percentile | Slow turns it caused |
|---|---|---|
| Listening (Deepgram end of speech, `stt_ms`) | 1.12 / 2.70 s | **16 of 19** |
| The model | (most turns didn't need it) / 2.21 s | **3**. All exactly 2.21 s: the server's `NLU_HEAD_DEADLINE_S` is 2.2, and Gemini from Sydney on the free tier timed out |
| Network and browser (`line`) | 0.40 / 0.42 s | 0 |
| Voice first byte | 0.20 / 0.47 s | 0 |
| Typing beat (on purpose) | 0.20 / 0.29 s | 0 |

On the PC on 6 Oct, listening was 0.8-1.0 s. Nothing has been tuned yet, because the report couldn't then tell which Deepgram signal ended each turn. The `end` column added in 740ca52 answers that on the next run.

## 5. What the owner decided (23-25 Oct)

- **Voice:** keep ElevenLabs Rachel, the "bold" one. The new key is in place. ElevenLabs Starter at $5/month is the fallback if it runs out; the free allowance resets on 12 Nov.
- **Hosting:** AWS only. The owner is earning the extra AWS credits (about 9-10 months in total).
- **Dropped:** the Wi-Fi router, and deciding where Emma moves after the AWS Free plan.
- **Future, not now:**
  - a real Indian phone number;
  - WhatsApp/SMS (neither is free for real patients: WhatsApp is about ₹0.12-0.15 per message, SMS needs DLT registration);
  - and everything in RESUME_HERE "Future".
- **Redesign:** ongoing. The owner will send a design-change list (O2).
- The PC `.env` is already cleaned.

## 6. Your tasks, in order

**Status, 25 Oct ~17:00 (local session):**
- **T1, T2:** done. `main` = 214cbe2, which combines the 23 Oct branch with the Apple redesign (Liquid Glass, patient phone page). 1068 tests, 53/53 scenarios both ways, all four sims pass; GitHub's run is green.
- **T3:** done. Deployed 214cbe2; `/health` is ok, with ElevenLabs ok on the new key. The PC Emma was restarted on `main`.
- **T5:** waiting for the owner's explicit yes. The server already has `SARVAM_API_KEY`, but `NLU_PRIMARY`, `SARVAM_URL` and `SARVAM_MODEL` aren't set yet.
- **T6:** done. 8 backups copied; Task Scheduler "Emma server backups" runs every 3 days at 10:00 (test run: result 0).
- **T4:** next. The owner makes 2-3 test calls on the site.
- **PC `.env`:** it still has two `SARVAM_API_KEY` lines (the last one wins).

The local Claude runs on the owner's PC, so it has the SSH key and can do what the cloud session couldn't. Each task says when it's done.

### T1. Get the latest code (no approval needed)
```bash
cd /a/Voice-Agent            # Git Bash; or A:\Voice-Agent in PowerShell
git fetch origin
git checkout claude/optimistic-edison-kbglfn
git pull
```
Run the tests once on the PC. Done when they pass.

### T2. Merge into `main`: ask the owner first ("Shall I merge the 23 Oct work into main?")
```bash
git checkout main && git pull && git merge --ff-only claude/optimistic-edison-kbglfn && git push origin main
```
It's a fast-forward: `main` hasn't moved since 2655be3. Done when GitHub's tests run on `main` is green.

### T3. Check the new ElevenLabs key on the server, then deploy (ask before deploying)
```bash
ssh -i ~/.ssh/emma_oracle ubuntu@3.27.254.11 "curl -s http://127.0.0.1:8000/health" | head -c 1500
bash tools/deploy_remote.sh          # ends with "== Live: 200"; keeps the server's .env, database and backups
```
- In `/health`, ElevenLabs must be ok.
- If the key is wrong, the owner edits it with `sudo nano /opt/emma/.env`, then you run `sudo systemctl restart emma`. Never print the key.
- Restart the PC Emma too, so she uses the new key.

### T4. Find out why listening is slow (the biggest delay callers feel)
1. Ask the owner for 2-3 test calls on the site. Ideally use a headset, and talk normally, including a name, a phone number, a day and a time.
2. Run `bash tools/call_report_remote.sh` (or `--last 3`).
3. Read the `end` column on the slow turns (`hearing` >= 1.5 s):
   - **Mostly `utt`:** the line never went quiet enough for Deepgram's 300 ms `speech_final`, so turns waited for the 1 s `UtteranceEnd`. Candidate fixes, smallest first:
     - (a) end the turn on Emma's side when an interim final is a complete answer and her own VAD (`vad.py`) has heard about 400 ms of silence (look at `turn_detector.py` and `call_session.py` around `endpoint_source`);
     - (b) `DEEPGRAM_ENDPOINTING_MS` from 300 to 400-500, so `speech_final` fires on noisier lines;
     - (c) test Deepgram's newer conversational model, built for end-of-turn detection.
   - **Mostly `final` but still slow:** the delay is between the server and Deepgram. The server is in Sydney and Deepgram's API is in the US. Check timings in the server log, and consider that a Mumbai server is the real fix (owner's call, it costs).
4. Whatever you change, measure before and after with the same report.
   - Replay the owner's recordings with `tools/replay.py` if they're on the PC.
   - Check that Emma doesn't start cutting callers off. Cutting a caller off is worse than 0.3 s of extra wait.
   - Run the full suite, the scenarios and the sims.
5. Done when the median listening time is under 1.0 s and no cut-offs show up in a test call.

### T5. Gemma on the server: ask the owner "yes or no?"
This would remove the 2.2 s Gemini timeouts. The PC has used Gemma via Sarvam as primary since 22 Oct, with Gemini as the retry.
If yes, add these four lines to `/opt/emma/.env`. The owner pastes the Sarvam key; check the PC's `.env` for the right names:
```
SARVAM_API_KEY=<owner pastes>
NLU_PRIMARY=sarvam
SARVAM_URL=https://api.sarvam.ai/v2/chat/completions
SARVAM_MODEL=gemma4
```
Then run `sudo systemctl restart emma` and check `/health`. Done when the next call report shows no 2.2 s `model` turns and the calls still go well.

### T6. Backups on the PC
1. Run `bash tools/pull_backups.sh` once (it prints `== N new, M kept`).
2. With the owner's OK, schedule it every 3 days in Windows Task Scheduler:
   - program `C:\Program Files\Git\bin\bash.exe`;
   - arguments `tools/pull_backups.sh`;
   - start in `A:\Voice-Agent`.

### T7. Live checks only the owner can do
- Press Reject on a recovery call after 9 am. It should be counted as declined.
- One microphone call on the redesigned talk page. The owner said other than the voice switching, there were no issues.

### T8. When the owner sends the design-change list (O2)
Apply it, keeping to [APPLE_DESIGN_PLAN.md](APPLE_DESIGN_PLAN.md) (Liquid Glass, section 13). Check in a browser at phone and desktop widths, with no console or CSP errors.

### T9. Good next improvements (only when the owner picks one; full list in RESUME_HERE "Future")
- **Elastic IP:** the address never changes. Same cost as now. The owner does it in the AWS console, and you guide.
- **UptimeRobot:** free uptime monitoring that emails or texts the owner.
- **A one-click deploy:** a GitHub Action that deploys `main` to AWS. It needs the SSH key as a GitHub secret, which only the owner can add.
- **Speaking:** fewer turns per booking (asking for two details at once, about 9 turns now, target 6-7), better name hearing, Kannada and Hindi.
- **A 1-2 minute demo video** for the resume (the owner records it; you can script it from DEMO_SCRIPT.md).

## 7. Watch out for

- **ElevenLabs characters:** a new sentence costs characters once, then it's cached.
  - About 300 characters are still to be rendered: the new greeting, the Sunday lines, and the no-number staff goodbye.
  - If the voice switches again, the report's voice column shows `backup`, and the server log shows `quota_exceeded`.
- **AWS Free plan:** never click "Activate advanced features" or "Upgrade plan". It leaves the protected plan. Delete anything the credit tasks create, but never the Emma instance.
- **Windows:** a long test run can hit `WinError 10055` (out of socket buffers). Restart the PC or close stale Python processes. `test_dashboard ... test_lists_filter_and_search` can be flaky under load (it uses the real clock).
- **The demo limit:** it counts across all demo logins and resets at midnight, clinic time. Set `DEMO_DAILY_CALL_LIMIT=0` for no limit.
- **The call report** prints no caller words, so it's safe to paste in chat. The server log lines it shows may include ElevenLabs request IDs (harmless) but never keys.
