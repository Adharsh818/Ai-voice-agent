# Viva notes: Emma, the Pearl Dental voice receptionist

A one-page brief for the project viva: what it is, how it works, why it's built this way, the numbers, the limits. Details: [ARCHITECTURE.md](ARCHITECTURE.md), [EVALUATION.md](EVALUATION.md), [THREAT_MODEL.md](THREAT_MODEL.md).

## In one paragraph

Emma answers the phone for a four-branch dental clinic in Bengaluru. Callers book, move and cancel appointments, ask about the clinic, and report emergencies, in natural Indian-accented English, and should feel they're talking to a skilled human receptionist. When a doctor suddenly can't come in, Emma calls the affected patients herself and moves them. Staff watch live calls, take over, and manage everything from a dashboard; bookings mirror to Google Calendar. Calls come in from a browser or a real SIP phone through Asterisk.

## How a turn works

```text
caller audio ─> Deepgram Nova-3 (en-IN) ─> voice-activity + turn detector ─> R2 dialogue engine
                                                                              │
     ┌── Tier-0 rules understand it (49% of real turns, no model wait) ───────┤
     └── else Gemini Flash-Lite reads it (streamed, with a deadline) ──────────┤
                                                                              v
          Python: apply facts → workflow (search, hold, verify, commit) → choose the next goal
                                                                              v
     reply = validated model wording + Emma's pre-written lines ─> ElevenLabs (cache first; Piper backup) ─> caller
```

## Key design decisions (and why)

1. **Python decides, the model only talks.** The LLM never books, cancels or states a fact on its own: it proposes wording; Python checks every slot, name, price and doctor against the database before anything is said. *Why:* LLMs hallucinate and can be talked into things; a clinic can't have either. Result: zero safety failures over 800 simulated calls.
2. **One clear yes to a heard summary.** Nothing is committed without a yes to a summary that played to the end (the "recap-heard rule"; interrupted summaries don't count).
3. **The database prevents double booking, not the code.** One row per doctor per 15-minute cell under a primary key, holds with expiry, idempotency keys for retries. Races can't produce two bookings.
4. **Works without the model.** Tier-0 rules handle names, numbers, yes/no, dates and plain requests; if Gemini is slow or down, Emma carries on with her own rules and lines (100% bookings completed with the model off).
5. **Sounds like a person.** Short varied lines, implicit confirmations ("Neha Kapoor, got it."), the number read back in groups, typing sounds when she writes something down, no background bed, barge-in that stops her instantly.
6. **Listening tuned on real recordings.** End of turn is decided from the caller's voice level and the words (not the recogniser's timing alone), measured on the owner's recordings: cut-off lines fell from 5 to 1 (headset) and 11 to 2 (laptop).
7. **Honesty boundary.** She never volunteers she's automated, and never claims to be human when sincerely asked.
8. **Solve it on the call.** Asked for a person, she first offers to help; only if they insist, a callback task (or a live transfer on the phone) — written before it's promised.

## Numbers (from EVALUATION.md, 6 Oct 2026)

| What | Result |
|---|---|
| Tests | about 950 unit/integration tests, 53 scripted conversation scenarios |
| Simulated calls (4 × 200, model faked and switched off) | bookings completed 100%, dead ends ≤ 0.5%, loops ≤ 0.5%, safety checks 0 |
| Real-call latency (520 replies) | rules turns p50 1.63 s (target 0.9 s), model turns p50 2.46 s (target 1.8 s) |
| Turns needing no model | 49%; model fallback 3% of model turns |
| Speech recognition (owner's recordings) | about 17% word error; names are the main misses, caught by read-backs |
| Fault drills | 5 of 5 (crash mid-booking, hang-up mid-booking, retries) |

## Likely questions

- **Why not let the LLM run the whole conversation?** It can't be trusted with actions or facts; it's used where it's strong (understanding messy speech, natural wording) and checked where it's weak.
- **What happens if Gemini goes down mid-call?** The turn falls back to Tier-0 understanding and pre-written lines; the caller hears a normal reply, slightly less flexible.
- **How do you stop double bookings?** Slot claims under a primary key, inside one transaction with the appointment; holds while offering; idempotent retries. Drills kill the process mid-transaction to prove it.
- **How do you know it works?** A simulated-caller harness (disruptions: corrections, silence, interruptions, intent switches, the bot question), scored against written success criteria with zero-tolerance safety checks; real-call latency logs; replay of real recordings.
- **Why is latency above target?** Most of it is end-of-speech detection; it was tuned to avoid cutting callers off, which matters more to the caller than half a second.
- **Privacy?** Minimal data to Calendar (first name, last 4 digits), phone numbers masked in logs, transcripts blanked after 30 days, no audio stored, staff actions audited.
- **Is it a real phone system?** Yes: Asterisk with AudioSocket; a SIP phone (or a softphone on a mobile over Wi-Fi) calls Emma, with caller ID, keypad entry, live transfer and outbound recovery calls. A public phone number (PSTN) is future work.

## Limitations and future work

- A public phone number (a SIP trunk or Twilio), several calls at once, per-staff dashboard accounts.
- Simple-turn latency (a faster turn detector or streaming end-of-turn model).
- Hindi/Kannada and Hinglish (out of scope by decision).
- Two-way calendar sync; SMS/WhatsApp confirmations.
