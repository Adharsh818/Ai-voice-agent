# Emma — Final Implementation Plan (v2)

**Written:** 2026-09-30 · **Demo:** Thursday 2026-10-08 · **Supersedes:** `A:\emma-approved-implementation-plan.md` wherever the two disagree (section 2 lists every difference).

**Scope for the demo:** Milestone A (safe conversational browser demo) and Milestone B (production-like scheduling) complete; Milestone C (doctor-unavailability recovery) at demo grade. Milestones D (Asterisk) and E (evaluation hardening) follow after the demo (section 13).

**Read [NORTH_STAR.md](NORTH_STAR.md) first.** Callers should feel they are talking to a real, skilled receptionist. Section 0 turns that into work and comes before everything else; where it conflicts with a later section, section 0 wins.

**Acceptance gate:** [SUCCESS_CRITERIA.md](SUCCESS_CRITERIA.md) (the owner's twelve criteria, metrics M1–M10, zero-tolerance Z1–Z7, turn-taking T1–T5) scores every sprint. **R2 engine design:** [R2_DESIGN.md](R2_DESIGN.md). **Language:** Indian-accented English is in scope; Hinglish (Hindi words mixed into English) is out of scope by the owner's decision (1 Oct), as are other languages.

---

## 0. Realism upgrade: first priority (added 30 Sep)

### 0.1 Decisions (realism MCQ, 30 Sep)

| # | Topic | Decision |
|---|---|---|
| R1 | Greeting | No disclaimers or "automated assistant". Rotate between "Hi, this is Emma at Pearl Dental, how can I help?", "Hello, Pearl Dental. How can I help you?", "Pearl Dental, Emma here. Go ahead." and "Hi, I'm Emma from Pearl Dental. How can I help you?" |
| R2 | Honesty | Emma never volunteers being automated. If sincerely asked whether she's a real person or a bot: "Yeah, you caught me, I'm the clinic's virtual receptionist," then straight back to helping. She never claims to be human |
| R3 | Recording | **No audio recording.** Text transcripts kept 30 days (replaces Q12). No recording notice is needed in the greeting |
| R4 | Ambience source | Free-licence (CC0) recordings you approved: a wooden door, a chair, footsteps on tile, laptop typing ([tools/ambience_sources.json](../tools/ambience_sources.json), fetched by `tools/fetch_ambience.py`). The two waiting-room murmurs were removed on 1 Oct (they still felt constant). The AC room tone and phone ring were never approved |
| R5 | Ambience behaviour | **Nothing plays constantly; sound follows the call; no background bed** (owner, 30 Sep and 1 Oct). Only an occasional door / chair / footsteps while Emma's line is active (speaking or typing), like a headset with a noise gate, plus typing (R6). Her side is silent while the caller talks |
| R6 | Realism extras | Typing when the caller gives something to write down (a 650–1000 ms beat before she replies) and while she checks the diary; phone-line sound in the browser demo; short openers ("Okay,", "Sure,", "Right,"); occasional light "umm" / "so" (capped); soft breath before long sentences |
| R7 | Asked for a person | Offer to help first; if they insist again, take a callback message. No reflexive handoff anywhere |
| R8 | Knowledge | A full DEMO knowledge base (price ranges, insurance, parking, payment, what to bring, branch landmarks) so she can answer almost everything |
| R9 | Listening symptoms reported | Mishears words or names; cuts the caller off mid-sentence; waits too long after they stop; sometimes doesn't hear at all |
| R10 | STT bake-off | Deepgram Nova-3 (current baseline), Deepgram Nova-2 Indian English, Google Chirp (en-IN), Sarvam AI. Flux dropped |
| R11 | LLM | Test Gemini Flash-Lite vs Flash on 20 scripted messy-caller conversations; pick on naturalness and speed |
| R12 | LLM coverage | Hybrid: simple turns (yes/no, digits, clear picks) get instant, human-written, varied lines; the LLM writes every other reply |
| R13 | Tone | Friendly-professional: warm, efficient, never chatty |
| R14 | Cut first | Dashboard extras (manual edit forms, audit viewer), then recovery calls |

### 0.2 What this changes in the architecture

- **The LLM now writes Emma's words**, within guardrails. Python still decides every action and owns every fact.
- **Per-turn brief.** Python builds a brief for each turn:
  - Slots known so far.
  - Slots still missing, in priority order (intent → name → phone → service → branch → when → offer → summary → wrap-up).
  - Verified facts that may be used.
  - The exact slots on offer and their spoken forms.
  - The rules: one question at a time; at most 2 sentences / 35 words; the R13 tone; never claim to have booked anything; never mention being a bot unless sincerely asked (R2); never offer a person (R7).
- **One streamed Gemini call** returns JSON with the understanding first (`acts`, `intent`, `emergency`, `confirmation`, `correction`, the details, `faq_ids`, `clinical`), then `next_goal`, then the reply in two parts: `say` (acknowledge or answer, no question) and `ask` (at most one question). The full schema is in [R2_DESIGN.md](R2_DESIGN.md) section 6.
- **Python applies the entities and computes its own next goal.**
  - The model's `say` is spoken sentence by sentence as it streams, each sentence only if it passes the validators.
  - The model's `ask` is used only if its `next_goal` matches Python's, the goal isn't commit-critical, and it validates.
  - Otherwise Emma speaks a natural pre-written line for Python's goal.
  - Any sentence that fails validation is dropped.
- **Validators:**
  - Every number, date, time, price, doctor, branch and name in the reply must appear in the brief.
  - Forbidden: bot/AI/assistant/automated wording unless answering a sincere question; "real person", "transfer", "connect you"; "booked", "confirmed" or "cancelled" unless Python committed it this turn; medication or dosage advice.
  - Maximum length and one question.
- **Commit-critical lines** are pre-written, with variants, so they're always exact: phone read-back, the final summary, and booking / cancel / reschedule confirmations.
- **Callers can go in any order.** "On the 2nd of October I want an appointment" stores the date, and the next missing item is the name: "Sure, the 2nd. Can I get your name first?" Nothing already given is asked again.
- **Natural confirmations** replace field-by-field checks:
  - Name, service, branch and date are confirmed implicitly: "Priya, got it."
  - The phone number is read back in groups.
  - One conversational summary comes before booking: "So that's a cleaning with Dr Rao at Jayanagar, Monday the 5th at 5. Shall I book it?"
- **Unchanged invariants:** no action without a clear yes to a summary the caller heard; only real slots are offered; idempotent transactions; NLU/parser disagreement means Emma asks again.

### 0.3 R1: Sound like a person (1 Oct, morning)
- [x] **R1.1** Greetings (R1) replace `GREETING` and `DISCLOSE_AI`. Pre-render all four; rotate so repeat callers don't hear the same one twice in a row.
- [x] **R1.2** Remove every disclaimer and robotic line: "automated assistant", "At the moment I can assist only…", "Name: X. Phone Number: Y.", "currently has one location". A test scans all speakable text for banned phrases.
- [x] **R1.3** Call-driven clinic sound in the browser (`static/ambience.js`, R5):
  - **Nothing constant, no background bed.** A gate opens (about 80 ms) when Emma speaks or types and closes (about 350 ms, after a 250 ms hold) when she stops. Her side is silent while the caller talks, so nothing leaks into their microphone.
  - **Movement.** Door, chair or footsteps: at most one every 25 s, 35 % chance per line-open, only while the line is open; long files contribute a random 3–6 s slice.
  - **Murmur removed on 1 Oct.** The waiting-room layer was dropped because, with Emma talking for much of the call, it still felt constant.
  - Measured by offline render: silence when idle and while she speaks (apart from the occasional movement sound); typing peaks around −24 to −31 dBFS.
- [x] **R1.4** Sound effects:
  - **Note-taking typing:** when the caller gives something to write down (`ai_engine.expects_information`), typing starts 120 ms after they stop and Emma's reply waits 650–1000 ms. The typing stops the moment she speaks, and the pause is logged as `pause_ms`.
  - **Checking typing:** "Let me just check that for you." plus a 0.9–1.6 s pause with typing.
  - **Breath:** a soft synthetic breath before sentences of 20+ words (never two turns running), to A/B test and drop if it sounds fake.
- [x] **R1.5** Phone-line sound in the browser demo: 300–3400 Hz band-pass plus gentle compression on Emma's voice and the ambience; switch `PHONE_LINE_EFFECT`.
- [x] **R1.6** DEMO knowledge base (R8) in `clinic_facts.json`, every entry with a fact ID and marked DEMO:
  - Price ranges in ₹ per service, insurance and reimbursement, payment methods (UPI, cards, cash).
  - Parking, what to bring, first-visit notes, children's visits.
  - Branch landmarks and per-branch hours from the rota.
- [x] **R1.7** The approved CC0 files are fetched by `tools/fetch_ambience.py` into `static/ambience/` (git-ignored, about 2.2 MB since the murmurs were removed), and the manifest is generated.

**Exit:**
- Greeting audio under 2.5 s.
- Zero banned phrases anywhere.
- Nothing plays while Emma is idle; no background bed at any time; movement sounds only while her line is active.
- Typing plays after information answers and during a check, never after a bare yes/no.
- All previous tests pass.

### 0.4 R2: A real conversation, the same outcome (design 1 Oct, Sprint 1b 2 Oct, integration 3 Oct; replaces Day 2 and Day 3 items 3.1–3.3)

Full design, interfaces and traceability: [R2_DESIGN.md](R2_DESIGN.md). Scored against [SUCCESS_CRITERIA.md](SUCCESS_CRITERIA.md). The interface stubs are in the repo (`dialogue/`, `nlu.py`, `prompts.py`, `facts.py`).

- [x] **R2.0** Design and interface stubs: `dialogue/context.py` (every enum and dataclass, picklable), `nlu.py` schema + `FakeNLU`, the `prompts.py` line registry, `facts.py` types, `dialogue/testing.py` (`DemoClinic`).
- [x] **R2.1** Context and policy (`dialogue/context.py`, `policy.py`, `apply.py`, `engine.py`; E1):
  - A checklist plus a priority order instead of fixed steps; details in any order, nothing re-asked (M2), corrections anywhere, intent switches at any point carrying name and phone.
  - The loop breaker: never the same line twice in a row; per-goal rungs ask → rephrase → choices → exit (default or callback offer); a non-answer goes straight to choices.
  - The steer-back rule: the caller's question is always answered first; the pending ask returns at most every other question turn, in new wording; OFFER_HELP at most every second answer.
  - The facade (`new_session`, `async_process_turn(..., on_sentence)`, `listening_hint`, `expects_information`) behind `config.R2_ENGINE` until integration.
- [x] **R2.2** One streamed structured Gemini call per LLM turn (`nlu.py`, `llm.py` streaming, `dialogue/brief.py`; E2). Understanding first, then `next_goal`, `say` and `ask`; a partial-JSON parser releases each finished sentence; one request per turn, never a second call.
- [x] **R2.3** Validators (`dialogue/validate.py`; E2): facts only from the brief (incl. price consistency), no bot / handoff / callback-promise / doctor-deflection wording, no false booked / cancelled / moved, one question, length caps, no medicine or diagnosis, no near-repeats, English only. Each rule unit-tested.
- [x] **R2.4** Lines and knowledge (`prompts.py`, `facts.py`, `clinic_facts.json`, `phrases.py`; E3):
  - Varied human-written lines for every goal (the fallbacks) and pre-written commit-critical lines (phone read-back, offers, summary, outcomes).
  - Openers and light "umm" / "so" with caps; the honesty line (R2); no line repeated word for word in a call; pre-render budget kept small (ElevenLabs quota).
  - The capability answer; knowledge from the verified base and the DB catalog (branches, doctors, which branch offers which service, hours); the doctor line only for clinical questions; an honest "not sure" otherwise. The old escalation line is removed.
- [x] **R2.5** Tier-0 v2 (`tier0.understand`, `dialogue/match.py`; E4): yes/no, digits accumulated across turns (incl. "double nine" and Deepgram's "(789) 937-7462"), spelled names, fuzzy names, services, branches, doctors, dates / times, picks, fragments, and the global patterns (repeat, wait, bye, bot question, capability). Plus a lenient mode as the no-model fallback.
- [x] **R2.6** Workflows on the Day 1 scheduling engine:
  - **BOOK** (E6): branch-aware from the DB (never a service at a branch that doesn't offer it; name the branches that do), doctor preference incl. unknown names and lady / male doctor, family bookings, max-3 and duplicate checks, real held slots, the summary gate (heard in full, clear yes), commit.
  - **MANAGE** (E5): verify phone + name + date, reveal nothing before; check, cancel, reschedule.
  - **Handlers** (E5):
    - Emergency: urgent → same-day slot + task; red flag → 108 / ER + task + end.
    - Asked for a person (R7): help first, callback task only if they insist.
    - Honesty line only on a sincere question.
    - English only, gently (Hinglish out of scope).
    - Abuse; repeat / wait; fragments ("Sorry, go on", merged with the next turn); silence ladder; closing; don't-keep.
  - **Questions:** answered from the knowledge base, the DB or general non-clinical dental knowledge. Unknown → an honest "I'm not sure about that one" plus an offer (a callback task only if they want it). Never a reflexive "the doctor can go through that".
- [x] **R2.7** Model bake-off (R11) on the conversation harness: Flash-Lite vs Flash, scored on correct outcome, validator rejections, naturalness (read aloud, 1–5) and reply latency. Pick one and record the numbers. **Done 6 Oct (live R2 sim, 4 calls each, seed 7):** Flash-Lite heads 1.7-3.4 s (median about 2.0 s), 1 of 17 requests failed (quota); Flash heads about 3.3 s on the 3 that arrived, 12 of 16 failed (8 too slow for a 4 s head, 4 quota). **Flash-Lite stays.** Direct timing: almost all of the delay is time to the first streamed chunk (about 1.4 s warm, 2.2 s cold; the head then takes 0.1-0.2 s more), so the latency work is the request size (about 4.5k tokens, mostly the 14k-character system brief) or caching, not the output.
- [x] **R2.8** Integration (5 Oct), changed by the owner: the 12-step machine stays the default and R2 runs end to end behind `R2_ENGINE=true` (or an R2 `CallContext`), through `call_session`, the harness (`--engine r2`), `tools/converse.py --r2` and `tests/test_r2_booking_flow.py` (the old booking tests ported; the originals still test the 12-step machine). Every HANDOFF section 5 scenario passes as a plain test on R2 (`tests/test_conversations.py` `R2Scenarios`). Removing the 12-step machine and `backend_actions.py`'s helpers waits for the owner to switch.

**Exit** (from [SUCCESS_CRITERIA.md](SUCCESS_CRITERIA.md), on at least 200 simulated calls per round):
- **Zero-tolerance:** Z1–Z7 never happen.
- **Outcomes:** M1 = 0; M7 > 95 %; M9 < 1 %; M10 < 2 %.
- **Repetition and answers:** M2 < 2 %; M3 < 1 %; M4 < 2 %; M5 < 2 %; M6 > 95 %.
- **Latency and turns:** T2 p50 ≤ 1.8 s / p95 ≤ 3 s; T5 median ≤ 9.
- **Regressions:** every failing call in [HANDOFF.md](HANDOFF.md) section 5 passes as a regression scenario.
- **Model / Python disagreement** never causes an action; the honesty line fires only on a sincere question; a person is offered only after the caller insists.
- **Degradation:** with Gemini off, every scenario still reaches an outcome on Tier-0 and pre-written lines.

### 0.5 R3: Listen like a local (3 Oct; replaces Flux spike 3.6; needs your recordings and accounts)
- [x] **R3.1** Diagnose each reported symptom (R9) from the Day 0 endpoint diagnostics and your captures: **Done 6 Oct on the owner's two takes (headset, laptop speakers), with `tools/replay.py`:** the cut-offs came from ending turns on transcript timing. Deepgram's words arrive 0.5-1 s late and in bursts, so "the words stopped changing" fired mid-sentence (5 of 30 lines cut on the headset, 11 on laptop speakers with the old 1 s watchdog; 10 on the headset with the 6 Oct per-words watchdog, since reverted). Fix: `vad.py` (microphone level) ends a turn only on real quiet, once the words reach where the voice stopped, and keeps a held turn open while the caller is still audible; repeated words are dropped. Result: 1 and 2 lines cut, last word -> turn p50 0.92 s / 0.80 s, p90 1.0 s / 0.9 s (was p90 1.8-1.9 s).
  - **Cut-offs:** `speech_final` firing on natural mid-sentence pauses.
  - **Long waits:** the `utterance_end` fallback plus holds.
  - **Not heard:** the echo filter dropping real answers that repeat Emma's words, browser noise suppression, or the 2 s pre-connect buffer.
- [x] **R3.2** Adaptive end-of-turn:
  - Raise raw endpointing to about 400 ms.
  - Commit immediately when the text is a complete answer to the question just asked (yes/no, a full 10-digit number, a complete date or time, a name after "my name is").
  - Wait up to 2 s when it's clearly unfinished (trailing "and", "so", "my number is", partial digits).
  - Tune on your recordings; measure the cut-off rate and end-of-turn p50 before and after.
- [x] **R3.3** Echo filter only while Emma's audio is actually audible, with stricter overlap, so a caller repeating her words ("yes, Monday at 5") is never dropped. Review the browser `noiseSuppression` / `autoGainControl` settings. Fix the microphone resampler (moved up from Day 4.5).
- [x] **R3.4** STT bake-off (R10) with adapters behind the same callbacks: Nova-3 (baseline), Nova-2 Indian English, Google Chirp en-IN streaming, Sarvam streaming. **Partly done 6 Oct:** Nova-3 vs Nova-2 en-IN on the owner's takes with the clinic keyterms: Nova-2 is about 0.1 s quicker but gets branch names wrong ("Jana girl", "Jain Agar") and can't use keyterms; **Nova-3 stays.** Both miss "Adharsh" (heard as "Adesh" or dropped; Emma's spell-back handles it). Chirp and Sarvam not tried (no accounts). Watch-outs: "the twenty sixth" heard as "the twentieth, sixth" or "twenty eighth", and "seven in the evening" once as "morning": Emma's read-back is the safeguard. **Sarvam added 6 Oct** (`stt_sarvam.py`, `tools/replay.py --provider sarvam`, saaras:v4 with keyterms): better on the owner's name ("Adarsh Kumar") and as good on numbers and branches, word errors ~19-20% vs 17%, but end of turn much slower (p50 0.81 / 2.25 s, p90 2.3 s vs Nova-3's ~1.0 s) with finals arriving in late bursts. **Nova-3 stays** (switch rule: better without adding over 200 ms). Chirp not tested: Google needs a ₹500+ prepayment in India; the owner chose not to.
  - Score word errors, names, phone digits, dates/times, end-of-turn latency and dropped utterances on your recordings.
  - Switch if a candidate is clearly better on names and digits without adding more than 200 ms.
  - Check each provider's current streaming model names and languages during the test.
- [x] **R3.5** Boost the clinic vocabulary: doctor names, branch names, services, Indian number words ("double", "triple").
- [x] **R3.6** Recover from mishearing like a person: (Built into R2: the model reads context, `ask.name.spell` after a failed name confirmation, fuzzy match against names heard this call. Not yet measured on recordings.)
  - The LLM uses context ("route canal" → root canal).
  - For names, "Sorry, could you spell that for me?" after one failed confirmation.
  - Fuzzy-match heard names against the names already in this call.
- [x] **R3.7** Real-time robustness moved from Day 3: `playout.py`, recap-heard rule, backchannel filter, silence ladder, maximum call length, call gate, Deepgram reconnect, Piper fallback. (Done, Piper fallback included: `tts_piper.py`, `tests/test_piper_fallback.py`.)

**Exit:**
- An STT choice recorded with numbers.
- Cut-offs and long waits measurably reduced on the recordings.
- No real answer dropped as an echo in the replay test.
- The old Day 3 exit tests.

### 0.6 Revised schedule

| Date | Work |
|---|---|
| 30 Sep | Day 0 + Day 1 ✓ (a day ahead of plan) |
| 1 Oct | R1 ✓, conversation test harness, R2 design + interface stubs ([R2_DESIGN.md](R2_DESIGN.md)) |
| 2 Oct | R2 Sprint 1b: R2.1–R2.6 in parallel (six developer tasks with disjoint files) |
| 3 Oct | R2.7–R2.8 (bake-off, integration); R3 (bake-off needs your recordings and API keys) |
| 4 Oct | Day 4: dashboard, Calendar sync, transcript retention (no audio recording) |
| 5 Oct | Day 5: recovery calls, demo grade |
| 6 Oct | Day 6: latency, fault drills, regression |
| 7 Oct | Day 7: freeze at noon, rehearse |
| 8 Oct | Demo |

The phone-path versions of the ambience, typing and phone-line effects (mixed server-side in the playout queue) move to Milestone D with Asterisk.

## 1. Locked decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Deadline | Demo 2026-10-08. A+B complete; C demo-grade (preview, start/stop, one attempt, staff task on failure). Retries/pause/resume after the demo |
| 2 | Data | Seeded demo data for all 4 branches, labelled **DEMO** on every screen (never spoken) |
| 3, 23 | Outbound | Demo: the dashboard rings a browser "patient" tab. After the demo: softphone extension via Asterisk. No PSTN |
| 4 | Durations | 30-min start grid. Check-up, Consultation, Cleaning, Pediatric 30 min; Filling, Extraction 45; Root Canal 60; Braces and Invisalign are booked as 30-min consultations |
| 5 | Horizon | Bookable up to 60 days ahead; earliest start is now + 2 h (emergencies: now + 30 min) |
| 6 | Doctor | Optional preference if the caller names one; otherwise the earliest suitable doctor at the chosen branch |
| 7 | Recovery | Ask the patient's preference first (same doctor later / another doctor same branch / another branch), then offer slots |
| 8 | Cancel | No fee; reason asked but optional |
| 9 | Family | Up to 3 future appointments per phone number; patient name stored separately from the caller |
| 10 | Verification | Phone + appointment date + patient name before reschedule, cancel or reading details |
| 11 | Confirmation | Spoken only. The Google Calendar invitation promise is removed |
| 12 | Recording | ~~Audio + transcript, consent notice in the greeting~~ **Replaced by R3:** no audio recording; text transcripts kept 30 days; no notice in the greeting |
| 13 | Tiers | Single Gemini key (multi-key rotation removed). Paid/no-training tiers before any real patient data; the demo uses fictitious callers |
| 14 | Emergency | Severe pain/swelling/bleeding → earliest same-day slot + urgent staff task. Breathing/swallowing trouble or spreading swelling → advise 108/ER, urgent task, end call |
| 15 | Handoff | Only for emergencies, doctor-initiated problems or the truly unresolvable (R7). Asked for a person: help first, callback task only if they insist. Live transfer after Asterisk |
| 16 | Busy | Only one call at a time. Outbound runner pauses while any call is active; an extra inbound caller hears a busy message (+ callback task once caller ID exists) |
| 17 | Calendar | One-way: SQLite is the source of truth, a worker mirrors it to Google Calendar; staff calendars are view-only; the dashboard is the only editor |
| 18 | Sheets | Dropped. CSV export from the dashboard |
| 19 | Fallbacks | Piper (local, free) as fallback TTS. Ollama dropped; Tier-0 + templates are the LLM fallback |
| 20 | Legacy | Delete `main.py`, `asterisk_agi.py`, `google_stt_engine.py`, `google_tts_engine.py`, `mock_db.json` (kept in git history) |
| 21 | STT | ~~Flux spike~~ **Replaced by R10:** bake-off of Nova-3 (baseline), Nova-2 Indian English, Google Chirp en-IN and Sarvam on Indian-accent recordings |

---

## 2. Changes from the approved plan, and why

| Approved plan said | This plan does | Why |
|---|---|---|
| LLM "speaks and extracts"; returns a `reply` | LLM only extracts. Every spoken sentence comes from Python templates | Free LLM text was spoken verbatim (problem 10): prompt injection or hallucinated prices could reach the caller |
| Off-topic answers generated by the LLM, grounded in facts | LLM returns a **fact ID**; Python speaks the verified text or escalates with a real callback task | Same reason; also makes answers pre-renderable (lower latency) |
| 12 fixed steps incl. separate date and time confirmations | Intent router + small workflows; date/time are confirmed once, inside the slot offer and recap | Removes dead-end loops and 2–4 turns per booking |
| `DISCLOSE_AI=true` and a disclosure in the greeting | Natural greeting with no disclaimer (R1). Emma never volunteers being automated but answers truthfully if sincerely asked (R2) | Owner's North Star; the honesty boundary protects the clinic |
| "LLM only extracts; every sentence from templates" (this plan's first version) | The LLM writes replies inside strict validators; commit-critical lines stay pre-written (section 0.2) | Natural conversation (North Star, principle 4) without letting the model invent facts or actions |
| OAuth with a clinic account | Google **service account** owns the 4 branch calendars and shares them to a demo Gmail as view-only | OAuth apps in "Testing" issue refresh tokens that expire after 7 days (a token made on 1 Oct dies on the demo day), and the old code could open a browser login mid-call |
| Calendar and Sheets projections | Calendar only; CSV export | Q17/Q18 |
| 8–10 paraphrases per prompt | 2–3 for frequent prompts, 1 elsewhere | ElevenLabs free tier is 10,000 characters/month; 8–10 variants of ~80 prompts would need ~40,000 characters just to pre-render |
| `GEMINI_API_KEYS` rotation | One key, periodic re-verification | Rotating free keys to raise quota likely breaks the provider's terms (Q13) |
| Optional Ollama fallback | Dropped; Piper TTS added | Q19 |
| p50 < 900 ms for common turns | Demo gate: Tier-0 p50 ≤ 900 ms / p95 ≤ 1.8 s, Tier-1 p50 ≤ 1.8 s / p95 ≤ 3 s. The original numbers stay as stretch goals | Measured today: Tier-0 p50 1.31 s, Tier-1 p50 3.08 s, dominated by end-of-speech detection (~1.2 s). The Flux spike decides how close we get |
| AudioSocket in Phase 7 only | A server-side **playout queue** is built now | Needed for accurate barge-in, the recap-heard rule and AudioSocket later (where it also mixes the ambience); one code path for browser and phone |

---

## 3. Target architecture

### 3.1 Module map (flat modules, like today)

| Module | Responsibility | Status |
|---|---|---|
| `clock.py` | `now()` in Asia/Kolkata; freezable in tests | new |
| `dateparse.py` | Speech phrases → date/time **constraints** (section 5.2) | new, replaces `resolve_date`/`resolve_time` |
| `db.py` + `migrations/*.sql` | SQLite (WAL, `foreign_keys=ON`, `busy_timeout`), migration runner, single writer thread | new |
| `seed_demo.py` | Idempotent DEMO seed (branches, doctors, services, rules, closures, appointments) | new |
| `scheduling.py` | Slot search, holds, book/reschedule/cancel transactions, idempotency | new, replaces `backend_actions.py` |
| `facts.py` + `clinic_facts.json` | Single source of clinic facts by ID (hours, services and branches read from the DB) | new |
| `nlu.py` | One streamed structured Gemini call per LLM turn: understanding, then `next_goal`, `say`, `ask` ([R2_DESIGN.md](R2_DESIGN.md) 6); stream parser; backend switch and `FakeNLU` for tests | new, streaming added to `llm.py` |
| `tier0.py` + `dialogue/match.py` | Deterministic fast path (`tier0.understand`, 5.5) and the matchers: yes/no, digits, names, catalog, fragments | extended / new |
| `dialogue/` `context.py` `runtime.py` `engine.py` `apply.py` `policy.py` `brief.py` `validate.py` `book.py` `manage.py` `handlers.py` `recovery.py` | Call context and checklist, turn pipeline, entity application and intent switching, next goal and loop breaker, the per-turn brief, reply validators, workflows, global handlers ([R2_DESIGN.md](R2_DESIGN.md) 2) | new, replaces the 12-step machine in `ai_engine.py` |
| `prompts.py` | Line ids → variants: the fallback for every goal and the commit-critical lines; rotation with no repeats in a call | new; `phrases.py` keeps fillers and the pre-render list |
| `ai_engine.py` | Thin facade: `new_session`, `async_process_turn(text, ctx, progress, on_sentence) -> TurnResult`, `listening_hint`, `expects_information` | shrinks |
| `call_session.py` | Turn-taking, barge-in, silence ladder, recap-heard rule, reconnects | extended |
| `playout.py` | Per-call paced audio queue, flush, playout clock (and the server-side ambience mix for the phone path) | new |
| `speech.py` / `tts_elevenlabs.py` / `tts_piper.py` | TTS chain: ElevenLabs WS → HTTP → Piper, per-voice prompt caches | extended / new |
| `stt_deepgram.py` (+ `stt_flux.py` if the spike wins) | Streaming STT with reconnect | extended |
| `recording.py` | Transcript retention: 30-day purge, delete-on-request (no audio, R3) | new |
| `calendar_sync.py` | Outbox worker mirroring appointments to Google Calendar | new |
| `outbound.py` | Blocks → preview → campaigns → job runner → browser ring | new |
| `events.py` | In-process pub/sub for SSE (bounded queues) | new |
| `auth.py` | Dashboard login (scrypt hash from env), session cookie, WS origin/token checks | new |
| `dashboard.py` + `templates/` + `static/vendor/htmx*.js` | FastAPI + Jinja + HTMX + SSE (htmx vendored, works offline) | new |
| `tools/setup_calendars.py` | Creates/shares the 4 DEMO calendars, stores IDs; idempotent | new |
| `tools/replay.py` | Streams recorded caller audio through STT + engine; latency/accuracy report | new |

### 3.2 Turn pipeline

> **R2 replaces the middle of this pipeline** (Tier-0 / one streamed model call → handlers → apply → workflow actions → `next_goal` → validated `say` + notices + ask): see [R2_DESIGN.md](R2_DESIGN.md) section 5. The transport, turn detection and playout ends are unchanged.

```
caller audio ─► STT (interim/final) ─► turn detector (holds for digits / trailing words)
   ─► globals (repeat · wait · human · end · bot question · emergency · language · abuse · don't-keep request)
   ─► Tier-0 for the current state ─► else Tier-1 NLU (structured JSON, 2.5 s budget) ─► else deterministic fallback
   ─► entity validation (Python) ─► router (intent switch?) ─► workflow.handle()
   ─► scheduling actions (transactions, holds) ─► reply = template id + params
   ─► speech plan (cached sentences + live TTS) ─► playout queue ─► transport
```

### 3.3 Invariants (each has a test)

1. Only Python changes appointments. The LLM's understanding is validated before use, and its reply is spoken only sentence by sentence after passing the validators; commit-critical lines are always pre-written (section 0.2).
2. No booking, reschedule or cancel without an explicit "yes" to a recap the caller heard in full (5.6).
3. If the NLU and the deterministic parser disagree about yes/no, Emma re-asks. Neither wins.
4. No double booking: a UNIQUE constraint on grid cells plus a single writer (4.2).
5. Every mutation has an idempotency key. Replays return the original result.
6. Every workflow state has a retry limit and an exit (callback task, staff handoff or polite close). No state can loop forever.
7. Emma never promises something that isn't created: every "staff will call you" creates a task.
8. Data the caller didn't confirm is never used (phone always confirmed; a name that fails confirmation 3 times is stored flagged `unverified`).
9. Clinic facts are spoken only from verified entries.

---

## 4. Data model

### 4.1 Tables (migration `001_init.sql`)

- `branches(id, name, area, phone, calendar_id, is_demo, active)`
- `doctors(id, name, spoken_name, gender, branch_id, is_demo, active)`
- `services(id, name, duration_min, is_consultation, aliases_json, active)`
- `doctor_services(doctor_id, service_id)`
- `availability_rules(id, doctor_id, weekday, start_time, end_time)`
- `closures(id, date, branch_id NULL=all, reason, is_demo)`
- `blocked_times(id, doctor_id, start_utc, end_utc, reason_category, note, created_by, created_at, lifted_at)`
- `patients(id, name, name_norm, phone_e164, created_at)` — unique `(phone_e164, name_norm)`
- `appointments(id UUID, patient_id, caller_name, caller_phone_e164, service_id, doctor_id, branch_id, start_utc, end_utc, status[booked|cancelled|needs_reschedule|completed|no_show], source[inbound|outbound|dashboard], name_unverified, cancel_reason, affected_by_block_id, version, calendar_event_id, calendar_synced_version, created_by_call_id, created_at, updated_at)`
- `slot_claims(doctor_id, cell_start_utc, appointment_id NULL, hold_id NULL)` — **UNIQUE(doctor_id, cell_start_utc)**
- `slot_holds(id, doctor_id, start_utc, end_utc, call_id, expires_at)`
- `actions(idempotency_key PK, action, result_json, created_at)`
- `calls(id, direction, started_at, ended_at, caller_phone_e164, outcome, workflow, recording_consent, recording_path, purge_after)`. Since R3 there is no audio: `recording_consent` means "keep the transcript", and `recording_path` stays empty.
- `call_turns(call_id, turn, role, text, tier, state_before, state_after, entities_json, latency_json, ts)`
- `tasks(id, kind[callback|emergency|red_flag|recovery_failed|escalation|language|abandoned], priority, call_id, appointment_id, phone_e164, note, status, created_at, due_at, done_by)`
- `contact_prefs(phone_e164 PK, do_not_call, updated_at)`
- `outbound_campaigns(id, block_id, status[running|stopped|completed], created_by, created_at)`
- `outbound_jobs(id, campaign_id, phone_e164, appointment_ids_json, snapshot_json, status[queued|ringing|in_call|done|failed|skipped], outcome, attempts, call_id, idempotency_key, updated_at)`
- `sync_outbox(appointment_id PK, due_at, attempts, last_error, status[pending|failed])`
- `audit_events(id, ts, actor, action, entity, entity_id, before_json, after_json, correlation_id)`
- `schema_migrations(version)`

Timestamps are stored as UTC ISO-8601; `clock.py` converts to IST for rules and speech.

### 4.2 Why this prevents double booking

A booking claims every 30-minute cell its duration touches (45 and 60 min both claim 2 cells). The claim rows sit under UNIQUE(doctor, cell) and are written in the same `BEGIN IMMEDIATE` transaction as the appointment. Every DB write goes through one writer thread. Two racing bookings can't both commit, even if the Python checks ran in parallel. Holds claim cells the same way, with an expiry. Expired holds are deleted at the start of every write transaction.

### 4.3 Reschedule atomicity

In one transaction: re-validate the new slot, delete the old appointment's claims, insert the new claims, update the appointment (same ID, `version+1`), write the audit row and the outbox row. On any error the transaction rolls back and the original appointment is untouched. Moving into cells the appointment already holds (10:00 → 10:30 for 60 min) works because the old claims are released inside the same transaction.

---

## 5. Specifications

### 5.1 Scheduling rules (`scheduling.py`)

A start time `s` for (doctor, service) is valid only if all of these hold:

1. `s` is on the :00/:30 grid, within 60 days, not on a Sunday, and not on a closure date for that branch.
2. `s ≥ now + 2 h` (emergency: `now + 30 min`).
3. `[s, s + duration)` fits inside the doctor's availability rule for that weekday **and** clinic hours 07:00–21:00.
4. `[s, s + duration)` does not overlap lunch 14:00–14:30 (so a 45-min 13:30 slot is invalid, and the last 60-min start is 20:00).
5. The doctor performs the service; the doctor is not blocked over the interval.
6. All claimed cells are free, or held by this same call.

**Search** `find_slots(service, branches, doctor?, gender?, date_range, time_window, near?, limit)` returns slots ordered by closeness to `near` (earlier wins ties), then by preferred doctor. If the requested day has nothing, it moves forward up to 7 days, then reports "none within 7 days" so the dialogue can widen the search or create a task.

**Offer policy:**
- If the exact request is free, offer it.
- Otherwise offer the 2 nearest free slots that day at that branch.
- If there are none, offer the earliest day that has slots.
- Another branch is offered only if the caller agrees.
- Never more than 2 options per sentence.
- Offered slots are **held for 5 minutes**, refreshed each turn. Holds are released on choice, rejection, a new search, or hang-up.
- After 3 rounds without agreement: offer a staff callback (task) and close.

**Transactions:** `book(hold_or_slot, patient, idem_key)`, `reschedule(appt_id, new_slot, idem_key, expected_version)`, `cancel(appt_id, reason, idem_key, expected_version)`. Each one re-runs the rules at commit time, so a block or closure added mid-call is respected.

### 5.2 Date and time understanding (`dateparse.py`)

The output is **constraints**, not a single datetime: `DateConstraint(kind=exact|range, start, end)` and `TimeConstraint(kind=exact|window|ambiguous|any, start, end, candidates)`. Errors are typed: `PAST`, `SUNDAY`, `CLOSED`, `BEYOND_HORIZON`, `AMBIGUOUS_AMPM`, `INVALID_DAY`, `UNPARSEABLE`.

| Input | Rule |
|---|---|
| today / tomorrow / day after tomorrow | relative to `clock.now()` in IST (never the server timezone) |
| in N days / weeks | relative |
| Monday, next Monday, this Monday | the next occurrence after today. "This Monday" said on a Monday means today. "Monday after next" / "next week Monday" adds 7 days. The full date is always spoken back ("Monday, 5 October") |
| next week / this week / this weekend | range Mon–Sat of next week / the rest of this week / Saturday |
| earliest, as soon as possible, any day | range today → horizon, sorted earliest |
| 3rd, the 26th, twenty-first | the next date with that day number (this month if still ahead, else next month; handles year-end). A month that lacks the day gets an `INVALID_DAY` reply |
| 5 October, October 5th, 5th of October | this year if still ahead, else next year; then the horizon check |
| 05/10, 5-10-2026 | always **day-first** |
| 5, five, 5 o'clock | bare hours 1–6 → PM; 9–11 → AM; 12 → noon; 7 and 8 → `AMBIGUOUS_AMPM` ("7 in the morning or the evening?") |
| half past four, quarter to five, 4:15 | parsed; off-grid times become a *near* preference, and the offer lists the nearest grid slots |
| morning / afternoon / evening | windows 07:00–12:00 / 12:00–16:00 / 16:00–21:00 |
| after 5, before noon, after lunch, lunch time | windows (after lunch = 14:30–17:00) |
| 9 PM, 10 PM, Sunday, yesterday | typed errors with helpful replies ("Last appointments start at 8:30, or 8 for longer treatments") |

The LLM may supply `date_iso_hint`. It is used only if Python can't parse the phrase, and only after the same validation. It is always spoken back in the offer.

**Test table:** 120+ cases with a frozen clock. They include a Sunday call, a call at 20:50, month-end (30 Sep), year-end (30 Dec → "3rd" = 3 Jan), 31st in a 30-day month, a closure date, and the horizon edge.

### 5.3 Phone numbers and names

- **Phone collection:**
  - Digits accumulate across turns: spoken digits, "double/triple", tens words ("ninety-eight"), +91/91/0 prefixes.
  - A partial number gets a cached "Mm-hmm." and Emma keeps listening. The turn hold is 1.2 s, and up to 3 s while speech continues.
  - More than 12 digits → "I got too many digits, could you say it once more?"
  - Mobile numbers are 10 digits starting 6–9. Landlines (0 + STD code, 11 digits, e.g. 080…) are also accepted.
  - Read-back is grouped: "98765, 43210". **Explicit "yes" is required**, and after 3 failed attempts Emma apologises and closes (outcome `phone_failed`).
  - Numbers are stored as E.164.
- **Names:**
  - Spelled letters ("A D H A R S H") are joined.
  - Common prefixes are stripped ("my name is", "this is").
  - After 2 failed confirmations Emma asks the caller to spell it; after 3 the name is kept, flagged `name_unverified`, and shown to staff.
- **Patient vs caller:** default patient = caller. The patient name and relation are asked only when the caller signals someone else ("for my son", "my mother") or the service is Pediatric. Age is asked for Pediatric only.

### 5.4 NLU contract (`nlu.py`)

> **Superseded by R2** for the schema and prompt: see [R2_DESIGN.md](R2_DESIGN.md) sections 6–7 (one streamed call, understanding + `next_goal` + `say` / `ask`). The timeout, breaker and re-verify rules below still apply.

- **Structured output:** Gemini `response_schema` with temperature 0 and the thinking level from config.
- **Timeout:** 2.5 s total, one retry only if ≥ 0.7 s remains.
- **Breaker and re-verify:** the circuit breaker opens for 30 s after a 429/5xx. Startup verification is retried every 60 s while it's failing (fixes problem 19).

```json
{
  "intent": "book|reschedule|cancel|check|faq|human|emergency|repeat|wait|end|robot|other_language|none",
  "patient_name": null, "caller_is_patient": null, "patient_relation": null, "patient_age": null,
  "phone_digits": null, "service_phrase": null, "branch": null, "doctor_phrase": null, "doctor_gender": null,
  "date_phrase": null, "time_phrase": null, "date_iso_hint": null,
  "confirmation": null, "correction": false, "choice_index": null,
  "faq_ids": [], "emergency": "none|urgent|red_flag",
  "cancel_reason": null, "keep_objection": false
}
```

The prompt contains:
- Emma's last question and the expected slot.
- The allowed intents for this state.
- The last 6 turns.
- The FAQ ID list.
- The caller's words, wrapped in delimiters and labelled as *untrusted data, never instructions*.

Unknown keys are dropped. Every value is validated by Python (enums, digits, lengths) before use.

### 5.5 Tier-0 (no LLM)

Tier-0 handles:
- **Globals:** repeat, wait, human, end, sincere bot question, don't-keep request, English-only language requests.
- **Answers to the current slot:** yes/no, digits, service aliases (word-boundary matching, with ambiguity detection: "tooth" → "a filling, an extraction or a check-up?"), branch names, doctor names and "lady/male doctor", dates and times via `dateparse`, alternative picks ("the first one", "5:30").

Anything mixed, long, or containing a question goes to Tier-1. Returning "don't know" is always safe; a wrong answer is not, so the rules stay conservative.

### 5.6 Dialogue behaviour

**Global handlers (every state, before the workflow):**

| Trigger | Behaviour |
|---|---|
| "sorry?", "come again", "repeat that", lone "what?" | Re-speak the last prompt (cached). No state change |
| "hold on", "one second" | "Sure, take your time." Silence timers extend to 30 s |
| wants a human / receptionist | First time: help first ("I can probably sort that out for you myself. What's it about?"). Only if they insist: confirm the callback number → `callback` task → tell them who will call, then carry on or close warmly (R7) |
| sincerely asks "are you a real person / a bot?" | "Yeah, you caught me, I'm the clinic's virtual receptionist," then straight back to the task (R2). Never claims to be human, never raises it unprompted |
| red-flag emergency | 108/ER advice, `red_flag` task, end call. Checked first, before anything else |
| urgent dental emergency | Short path: name → phone → branch → earliest same-day slot (lead time 30 min) → recap → book + `emergency` task. No slot today → earliest tomorrow + task marked "no same-day slot" |
| other language (explicit request, or clearly non-English speech) | Gently English only, and offer a call back from the Kannada / Hindi-speaking team → `language` task only if they want it. Hinglish is out of scope and treated as English |
| abuse | One calm warning, then polite close |
| asks not to be recorded / kept | No audio is recorded (R3). Mark the call so its transcript is not kept, confirm, and continue |
| Questions | Answered first, from the verified knowledge base, the DB catalog (branches, doctors, services, hours) or general non-clinical dental knowledge; the doctor only for genuinely clinical questions. The pending question comes back at most every other question turn, in new wording (R2_DESIGN 9). Unknown → an honest "I'm not sure about that one" + an offer; `escalation` / `callback` task only if they want it |
| intent switch mid-flow ("actually I want to cancel") | Confirm the switch if booking data would be abandoned; caller name and phone carry over |
| long monologue with no usable content | "To help quickly, could you tell me in a few words what you need?" |

**BOOK workflow:**
1. Absorb any volunteered info.
2. Name (confirm).
3. Phone (confirm).
4. Patient (only if someone else).
5. Service (ambiguity and unknown handling; unknown treatment → offer a Consultation).
6. Branch (the four, or "whichever is earliest").
7. Optional doctor or gender preference.
8. "When would you like to come in?" → search → offer (held).
9. Recap → explicit yes → commit.
10. Spoken confirmation, plus "If anything changes, we'll call you on this number" (opt-out → do-not-call).
11. "Anything else?"

Checks before search:
- Max 3 future appointments on the number.
- Duplicate check: the same patient and service already booked in the future → "keep it or book another?"

**MANAGE workflows (reschedule, cancel, check):**
1. Verify: phone + patient name (fuzzy match, ratio ≥ 0.8 to absorb STT spelling) + appointment date. On failure, reveal nothing ("I couldn't find an appointment matching those details"). 2 attempts, then a callback task.
2. With multiple matches, disambiguate by name, then time.
3. **Cancel:** state the appointment, say there's no fee, ask the optional reason, get an explicit yes, cancel, then offer to rebook.
4. **Reschedule:** take the new preference, search (excluding the current slot), offer, recap ("from X to Y"), yes, move atomically.
5. Appointments already started or past can't be changed. The new slot must respect the 2 h lead time.

**Corrections anywhere:** an entity for an already-filled slot, combined with a correction cue ("actually", "instead", "make it", "no") or the NLU `correction` flag, updates that slot. Emma acknowledges it ("Okay, Tuesday instead"), drops dependent holds and resumes at the first affected step. Without a cue she asks: "Did you want to change the date to Tuesday?" At the recap, any slot entity is treated as a correction (fixes problem 14).

**Recap-heard rule:**
- A "yes" counts only if every detail sentence of the recap finished playing. Sentence durations come from their PCM lengths; the final question sentence may be cut off.
- If the caller barges in earlier, Emma says "Let me just finish the details," gives a short recap, and asks again.

**Backchannels:** "yeah", "okay", "mm-hmm", "right" (≤ 2 words) while Emma speaks do not interrupt her. If such an utterance ends before Emma's question sentence has started, it is dropped.

**No-dead-end guarantee:**
- Every state keeps an attempts counter: 3 tries, then the exit defined in invariant 6.
- A fuzz test feeds 25 turns of gibberish, silence markers and refusals into every state, and asserts the call reaches a terminal outcome.

### 5.7 Real-time behaviour (`call_session.py`, `playout.py`)

- **Playout queue:**
  - TTS audio goes into a per-call queue that sends 20 ms frames in real time, at most 300 ms ahead of playback.
  - `flush()` empties it instantly. The server knows exactly what was played (`played_ms`), which feeds barge-in, trimming Emma's history and the recap-heard rule.
  - The browser keeps its 60 ms prebuffer and still reports when audio became audible, for latency metrics.
- **Silence ladder** (timer starts when Emma's audio finishes; paused while the caller speaks or a turn is processing):
  - 8 s: "Are you still there?" + the current question.
  - 16 s: "I can't hear you, if you're there please say something."
  - 24 s: goodbye and end the call (outcome `silence`).
  - A "hold on" extends each step to 30 s.
- **Maximum call length:** 15 min. At 13 min Emma wraps up with a callback task.
- **Deepgram reconnect:** on an unexpected close, reconnect with backoff (0.25 / 0.5 / 1 s), buffering up to 5 s of audio. After 3 failures Emma says she's having trouble hearing, creates a callback task if a phone number is known, and ends the call.
- **TTS chain:** ElevenLabs WebSocket → ElevenLabs HTTP → Piper. Once Piper is used, the **whole rest of the call** uses Piper (and the Piper prompt cache) so the voice doesn't flip back and forth. Startup checks the ElevenLabs character balance (`GET /v1/user/subscription`) and shows it on `/health`.
- **Call gate:** one global slot (idle / inbound / outbound).
  - The browser talk page, dashboard test calls and outbound jobs all acquire it.
  - An extra inbound caller gets the busy message.
  - The gate is always released in `finally`, including on errors and hang-ups.
- **Hang-up at any point:** release holds, save the transcript and outcome (`abandoned@<state>` with the extracted slots). A commit already running finishes and is recorded as `booked_hangup`.

### 5.8 Transcripts and retention (`recording.py`)

- **No audio recording** (R3). The greeting is the natural one from R1, with no recording notice. Development captures (`DEV_CAPTURE_AUDIO`) stay a local, off-by-default tool for your own test calls only.
- **Transcripts:** caller and Emma turns are stored in `call_turns`, shown on the dashboard, and never written to log files.
- **Purge:** runs at startup and every 6 h. It blanks transcript text older than 30 days. Appointment records are business records and are kept. Staff can "Delete call data" on request (audited). A caller who asks not to be kept gets their transcript blanked at hang-up.
- **Logs:** `logs/turns.jsonl` stops storing caller text, and app logs mask phone numbers (last 4 digits only).

### 5.9 Google Calendar one-way sync (`calendar_sync.py`)

- **Ownership:** a service account owns one calendar per branch ("Pearl Dental — Jayanagar"; "(DEMO)" was dropped from the names on 6 Oct), created by `tools/setup_calendars.py` and shared as **reader** to the demo Gmail.
- **Triggers:** every appointment change writes an outbox row in the same transaction.
- **State-based worker:** reads the **current** appointment and makes the calendar match it. Upsert when booked, delete when cancelled, prefix "NEEDS RESCHEDULE" when flagged. Ordering and coalescing problems disappear.
- **Deterministic event ID** `emma<appointment uuid hex>`: a retried insert gets 409 and becomes a patch, so duplicates are impossible. Delete treats 404/410 as success.
- **Retry:** backoff 5 s → 30 s → 2 min → 10 min → 1 h. After 12 attempts the row is marked `failed` and shows on the dashboard with a Retry button.
- **Event content:** service, doctor, branch, the patient's first name, the phone's last 4 digits and the appointment ID. Full details stay in the dashboard.
- **Fallback:** Emma never reads the calendar during a call, so a Google outage can't block or double-book anything.

### 5.10 Dashboard (`dashboard.py`)

- **Access:** login required (password hash from `.env`, `SameSite=Strict` HttpOnly cookie, login rate limit). The server binds 127.0.0.1 by default; the WebSocket checks Origin and a per-page token.
- **Live call:**
  - Interim and final captions, workflow and state, slots, holds, tier, per-turn STT/NLU/TTS/perceived latency.
  - **Take over** — Emma announces "A member of our team is taking over", automation pauses, and typed operator lines are spoken.
  - **Hand back to Emma** / **End call + task**.
- **Appointments:** today / by branch / by doctor, search, manual book/reschedule/cancel using the same engine, CSV export (audited).
- **Tasks:** callbacks, emergencies, escalations, language, recovery failures, with priority and done state.
- **Calls:** history, transcript, outcome, delete-data button (no audio, R3).
- **Doctor unavailability and recovery** (5.11).
- **System:** sync outbox (failed + retry), provider health, ElevenLabs characters left, audit log, do-not-call list.

### 5.11 Doctor-unavailability recovery — demo grade (`outbound.py`, `dialogue/recovery.py`)

1. Staff block a doctor for a date/time range with a reason category (illness, emergency, training, personal, other). The block is effective immediately, so no new bookings land there.
2. **Preview** lists the affected booked appointments, grouped by phone. No calls happen yet.
3. Staff tick appointments → **Start recovery calls** → confirm dialog → a campaign is created with a snapshot (appointment version) per job.
4. **Runner:** one job at a time. It waits for the call gate and respects the calling window (09:00–20:00, configurable) and do-not-call. It re-checks each appointment's version against the snapshot; if staff or the patient already changed it, the job is `skipped (stale)`.
5. **Ring:** the logged-in `/patient` tab shows "Incoming call from Pearl Dental" with Answer / Decline. There is no answer after 30 s, or the patient declines → **one attempt only** → `recovery_failed` task.
6. **Call script:**
   - A natural opener: "Hi, is this Priya? This is Emma from Pearl Dental." No disclaimer (R1); the honesty line if sincerely asked (R2).
   - "Am I speaking with <first name>?" No details are shared before identity is confirmed.
   - Wrong person → ask them to have the patient call the clinic, no details → task.
   - "Is this a scam / who is this?" → clinic name, invite them to call the clinic directly → task.
7. Emma explains: "Dr Rao is unavailable on Monday, so we need to move your consultation at 5 PM." The reason category is never spoken.
8. **Ask the preference first (Q7):** same doctor on another day / another doctor at the same branch around the same time / another branch / "whatever is earliest". Then search.
9. Offer the nearest slot first, then up to two alternatives. The patient may also choose: keep it pending (status `needs_reschedule` + task), cancel, talk to staff (task), or "don't call me again" (do-not-call + task).
10. Accept → recap → yes → atomic reschedule (idempotency key = job ID + appointment ID) → confirmation → calendar sync.
11. Several affected appointments for one patient are handled one by one in the same call.
12. No valid alternative within the horizon → task. Emma never improvises.
13. **Stop** cancels queued jobs; a call in progress finishes. **Lifting the block** mid-campaign stops the remaining jobs; appointments already moved stay moved (the patient agreed).

---

## 6. Day-by-day build plan

Every stage ends with its exit tests green. Tests run after every step.

### Day 0 — today, 30 Sep: baseline and hygiene
- [x] **0.1** Delete legacy files; clean `requirements.txt` (remove Google STT/TTS, numpy, scipy; add jinja2, itsdangerous, piper-tts); pin Python 3.12.
- [x] **0.2** Rewrite `.env.example` (Deepgram, ElevenLabs, single Gemini key, dashboard password, service-account path, retention, calling window) and write the README quickstart. Replace `PRODUCTION_CALL_PATH.md` with `docs/TELEPHONY.md` (AudioSocket).
- [x] **0.3** Bind to 127.0.0.1 by default; WebSocket Origin check.
- [x] **0.4** Single Gemini key; periodic re-verification; remove `GEMINI_API_KEYS`.
- [x] **0.5** `clock.py`; replace every `date.today()` / `datetime.now()`.
- [x] **0.6** Remove caller text from `turns.jsonl`; mask phones in logs.
- [x] **0.7** Dev capture of caller PCM (`DEV_CAPTURE_AUDIO=true`) for the spike and replay.
- [x] **0.8** Latency diagnostics: `endpoint_source` (speech_final / UtteranceEnd / hold), `hold_ms`, Deepgram lag.

**Exit:** the 28 existing tests pass; a clean clone starts with only `.env.example` filled.

### Day 1 — 1 Oct: foundations (Milestone B core)
- [x] **1.1** `dateparse.py` + the 120-case table.
- [x] **1.2** `db.py`, migration runner, `001_init.sql`, single writer thread, WAL.
- [x] **1.3** `seed_demo.py`: 4 branches, 8 fictional DEMO doctors (general, endodontist, orthodontist, pedodontist, mixed genders, varied hours within 07–21), services + durations, one DEMO closure after the demo date, ~40 future DEMO appointments with fictitious numbers.
- [x] **1.4** `scheduling.py`: rules 5.1, search, holds, book/reschedule/cancel, idempotency, audit + outbox rows.
- [x] **1.5** Flux spike prep: record the 30-utterance test set (user, ~10 min, script in section 12). **Done 6 Oct:** the owner recorded the 30 lines twice (headset, laptop speakers); used for R3.1 and R3.4.

**Exit:**
- Every rule has a unit test.
- 10 threads racing for one slot → exactly 1 success.
- A failed reschedule leaves the original intact.
- A replayed idempotency key returns the same result.

> **Days 2 and 3 are replaced by section 0 (R1–R3).** They're kept below for reference: R2 covers 2.1–2.6 and 3.1–3.3 on the natural engine, and R3 covers 3.4–3.6.

### Day 2 — 2 Oct: dialogue engine and BOOK (Milestone A core), superseded by R2
- [x] **2.1** `dialogue/context.py` (serialisable call context), `router.py`, `globals.py`. **Superseded, done in** R2.0/R2.1 (`dialogue/context.py`, `policy.py`, `handlers.py`).
- [x] **2.2** `nlu.py` structured schema + prompt; `facts.py` with fact IDs; unknown → escalation task. **Superseded, done in** R2.2-R2.4 (`nlu.py`, `facts.py`; unknown questions get an honest answer and a callback offer).
- [x] **2.3** Tier-0 v2 (5.5): phone accumulator, name spelling, service ambiguity. **Superseded, done in** R2.5 (`tier0.understand`, `dialogue/match.py`).
- [x] **2.4** `prompts.py` with template IDs, 2–3 variants for frequent prompts, no repeats within a call; speakable rewriting of dates, times and grouped digits. **Superseded, done in** R2.4 (`prompts.py`).
- [x] **2.5** BOOK workflow on the scheduling engine, incl. corrections, family booking, duplicate and max-3 checks. **Superseded, done in** R2.6 (`dialogue/book.py`).
- [x] **2.6** Port the 28 tests. Tests whose behaviour changes on purpose (location step, separate date/time confirmations) are rewritten against the new flow and listed in the commit. **Superseded, done in** R2.8 (`tests/test_r2_booking_flow.py`; the originals still test the 12-step machine).

**Exit:**
- Scripted text conversations book correctly with the fake NLU.
- NLU/parser disagreement never commits.
- Clean confirmations use no LLM.

### Day 3 — 3 Oct: manage, safety flows, real-time robustness, superseded by R2 and R3
- [x] **3.1** MANAGE workflows: verify, check, cancel, reschedule. **Superseded, done in** R2.6 (`dialogue/manage.py`).
- [x] **3.2** Emergency (urgent + red flag), human/callback, language, abuse, bot question, don't-keep request. **Superseded, done in** R2.6 (`dialogue/handlers.py`).
- [x] **3.3** No-dead-end fuzz test across all states. **Superseded, done in** the harness: M3/M10 on 200-call sims per seed, with and without the model (`python -m harness run sim`).
- [x] **3.4** `playout.py`; recap-heard rule; backchannel filter; silence ladder; max length; call gate; hang-up handling. **Superseded, done in** R3.7; the playout is `speech.Speaker` (turn-tagged audio, flush on barge-in) rather than a separate `playout.py`.
- [x] **3.5** Deepgram reconnect; TTS chain with Piper (voice chosen by user) and a Piper prompt cache. **Superseded, done in** R3.7 (Deepgram reconnect, `tts_piper.py` + its prompt cache). Piper voice: `en_GB-cori-medium`, confirmed by the owner on 7 Oct.
- [x] **3.6** Flux spike, **time-boxed to 4 h**: `stt_flux.py` adapter behind the same callbacks, then replay the test set through both. **Switch only if** end-of-turn p50 improves ≥ 300 ms with no more than 1 extra misrecognised slot value on the set. Otherwise tune Nova-3 (endpointing / UtteranceEnd / holds) using the Day 0 diagnostics. **Superseded, done in** R3.4 (Flux replaced by the STT bake-off on the owner's recordings).

**Exit:**
- A wrong customer can't read, move or cancel an appointment, and learns nothing.
- Silence closes an abandoned call within 30 s and frees the gate.
- A Deepgram drop recovers mid-call.
- A barged-in recap can't be confirmed.

### Day 4 — 4 Oct: dashboard, calendar, transcripts (Milestone B complete)
- [x] **4.1** `auth.py`, `events.py` (SSE), dashboard shell (vanilla JS instead of htmx).
- [x] **4.2** Live call panel + takeover (Take over, typed lines, Hand back, End call + task; 6 Oct); appointments views + manual edits + CSV; tasks; calls (transcripts, no audio per R3); audit; system page.
- [x] **4.3** `tools/setup_calendars.py` + `calendar_sync.py` worker; health checks for credentials and calendar access.
- [x] **4.4** `recording.py`: transcript retention, don't-keep requests, 30-day purge job (no audio, R3).
- [x] **4.5** Talk page: clear states for connecting, busy, connection lost (reconnect button) and mic blocked; anti-aliased resampling (AudioContext at 16 kHz where supported).

**Exit:**
- A booking made with the network cut appears in SQLite and the dashboard immediately, and in Calendar exactly once after reconnecting.
- The purge deletes 31-day-old data (fake clock).
- The dashboard is unreachable without login.

### Day 5 — 5 Oct: recovery, demo grade (Milestone C)
- [x] **5.1** Blocks + preview + campaign start/stop + runner (gate, window, do-not-call, stale check).
- [x] **5.2** `/patient` ring page; outbound transport over the existing WebSocket.
- [x] **5.3** Recovery workflow (5.11), incl. identity check, preference-first offers, multi-appointment and all exits.

**Exit:**
- A block produces an accurate preview.
- Approved calls offer only valid slots.
- Accept → atomic reschedule + calendar update.
- Decline / no answer → task.
- A stale appointment is skipped.
- An inbound call during a campaign pauses the runner.

### Day 6 — 6 Oct: latency, fault drills, regression
- [x] **6.1** Latency pass with data: Tier-0 coverage, filler policy, first sentence cached, endpointing settings from the spike. Speculative NLU on stable interim text **only if** the gate isn't met. **Done 6 Oct (measured shortfall documented):** on the 5 Oct R2 voice calls end of speech was the largest cost (p50 1.6 s; Deepgram sent speech_final on only about a third of turns, so most ended on the 1 s STT watchdog). The watchdog now waits by what was heard (complete answer 0.35 s, likely 0.6 s, default 0.8 s, unfinished 1 s): replayed on those calls, end of speech p50 1.6 -> 1.3 s and perceived p50 2.1 -> 1.8 s. Gemini's time to first chunk has a floor of about 1.05 s whatever the request size (current 1.3 s; no schema or an 80% shorter brief each save about 0.2 s; implicit caching reports no cached tokens), so the brief was left as is. Filler policy (450 ms, at most every third turn) unchanged. Gate not met: Tier-0 p50 about 1.6 s vs 0.9 s; the rest is why speech_final rarely fires, to diagnose on the R3 recordings.
- [x] **6.2** Fault drills: Automated 6 Oct (`tests/test_fault_drills.py` + existing suites): killed inside a booking -> nothing written, the retry books once; killed after the commit -> the retry is replayed; restart during a recovery call -> job closed with a task; hang-up -> held slots freed at once (they used to stay held for 5 min) and a booking being written finishes as `booked_hangup`; Gemini off: 200 sim calls, Z1-Z7 0, M7 97%, M10 2.0% (after fixing a date misread after "go on?"). Live drills (ElevenLabs quota, Deepgram drop, closing the tab) are for the rehearsal.
  - Gemini off (Tier-0 + templates).
  - ElevenLabs off / quota exhausted (Piper).
  - Deepgram drop.
  - Calendar off.
  - Kill the server mid-booking (restart → no lost or duplicate appointment).
  - Close the tab at every state.
- [ ] **6.3** Full test suite + the manual checklist in section 10; fix list. Suite (872) and the 53 scripted scenarios pass on R2 (6 Oct); the manual pass through section 10 is the owner's rehearsal.

**Exit:** latency gate met or measured shortfall documented; all drills pass.

### Day 7 — 7 Oct: freeze and rehearse
- [ ] **7.1** Code freeze at noon; only demo-blocking fixes after.
- [x] **7.2** `docs/ARCHITECTURE.md` (pipeline, invariants, data model, sync, threat model summary) and `docs/DEMO_SCRIPT.md`. **Done 7 Oct:** [ARCHITECTURE.md](ARCHITECTURE.md) and [DEMO_SCRIPT.md](DEMO_SCRIPT.md). Scenes 1-5 were run through the engine on the demo data (model off); that found and fixed four bugs: a question about a branch chose it, "Her name is Diya" wasn't read as the patient's name, "pain since last night" was searched as an evening slot, and a reason given with a cancel request was dropped.
- [ ] **7.3** Three full rehearsals with a headset; screen-record one complete run as backup.
- [x] **7.4** Reset and reseed the demo DB; warm prompt caches; check provider balances. **Tooling done 7 Oct:** `tools/demo_reset.py` (clears Emma's Calendar events, rebuilds the DEMO clinic, books the script's fixtures) and `tools/preflight.py` (one-screen readiness check). Run both on the demo morning. **Done 7 Oct** (reset, fixtures, calendars synced, prompts warm, preflight READY). Repeat `tools/demo_reset.py` + `tools/preflight.py` before each rehearsal and on the demo morning; credit is checked on the providers' websites (the keys can't read balances).

### Day 8 — 8 Oct: demo
Run `docs/DEMO_SCRIPT.md`. Backups: typed-input mode (`emma.say()`), the recorded run.

---

## 7. Edge-case matrix

| Area | Edge case | Behaviour | Where |
|---|---|---|---|
| Turn-taking | Silence / abandoned tab | Silence ladder → close, gate freed | 5.7 |
| | "Hold on" | Timers extended to 30 s | 5.6 |
| | Asks to repeat | Re-speak last prompt | 5.6 |
| | Backchannel while Emma talks | Not a barge-in | 5.6 |
| | Barge-in during recap then "yes" | Finish recap, re-ask | 5.6 |
| | Two utterances in quick succession | Merged if still understanding; otherwise sequential | `call_session` |
| | Speakerphone echo | Browser AEC + echo filter; headset for the demo | 5.7 |
| | TV / background speech | Minimum words for barge-in; low-confidence handling | 5.6 |
| | Long monologue | Ask for a short version | 5.6 |
| | Gibberish repeatedly | Retry limit → callback/close | 3.3 |
| Intent | Book → cancel mid-call | Confirm switch, carry name/phone | 5.6 |
| | Several requests in one call | "Anything else?" loop | 5.6 |
| | Booking for a family member | Patient name/relation; age for pediatric | 5.3 |
| | Question mid-flow | Answer first (knowledge base, DB, general dental knowledge); steer back occasionally, never the same wording | 0.4, R2_DESIGN 9 |
| | Unverified/unknown fact | Honest "I'm not sure about that one" + offer; a real task only if they want a callback. Never the doctor deflection | 0.4, R2_DESIGN 11 |
| | "Are you a robot?" | Honest answer | 5.6 |
| | Wants a human | Help first; callback task only if they insist | 5.6, R7 |
| | Hindi/Kannada/other | Gently English only + offer a call back from the Kannada / Hindi-speaking team; `language` task only on yes. Hinglish out of scope | 5.6 |
| | Abuse | Warning → close | 5.6 |
| | Prompt injection in speech | Treated as data; nothing free-form spoken | 5.4 |
| Emergency | Severe pain / swelling / bleeding / broken tooth | Same-day earliest + urgent task | 5.6 |
| | Breathing/swallowing trouble, spreading swelling | 108/ER advice, task, end | 5.6 |
| | No same-day slot | Earliest tomorrow + task flagged | 5.6 |
| Identity | Phone in groups across turns | Digit accumulator | 5.3 |
| | +91 / 0 / landline / tens words / "double" | Normalised | 5.3 |
| | Too many digits | Reset and re-ask | 5.3 |
| | Phone unconfirmed after 3 tries | Close, `phone_failed` (never auto-accepted) | 5.3 |
| | Spelled name / initials / long South Indian names | Joined; 6-word cap lifted for spelled input | 5.3 |
| | Name keeps failing | Stored `name_unverified` | 5.3 |
| | Wrong details when managing | Generic "couldn't find", 2 tries → task | 5.6 |
| | Two family members, same date | Disambiguate by name, then time | 5.6 |
| | Doesn't remember appointment date | Can't verify → task | 5.6 |
| Dates/times | All rows of 5.2 | Parser rules + typed errors | 5.2 |
| | Midnight rollover during call | `clock.now()` on every parse and commit | 5.2 |
| | "Today" at 20:50 | Lead time → earliest tomorrow offered | 5.1 |
| | Sunday / closure / beyond 60 days | Typed reply + nearest valid | 5.1 |
| | Duration crosses lunch or closing | Rule 4 / 3 | 5.1 |
| Scheduling | Requested slot taken | 2 nearest, then next days | 5.1 |
| | Refuses all alternatives | New preference; 3 rounds → task | 5.1 |
| | Asks for a doctor / "lady doctor" | Filter; not available → others | 5.1 |
| | Service not at chosen branch | Name branches that offer it | 5.1 |
| | Unknown treatment (whitening, implant) | Offer a Consultation | 5.6 |
| | Two services in one call | Book one, "anything else?" for the next | 5.6 |
| | Same patient + service already booked | Keep or book another | 5.6 |
| | 3 future appointments on number | Refuse politely, offer manage/staff | 5.6 |
| | Slot taken between offer and yes | Hold prevents it; commit re-validates anyway | 4.2 |
| | Block added during a live booking | Commit re-validates → apologise + re-offer | 5.1 |
| | Reschedule into overlapping own cells | Supported in the transaction | 4.3 |
| | Reschedule to same slot | "That's already your time" | 5.6 |
| | Past / within-2 h appointment change | Not by phone → task | 5.6 |
| | Duplicate request / retried action | Idempotency key | 4.1 |
| Call lifecycle | Hang-up mid-flow | Holds released, `abandoned@state` saved | 5.7 |
| | Hang-up right after "yes" | Commit completes, `booked_hangup` | 5.7 |
| | Max call length | Wrap-up + task | 5.7 |
| | Second caller while busy | Busy message | 5.7 |
| | Server crash mid-commit | SQLite transaction all-or-nothing; outbox resumes | 4.2 |
| Providers | Gemini down / slow / quota | Tier-0 + fallback, breaker, re-verify | 5.4 |
| | Invalid LLM JSON / hallucinated fields | Schema + validation, unknowns dropped | 5.4 |
| | ElevenLabs down / out of characters | Piper for rest of call; balance on health page | 5.7 |
| | Deepgram drop | Reconnect with buffer → graceful end | 5.7 |
| | Google Calendar down / token issues | Outbox retries; service account (no expiring refresh token) | 5.9 |
| Privacy | Caller asks not to be recorded or kept | No audio is ever recorded; transcript blanked at hang-up; continue | 5.8 |
| | Data older than 30 days | Purge | 5.8 |
| | PII in logs / calendar | Masked / minimised | 5.8, 5.9 |
| | Unauthenticated access | Login, localhost bind, origin/token | 5.10 |
| Outbound | Wrong person answers | No details, task | 5.11 |
| | "Is this a scam?" | Clinic identity, call-back invitation, task | 5.11 |
| | No answer / declined | One attempt → task (retries after demo) | 5.11 |
| | Opt-out | Do-not-call + task; never dialled again | 5.11 |
| | Appointment already changed | Stale → skipped | 5.11 |
| | Block lifted mid-campaign | Remaining jobs stopped | 5.11 |
| | Several affected appointments | Handled in one call | 5.11 |
| | No alternative within horizon | Task, no improvising | 5.11 |
| | Outside calling window | Job waits | 5.11 |
| | Inbound arrives during campaign | Runner pauses | 5.7 |

---

## 8. Audit problems → where they are fixed

| # | Problem | Fix |
|---|---|---|
| 1 | Hangs up on "later"/"reschedule" | Router + globals (2.1, 3.1) |
| 2 | Dead-end loops (location, no alternatives, step 11) | Workflows + retry limits + fuzz test (2.5, 3.3) |
| 3 | Phone/name auto-accepted | 5.3 (0 auto-accept for phone; flagged names) |
| 4 | Past slots / no horizon | Rules 1–2 (1.4) |
| 5 | JSON DB wiped on parse error | SQLite WAL + migrations (1.2); JSON removed (0.1) |
| 6 | Calendar failure → mock check → double-book | Calendar never read in-call (5.9) |
| 7 | Browser OAuth mid-call | Service account + setup tool (4.3) |
| 8 | No silence timeout / max length | 5.7 (3.4) |
| 9 | Deaf after Deepgram drop | Reconnect (3.5) |
| 10 | LLM answer spoken verbatim | Fact IDs only (2.2) |
| 11 | Service mis-matching | Alias table + ambiguity (2.3) |
| 12 | Date bugs, server timezone | `dateparse` + `clock` (0.5, 1.1) |
| 13 | Time bugs, lunch overlap, off-grid | `dateparse` + rules 3–4 (1.1, 1.4) |
| 14 | Corrections without "no" ignored | Correction handling (2.5) |
| 15 | Fuzzy alternative pick | Structured choice (`choice_index`, exact grid match) (2.3) |
| 16 | False invite / callback promises | Q11 wording; tasks for every promise (2.2) |
| 17 | "Come again?" gets escalation | Global repeat (2.1) |
| 18 | Spelled name rejected | 5.3 (2.3) |
| 19 | Gemini never re-verified | 0.4 |
| 20 | Hard-coded Nagarbhavi | Branch step + DB facts (2.5) |
| 21 | Recap confirmed unheard | Recap-heard rule (3.4) |
| 22 | Backchannels interrupt | Filter (3.4) |
| 23 | Split phone rejected | Accumulator (2.3) |
| 24 | No emergency handling | 3.2 |
| 25 | Caller ≠ patient | 5.3 (2.5) |
| 26 | No durations/doctors/holidays/lead time | 5.1 (1.4) |
| 27 | Open network, no auth | 0.3, 4.1 |
| 28 | No server-side pacing | `playout.py` (3.4) |
| 29 | Stale env/README, broken `main.py` | 0.1, 0.2 |
| 30 | Dead code, conflicting docs | 0.1, 0.2 |
| 31 | Duplicated facts | DB + `facts.py` (1.3, 2.2) |
| 32 | `print`, service rebuilt per call | Logging; worker holds one client (4.3) |
| 33 | Mic aliasing | 4.5 |
| — | Latency dominated by end-of-speech | 0.8 → 3.6 → 6.1 |

---

## 9. Test plan

| Layer | What | Count (target) |
|---|---|---|
| Unit | `dateparse` table, phone/name normalisers, service/branch/doctor matching, confirmation parser, Tier-0 | 250+ cases |
| Scheduling | Every rule, holds, expiry, concurrency race, reschedule atomicity, idempotency, stale version | 40+ |
| Dialogue | Scripted conversations per workflow and per edge-case row in section 7, using a fake NLU; golden transcripts | 60+ |
| Fuzz | No-dead-end: random refusals/gibberish/silence in every state | 1 property test, 500 runs |
| Call session | Fake STT/TTS/transport: silence ladder, barge-in, recap-heard, backchannel, reconnect, gate, hang-up | 20+ |
| Sync | Fake Calendar client: create/patch/delete, 409 on retry, 404/410, backoff, coalescing | 10+ |
| Replay | `tools/replay.py` on the recorded set: latency + slot accuracy report | 1 report |
| Manual | Section 10 checklist in the browser, with a headset | 1 pass/day from Day 3 |

All external services are faked in automated tests. One manual real-Calendar test runs on Day 4.

---

## 10. Demo script (8 Oct), about 12 minutes

1. **Inbound booking** — the caller volunteers service and day, interrupts with "where is your Jayanagar branch?" (fact answer), barges in once. Requested time taken → alternatives → recap → booked. The dashboard live panel and the Calendar event appear.
2. **Family booking** — "for my daughter", Pediatric → age asked → booked with a lady doctor preference.
3. **Reschedule** — a first verification attempt with a wrong date fails without leaking anything; the second succeeds; the appointment is moved.
4. **Cancel** — reason given; Calendar event disappears.
5. **Emergency** — "severe swelling and pain" → same-day slot + urgent task pops up on the dashboard.
6. **Recovery** — block Dr Rao on a day → preview → start calls. The patient tab rings → identity check → "another doctor at the same branch" → rescheduled. The second job is declined → staff task.
7. **Resilience** (optional) — restart with the Gemini key blanked: the flow still works on Tier-0 and templates; show a failed-sync row retrying after the network is cut.
8. **Wrap-up** — latency panel (p50/p95), audit log, CSV export.

Backups: typed input, the recorded full run, reseed script.

---

## 11. Cut line (if we fall behind, cut from the top)

1. Speculative NLU
2. Dashboard extras: manual edit forms (keep manual cancel), audit viewer (R14)
3. Typed-speech takeover (keep "End call + task")
4. Recovery calls reduced to preview + one scripted call, then moved after the demo (R14)
5. STT candidates beyond the best two in the bake-off

**Never cut:** the North Star realism work (R1–R3), invariants in 3.3, scheduling rules and transactions, idempotency, verification, emergency handling, no-dead-end guarantee, silence ladder, calendar outbox.

---

## 12. What only you can do

| When | Task | Cost |
|---|---|---|
| Day 0–1 | Create a new Gmail for the demo; create a Google Cloud project; enable the Calendar API; create a service account and download its JSON key to `secrets/google-service-account.json` (I'll give exact clicks) | free |
| by 2 Oct | Record the 30-line test set ([STT_TEST_SET.md](STT_TEST_SET.md)); a second speaker is a bonus | 10 min |
| by 2 Oct | For the STT bake-off: enable Speech-to-Text on the same Google Cloud project as Calendar (new accounts get free credit; billing must be switched on for Chirp), and sign up at Sarvam AI for an API key | free credits |
| 1 Oct | Approve the ambience and sound-effect downloads I propose (each with file, source, licence and size) | free |
| Day 1 | Check ElevenLabs characters left. Rehearsals may exhaust the free 10,000; either take the Starter plan for the week or accept Piper as the voice when it runs out | optional ~$5 |
| Day 3 | Pick a Piper voice from 2–3 samples | free |
| Day 4 | Set the dashboard password in `.env` | free |
| Day 7 | Rehearse with a headset in a quiet room; have a phone hotspot as internet backup | — |
| Before any real patient data | Move Gemini, Deepgram and ElevenLabs to paid/no-training plans and review each vendor's data-use terms (Q13) | paid |

---

## 13. After the demo

**C-full (≈2 days): done 6 Oct, before the demo by the owner's choice.**
- [x] Retry policy: an unanswered call is tried again `RECOVERY_RETRY_GAP_MIN` (120) later, up to `RECOVERY_MAX_ATTEMPTS` (3), inside the calling window and the patient's hours; only then NEEDS RESCHEDULE + task. A declined call is not retried (the demo's "Rahul declines → task" is unchanged). A patient's "call me after 6" / "in an hour" / "tomorrow morning" is booked as the next try (even past the attempt limit) and Emma says when.
- [x] Pause/resume (campaign `paused_at`; a call in progress finishes).
- [x] Per-patient calling windows (`contact_prefs.call_after/call_before`, set on the Recovery tab; Sundays skipped).
- [x] Campaign reporting: a results line per campaign (moved, cancelled, need a new time, retrying, waiting) and a CSV export (masked phones, audited).
- Schema: `migrations/002_recovery_full.sql` adds columns only, so existing databases upgrade in place. Tests: `tests/test_recovery_full.py` and additions to `tests/test_recovery.py`.

**D — Asterisk: built 6 Oct, on the demo branch by the owner's choice; the real-softphone run waits for WSL2.** Details: [TELEPHONY.md](TELEPHONY.md).
- [ ] WSL2 + Ubuntu (owner installs: admin rights and a reboot), mirrored networking, Asterisk 20 LTS from Ubuntu (`telephony/install_asterisk.sh`; 22 only if 20 lacks something), MicroSIP extensions 1001 (caller / patient) and 1002 (front desk).
- [x] `audiosocket.py` implementing the same Transport (the plan's `audiosocket_server.py`):
  - [x] TCP frames of `type(1) + length(2, big-endian) + payload`: UUID, audio (8 kHz; 16 kHz accepted inbound), keypad digit, hang-up, error.
  - [x] A real-time playout clock paces 20 ms frames and reports playback started / ended / interrupted with played_ms (the browser's job on the talk page).
  - [x] Barge-in: at most one frame is queued in Asterisk, so a flush is instant.
  - [x] Typing and the occasional door / chair / footsteps mixed server-side (`phone_audio.LineSounds`, the twin of `ambience.js`).
- [x] Sample rates: Emma stays at 16 kHz (Deepgram, VAD, voice, one prompt cache); stateful anti-aliased resampling at the line's edge (`phone_audio.py`) instead of per-format caches.
- [x] Caller ID: the dialplan registers `CALLERID(num)` (POST body) and gets the UUID; Emma asks "Is the number you're calling from the best one to reach you on?" instead of digits (`ai_engine.phone_line`).
- [x] DTMF entry for phone numbers (`#` or 3 s of quiet sends them; the first key stops Emma).
- [x] Live transfer to the front desk: after the caller insists, the callback task is written, then "I'm putting you through..."; `/telephony/next` tells the dialplan to dial `TELEPHONY_FRONT_DESK`.
- [x] Outbound recovery calls via AMI Originate to the softphone (`ami.py`, `outbound.set_dialer`); Reject on the phone declines.
- [ ] Verify on the real Asterisk: keypad frames through AudioSocket, 16 kHz AudioSocket audio, the Reject reason code (TELEPHONY.md, last section).
- Tests: `tests/test_telephony.py` (25) with a simulated Asterisk and manager; live smoke test on the real server (greeting 0.07 s after connect, 49.9 frames/s, clean hang-up).

**E — evaluation hardening: built 6 Oct** (plan: [FINAL_PHASES_PLAN.md](FINAL_PHASES_PLAN.md)).
- [x] p50/p95 latency, task success, fallback and interruption rates: `tools/evaluate.py` → [EVALUATION.md](EVALUATION.md), regenerated from the data.
- [x] STT word error on the recorded set: `tools/evaluate.py --stt` (6 Oct: 17% / 18%, 1-2 lines cut of 30, ~0.9 s after the last word).
- [x] Booking-integrity report from failure drills (EVALUATION.md section 4), including `tools/crash_drill.py`: a writer process booking, moving and cancelling through `scheduling.py` is killed outright at random moments; after each kill the database is checked (integrity, claims, nothing lost or half-written) and the interrupted request is retried (acts once, then replays).
- [x] Daily SQLite backup (`tools/backup.py`: online backup, verified, keep 14, restore, consistency check), log rotation (`turns.jsonl` by size, optional daily `LOG_FILE`).
- [x] Linux deployment kit (`deploy/`: systemd unit, Caddy HTTPS, backup timer, install script; [DEPLOY.md](DEPLOY.md)). [ ] Verify it in WSL Ubuntu.
- [x] [THREAT_MODEL.md](THREAT_MODEL.md), [VIVA_NOTES.md](VIVA_NOTES.md).

---

## 14. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Schedule slip | High | Cut line; Day 7 freeze; daily green tests |
| End-of-speech latency stays ~1 s | Medium | Flux spike; tuning with diagnostics; honest reporting against the gate |
| ElevenLabs characters run out in rehearsals | High on free tier | Piper fallback per call; balance on health page; optional Starter |
| Gemini free-tier limits during the demo | Medium | Tier-0 handles most turns; breaker; fallback |
| Demo-room echo / noise | Medium | Headset, quiet room, echo filter |
| Network at the venue | Medium | Hotspot; recorded run; calendar outbox tolerates outages |
| Google setup delays | Medium | Do it on Day 0–1; everything else works without Calendar |
| Flux API differs from expectations | Medium | 4 h time box; adapter behind the same callbacks |

---

## 15. Out of scope for v1

PSTN calling, SMS/WhatsApp/email confirmations, OTP, payments, insurance claims handling, languages other than English (including Hinglish, by the owner's decision on 1 Oct), multiple concurrent calls, two-way calendar sync, Google Sheets, returning-caller personalisation before verification, medical advice of any kind.
