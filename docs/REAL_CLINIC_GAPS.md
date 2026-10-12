# What Emma needs for real clinics (audit, 11 Oct 2026)

Owner's request (11 Oct): check latency and which tier answers, check the whole dashboard, fix the jumbled live transcript, find every improvement and edge case a real clinic would hit, give Emma real clinic knowledge (callers ask about problems, prices, doctors, family suitability, not only appointments), and list what's missing to sell Emma as a service to clinics. Then do the work.

Sources: an audit agent read the code and drove the R2 engine with the model off (the path that answered 63 of 84 phone model turns on 8 Oct); a research agent read Bangalore clinic sites (Clove, Partha, Sabka, Samhita, Dentick), Practo, insurer guides and the DCI ethics code; latency from `logs/turns.jsonl` (766 replies); the dashboard was reviewed on a copy of the live database. Status is ticked as items are done.

## Done already

- [x] **Live tab transcript jumbled on phone calls** (owner): Emma's line appeared seconds before it was heard and each sentence added another copy of the reply. Now paced to playback, one line per turn (`caption_pacer.py`, 1671820).
- [x] **K1-K3 clinic knowledge, doctor profiles, Dr Ali an oral surgeon** (746cbf3): 26 new or rewritten facts, complete answers (owner: no "the doctor will confirm"), profiles with degrees/years/languages, kids and language questions, the most specific fact wins.
- [x] **D1-D8 dashboard** (e3b9a94): plain outcomes, browser/phone column (migration 003), "How Emma is doing" panel, readable audit log, Attended/No-show, Sunday note, upcoming-only reschedule count.
- [x] **C1 up to 3 calls at once** (9a5dd4b): one line always kept for callers; the Live tab follows one call with a switcher.
- [x] **Sim-found fixes**: honest reasons for impossible times (doctor hours, lunch), the same time at another branch offered at once and never assumed, turned-down reschedule times not offered again, "why do you need my number?", no "consultation" line after a treatment answer.

## Latency and tiers (real calls)

Tier 0 = Emma's own rules, 1 = the model (Gemini), 2 = fallback when the model is too slow (over `NLU_HEAD_DEADLINE_S` 2.2 s) or down.

| | Browser (119 calls) | Phone (22 calls, 8 Oct) |
|---|---|---|
| Replies by tier 0 / 1 / 2 | 284 / 265 / 14 | 119 / 21 / **63** |
| Simple reply, p50 / p95 | 1.51 / 3.06 s | 1.64 / 3.15 s |
| of which end-of-speech detection, p50 | 1.23 s | 1.24 s |
| Model reply, p50 / p95 | 2.46 / 5.15 s | 3.13 / 6.73 s |
| Model time, p50 / p95 | 1.39 / 2.49 s | **2.21 / 2.23 s** (at the limit) |
| End of turn found by Emma's watchdog, not Deepgram | 23% | **51%** |
| Simple replies spoken by live TTS, not the prompt cache | 36% | **58%** |

1. **L1 Gemini on the phone day ran into the limit** on almost every model turn (p50 2.21 s against 2.2 s), so 3 in 4 model replies were the plainer fallback. Measure Gemini today (`tools/preflight.py`); the fallback path must stay good (it's what the audit tested).
2. **L2 End-of-speech detection is most of the wait** on simple turns (1.2 s of 1.6 s). On the phone Deepgram's own end-of-speech came less often (8 kHz audio), so the slower watchdog decided half the turns.
3. **L3 Rule replies often go to live TTS** because their wording varies (prompts.py variants); the cache only holds fixed lines. Pre-render all variants of the commonest tier-0 lines.
4. **L4 Deliberate pauses**: tier-0 replies add a 0.2-0.36 s "typing beat" (realism) on top.

## Dashboard

- [x] **D1** Calls tab shows internal outcome codes (`abandoned@ask_name`); show plain words ("Hung up while giving name"). *Done: e3b9a94.*
- [x] **D2** No browser/phone column on calls (the turn log has it from 11 Oct). *Done: e3b9a94.*
- [x] **D3** Nothing on the dashboard shows latency or tiers; add a "How Emma is doing" panel (last 7 days: reply time, share answered by rules / model / fallback, by channel). *Done: e3b9a94.*
- [x] **D4** Transcript turns show "tier -1" etc.; label them (greeting, rules, model, fallback). *Done: e3b9a94.*
- [x] **D5** Audit log shows appointment hashes (`appointment 753b50db…`); show patient, time, branch. *Done: e3b9a94.*
- [x] **D6** `needs_reschedule` count has no date filter (`dashboard.py:159`): past ones count forever. *Done: e3b9a94.*
- [x] **D7** Appointments never become `completed` / `no_show`; add buttons (and a nightly "past and still booked" list). *Done: e3b9a94.*
- [x] **D8** A closed day (Sunday) shows an empty list with no "closed" note. *Done: e3b9a94.*

## P0: would break or embarrass in a real clinic

- [x] **C1 One call at a time** (`server.py` `CallGate`): a second caller gets "busy"; a recovery campaign blocks inbound calls. *Done: 9a5dd4b, up to 3 lines.*
- [ ] **C2 Recovery calls ring one fixed phone** (`TELEPHONY_PATIENT_PHONE`), not the patient's number (needs a trunk, P1-13). *Open: code ready (TELEPHONY_TRUNK_DIAL, docs/TRUNK.md); needs a SIP trunk account (T1).*
- [x] **C3 Closed hours**: urgent callers hear "let's get you seen today" at 22:40; no "we're closed now"; no after-hours advice; "are you open now?" recites weekly hours. *Done: closed-now line, after-hours advice, first open day.*
- [x] **C4 Bleeding ends the call with "call 108"**: post-extraction bleeding is the commonest post-op call; it should be first aid + urgent slot, with 108 only for red flags. *Done: first aid + urgent slot; 108 only for red flags.*
- [x] **C5 Post-treatment complaints become new bookings** ("I had an extraction yesterday…" → "that date's passed"). Needs a follow-up intent. *Done: past treatment dates aren't booking days.*
- [x] **C6 Names captured from sentences**: "I'm stuck in traffic" → "Nice to meet you, Stuck"; "this is Dr Rao, I'm not coming" → "Dr Rao I'M". *Done: names only from name-shaped answers.*
- [x] **C7 "Running late"** starts a new booking. *Done: late patients told it's fine, no new booking.*
- [x] **C8 Bookings made outside Emma** (walk-ins, paper diary) are invisible; the dashboard's "New appointment" must be the diary. *Done: dashboard "New appointment" is the diary for walk-ins and phone bookings.*
- [x] **C9 Spoken hours vs rota**: "our timings are 7 to 9" while 8:30 pm can't be booked; hours live in three places (config, clinic_facts.json, rota). *Done: hours say "depending on the branch"; closures from the dashboard (S3).*

## P1: needed to sell this

- [x] **K1 Clinic knowledge** (research): crowns, dentures, gum treatment, X-ray and kids' preventive prices; after-care; medical conditions (diabetes, BP, blood thinners, pregnancy: "tell the doctor, don't stop medicines"); child's first visit; elderly patients; second opinions; records copies; written estimates; warranties; EMI over ~10,000; insurance detail (OPD/reimbursement only); wheelchair access per branch; red-flag emergencies. Price corrections: extraction, implant lower bound, aligners. All need the owner's sign-off (decision R8: Emma speaks only verified facts). *Done: 746cbf3; prices marked demo until the owner signs off.*
- [x] **K2 Doctor profiles**: degrees (BDS / MDS specialty), years, languages, memberships; "who's best for my kid" → the pediatric dentists. Never "best" (DCI ethics 8.2.7); "specialist" only for MDS holders. *Done: 746cbf3.*
- [x] **K3 "Can you do X?"** misses treat/remove/fix verbs (`facts._DO_YOU_DO`). *Done: 746cbf3.*
- [x] **K4 Other callers**: wrong number, sales/vendor, staff or doctor calling, records/bill/prescription requests: polite handling + a task. *Done: wrong numbers, vendors, staff, records requests.*
- [x] **F1 Family bookings**: second person in the same call loops; the per-phone cap of 3 blocks families and multi-sitting treatment. *Done: 7dfed64.*
- [x] **F2 Kannada/Hindi callers** get no next step; always offer a callback and create a language task. *Done: 29880be.*
- [x] **F3 Urgent callers who don't book leave no task.** *Done: urgent hang-ups make a task.*
- [x] **F4 Doctor on leave** is described as "fully booked"; say she's away and offer a colleague. *Done: doctor on leave said, colleague offered.*
- [ ] **T1 Real phone number** (Exotel / Twilio India / Plivo SIP trunk); today it's a laptop's Asterisk with softphones. *Open: guide in docs/TRUNK.md; needs the owner to open an account.*
- [x] **T2 WhatsApp/SMS confirmations and reminders** (DLT-registered templates for SMS). *Done: notify.py ready, log-only until a provider is set up (docs/NOTIFICATIONS.md).*
- [x] **S1 DPDP Act 2023**: notice, retention beyond transcripts (audit JSON, actions, patients), erase-by-phone, breach runbook, `LOG_CALLER_TEXT=false` default. *Done: 8d26bdf.*
- [x] **S2 Per-staff logins and roles**; audit says "staff" for everyone. *Done: 4a28406.*
- [ ] **S3 Config without code**: clinic hours, closures/holidays (Diwali etc. unknown today), doctors, rotas from the dashboard. *Open: closures and holidays done (bc1c322); hours, doctors and rotas still come from config and the seed.*

## P2: SaaS readiness and polish

- [ ] Multi-tenancy (one process + DB + config per clinic first; `clinic_id` later); "Pearl Dental" is hard-coded in greetings, prompts, keyterms. *Open: one deployment per clinic: name from .env (c2c37f3); a shared `clinic_id` database is later.*
- [x] Cost metering per call; alerting on degraded health, failed sync, stuck call slot; `/health` and `/metrics` are public behind Caddy. *Done: c2c37f3: cost per call, alerts (alerts.py); /health and /metrics refused by Caddy.*
- [x] `/ws/voice` public in production spends API credit: gate it. *Done: 0a974ee, the talk page needs a staff login.*
- [x] `deploy/install.sh` should generate `DASHBOARD_SESSION_SECRET`. *Done: installer writes it.*
- [x] Booking edge cases (12 Oct): two branches without travel time (90-min buffer, also when offering times); one name spelled two ways is one patient (identical sound-alike key only: Adarsh/Adharsh merge, Arun/Arjun don't; a real mishearing like Adesh/Adharsh still makes two); a child's age asked once for "my son/daughter"; foreign numbers told plainly; landlines read back as STD code + number (080 numbers as the caller said them); "today" pinned to the call's start day; inside the 2-hour notice a move later the same day with the same doctor (30 min ahead at least), anything else a callback.
- [ ] The honesty line "Yeah, you caught me…" implies hiding; NORTH_STAR's example is neutral. *Open: the owner's locked decision (R-list); waits for the owner.*
- [ ] Prune `actions`, old `slot_claims`, expired holds; retire the legacy 12-step engine. *Open: pruning done (c2c37f3); retiring the legacy engine proposed as its own session.*
- [x] Research flags: Dr Ali (extractions + braces + Invisalign) is unrealistic, make him an oral surgeon or move braces; OPG at every branch is unlikely; say "clear aligners, including Invisalign". *Done: 746cbf3 (Dr Ali an oral surgeon; OPG at two branches; clear aligners wording).*
