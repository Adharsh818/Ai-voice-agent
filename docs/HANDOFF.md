# Project memory and next steps

**Last updated:** 6 Oct 2026, end of session (see START HERE; Day 7 docs and demo tools; Day 6 latency and drills, Day 4 finished, Day 5 built; section 0 is from 5 Oct, sections 1-7 from 1 Oct) · **Demo:** Thursday 8 Oct 2026 · **Owner:** Adharsh (GitHub `Adharsh818`)

Read [NORTH_STAR.md](NORTH_STAR.md) first, then this file, then [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) (section 0 is the current priority). This file records what happened in the working sessions of 30 Sep – 1 Oct, the decisions made, the owner's latest feedback, and what to do next.

---

## ▶ START HERE (state at the end of the 5-6 Oct session)

**Where the code is.** Branch `day2-r2-engine`, everything pushed; PR [#5](https://github.com/Adharsh818/Ai-voice-agent/pull/5) is open (stacked on #1-#4, none merged). 900 tests (`.\.venv\Scripts\python.exe -m unittest discover -s tests`), all passing.

**Done in this session (details in the dated sections below):** R2 switched on for voice tests; Day 4 (takeover), Day 5 (recovery calls, `/patient` page), Day 6 (latency, fault drills), Day 7 (ARCHITECTURE.md, DEMO_SCRIPT.md, `tools/demo_reset.py`, `tools/preflight.py`), Day 8 prep (typed backup without a mic, Gemini warm-up, slow-check fix), listening rebuilt on the owner's recordings (`vad.py`, `tools/replay.py`), R2.7 and R3.4 bake-offs (Flash-Lite and Nova-3 stay; Sarvam tested), one-yes booking, C-full recovery calls (retries, call-back times, pause/resume, calling hours, reports, migration 002).

**Last change, committed at the end of the session, not yet heard by the owner on a call** (owner's request: "it should always mention its name Emma"; "its latency is too high"):
- Every greeting names Emma and asks one question (`config.GREETINGS`, `config.TIME_GREETINGS` for morning/afternoon/evening, at most 12 words). A bare "yes" to "Would you like to book an appointment?" starts a booking; to "Any questions about the clinic?" Emma says "Sure, what would you like to know?" (`dialogue/engine.py` `_answer_to_greeting`).
- Faster replies: when Emma's own rules clearly understood the turn (a name, number, yes, plain request), Gemini gets only `NLU_FAST_DEADLINE_S` = 0.8 s instead of 2.2 s (`dialogue/engine.py` `_clear_enough`); the typing beat is 150-300 ms (was 300-500). Reason: on 6 Oct Gemini took 1.8-6 s to start answering; no Flash-Lite version is faster (3.5-flash-lite ≈ flash-lite-latest ≈ 1.8 s median; 3.1 slower; 2.5 retired).

**Next steps, in order:**
1. **Owner voice test** (Chrome, http://localhost:8000): check the new greetings and that replies feel quicker; watch for cut-offs or worse understanding (the fast path uses Emma's written lines more often). If needed: `NLU_FAST_DEADLINE_S` up, or the typing beat back.
2. **Rehearsals** (plan 7.3): three runs of [DEMO_SCRIPT.md](DEMO_SCRIPT.md), one screen-recorded; reset with `tools/demo_reset.py` (Emma stopped) before each. Fix what they find, then **code freeze** (7.1).
3. **Demo, Thu 8 Oct:** stop Emma → `tools/demo_reset.py` → start Emma → `tools/preflight.py` (apply its `NLU_HEAD_DEADLINE_S` advice if Gemini is slow) → run the script. Mic fails: **Type instead** / `/?typed=1`.
4. **After the demo:** phase D (Asterisk phone calls; needs WSL2 + Ubuntu installed, a reboot, with the owner's permission) and phase E (hardening), plan section 13.

**Known gaps:** end-of-turn + reply ≈ 1.6 s on rule-handled turns (plan target 0.9 s); simple booking median 10 turns on sim seed 7 (9 on seed 11); Deepgram mishears "Adharsh" and some ordinals (Emma's read-backs catch them; Sarvam hears names better but is 1-1.5 s slower); Chirp untested (Google India needs a ₹500 prepayment).

**Working notes for whoever continues:**
- Editing files through `python - <<'EOF'` heredocs mangled backslash escapes several times this session (`\b` became a backspace, `\x00` a NUL, and a `str.replace` with an empty search string flooded `config.py`). Prefer the Edit tool for code containing regexes; after scripted edits, scan for control characters and check file sizes.
- After a long session the full test run hit Windows `WinError 10055` (out of socket buffers) in 3 network tests; they pass when run alone. Restart the PC (or close stale python/server processes) if it happens.
- The app's built-in browser pane has no microphone: use Chrome for voice, or `/?typed=1` in the pane.


---

## 6 Oct, C-full recovery calls (owner chose: now, on the demo branch)

- **Retries:** unanswered → tried again 2 h later, up to 3 tries, inside calling hours; then NEEDS RESCHEDULE + task. **Declined → task at once** (the demo scene is unchanged).
- **"Call me after 6":** Emma says "No problem at all, we'll call you back after 6" and the runner books that time as the next try. "In an hour", "in 20 minutes", "tomorrow morning" work too; "call me later" uses the 2 h gap. (Found and fixed by its test: this path would have crashed the call.)
- **Pause / Resume / Stop** per campaign; **per-patient calling hours** (Recovery tab, bottom card); **results line + Export CSV** per campaign; the runner badge shows "Waiting" with the next retry time.
- `migrations/002_recovery_full.sql` (columns only) upgraded the live demo database in place: fixtures intact, preflight READY, demo scenes still complete. 900 tests pass.
- Settings: `RECOVERY_MAX_ATTEMPTS=3`, `RECOVERY_RETRY_GAP_MIN=120` (in `.env.example`).

---

## 6 Oct, R3.4: Sarvam tested, Nova-3 stays

Sarvam saaras:v4 (`stt_sarvam.py`; `python tools/replay.py --provider sarvam <wav>`; `SARVAM_API_KEY` in `.env`) on the owner's two recordings: it heard "Adarsh Kumar" (Deepgram: "Adesh" or dropped), numbers and branches right, word errors ~19-20% (Deepgram 17%), but turns end much later (p50 0.81 s headset / 2.25 s laptop, p90 2.3 s; Deepgram ~0.8-0.9 / ~1.0 s) and finals sometimes arrive seconds late in bursts. **Deepgram Nova-3 stays.** Google Chirp wasn't tested: billing in India needs a ₹500+ prepayment, and the owner chose Sarvam only.

---

## 000000. 6 Oct (Day 8 prep; the demo is Thursday 8 Oct)

- **Typed backup that works without a microphone:** the talk page offers **Type instead** when the mic is blocked or missing, and http://localhost:8000/?typed=1 starts a typed call directly. Emma still speaks; the silence ladder is off for typed calls. Same on the patient page (Answer, then Type instead). Tested in the app's browser pane, which has no microphone.
- **Gemini is slow and variable today** (first words after 1.7-4.3 s, a cold first request 4.75 s). Emma now sends one realistic warm-up request at startup and another every 4 min while no call is on (`NLU_KEEP_WARM_S`); this removes the very slow cold first turn but can't fix the service's own variability. `tools/preflight.py` now times Gemini and, if it's slow that day, suggests an `NLU_HEAD_DEADLINE_S` (today: 2.6) to put in `.env`.
- **Fix:** a question about opening hours ("What are your timings on Saturday?") no longer sets Saturday as a booking day in the fallback.
- 889 tests pass. My typed test calls left 2 calls in the demo database; `tools/demo_reset.py` clears them before rehearsals and the demo.

---

## Owner decisions, 7 Oct

- **Backup voice:** Piper `en_GB-cori-medium` (the default) stays.
- **One yes, not two (T5):** when the exact day and time the caller asked for is free, Emma says so and goes straight to the summary ("Good news, that time's free. That's a cleaning with Dr Rao… Shall I book it?"); the same for a reschedule. Changes R2_DESIGN 10.1's `offer.exact`. Simple-booking median: seed 11 now 9 (target met), seed 7 still 10 (those callers give the day and time in separate turns). 887 tests, 53/53 scenarios, sims clean.

---

## 00000. 7 Oct, Day 7: demo docs, reset and preflight

- **Docs:** [ARCHITECTURE.md](ARCHITECTURE.md) (pipeline, who decides what, invariants with their tests, data, Calendar, recovery, security, failure behaviour, tools) and [DEMO_SCRIPT.md](DEMO_SCRIPT.md) (setup, eight scenes with exact lines, fixtures, recovery steps, what to do if something goes wrong).
- **Demo data:** `tools/demo_reset.py` (Emma stopped) removes the Calendar events Emma created, rebuilds `data/emma.db`, books the fixtures the script uses (Priya and Rahul with Dr Rao on the day after the demo for recovery; Anita to move; Kiran to cancel) plus ~40 random DEMO bookings, and reconnects the calendars without new sharing emails. `--dry-run` reports only.
- **Readiness:** `tools/preflight.py` (Emma running) checks the server, Gemini, keys, prompts, Calendar, dashboard login, settings, recovery window and fixtures. The ElevenLabs and Deepgram keys can't read balances (fine for calls): check credit on their websites.
- **Bugs found by running the script through the engine, all fixed and tested (`tests/test_rehearsal_fixes.py`):** a question about a branch chose that branch; "Her name is Diya" wasn't taken as the patient's name; "pain since last night" made an emergency search the evening; a reason given with a cancel request was dropped (model-down path).
- **Checks:** 887 tests, 53/53 scenarios, 200 fake-model calls (Z1-Z7 0, M7 100%, M10 0%).
- **Owner, before the demo:** three rehearsals with the headset following DEMO_SCRIPT.md (run `demo_reset.py` before each), screen-record one full run as the backup, check ElevenLabs credit on the website. Code freeze after the rehearsal fixes.

---

## 0000. 6 Oct morning: the owner's recordings, and listening fixed

The owner recorded the 30 test lines twice (headset; laptop speakers), in `captures/` (git-ignored). `tools/replay.py` streams a recording through the live listening pipeline in real time and counts lines cut in half and the wait after the last word.

- **Finding:** cut-offs came from ending turns on Deepgram's transcript timing (words arrive late and in bursts). Day 6's quicker watchdog made it worse (10 of 30 lines cut on the headset): replaced.
- **Fix:** `vad.py` measures the caller's microphone level. A turn ends only once the line is really quiet and the recognised words reach the point where the voice stopped; a held turn stays open while the caller is audible; words Deepgram sends twice are dropped.
- **Result (cut lines, wait after last word p50/p90):** headset 5 -> 1, 1.30/1.80 s -> 0.92/1.01 s; laptop speakers 11 -> 2, 0.87/1.88 s -> 0.80/0.91 s.
- **Recogniser:** Nova-3 stays (Nova-2 en-IN mangles branch names). Both mishear "Adharsh"; ordinals like "the twenty sixth" and "seven in the evening" were misheard once each, so Emma's read-backs matter.
- `DEV_CAPTURE_AUDIO` is back to false. 880 tests pass.

---

## 000. 6 Oct, Day 6: latency pass and fault drills

- **Latency (6.1).** The 5 Oct R2 voice calls show end-of-speech detection, not the model, as the biggest wait: p50 1.6 s, because Deepgram sent `speech_final` on only about a third of turns and the rest waited for the 1 s STT watchdog. The watchdog now asks the turn detector how complete the words are (complete answer 0.35 s, likely 0.6 s, default 0.8 s, unfinished 1 s). (Superseded the same morning: on real recordings this cut more lines; see section 0000.) Gemini's first chunk has a floor of about 1.05 s however small the request (now about 1.3 s), so the brief stays as it is. **Check in the morning's voice test**: replies to "yes", numbers and dates should feel quicker; tell me if Emma now cuts you off.
- **Fault drills (6.2), automated** in `tests/test_fault_drills.py`. Two real fixes: a hang-up now frees the offered slots at once (they stayed held for 5 minutes), and a booking being written when the caller hangs up finishes and is recorded as `booked_hangup`. Gemini-off drill (200 calls): safety checks all 0, bookings 97%, dead ends 2.0% after fixing "It's on the 13th, and..." / "...move it to the 20th" being checked against the 20th. Correction acknowledgements now use Emma's words ("Okay, around 6 instead") and no longer say "Sure, 6:30 it is" before offering another time.
- **Regression (6.3).** 872 tests, 53/53 scripted scenarios, and 200 fake-model calls all pass on R2 (Z1-Z7 0, M7 100%, M10 0%); T5 is still 10 vs 9 (owner's call).
- **Fix list for the rehearsal:** live drills (ElevenLabs quota, a Deepgram drop, closing the tab mid-call), the latency gate (Tier-0 p50 about 1.6 s vs 0.9 s; needs the recordings to see why `speech_final` rarely fires), T5.

---

## 00. 6 Oct (night of 5-6 Oct): Day 4 finished, Day 5 recovery calls built

**Owner setup done on 5 Oct:** dashboard password set, `SERVER_HOST=127.0.0.1`, Google Calendar connected (4 branch calendars, all 46 appointments synced; the owner confirmed a booking appears once and a cancellation disappears), ElevenLabs has 8,000+ characters. `.env` has `R2_ENGINE=true`, `NLU_HEAD_DEADLINE_S=2.2`, `GEMINI_TIMEOUT=4`.

**Owner's next steps (morning of 6 Oct):** the voice test calls and the 30-line recordings ([STT_TEST_SET.md](STT_TEST_SET.md)), then a recovery-call rehearsal (below).

**Day 5, doctor-unavailability recovery (plan 5.11), demo grade:**
- `outbound.py`: blocks (effective at once), preview grouped by phone, campaigns with a version snapshot per job, stop, lift, and the `Runner` (one job at a time; waits for the call gate, so an inbound call pauses it; calling window `RECOVERY_CALL_WINDOW`, default 09:00-20:00 clinic time; do-not-call; stale check). No answer within `RECOVERY_RING_TIMEOUT_S` (30 s) or Decline: one attempt only, the appointments go to NEEDS RESCHEDULE (Calendar shows the prefix) and staff get a `recovery_failed` task.
- `dialogue/recovery.py`: Emma's side of the call, Tier-0 only (no model wait). Identity before any detail; wrong person or "is this a scam?" ends politely with nothing shared; what changed (never the block's reason); preference first (same doctor another day / another doctor same branch / another branch / earliest); the nearest valid slot, then two alternatives; a named day or time is searched; recap with the heard rule; atomic idempotent reschedule; several appointments one by one; cancel, on hold, staff, busy, do-not-call and the honesty line at any point. When the preference has no slot (only Dr Rao does cleanings at Nagarbhavi) she offers the nearest thing that exists.
- Dashboard **Recovery** tab: block a doctor, preview with ticks, Start recovery calls (confirm), live job status, Stop, Lift. **`/patient`** (staff login) is the demo patient phone: it rings with Answer / Decline and runs the call over `/ws/outbound`, with the same voice and listening as inbound calls.
- **To rehearse:** log in to the dashboard, open http://localhost:8000/patient in a second tab and click anywhere on it once (browsers only allow the ringtone after a click), then in Recovery block Dr Rao for a day that has bookings, tick, Start. Outside 09:00-20:00 the runner waits ("Paused: outside the calling window"); for a late-night test set `RECOVERY_CALL_WINDOW=00:00-23:59` in `.env` and restart.

**Day 4 finished:** live-call takeover on the dashboard's Live panel (Take over: Emma says a colleague is taking over and stops answering; typed lines are spoken and recorded as staff; Hand back: Emma re-asks her last question; End call + task).

**Also:** R2.7 bake-off done, **Flash-Lite stays** (Flash was slower and hit the free quota). Time to the model's first streamed chunk is about 1.4 s warm (2.2 s cold) and the head takes 0.1-0.2 s more, so Day 6's latency work is the request size (about 4.5k tokens, mostly the 14k-character system brief) or caching. The two `tests/test_nlu.py` tests that read `.env` deadlines now pin the config instead.

**Tests:** 865 pass (40 expected failures, the 12-step machine's known bugs). New: `tests/test_recovery.py` (30), `tests/test_takeover.py` (2).

**Still open before the demo:** R3.1/R3.4 (need the recordings), Day 6 latency pass and fault drills, Day 7 docs and rehearsal, T5 (10 turns vs 9, owner's call on skipping the exact-slot offer). Committed on `day2-r2-engine` (PR #5).

---

## 0. Sprint 1b integration (5 Oct): R2 works end to end, still off by default

**How to switch.** The 12-step machine is still the engine the talk page uses. `R2_ENGINE=true` in `.env` (then restart the server) runs the R2 engine for every call: the talk page through `call_session`, the harness (`python -m harness run sim --engine r2`) and `python tools/converse.py new --r2`. Committed as `06c3aa6` on `day2-r2-engine`. **On 5 Oct evening the owner's local `.env` was switched to `R2_ENGINE=true` for voice testing** (see the latency note below).

**Tests.** 829 tests pass with `R2_ENGINE` off and on (40 expected failures: the 12-step machine's known bugs in `tests/test_conversations.py`). Every HANDOFF section 5 call passes as a plain test on R2 (`R2Scenarios`): the location "No" loop, cancel at the recap, braces at Nagarbhavi, the time-only fragment, meta and price questions, fragments. `tests/test_r2_booking_flow.py` is the old booking tests ported to R2 (its docstring lists each dropped test and its replacement); `tests/test_r2_integration.py` pins this round's fixes.

**Simulated calls (offline fake NLU, R2, 200 calls per seed, seeds 7 / 11 / 23):** M1 0, M2 under 0.5%, M3 under 1%, M7 above 95%, M9 0, M10 under 2%, Z1-Z7 all 0 (every target met except T5). T5 (simple booking) median 10 against a target of 9 (see below). With the model down (`--nlu-down`, seed 7): M1 0, M7 above 95%, M10 2.0% (4 of 200, target under 2%), M3 3%, Z1-Z7 0.

**What the rounds found and fixed:** "Wednesday the 14th" read as the 7th (dateparse); "Tuesday, sorry, I mean Monday" kept Tuesday; a full day insisted on now tries the other branches that day ("Nagarbhavi is full that day, but Jayanagar has 2:30"); "No, it should be 11:30" at the summary when 11:30 wasn't free looped on "what should I change?"; a trailing "bye" got another question; slots kept being offered after a callback was arranged; ", no?" tag questions read as a no; "would be best" read as a cut-off; a declined callback or small talk closed the call while the caller still wanted the change; "cancel that one instead" rejected the phone read-back; an overlapping appointment re-asked the duplicate question; a pick during a reschedule was searched again; a question's date ("timings on Saturday?") taken as the appointment date; model-down reading of "teeth cleaned", "braces consultation", "I'm in a lot of pain" (read as the name "In"), "move it to" and "my number is ..."; repeated offers now rephrase on rungs 2-3.

**Before switching R2 on for voice tests:**
- **Model latency.** On 5 Oct the first streamed answer from `gemini-3.5-flash-lite` took about 2.3 s in a fresh process, above the 1.6 s head deadline (`NLU_HEAD_DEADLINE_S`) and close to the 2.5 s reply limit (`GEMINI_TIMEOUT`). Turns that miss it are answered by the no-model fallback, which works but is less natural. Measure on the running server (warm client) and, if needed, set `NLU_HEAD_DEADLINE_S=2.2` and `GEMINI_TIMEOUT=4` in `.env`. Live harness runs and `converse.py --live` already allow 4 s / 6 s, so they score the conversation rather than the cold start.
- **Measured 5 Oct (warm client, one process, 4 live R2 sim calls, 16 model turns):** head 1.7-3.4 s, median about 2.0 s. All 16 missed 1.6 s, 4 missed 2.2 s, 3 missed 2.5 s. The local `.env` now has `NLU_HEAD_DEADLINE_S=2.2` and `GEMINI_TIMEOUT=4`, so about 3 in 4 model turns get the model, at the cost of a pause of about 2 s on those turns. The real fix is a faster head (trim the brief or cache its fixed part); that is the top latency task before the demo. Run: `harness_runs/20261005-225210-sim-live-r2-latency` (git-ignored).
- **T5.** A simple booking takes 10 caller turns in the sim. The exact time asked for is offered first ("Monday at 4 is free with Dr Rao. Shall I take that?") and then summarised, which is two yeses for one slot (R2_DESIGN 10.1 says `offer.exact`). Going straight to the summary when the exact slot is free would save a turn; it changes the locked design, so it is the owner's call.
- **Logs.** `LOG_CALLER_TEXT=false` keeps the caller's words out of the console log (default true for development).

**Realtime and plan notes.** Silence belongs to `call_session`'s ladder for both engines (the engine is only called with "" for the greeting). The talk page handles the server's busy message. Deepgram endpointing is 300 ms (recommended; `turn_detector.py` waits on the words), and the typing beat is 300-500 ms and overlaps engine time.

---

## 1. Where things stand

| Item | State |
|---|---|
| Current branch | `r1-sound-human` (clean, pushed) |
| Tests | 114 passing: `.\.venv\Scripts\python.exe -m unittest discover -s tests` |
| Server | `.claude/launch.json` → `emma` (port 8000). Talk page http://localhost:8000; `/health`, `/metrics`, `/client-config` |
| Sounds | `python tools/fetch_ambience.py` downloads the approved CC0 files (git-ignored) |
| Database | `data/emma.db` (SQLite, git-ignored), DEMO-seeded on first start; `python seed_demo.py --reset` rebuilds it |

### Pull requests (all open, none merged; each stacks on the previous)

| PR | Branch | What |
|---|---|---|
| [#1](https://github.com/Adharsh818/Ai-voice-agent/pull/1) | `docs/implementation-plan-v2` | Implementation plan v2 + the owner's earlier unpushed mic-fix commit `9f08ab3` |
| [#2](https://github.com/Adharsh818/Ai-voice-agent/pull/2) | `day1-foundations` | Day 0 (`1333880`) + Day 1 (`b0b8200`) |
| [#3](https://github.com/Adharsh818/Ai-voice-agent/pull/3) | `realism-north-star` | North Star + CLAUDE.md (`1512a08`) |
| [#4](https://github.com/Adharsh818/Ai-voice-agent/pull/4) | `r1-sound-human` | R1 realism (`135ab97`) + background murmur removed (`4ae1e30`) |

Merge in order #1 → #2 → #3 → #4 so each diff shrinks to its own work. `day0-baseline` is a local-only branch (its commit is inside #2).

---

## 2. What was built (chronological)

1. **Audit.** 33 confirmed problems in the original code: dead-end loops, auto-accepting unconfirmed data, date bugs, a JSON store that a parse error could wipe, Calendar fallback double-booking, LLM text spoken verbatim, no silence timeout, and more. They're listed in section 8 of the plan.
2. **Plan v2.** 23 decisions locked by multiple-choice questions (plan section 1), then 14 more realism decisions (R1–R14, plan section 0).
3. **Day 0** (`1333880`):
   - Deleted the legacy Whisper/AGI/Google STT-TTS code.
   - Server binds to 127.0.0.1 and `/ws/voice` checks the Origin header.
   - Single Gemini key, re-verified every 60 s while failing.
   - `clock.py` gives clinic-timezone time.
   - Phone numbers masked in logs (`logredact.py`).
   - `DEV_CAPTURE_AUDIO` capture (`capture.py`).
   - End-of-speech diagnostics (`endpoint_source`, `stt_ms`, `hold_ms`).
4. **Day 1** (`b0b8200`):
   - `dateparse.py`: date/time constraints, 172-row test table.
   - `db.py` + `migrations/001_init.sql`: SQLite WAL, full schema. `slot_claims` has a primary key per 30-min cell, so the database itself blocks double bookings.
   - `scheduling.py`: every rule, holds, idempotent book/reschedule/cancel, audit and outbox rows.
   - `seed_demo.py`: DEMO clinic with 4 branches, 8 fictional doctors, 9 services and about 40 bookings.
   - `phones.py`.
   - The old 12-step dialogue now books through the engine at `DEFAULT_BRANCH` (Nagarbhavi) via `backend_actions.py`.
   - Listen-only capture mode and the 30-line STT test script (`docs/STT_TEST_SET.md`).
5. **North Star** (`1512a08`): the owner's overriding goal (below) plus `CLAUDE.md`.
6. **R1 realism** (`135ab97`):
   - Four rotated greetings and the honesty line.
   - Robotic lines rewritten; `tests/test_realism.py` bans bot, disclaimer, form and handoff wording.
   - DEMO knowledge base in `clinic_facts.json`.
   - Phone-line filter in the browser (`static/ambience.js`).
   - Typing: a 650–1000 ms note-taking beat after information answers, and a 0.9–1.6 s pause after "Let me just check that for you."
   - A soft synthetic breath before sentences of 20+ words.
7. **Murmur removed** (`4ae1e30`): the owner heard the waiting-room recordings as constant background, so there's now **no background bed at all**. Only typing, plus an occasional door, chair or footsteps while Emma is speaking or typing.

---

## 3. The owner's goal and rules (don't re-litigate)

- **North Star:** callers should feel they are talking to a real, skilled receptionist. Natural, friendly, fast, solves things on the call. See [NORTH_STAR.md](NORTH_STAR.md).
- **Honesty boundary:** Emma never volunteers she's automated. If sincerely asked, she says "Yeah, you caught me, I'm the clinic's virtual receptionist" and carries on. She never claims to be human.
- **Sound:** nothing constant, sound follows the call, no background loop. Keep the same ElevenLabs voice.
- **Handoff:** no reflexive "I'll connect you to someone". Escalate only for emergencies, doctor-initiated problems, or the truly unresolvable. If asked for a person: help first, callback only if they insist.
- **Scope for 8 Oct:** A+B complete, C (recovery calls) demo-grade, outbound via a browser "patient" tab. If time runs short, cut dashboard extras first, then recovery calls.
- **Locked policies:** no audio recording (transcripts kept 30 days); 30-min grid; 60-day horizon; 2 h lead time; verification = phone + date + name; spoken confirmations only; single Gemini key; one-way Google Calendar via service account; no Google Sheets.

### Working agreements with the owner
- Present decisions as **multiple-choice questions** with a recommended option and trade-offs.
- **Commit, push or open PRs only when asked.** PRs are opened with `"C:\Program Files\GitHub CLI\gh.exe"`, which isn't on the bash PATH.
- The owner tests by **talking to Emma in the browser**. Check `preview_logs` for an active call (`call started` without `call closed`) before restarting the server.
- Files use **LF** line endings. Python's `write_text` on Windows writes CRLF unless you pass `newline="\n"`.
- Ultracode was switched on at the end of the 1 Oct session: use the Workflow tool for substantive work (testing and fixing).

---

## 4. Latest feedback (1 Oct, after testing by voice)

In the owner's words, condensed. **Their instruction: first test all the cases, then fix.**

1. Emma sometimes **keeps repeating words**.
2. She sometimes **doesn't listen properly**.
3. She **often deflects to the doctor** ("the doctor will go through that at your visit").
4. She **breaks when asked other questions**, or when the caller doesn't answer her question: she repeats the same question instead of using the LLM to talk naturally and then bring them back to booking.
5. She sometimes **skips questions**.
6. The call feels like a **walkie-talkie conversation**; it should be seamless.
7. "**How can you help?**" gets "would you like to book an appointment" instead of a casual answer like "I can tell you about our clinic, our services, prices, timings, and book, change or cancel appointments…"
8. She **talks in loops**.
9. She **still can't handle the Indian accent**.
10. She should be **much more natural, less bound to the script, able to converse generally, and friendlier**.

---

## 5. Evidence from the 30 Sep – 1 Oct test calls (server log)

| Symptom | What the log shows | Likely cause |
|---|---|---|
| Loop | "No." ×4 → "Sorry, on this call I can only book our Nagarbhavi branch. Would that still work?" ×4 | The step 6 location refusal has no exit (the old 12-step engine, audit problem 2) |
| Loop + wrong outcome | At the recap: "So I would like to cancel." → "Sure, what should I change?" repeated through "Cancel the complete / Cancel it / Nothing / Nothing". Then "Bye." → "Sorry, shall I go ahead and book that?" → "Yeah." → **booked** | No intent switch (book → cancel) in the old engine; the recap `no` branch only accepts field corrections |
| Loop | "8AM." ×3 while booking **Braces** | `DEFAULT_BRANCH` is Nagarbhavi, but in the DEMO seed only Indiranagar and Whitefield do braces, so no slot can ever exist and step 10 has no exit |
| Wrong date | "November 22, at" [cut off] then "5PM." → "So Thursday, 01 October, is that right?" | A time-only fragment reached the date step; `backend_actions.resolve_date("5PM")` fell through to today |
| Cut-offs ("not listening") | Fragments such as "What's the best", "Don't", "But", "Tell me what can you", "Cancel the com", "Tell me how good you" | `speech_final` at 200 ms endpointing ends turns on natural pauses; barge-in then interrupts Emma (plan R3.2) |
| Accent ("not listening") | Adharsh → "Adashar", "Adrish", "Bharat"; "check-up" → "Chicken, chicken."; the phone number needed about 4 tries (789 **8**37 vs 937) | Nova-3 with Indian English isn't good enough on Indian names and digits (R3 recogniser comparison); Deepgram also formats numbers US-style "(789) 937-7462" |
| Meta questions dead-end | "Tell me what can you help me with", "Who are you?", "Tell me about the clinic / your company", "How can you help me?" (many short calls ending right after) | Handled as an off-topic question: the old engine answers from facts or the escalation line, then re-asks "Would you like to book a visit?". There's no "what I can do" fact |
| Deflection | Price/visit questions answered with "The doctor can go through that with you at your visit" | `ESCALATION_LINE` is the fallback whenever the LLM's `answer` is missing, and whenever the model is down |
| Walkie-talkie feel | Perceived latency: Tier-0 about 0.8–1.3 s, Tier-1 about 1.8–3 s (NLU 0.9–2.1 s), plus the deliberate 0.65–1.0 s typing beat on information turns | End-of-speech wait + LLM + no streaming + the new typing beat. Revisit the beat length |
| Logging gap | Off-topic replies aren't logged (only "off-topic question at step N") | `ai_engine.async_process_turn` pure-question path. Log the reply so the test harness can see it |

"Keeps repeating words" still needs reproducing. It is probably the loops above; other candidates are a filler ("Okay.") followed by a reply that starts "Okay,", or Emma's own voice echoing into the microphone and being re-transcribed on speakers.

---

## 6. Next steps (in order)

### Step 1: test everything first (the owner's explicit instruction)
Build a **conversation test harness** before fixing anything:
- **Text-level scenario runner.** Drive `ai_engine` / `call_session` with fake STT and TTS and log full transcripts. Include every failing call in section 5 as a regression scenario (location "No" loop, cancel at recap, braces at Nagarbhavi, time-only fragment at the date step, meta questions, price questions, fragments).
- **A scenario catalogue** covering:
  - meta questions ("how can you help", "who are you", "tell me about the clinic");
  - off-topic and chit-chat, refusing to answer, changing mind, intent switches (book → cancel, reschedule);
  - out-of-order details ("on 2nd October I want…"), corrections, unknown services;
  - service–branch mismatches, silence, fragments, the bot question, a person request, emergencies;
  - family bookings, cancel and reschedule with verification.
- **LLM-simulated callers** (several personas, including Indian-English phrasing) run against the engine through a Workflow: generate scenarios → run → judge each transcript against the North Star checklist → classify failures.
- **Audio replay** of the owner's recordings (`docs/STT_TEST_SET.md`, still to be recorded), to measure mishearings and cut-offs.
- Output: a failure catalogue with counts, one row per root cause, that decides the fix order.

### Step 2: R2, the natural conversation engine (fixes items 3–8 and 10)
Plan section 0.4:
- **Engine design.**
  - A checklist and priority context instead of 12 fixed steps.
  - A per-turn brief; one streamed Gemini call returning entities + `next_goal` + reply.
  - Validators (only facts from the brief; no bot/handoff wording; no false "booked").
  - Pre-written lines only for commit-critical moments (phone read-back, final summary, confirmations).
- **Must-haves from the feedback:**
  - A friendly casual **capability answer**: "I can tell you about our clinic, our services, prices and timings, and book, move or cancel appointments. What would you like?"
  - General conversation and off-topic handling, then a natural steer back to booking.
  - **Intent switching** at any point (book ↔ cancel ↔ reschedule ↔ questions).
  - **Loop breaker:** never the same line twice in a row; after 2 misses, rephrase or offer choices; after 3, move on or offer a callback.
  - **Stop deflecting to the doctor.** Answer from the knowledge base and general non-medical dental knowledge. Use the doctor line only for genuinely clinical questions (diagnosis, "do I need X").
  - **Branch-aware booking**, so a service is never accepted at a branch that doesn't offer it. Offer the branches that do ("Braces are at Indiranagar or Whitefield, which suits you?").
  - **No skipped details:** the checklist guarantees every required item; no auto-accept.
  - Warmer, friendlier tone (R13: friendly-professional, a bit warmer per the owner).
- **Model bake-off:** Flash-Lite vs Flash on the harness scenarios (R2.7).

### Step 3: R3, listening for Indian English (fixes items 1, 2 and 9)
Plan section 0.5:
- **Adaptive end-of-turn:** about 400 ms raw endpointing, commit fast on complete answers, wait on unfinished ones. This fixes the fragments.
- **Echo filter** only while audio is audible, so repeated words aren't dropped. Review the browser `noiseSuppression` / `autoGainControl` settings. Fix the microphone resampler.
- **Recogniser comparison** on the owner's recordings: Nova-3 (baseline) vs Nova-2 Indian English vs Google Chirp en-IN vs Sarvam.
- **Better recognition of the clinic's words:** doctor, branch and service names, "check-up". Recover digit groups across turns.
- **Name capture:** spell-back after one miss; fuzzy match against names already heard.

### Step 4: remove the walkie-talkie feel (item 6)
- Faster end-of-turn (Step 3).
- Stream the first reply sentence to TTS as soon as it's validated.
- Short openers while thinking.
- **Shorten or overlap the typing beat.** Start the reply sooner and let typing run under "Okay, …"; measure perceived p50/p95 before and after (`/metrics`, `pause_ms`).
- Allow natural overlap: backchannels don't interrupt Emma (plan 5.6).

### Step 5: continue the plan
Day 4 (dashboard, Calendar sync, transcripts), Day 5 (recovery calls, demo grade), Day 6 (latency and fault drills), Day 7 (freeze at noon, rehearse). Schedule in plan section 0.6. The feedback work above now takes 1–3 Oct; watch the cut line (plan section 11).

---

## 7. Still waiting on the owner

| Task | Why |
|---|---|
| Record the 30-line STT test set with the demo headset (`DEV_CAPTURE_AUDIO=true`, open `/?mode=listen`); a second speaker is a bonus | The recogniser comparison and replay tests need it |
| Google Cloud project: Calendar API + a service-account JSON key in `secrets/`, and Speech-to-Text enabled (billing on, for Chirp) | Day 4 Calendar sync; the recogniser comparison |
| Sarvam AI signup and API key | Recogniser comparison |
| Set `SERVER_HOST=127.0.0.1` in `.env` (it's `0.0.0.0` now; the server warns) | Security |
| Check the ElevenLabs character balance (free tier is 10k/month) | Rehearsals may run out; Piper fallback is planned |
| Delete the unused `mock_db.json` (old test bookings) | Tidy-up |
| Merge PRs #1 → #4 when happy | Keeps diffs reviewable |
