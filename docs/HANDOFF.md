# Project memory and next steps

**Last updated:** 5 Oct 2026 (Sprint 1b integration; sections 1-7 are from 1 Oct) · **Demo:** Thursday 8 Oct 2026 · **Owner:** Adharsh (GitHub `Adharsh818`)

Read [NORTH_STAR.md](NORTH_STAR.md) first, then this file, then [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) (section 0 is the current priority). This file records what happened in the working sessions of 30 Sep – 1 Oct, the decisions made, the owner's latest feedback, and what to do next.


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
