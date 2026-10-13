# Resume here (updated 12 Oct 2026)

When the owner says **"continue your work"**, start from this file. The full plan is [FINISH_PLAN.md](FINISH_PLAN.md); project history is in [HANDOFF.md](HANDOFF.md).

## Where everything is

- **Code:** branch `day2-r2-engine` = `main` on GitHub up to f5cd820; later commits may be local (check `git status -sb`). The working copy Emma runs from is the main checkout **`A:\Voice-Agent`** (it has `.env`, `data/`, `logs/`, `captures/`). Sessions may work in a git worktree under `A:\Voice-Agent\.claude\worktrees\...`; fast-forward the main checkout with `git merge --ff-only <worktree branch>` before running Emma.
- **PRs #1-#5:** all merged (8 Oct): `main` was fast-forwarded to `day2-r2-engine`; #5 merged into its base.
- **Tests:** `.\.venv\Scripts\python.exe -m unittest discover -s tests` (about 1050, all passed before the WIP commit; `test_dashboard ... test_lists_filter_and_search` is flaky under load: real clock).
- **Scenarios / sims:** `.\.venv\Scripts\python.exe -m harness run scenarios --engine r2 --offline`; sims `... run sim --engine r2 --offline|--nlu-down --n 200 --seed 7|11 --concurrency 2`.

## Pushing

Push from `A:\Voice-Agent`: `git push origin day2-r2-engine` and `git push origin day2-r2-engine:main` (owner approved push + merge into main on 8 Oct). Pushed up to the WIP commit of 12 Oct.

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
