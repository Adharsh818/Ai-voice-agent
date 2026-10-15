# Resume here (updated 15 Oct 2026)

When the owner says **"continue your work"**, start from this file. The full plan is [FINISH_PLAN.md](FINISH_PLAN.md); project history is in [HANDOFF.md](HANDOFF.md).

## Where everything is

- **Code:** branch `day2-r2-engine` = `main` on GitHub up to f5cd820; later commits may be local (check `git status -sb`). The working copy Emma runs from is the main checkout **`A:\Voice-Agent`** (it has `.env`, `data/`, `logs/`, `captures/`). Sessions may work in a git worktree under `A:\Voice-Agent\.claude\worktrees\...`; fast-forward the main checkout with `git merge --ff-only <worktree branch>` before running Emma.
- **PRs #1-#5:** all merged (8 Oct): `main` was fast-forwarded to `day2-r2-engine`; #5 merged into its base.
- **Tests:** `.\.venv\Scripts\python.exe -m unittest discover -s tests` (about 1050, all passed before the WIP commit; `test_dashboard ... test_lists_filter_and_search` is flaky under load: real clock).
- **Scenarios / sims:** `.\.venv\Scripts\python.exe -m harness run scenarios --engine r2 --offline`; sims `... run sim --engine r2 --offline|--nlu-down --n 200 --seed 7|11 --concurrency 2`.

## Pushing

Push from `A:\Voice-Agent`: `git push origin day2-r2-engine` and `git push origin day2-r2-engine:main` (owner approved push + merge into main on 8 Oct). Pushed up to 182bd00 (15 Oct, ~01:45); the two RESUME_HERE commits after it are local only.

## START HERE (written 15 Oct, ~02:00): the next session continues from this list

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

**Still to do, in order:** the hosting choice above; then items 4 (design feedback list), 5 (network) and 8 (owner's accounts) below.

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
7. **Optional:** ~~close PR #6~~ (closed 15 Oct with a "superseded" note). The remote branch `claude/dashboard-v2-apple-wip` stays on GitHub (owner's choice, 15 Oct). The REA residual unknown `unk_d588f18b` can't be resolved from a new session: REA's unknowns store was empty on 15 Oct, so it lived only in the 14 Oct REA session. Low severity, and our own pages were verified in the browser; it is dropped.
8. **Still needs the owner's accounts:** a real Indian number (T1, docs/TRUNK.md) and a WhatsApp/SMS provider (T2, docs/NOTIFICATIONS.md).

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
