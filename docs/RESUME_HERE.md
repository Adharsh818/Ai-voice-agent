# Resume here (updated 17 Oct 2026)

When the owner says **"continue your work"**, start from this file. The full plan is [FINISH_PLAN.md](FINISH_PLAN.md); project history is in [HANDOFF.md](HANDOFF.md).

## Where everything is

- **Code:** branch `day2-r2-engine` = `main` on GitHub up to f5cd820; later commits may be local (check `git status -sb`). The working copy Emma runs from is the main checkout **`A:\Voice-Agent`** (it has `.env`, `data/`, `logs/`, `captures/`). Sessions may work in a git worktree under `A:\Voice-Agent\.claude\worktrees\...`; fast-forward the main checkout with `git merge --ff-only <worktree branch>` before running Emma.
- **PRs #1-#5:** all merged (8 Oct): `main` was fast-forwarded to `day2-r2-engine`; #5 merged into its base.
- **Tests:** `.\.venv\Scripts\python.exe -m unittest discover -s tests` (about 1050, all passed before the WIP commit; `test_dashboard ... test_lists_filter_and_search` is flaky under load: real clock).
- **Scenarios / sims:** `.\.venv\Scripts\python.exe -m harness run scenarios --engine r2 --offline`; sims `... run sim --engine r2 --offline|--nlu-down --n 200 --seed 7|11 --concurrency 2`.

## Pushing

Push from `A:\Voice-Agent`: `git push origin day2-r2-engine` and `git push origin day2-r2-engine:main` (owner approved push + merge into main on 8 Oct). Pushed up to date on 15 Oct (afternoon); check `git status -sb` for anything newer.

## START HERE (written 17 Oct, ~09:45): Emma is online at https://pearl-emma.duckdns.org

The owner continues from a cloud session. **Read the "cloud" note below first.**

**What's live (17 Oct, ~09:40):** Emma runs on an AWS server, reachable from any device.
- `https://pearl-emma.duckdns.org/` is the talk page (200).
- `/dashboard` redirects to sign-in (307).
- `/health` is refused from outside (404, as designed). Check it on the server at `http://127.0.0.1:8000/health`; on 17 Oct it showed Gemini, Deepgram and ElevenLabs all ok.
- The certificate is from Let's Encrypt, obtained by Caddy.
- It's for the owner's resume: recruiters open the link and sign in with the demo login.

**Where it runs:**
- **Account:** AWS **Free plan** (no card), project **"Emma"**, region **Sydney `ap-southeast-2`**. The Free plan pins a project to one region, and Mumbai needs "advanced features" (an upgrade), so don't click "Activate advanced features": it leaves the protected Free plan.
- **Server:** EC2 `i-0bbb4d005a770fbb8`, **t4g.small** (ARM, 2 vCPU, 2 GB), Ubuntu 24.04, 20 GB gp3, plus a 2 GB swapfile; time zone Asia/Kolkata.
  - Public IP **3.27.254.11**, ephemeral: it changes if the instance is stopped and started, and then the DuckDNS IP must be updated.
  - Security group: 22/80/443 open; ufw off.
- **Credit:** $100, and the Free plan ends **17 Apr 2027**; after that AWS suspends the account and deletes it 90 days later.
  - At about $21/month (t4g.small $0.0212/h, public IPv4, disk), the $100 lasts about 4½-5 months.
  - The owner can earn up to $100 more from the "earn credits" tasks on the AWS home page.
  - Plan: move to Oracle before then (below).
- **SSH:** `ssh -i ~/.ssh/emma_oracle ubuntu@3.27.254.11`. **That key exists only on the owner's PC** (`C:\Users\adhar\.ssh\emma_oracle`). The same public key is imported in AWS as `emma-key` and is the one Oracle will get.
- **DuckDNS:** the owner's account (signed in with Google, adharsh1001r@gmail.com); sub-domain `pearl-emma`, IP set by hand. Its token is the owner's; never put it in the repo.

**What's on the server (`/opt/emma`, installed by `deploy/install.sh pearl-emma.duckdns.org`):**
- **Code:** a `git archive` of 06de9b8.
- **`.env`:** the PC's keys with the following changes:
  - set: `CALENDAR_SYNC_ENABLED=false` (calendar stays on the PC only, owner's choice), `TELEPHONY_ENABLED=false`, `TALK_PAGE_PUBLIC=false`, `DASHBOARD_COOKIE_SECURE=true`, `LOG_CALLER_TEXT=false`;
  - removed: the SIP/AMI/trunk/Google lines;
  - no `secrets/` folder.
- **Database:** a **copy of the PC's database taken 17 Oct 09:34** (owner's choice: "copy of today's"). From then on the two databases are separate.
- **Also there:** the `cache/` copy (215/215 sentences, so no ElevenLabs re-render) and the Piper voice `en_GB-cori-medium`, downloaded on the server.
- **Logins:**
  - the owner signs in with the same dashboard password as on the PC;
  - a **`demo` staff login** for recruiters, whose password is in **`A:\Voice-Agent\secrets\demo_login.txt`** (git-ignored, on the owner's PC only).

**Since then (17 Oct, ~10:00):**
- **Sign-in prompt (93c68f1, deployed):** the hosted talk page told signed-out visitors "Couldn't reach Emma" (`/ws/voice` refused them with 403). `/client-config` now reports `signin_required`, so the page says "Sign in to talk to Emma." with a Sign in button, and the sign-in page returns to `?next=`. Checked live in the browser.
- **Update script:** `tools/deploy_remote.sh` now does the update below in one step. It keeps the server's `.env` and `models`, and refuses to run with uncommitted changes.
- **Owner password:** the owner doesn't remember the dashboard password. Use the `demo` login for the test call. To reset the owner password: `tools/hash_password.py` gives the new hash; set it as `DASHBOARD_PASSWORD_HASH` on the PC and in `/opt/emma/.env`, then restart (the owner types the password).
- **Known failing test:** `test_operations ...test_check_finds_a_booking_without_its_slot_claims` fails in this worktree on the code before 93c68f1 too. It depends on the worktree's own `data/emma.db`. Look at it later.
- **Oracle retry:** still running; about 95 tries by 10:04, all out of capacity.

**Audit and model tests (19 Oct, d15b429, deployed):**
- **Checks:** 1033 tests and 53/53 offline scenarios pass; pyflakes and vulture clean.
- **AWS server:** healthy; daily backups pass `--check`. A test booking call on the hosted site worked (17 Oct 10:02).
- **Done:**
  - **Demo role** (migration 007): the `demo` login can look and call, but every change is refused in `auth.require_staff`. The server's `demo` user was switched to it.
  - **`constraints.txt`:** exact versions (the server's set; the PC venv was upgraded to it and the suite passes); `install.sh` uses `-c`.
  - **Date-proof test:** the backup slot-claims test no longer depends on the demo bookings' dates.
  - **Sarvam client:** `llm.SarvamNLU`, `llm.stream_turn`, **off by default**.
- **Gemini:**
  - `gemini-3.5-flash-lite` stays the best Gemini model: about 1.25 s for a short request, 2.35 s for first words with Emma's real brief (over the 2.2 s head deadline).
  - 3.8 Flash: 4.7-5.7 s and mostly 503.
  - The 2.5 models are retired (404).
  - `GEMINI_FALLBACK_MODEL=gemini-flash-lite-latest` likely aliases the same model and failed at the same moments on 17 Oct. 3.6 Flash and 3.1 Flash-Lite measured slow or overloaded too, so it's unchanged.
  - **The free daily quota ran out in the live scenario run** (22 of 75 requests were 429s): on a busy day Emma runs on the fallback.
- **Second Gemini key:** the owner's `GEMINI_API_KEY_NEW` is also free, and rotating free keys breaks Google's terms, so it's unused. Remove it from `.env`; a paid key would replace the old one instead.
- **Sarvam (two `SARVAM_API_KEY` lines in the PC `.env`; the last one wins, and both work):**
  - With Emma's real brief, `sarvam-105b-conversations` (reasoning off, json_schema) gave first words in 0.72 s against Gemini's 2.35 s.
  - On the 53 **live** scenarios (`NLU_PRIMARY=sarvam`), the safety checks Z1-Z7 stayed at 0, but it re-asked answered questions 38/405, looped in 6/52 calls, and completed 29/31 bookings.
  - The no-model fallback scored 3/387, 0 loops and 31/31.
  - So it's off; `NLU_BACKUP=sarvam` or `NLU_PRIMARY=sarvam` turns it on.
  - Next step if wanted: tune the brief for Sarvam, then re-run the live scenarios.
- **Voice:**
  - Sarvam `bulbul:v4-flash`: first audio 410 ms against ElevenLabs flash 486 ms (HTTP, Sydney-free test from the PC).
  - Clips are in the worktree's `voice_samples/` (not committed).
  - **The owner listens and decides**; Sarvam's default voices are male.
- **Speech-to-text re-test (20 Oct, `tools/replay.py` scoring, the owner's two 6 Oct recordings): Deepgram stays.**
  - **Deepgram nova-3** with the name hints: word errors 16.8% / 17.3%, hears "Adharsh", end of speech noticed in p50 0.8-1.0 s and p90 1.0-1.1 s.
  - **Sarvam `saaras:v4`:** 19.6% / 19.6%, hears "Adarsh", p90 2.2 s.
  - **Sarvam `saaras:v3-realtime`** (the new default): 20.7% / 21.2%, p90 2.2 s.
  - Sarvam waits about a second longer to end a turn, which callers would hear. Same verdict as 6 Oct.
- **Voice switched to Sarvam (owner, 20 Oct, 84fd983 and 25ba731).**
  - **The setup:** `TTS_PROVIDER=sarvam` and `SARVAM_TTS_SPEAKER=ishita_en_customer` (Bulbul v4 Flash, linear16 16 kHz over HTTP streaming, `tts_sarvam.py`). ElevenLabs, then Piper, take a sentence Sarvam refuses.
  - **Where:** the PC's `.env` only. **The hosted site still uses ElevenLabs.** It needs `SARVAM_API_KEY`, `TTS_PROVIDER` and `SARVAM_TTS_SPEAKER` in `/opt/emma/.env` plus a deploy; the owner wanted the PC first.
  - **Cache:** pre-rendered one sentence at a time, because the free tier answered 429 to three in parallel; all 215 sentences in Ishita's voice (6,698 characters, about ₹13).
  - **Speed:**
    - On its own, first audio p50 0.62 s against ElevenLabs' WebSocket 0.33 s.
    - In a real call (a3f6caa4), live-spoken replies took p50 1.14 s against 1.10 s with ElevenLabs, because the next sentence is prepared while one plays. The owner kept Sarvam.
    - Idea if wanted: Sarvam's WebSocket TTS (`/text-to-speech/ws`) to cut the first-audio time.
  - **Test calls:** 77dd115a (the Sarvam brain test) and a3f6caa4 (the Sarvam voice test) both booked, and both bookings were cancelled (owner).
  - **Free-tier credits:** about ₹2 per call for the voice, ₹0.7 per call for the brain if switched on. The owner checks the balance on Sarvam's dashboard.
  - **Gemini key:** a key isn't tied to a model, so the "3.8 key" question was moot. Use one free key at a time (Google's terms); the owner was shown how to create one in AI Studio and check its tier.
- **Still open from the audit:**
  - A daily call limit for the demo login.
  - Mask phone numbers for the demo role: the server's database is a copy holding the owner's number.
  - Security headers on the talk page (the dashboard has them; only HSTS on `/`).
  - Off-machine copies of the server backups.
  - The Oracle retry dies when the Claude app restarts: it was restarted 19 Oct 23:14.

**Updating the server after a code or design change** (a design change reaches the hosted page only after this): `bash tools/deploy_remote.sh`. By hand, it does:
```bash
git archive --format=tar.gz -o emma.tar.gz HEAD
scp -i ~/.ssh/emma_oracle emma.tar.gz ubuntu@3.27.254.11:~/
ssh -i ~/.ssh/emma_oracle ubuntu@3.27.254.11 'rm -rf ~/emma-src/* && tar -xzf ~/emma.tar.gz -C ~/emma-src && cd ~/emma-src && sudo bash deploy/install.sh pearl-emma.duckdns.org'
```
`~/emma-src/.env` and `~/emma-src/models/` on the server must survive the update: `rm -rf ~/emma-src/*` keeps the dot-file `.env`, but re-download the Piper voice into `~/emma-src/models/piper` (see the 17 Oct session) or exclude `models` from the rm. The installer keeps `/opt/emma/.env`, the database and the backups.

**Cloud note:** a cloud session has no SSH key and isn't on the owner's PC.
- It **can** change code, run tests and push.
- It **can't** deploy to AWS, read the PC's `logs/` or `.env`, or watch the Oracle script. Those need the owner's PC (a local session), or the owner adding a new key for the cloud session.

**Oracle (free forever, no 6-month limit) is still being tried:**
- The `tools/oracle_launch.py` loop runs on the owner's PC in a Terminal tab ("Oracle retry"), one try every 3 minutes, alternating 2/12 and 1/6.
- By 17 Oct 09:30: about 75 tries, all "out of capacity".
- It needs the PC on, plugged in and the Claude app open. It keeps the screen on, because Modern Standby paused it from 05:27 to 07:53.
- When it prints SERVER READY (IP in `%USERPROFILE%\.oci\emma_server.txt`): install Emma there the same way (Oracle's Ubuntu also needs 80/443 opened in iptables), point DuckDNS at the new IP, then stop the AWS instance before the credit or the plan runs out.

**Left, Claude:**
1. **A test call on the hosted site** from a phone browser with the owner (microphone, audio both ways, a booking showing on the hosted dashboard). Not done yet: HTTPS went live at 09:40 and the session ended there.
2. **Write the AWS hosting into docs/DEPLOY.md** ("Tested on AWS"): the results above, the update command, and the Free-plan region lock.
3. ~~`tools/deploy_remote.sh`~~ (done 17 Oct, 93c68f1; local session only, it needs the key).
4. Then the task list below: latency re-measure (2), design changes (3), final HANDOFF (4).

**Left, owner:**
- Make the test call with Claude (above).
- Put `https://pearl-emma.duckdns.org` and the demo login on the resume.
- Optionally earn the extra AWS credits.
- Keep the PC on for the Oracle script.
- O2 (design list), O4 (network) and O5 (accounts) below.

## TASK LIST (written 15 Oct, afternoon)

Done today, afternoon: the Reject test on a recovery call passed (item 3 below); the old v2 branch is kept as the tag `archive/dashboard-v2-apple-wip`. Everything is pushed (`day2-r2-engine` = `main`).

**Claude's work, in order:**
1. ~~**Hosting the talk page**~~ (live 17 Oct on AWS; see START HERE above; Oracle still being tried).
2. **Latency re-measure (L1/L2).** The 12 Oct list said to re-measure after the Phase 3 phone calls, which are now done:
   - re-run `tools\preflight.py` for Gemini's first-word time against `NLU_HEAD_DEADLINE_S` (2.2 s);
   - recompute the endpoint and perceived p50 for phone turns from `logs/turns.jsonl` (main checkout);
   - change `.env` only if the numbers say so (owner's rule: measure first).
3. **The owner's design changes** (item 4 below), as soon as the owner sends the list (O2). Click-test each change in the browser, light only.
4. **Final HANDOFF "state at the end".** The START HERE of docs/HANDOFF.md is still 14 Oct, and its "Still to do" line is stale. Then mark FINISH_PLAN Phase 7 closed and push.
5. **Later (only if the owner wants it):** multi-tenancy with a shared `clinic_id` database (REAL_CLINIC_GAPS). Today it's one deployment per clinic with the name read from `.env`.

**The owner's part:**
- ~~**O1.** Choose hosting~~ (done: Oracle chosen, out of capacity; AWS Free plan used meanwhile, 17 Oct).
- **O2.** Send the list of design changes ("lots of changes").
- ~~**O3.** Delete the old remote branch~~ (done by the owner, 15 Oct; the tag `archive/dashboard-v2-apple-wip` keeps its commits).
- **O4. Network** (item 5): a Wi-Fi router; on the phone, Linphone battery Unrestricted, G.722 first, adaptive jitter on; change the hotspot password if it is "password". Optional: re-run phone check 6 on the router.
- **O5. Accounts:**
  - a real Indian number (T1, docs/TRUNK.md); this also lets recovery calls ring the patient's own number (C2);
  - a WhatsApp/SMS provider (T2, docs/NOTIFICATIONS.md).

  Claude connects each one once the account exists.
- ~~**O6.** Delete the `CHARIOT_API_KEY` line from `.env`~~ (done by the owner, 15 Oct).

## Session notes (written 15 Oct, ~02:00)

**What happened in the session of 15 Oct, 01:00-02:00** (worktree `claude/continue-work-b9e1f9`, fast-forwarded into `A:\Voice-Agent`):
- **Done:** the 44f15bd6 "change the branch" bug (0d9c4c5); the three 14 Oct test bookings cancelled; phone_up keep-alive replaced by WSL's own `instanceIdleTimeout=-1` (182bd00, tested); PR #6 closed; pushed. Details in items 1, 2, 6 and 7 of the list below.
- **Emma still runs the old code:** restart her (`.\.venv\Scripts\python.exe server.py` in `A:\Voice-Agent`) to pick up the "change the branch" fix.
- **Phones:** the "visible terminal" keep-alive tab below is no longer needed; running `phone_up` once is enough (it checks the `.wslconfig` setting). Both phones re-registered by themselves after a `wsl --shutdown`.
- **Chariot TTS (owner's idea, dropped by the owner):** Chariot (platform.chariot.in, docs.chariot.in) is an Indian TTS service with 21 Indian female voices, HTTP streaming at 44.1 kHz PCM, 1 credit per character. The owner's key is `CHARIOT_API_KEY` in `A:\Voice-Agent\.env` (10,000 credits until 13 Nov; 1,704 used by the trial). Measured from this PC: first audio about 150-300 ms warm, the same as ElevenLabs (about 220 ms). The owner stopped the trial before the listening page was made and said "no need of Chariot": the ElevenLabs voice stays (NORTH_STAR). The trial script and clips were in the session's scratchpad only. The owner can delete the key line from `.env`.
- **Open question from the owner: "launch the talk page on Vercel-like free platforms and talk to Emma from any device".** Answered, not decided yet:
  - Vercel / Netlify / Cloudflare Pages can't host Emma: the talk page streams audio over a long-lived WebSocket (`/ws/voice`) to the Python server, which holds the SQLite database and runs up to 3 calls.
  - **A (recommended first): Cloudflare Tunnel from this PC.** Free HTTPS address (the microphone needs HTTPS), no code change, about 15 minutes; works only while the PC is on.
  - **B: Oracle Cloud Always Free VM** (Mumbai/Hyderabad) with `deploy/install.sh` (tested in WSL, docs/DEPLOY.md) and a free domain for the certificate; runs 24/7; about an hour; the owner opens the account.
  - Either way keep the talk-page login: every call spends Deepgram, ElevenLabs (free tier 10,000 characters a month) and Gemini credits.
  - Next step: ask the owner A or B (MCQ, A recommended), then set it up.

**Still to do:** see TASK LIST at the top.

## Previous list (written 15 Oct, ~01:00; items 1, 2, 6, 7 done since)

**What happened on 14 Oct, evening:**
- **Redesign live:** the Apple-style redesign of the talk page, sign-in and dashboard was built, click-tested and made live. The plan, the REA evidence ledger and the feature checklist are in [APPLE_DESIGN_PLAN.md](APPLE_DESIGN_PLAN.md) (sections 8-10). Owner's choices:
  - light only, with apple.com's colours exactly;
  - a glass sidebar;
  - a Today tile grid;
  - Live call as Messages;
  - Appointments as Apple Calendar (no drag-to-move);
  - Tasks as Reminders, Calls as Mail, System as Settings.

  Earlier redesign attempts (PR #6, branch `claude/dashboard-v2-apple-wip`) are superseded and can be closed.
- **Dead code removed** (12b7e9e), with pyflakes, vulture and whole-repo searches as proof.
- **Fixes from the owner's phone calls:**
  - a closed day is said as soon as the day is given (no "morning or evening?" first);
  - two times on one day name the day once ("Friday the 16th at 9 or 9:30");
  - the live view shows the caller's words from the stored turn;
  - "tell me the services" reads the full list instead of offering a callback.

  Each fix has a test. Result: 1017 tests, 53/53 scenarios, and a 200-call sim (seed 7) on target.
- **Owner's test calls:** Linphone (two bookings), MicroSIP, and the talk page with the microphone (worked well).

**Before testing anything with phones:**
- Start `phone_up` **in a visible terminal**, then keep a tab attached to Ubuntu open the whole time:

  ```powershell
  powershell -ExecutionPolicy Bypass -File tools\phone_up.ps1
  wsl -d Ubuntu-24.04 -u root -- tail -n 0 -F /var/log/asterisk/messages.log
  ```

  The second command is the keep-alive: leave that tab open.
- Why: on 14 Oct the hidden WMI keep-alive (`wsl --exec sleep infinity`) died twice under WSL 3.0.3. Ubuntu then stopped about 4 minutes later and took Asterisk with it, and Linphone and MicroSIP showed offline. After Asterisk restarts, restart Linphone and MicroSIP.
- Emma's phone number is **100** (from Linphone 1001 or MicroSIP 1002).

**To do, in order:**
1. ~~**Bug (talk-page call 44f15bd6)**~~ (done 15 Oct): "change the branch / service / day / time" with no new value now clears that detail in `book._reopen` and asks it again ("Sure. We also do cleanings at Indiranagar, Jayanagar or Whitefield. Which would you like?"; lines `ask.*.instead`). "Can we do a different day?" is the change, not a clinic question. Tests: `ChangeWithoutValueTests` in tests/test_real_clinic_calls.py. 1022 tests, 53/53 scenarios, seed-7 sim on target. Goes live at Emma's next restart.
2. ~~**Cancel tonight's three test bookings**~~ (done 15 Oct, owner approved): Adharsh 9:00 and 9:30 (Dr Rao, Nagarbhavi) and Jimmy 9:30 (Dr Kulkarni, Jayanagar) on Fri 16 Oct cancelled through `scheduling.cancel` (actor "owner", reason "Test booking (owner, 15 Oct)"); the calendar removals went through the sync outbox.
3. ~~**Reject test on a recovery call**~~ (passed 15 Oct, 14:46): a test booking (Adharsh, cleaning, Dr Rao, Tue 20 Oct 11:00), the owner blocked Dr Rao for that day on the dashboard and started campaign 9; Linphone (1001) rang and the owner pressed Reject. Linphone answered **SIP 603 Decline**; job 15 ended `failed` / `declined` after 1 attempt, no retry queued; the Recovery tab shows "Declined the call"; task 10 (`recovery_failed`, high) was created. Cleaned up afterwards: block 17 lifted, the booking cancelled (reason "Test booking (owner, 15 Oct)"), task 10 marked done.
4. **Owner's design feedback.** The owner said there are "lots of changes"; ask for the list and work through it.
5. **Network (owner's choice):** the voice breaks on Linphone calls came from the phone, which hosts the hotspot and runs Linphone at once. Emma's own audio left the PC clean (3 late frames per call). One MicroSIP call had a single "Emma's live audio ran dry" stall, caused by the ElevenLabs stream over mobile data.
   - The real fix is a Wi-Fi router.
   - Suggested phone settings: Linphone battery set to Unrestricted, G.722 first, adaptive jitter on.
   - Change the hotspot password if it really is "password".
   - Don't raise `PHONE_PREBUFFER_MS` (250 ms) without measuring: it delays every reply.
   - Note: the "gaps" count in `line timing` includes the softphone pausing its audio during silence, so it isn't packet loss.
6. ~~**phone_up keep-alive**~~ (done 15 Oct): WSL 3.0.3 has its own setting, `[general] instanceIdleTimeout=-1` (found in wslservice.exe). phone_up now adds it to `%USERPROFILE%\.wslconfig` when missing (old file kept as `.wslconfig.bak`); it is set on the owner's PC. Tested: after `wsl --shutdown` and a start with no keep-alive, Ubuntu was still running 5 minutes later with no WSL client at all (it used to stop in 1-4 minutes). Both phones re-registered by themselves. The WMI keep-alive stays as a backup (it had died again before this fix).
7. **Optional:** ~~close PR #6~~ (closed 15 Oct with a "superseded" note). The remote branch `claude/dashboard-v2-apple-wip`: the owner asked to delete it (15 Oct, afternoon). Its tip ee63c26 (3 commits not in `main`) is kept as the tag `archive/dashboard-v2-apple-wip` (pushed). The branch deletion itself was blocked by the permission check again, so the owner deletes it by hand: `git push origin --delete claude/dashboard-v2-apple-wip` (or GitHub → Branches → delete). The REA residual unknown `unk_d588f18b` can't be resolved from a new session: REA's unknowns store was empty on 15 Oct, so it lived only in the 14 Oct REA session. Low severity, and our own pages were verified in the browser; it is dropped.
8. **Still needs the owner's accounts:** a real Indian number (T1, docs/TRUNK.md) and a WhatsApp/SMS provider (T2, docs/NOTIFICATIONS.md).
9. **Hosting the talk page (owner's request, 15 Oct): talk to Emma from any device.** Options and reasons are under "Open question" in the 15 Oct session notes above. Steps:
   1. Ask the owner A or B (MCQ, A recommended).
   2. **A, Cloudflare Tunnel:** install `cloudflared` on this PC, run a tunnel to `http://localhost:8000`, open the HTTPS address on a phone, sign in, and make one call from the phone's browser (microphone, audio both ways, a booking shows on the dashboard).
   3. **B, Oracle Cloud VM:** the owner opens the Always Free account (Mumbai/Hyderabad); then `deploy/install.sh` per docs/DEPLOY.md, a free domain, Caddy's certificate, and the same one-call check.
   4. Either way the talk-page login stays on, and the address isn't shared publicly (every call spends Deepgram, ElevenLabs and Gemini credits).
   5. Write the result into docs/DEPLOY.md.

   **State (15 Oct, evening): the owner chose B.** It's mainly for the owner's resume: recruiters open the link.
   - **Decisions:**
     - a copy of the PC's database (the owner kept this after being told it holds their phone number and test transcripts);
     - Google Calendar sync on the PC only: `CALENDAR_SYNC_ENABLED=false` on the server, no `secrets/` copied;
     - DuckDNS for the address;
     - a `demo` staff login for recruiters (talk page stays behind the login, `TALK_PAGE_PUBLIC=false`);
     - `TELEPHONY_ENABLED=false` on the server (phones stay on the PC).
   - **Done:**
     - Oracle account, home region India South (Hyderabad), tenancy `adharsh1001r`;
     - SSH key on this PC at `~/.ssh/emma_oracle` (no passphrase), pasted into the create form;
     - create form: Ubuntu 24.04 (not 26.04: Python 3.12), VM.Standard.A1.Flex 2 OCPU / 12 GB, new VCN plus a new public subnet, burstable off, shielded off.
   - **Blocked: "Out of capacity" for A1 in AD-1** (Hyderabad has one AD). It failed at 2/12 and at 1/6. The owner retries tonight (option 2). If it keeps failing, upgrade to Pay As You Go (explained to the owner: free inside Always Free limits, set a ₹100 budget alert); a second account is against Oracle's terms.
   - **16 Oct:**
     - **Retries:** still out of capacity. Clicking Create too often gives "Too many requests" (wait about 5 minutes between tries).
     - **Pay As You Go:** the "Upgrade your account" button is greyed out (Payment Method shows "-"). Retry in a day or two, or ask Oracle support.
     - **Other free hosts, checked 16 Oct on the providers' own pages:**
       - **Azure for Students:** no card; $100 credit for 12 months, renewable while a student; 750 h/month free B1s/B2pts v2/B2ats v2 (about 1 GiB RAM; tight for Emma, add swap). Needs a college email.
       - **AWS Free plan:** up to $200 over 6 months; Mumbai region; card needed. The account closes after 6 months unless upgraded.
       - **Google Cloud:** an always-free e2-micro (1 GB), US regions only, 1 GB of outbound data a month; $300 trial for 90 days.
       - **Render free:** 512 MB; sleeps after 15 minutes idle (about 1 minute to wake); local files (SQLite) lost on every sleep or restart. Unsuitable.
       - **Koyeb:** no free plan any more ($29/month Pro).
       - **Hugging Face Spaces:** Docker and Gradio Spaces now need a paid PRO plan to create. Not free.
       - **GitHub Student Pack:** no DigitalOcean any more; Heroku $13/month for 24 months (US/EU, ephemeral disk, so SQLite is lost); a free .TECH domain for 1 year.
   - **16 Oct: Oracle only** (owner's decision; the free-host list above is for reference, nothing else is being set up).
   - **17 Oct, ~01:20: auto-retry running.**
     - **Why:** the Pay As You Go upgrade failed (the owner's debit card was declined: insufficient funds for Oracle's verification hold), so the owner can't upgrade.
     - **The script:** `tools/oracle_launch.py`, using Oracle's Python SDK in its own venv `%USERPROFILE%\oci-venv` (not Emma's .venv) and the API key in `%USERPROFILE%\.oci\` (`config`, `oci_api_key.pem`). It reuses the console's network `vcn-20261016-0134` / `subnet-20261016-0134`, and opened TCP 80/443 in its default security list. It makes one launch try every 240 s, alternating 2/12 and 1/6 (a second call within seconds gets 429).
     - **When it succeeds:** it prints SERVER READY with the public IP and writes `%USERPROFILE%\.oci\emma_server.txt`.
     - **To run it:** `C:\Users\adhar\oci-venv\Scripts\python.exe tools\oracle_launch.py`; add `--check` for read-only.
     - **Afterwards:** only DuckDNS is left for the owner, then Claude's install steps above.
   - **Known console quirk:** the public IPv4 switch is greyed out when the form creates the VCN. Add an ephemeral public IP after creation (VNIC → IP administration → Edit).
   - **After the owner sends the IP and DuckDNS name (Claude):**
     1. `ssh -i ~/.ssh/emma_oracle ubuntu@<ip>`.
     2. Open 80/443 in iptables: Oracle's Ubuntu image rejects all but 22 in iptables, so open them there as well as in the security list.
     3. Copy the repo (git archive), the `.env` with calendar and telephony off, and `data/emma.db` (stop Emma or use `tools/backup.py`).
     4. Run `sudo bash deploy/install.sh <name>.duckdns.org`.
     5. Add the `demo` staff user.
     6. Make one call from a phone browser, then write the result into DEPLOY.md.

## TOMORROW (written 14 Oct, night): start here

Everything is committed and pushed (`day2-r2-engine` = `main`). Phone checks 1-8 were done 12-14 Oct
(results in docs/PHONE_CHECKS.md); the final state is in docs/HANDOFF.md "START HERE (14 Oct)".

**To do, in order:**
1. **Before 9 am:** last night's test campaign has two retries queued for 9 am that will ring the owner's
   phone. Either stop it (dashboard → Recovery → stop the campaign, lift the Dr Rao test blocks), or let one
   ring and use it for step 2.
2. **Re-test a Reject live (between 9 am and 8 pm):** block Dr Rao on the Recovery tab for a day with a booking,
   start the campaign, press Reject on the phone. The dashboard must show "declined", not "no answer"
   (fixed in 3519890: Linphone's Reject arrives as reason "0" about 2 s in).
3. **Optional clean re-run of check 6 on a Wi-Fi router** (not the phone hotspot, which lost 10-50% of the
   caller's audio): answer Emma's recovery call and move the booking to another day with Dr Rao.
4. **One browser call** on the talk page (http://localhost:8000): book something, then check it on the dashboard.
5. **Then:** final push (docs only) and close Phase 7.

**Before testing:** run `.\tools\phone_up.ps1` (it prints any firewall commands for an admin window); start
Emma from `A:\Voice-Agent`; restart Linphone/MicroSIP if they don't show registered.

**Notes:** WSL (Asterisk) now lives on `A:\WSL\Ubuntu` (moved 14 Oct after C: filled up; C: had 4 GB free
after). If C: is ever full, run tests with `TEMP`/`TMP` set to `A:\emma-tmp`. Still needing the owner's
accounts: a real Indian number (T1, docs/TRUNK.md) and a WhatsApp/SMS provider (T2, docs/NOTIFICATIONS.md).

## START HERE (written 12 Oct): the next session continues from this list

**WIP commit verified (12 Oct, later session):** 1058 tests pass, 53/53 scenarios, all four 200-call sims on target (Z1-Z7 = 0; M3 0-0.5%; M7 99-100%; M10 0-1%; T5 = 9). Fixed on the way, each with a test: the travel buffer is now applied when *offering* times too (Emma said "that's free" and then refused it at booking, five times in one call); small talk without the model gets a friendly reply instead of "Honestly, I don't know that one", and "raining a lot today" is no longer taken as the day; "move my appointment" again right after a move reopens the move (it said "anything else?" and hung up); a turned-down time asked for by name is offered again; at "another branch or a later date?" the same branch or "later" searches the week after (it looped seven times).


The to-do list with everything found by the 11 Oct audit is **docs/REAL_CLINIC_GAPS.md**. Done since 11 Oct (all committed, pushed up to 0a974ee):
- Live transcript paced to the audio; 26 clinic facts and doctor profiles (complete answers, owner's rule); dashboard D1-D8 with a performance panel; up to 3 calls at once.
- Closed-hours emergencies, bleeding first aid, names, late patients, wrong numbers, sales and staff calls, records requests, doctors on leave, urgent hang-ups, past-treatment dates.
- WhatsApp/SMS messages ready to plug in (notify.py, log-only; docs/NOTIFICATIONS.md); trunk dialling for recovery calls (TELEPHONY_TRUNK_DIAL; docs/TRUNK.md).
- 17 more rule lines pre-rendered (latency L3); family bookings (second person in the same call, own name; caps 3 per patient / 8 per number); reschedule instead of a false "Got it" on a booked draft.
- Kannada/Hindi callback offer without the model; DPDP retention + export/erase by phone (privacy.py); holidays and closures from the dashboard; named staff logins (staff.py, migration 005); deployed talk page needs a login; installer writes a session secret.
- 90-minute travel buffer between one patient's appointments at two branches (TRAVEL_BUFFER_MIN); a foreign number while Emma takes the number gets "I need an Indian number" (handlers `_FOREIGN_NUMBER`, line `phone.foreign`).

Owner-visible changes to mention: `LOG_CALLER_TEXT` now defaults to false (set it to true in `.env` for development logs); the shared dashboard password is audited as "owner" (staff accounts on System tab); the cache budget rose to 4,800 characters (about 1,900 ElevenLabs characters rendered once at the next start).

**Left, in order:**
1. ~~Verify the WIP commit~~ (done 12 Oct, see above).
2. ~~Remaining booking edge cases (P2)~~ (done 12 Oct; 1067 tests, 53/53 scenarios, four sims on target): landlines read back as STD code + number, `phones.said_as_landline` for 080 numbers; `scheduling.patient_norm` merges only identical sound-alike keys (so "Adesh" vs "Adharsh", a true mishearing, is still two patients); `clock.call_day` pins "today" per call; `scheduling.short_notice_move` (same day, same doctor, 30 min ahead; another day is still the too-late callback); a child's age asked once for "my son/daughter".
3. ~~Latency L1/L2~~ (measured 12 Oct, no change): **L1** owner chose "measure first"; `tools/preflight.py` timed Gemini's first words at 1.3, 1.8, 1.9 s, inside `NLU_HEAD_DEADLINE_S` 2.2 s, so the 8 Oct misses look like a slow day; `.env` unchanged (re-run preflight before Phase 3). **L2** from logs/turns.jsonl (230 phone turns): the watchdog ends 46% of phone turns but at the same moment as Deepgram's speech_final (endpoint p50 1.22 s vs 1.21 s; perceived p50 1.86 s vs 1.97 s), so it adds no delay; shortening it would risk cutting callers off mid-number. Only utterance_end is slow (2.5 s) and it is 4% of phone turns. Re-measure after the Phase 3 phone calls.
4. ~~P2 SaaS items~~ (done 12 Oct, c2c37f3): clinic name/city from `.env` (`CLINIC_NAME`, `CLINIC_SHORT_NAME`, `CLINIC_CITY`; pages.py for the web pages); cost per call (usage.py, migration 006, `COST_*` rates, dashboard panel); alerts banner (alerts.py: speech service down, calendar sync stalled, stuck call line); `scheduling.prune` for holds and old slot claims; Caddy refuses `/health` and `/metrics` from outside. **Still open:** ~~retiring the legacy 12-step engine~~ (done 12 Oct, 17cfa9c: R2 is the only engine; `R2_ENGINE` in `.env` is now ignored); ~~hours, doctors and rotas from the dashboard~~ (done 12 Oct: System tab "Doctors and hours", clinic_admin.py); the honesty line is now "Good question! I'm Emma, the clinic's virtual receptionist." (owner, 12 Oct).
5. ~~EVALUATION, HANDOFF, REAL_CLINIC_GAPS~~ (done 12 Oct): EVALUATION.md regenerated with fresh runs and the crash drill; HANDOFF START HERE (12 Oct); every audit item marked done or open with what's missing.
6. ~~Phase 3 live phone checks~~ (done 12-14 Oct, docs/PHONE_CHECKS.md; fixes pushed). 7. ~~Phase 7 final checks~~ (13-14 Oct: suite, scenarios, sims, crash drill, preflight READY, phone calls). **Left:** one browser call on the talk page; re-try a Reject on a recovery call after 9 am; T1/T2 need the owner's accounts.

How to see the dashboard without the owner's password: a review copy on port 8001 with a copied database and a throwaway test password (see the 11 Oct session: `.claude/launch.json` entry `emma-review` running a scratch `review_server.py`, not committed).

## Done on 11 Oct

- **Step 2 (sims):** 992 tests, 53/53 scenarios, the four 200-call sims all on target: Z1-Z7 all 0; M3 0-0.5%; M7 97.9-100%; M10 0-1%; **T5 = 9 in all four** (was 10).
- **Step 4 (deploy check in WSL): done.** Throwaway install from a `git archive` copy (no `.env`, no `secrets/`), all checks passed, including a distro restart; then disabled. Two bugs fixed (backup left `-wal/-shm` files; `.env.example` CID line unparseable). Results in DEPLOY.md, "Tested in WSL".
- **Step 5 (EVALUATION):** the phone section shows the 8 Oct calls: model turns p50 3.13 s and 63/84 answered by the fallback (Gemini slow that day); simple turns p50 1.63 s, the same as in the browser.

## What was done on 8 Oct (after the demo, which included a real phone call)

- **Phase 0 (done):** all 22 demo-day calls replayed through the engine; ~20 conversation bugs fixed with tests in `tests/test_call_fixes_8oct.py` (31 tests): phantom name changes, the duplicate loop, digits taken from dates, "I only know the name", "can you hear me?", a time said right after booking now moves that booking, AM/PM from context, requested times that can't be had are said, "change that one" no longer ends the call, "Nothing." no longer ends the call, mumbling, cancel-flow wording, insistent cancel/move callers (one more look, then the callback, then a kind goodbye). Validator: no claimed changes, no invented policies (V9), no "speech-to-text", no "Mr <name>".
- **Phase 1 (done):** pushed; PRs merged into `main`.
- **Phase 2 (done):** `tools/phone_up.ps1` (keeps Ubuntu running, re-binds SIP on a new Wi-Fi address, checks Asterisk, prints firewall commands, lists phones); Asterisk accepts SRTP or plain audio.
- **Phase 5 (partly):** TELEPHONY.md ("Every time: phone_up", what broke on 8 Oct), VIVA_NOTES (Asterisk/SIP/RTP/UDP, demo lessons), HANDOFF START HERE, FINISH_PLAN phases 0 and 2 marked done.
- **Phase 6 (code done, one check open):**
  - Duplicate question only when the same service is already booked (owner chose this). Seed-7 faked-model sim afterwards: T5 = 9.0 (target met), M3 0%, M7 97.9%, M10 1%, all Z checks 0.
  - 27 common Indian first names added as Deepgram keyterms after the clinic words (`config.DEEPGRAM_NAME_KEYTERMS`, cap 90; Deepgram allows 100). Measured 11 Oct: the owner's name now heard right (step 3).

## Work left, in order

1. ~~Push~~ (done 9 Oct).
2. ~~Re-run the four sims and the scenarios~~ (done 11 Oct, all on target).
3. ~~Name test~~ (done 11 Oct, owner approved): with the 27 name hints, "Adharsh Kumar" is heard right in both recordings (6 Oct: "Adesh"); word error 31 and 32 of 179 went to 30 and 30 (17%). Hints stay on. Results in `logs/stt_eval.json` (main checkout) and EVALUATION.md section 3.
4. ~~Phase 4, deploy check in WSL~~ (done 11 Oct; results in DEPLOY.md "Tested in WSL"; the test copy stays in `/opt/emma`, disabled).
5. ~~Phase 5, rest~~ (done 11 Oct): EVALUATION.md regenerated with fresh runs and the crash drill; it now reports browser and phone calls apart (the turn log records the channel from 11 Oct; the 8 Oct phone calls are found by caller ID). DEMO_SCRIPT notes the demo happened. Recognition section refreshed after the name test.
6. **Phase 3, with the owner holding the phone (next session, ~20 min):** run `tools\phone_up.ps1` first (and the firewall commands it prints, in an admin window). Then: interrupt Emma mid-sentence; key a number with `#` (do keypad frames reach Emma?); "I want to talk to a person" twice → MicroSIP 1002 rings; block Dr Rao on the dashboard → recovery call to the phone, answer once, reject once (which reason code does Reject send? adjust `ami.REASON_DECLINED`); does Asterisk send 16 kHz audio? Also watch for calls that end at the greeting with nothing heard (5 on 8 Oct). Results into TELEPHONY.md.
7. **Phase 7:** full suite, scenarios, 4 sims, crash drill, preflight READY, one browser and one phone call; push; final HANDOFF "state at the end".

## Facts a new session needs

- **Phone line:** Asterisk 20.6 in WSL Ubuntu-24.04. Linphone on the owner's phone = 1001 (Media encryption None or SRTP, transport UDP, register URI `sip:<PC Wi-Fi IP>;transport=udp`); MicroSIP on the PC = 1002 at `127.0.0.1`, source port 5070. SIP passwords are in `A:\Voice-Agent\.env` (`TELEPHONY_SIP_PASSWORD_1001/1002`), never commit them.
- **The PC's Wi-Fi address changes** with each hotspot (10.49.155.x, 10.69.54.x, 10.221.82.x on 8 Oct). `phone_up.ps1` handles it; the firewall rules (`Emma SIP (Wi-Fi only)` and Hyper-V `EmmaSIP`) need the owner's admin window.
- **WSL stops Ubuntu** a minute after its last window closes. `phone_up.ps1` starts a keep-alive via WMI. From Git Bash, run root commands as `MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/<script>.sh` (inline `bash -c` quoting from Git Bash is fragile).
- **Gemini** was slow on 8 Oct (~2.1 s against `NLU_HEAD_DEADLINE_S` 2.2 s), so many replies used the fallback path; `tools\preflight.py` suggests a value. The owner hasn't changed it.
- **Campaign 3** had recovery retries queued for the evening of 8 Oct; check the dashboard's Recovery tab and stop old campaigns before testing.
- Editing with Python heredocs mangles `\n` and backslashes: use the Edit tool for code with escapes.
- The owner prefers decisions as multiple-choice questions with a recommended option, and plain step-by-step instructions for anything they do by hand.
- VoiceLink (a real Indian number) is **dropped**.
