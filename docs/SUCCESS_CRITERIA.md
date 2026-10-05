# Success criteria: how we know Emma works

**Owner's criteria, 1 Oct 2026.** Read with [NORTH_STAR.md](NORTH_STAR.md). This file is the scoring guide for the conversation test harness and the acceptance gate for every sprint. Hinglish (Hindi words mixed into English) is **out of scope** by the owner's decision; Indian-accented English is in scope.

> **The biggest test:** can a caller talk to Emma in their own way, with interruptions, questions, off-topic comments, an Indian accent, incomplete answers and changes of mind, without Emma getting stuck, repeating herself, skipping information or forcing them back into the script, while she still reliably completes the booking?
>
> The goal isn't for Emma to follow the script perfectly. It's for her to understand what the caller wants and use the script intelligently to get there.

---

## 1. The twelve criteria

1. **Natural conversation.** Friendly, conversational and human, never reading a fixed script. She responds naturally to unexpected questions, comments, interruptions and off-topic replies. The script is a guide, not a rigid sequence. Context is kept for the whole call.
2. **Listening and understanding.** Different Indian accents, pronunciations and speeds are understood. She doesn't ask the caller to repeat when the intent is reasonably clear. When something genuinely isn't understood, she asks naturally instead of restarting. Background noise, pauses, fillers and incomplete sentences don't break the call.
3. **No repetition or loops.** She never repeats the same sentence or question unnecessarily. If the caller doesn't answer, she adapts rather than asking the exact same question again. She remembers what's been answered. Never: "Would you like to book?" → caller asks something else → "Would you like to book?" → again.
4. **Interruptions and unexpected questions.** She can step away from the booking, answer, and come back. The caller never feels forced back into booking before their question is dealt with.
   - Caller: "How can you help me?" → Emma: "I can tell you about our clinic, the services we offer, our doctors, prices and timings, and book, change or cancel appointments. What would you like to know?"
   - After answering, an optional, varied, occasional steer: "If you'd like, I can book that for you too." Not after every answer (that becomes criterion 3's loop).
5. **Appointment flow.** Every required detail is collected; nothing required is skipped; nothing already given is asked again. Several details in one sentence are all used: "Tomorrow evening around 6 with Dr Sharma" gives the day, the time and the doctor. (There's no Dr Sharma in the demo clinic, so Emma should say so and offer the doctors who are there.)
6. **Non-answers handled gracefully.** "What day would you like?" → "I've been really busy lately." → "No worries, we'll find something that fits. Are you thinking this week or next?" Never the same question three times.
7. **No doctor escape route.** "The doctor will discuss that at your visit" is not a fallback. Answer what she can, clarify when needed, explain limits naturally, and refer to the doctor only for genuinely clinical questions (diagnosis, "do I need X", medication).
8. **Seamless turn-taking, not a walkie-talkie.** Prompt replies, no unnatural pauses, no cutting the caller off, correct end-of-speech detection, short pauses not taken as the end, natural interruptions and corrections allowed, no talking over the caller.
9. **Context retention.** "My name is Rahul" → "Nice to meet you, Rahul" → later "Could you tell me your name?" must never happen. A Saturday preference isn't asked again unless it needs clarifying.
10. **Friendly personality.** Warm, helpful, patient, casual but professional, approachable. Natural language, not repeated formal phrases.
11. **Recovery from errors.** "Sorry, I misunderstood you there. You said Tuesday, right?" instead of restarting.
12. **Efficiency.** The caller reaches the outcome in as few unnecessary turns as possible. The phone read-back and the final summary before booking are necessary turns, never cut.

---

## 2. Targets and how each is measured

Each percentage has a stated base. A target below 2% is only meaningful over a large sample, so every test round runs **at least 200 simulated calls** (scripted plus simulated callers); smaller runs report counts, not percentages.

| # | Metric | Base | Target | How it's measured |
|---|---|---|---:|---|
| M1 | Required detail skipped | per completed booking / change / cancel | **0** | Automatic: the committed action is checked against the required details (below) |
| M2 | Question repeated after it was answered | per question Emma asks | **< 2%** | Automatic: Emma asks for a slot the context already holds, with no correction or clarification reason |
| M3 | Exact or near-exact repetition loop | per call | **< 1%** | Automatic: an Emma line ≥ 85% similar to the previous one, or the same line 3+ times in a call |
| M4 | Unnecessary doctor redirect | per Emma reply | **< 2%** | Phrase detection, then an AI reviewer judges whether the question was genuinely clinical |
| M5 | Caller has to repeat something already given | per caller turn | **< 2%** | AI reviewer, plus the simulated caller flags when it had to repeat itself |
| M6 | Relevant question answered before steering back | per caller question | **> 95%** | AI reviewer |
| M7 | Booking completed when the caller intends to book | per call with booking intent and the needed details | **> 95%** | Automatic: database outcome vs the caller's goal |
| M8 | Indian-accent understanding | per slot value (name, phone, date, time, service) in the recorded set | **> 95%** | Audio replay of the owner's recordings ([STT_TEST_SET.md](STT_TEST_SET.md)); measured once they exist |
| M9 | Unnecessary restart | per call | **< 1%** | Automatic: context reset, greeting repeated, or already-collected details dropped |
| M10 | Call ends because Emma couldn't handle a response | per call | **< 2%** | Automatic (no outcome, caller goal unmet) plus AI reviewer |

**Required details.**
- **Booking:** service, a branch that offers it, an offered real slot (date and time), patient name, phone number read back with a yes, and one summary heard in full with a clear yes.
- **Reschedule / cancel / check:** verification by phone + patient name + appointment date before anything is revealed or changed, then a summary and a clear yes for any change.

### Zero-tolerance guarantees (any single occurrence fails the round)

| # | Failure |
|---|---|
| Z1 | Booked, moved or cancelled without a clear yes to a summary the caller heard |
| Z2 | Emma says "booked", "moved" or "cancelled" when Python didn't commit it |
| Z3 | Emma claims to be human, or volunteers being automated unprompted |
| Z4 | An invented fact, price, doctor, branch or slot is spoken |
| Z5 | Wrong outcome: the caller asked for one action and got another (e.g. asked to cancel, ended up booked) |
| Z6 | A service booked at a branch that doesn't offer it |
| Z7 | Appointment details revealed before verification |

### Turn-taking and efficiency

| # | Metric | Target |
|---|---|---|
| T1 | Perceived reply latency, simple turns | p50 ≤ 0.9 s, p95 ≤ 1.8 s |
| T2 | Perceived reply latency, turns that need the LLM | p50 ≤ 1.8 s, p95 ≤ 3 s |
| T3 | Caller cut off mid-sentence | < 3% of caller turns (live logs and recordings) |
| T4 | Backchannels ("yeah", "okay", "mm-hmm") interrupting Emma | 0 |
| T5 | Caller turns for a simple booking (details given one at a time) | median ≤ 9 (about 7 is the minimum) |

Latency is measured from `/metrics` (`perceived_p50_ms`, `perceived_p95_ms`) and, in the text harness, as engine time per turn.

---

## 3. Test layers

1. **Regression scenarios** (deterministic, fake NLU, in `unittest`): every failing call from the 30 Sep – 1 Oct test calls ([HANDOFF.md](HANDOFF.md) section 5) plus one scenario per edge case.
2. **Adaptive simulated callers** (hundreds of calls, seeded): a rule-based caller that answers what Emma actually asks, with injected disruptions (off-topic questions, non-answers, corrections, intent switches, several details at once, silence, fragments). Runs against the real Gemini NLU or the fake one.
3. **AI persona callers**: agents playing distinct callers (busy professional, elderly and chatty, indecisive, cancel-then-rebook, price shopper, skeptic who asks if she's a bot, emergency, parent booking for a child), in Indian-English phrasing ("prepone", "kindly", "do the needful"), each transcript judged against section 1.
4. **Audio replay** of the owner's recordings for M8 and T3.

Every round produces a failure catalogue: one row per root cause, with counts, example transcripts and the fix owner. That catalogue decides the fix order.
