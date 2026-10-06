# Demo script: Thursday 8 October 2026 (about 12 minutes)

Everything on screen is fictitious DEMO data. The appointments the scenes rely on are created by `tools/demo_reset.py` (its `FIXTURES`), relative to the demo date:

| Fixture | Patient | Phone to say | Appointment |
|---|---|---|---|
| Recovery, answers | Priya Sharma | (rings on /patient) | Consultation, Dr Rao, Nagarbhavi, **Fri 9 Oct 11:00** |
| Recovery, declines | Rahul Verma | (rings on /patient) | Check-up, Dr Rao, Nagarbhavi, **Fri 9 Oct 12:00** |
| Reschedule | Anita Desai | **98450 12345** | Cleaning, Dr Menon, Indiranagar, **Mon 12 Oct 10:00** |
| Cancel | Kiran Rao | **98450 67890** | Filling, Dr Nair, Jayanagar, **Sat 10 Oct 13:00** |

If you rehearse on another day, run `tools/demo_reset.py --demo-date <that day>`; the days below then shift with it.

---

## 0. Thirty minutes before

1. Stop Emma if she's running. Run `.\.venv\Scripts\python.exe tools\demo_reset.py`, then start Emma (`.\.venv\Scripts\python.exe server.py`).
2. Run `.\.venv\Scripts\python.exe tools\preflight.py`. It should say **READY**. Two WARN lines about credit are normal (the keys can't read balances): check ElevenLabs (over 3,000 characters) on the website. It also times Gemini: if it says "slow today", put the `NLU_HEAD_DEADLINE_S` value it suggests in `.env` and restart Emma (she waits a little longer for the model instead of using her written lines; a short "Okay," covers the pause).
3. Open, in **Chrome**, in this order (one window, tabs left to right):
   1. http://localhost:8000 (talk page)
   2. http://localhost:8000/dashboard (log in; Live call tab)
   3. http://localhost:8000/patient (click once anywhere so its ringtone can play)
   4. Google Calendar, week view of 8-12 Oct, with the four Pearl Dental calendars ticked
4. Headset on, plugged in before Chrome opens. Close Zoom, WhatsApp and anything else using the mic. Phone hotspot ready as backup internet.
5. Quick sound check: a 10-second call on the talk page ("Hi, what are your timings on Saturday?"), then hang up.

**Rehearsed lines:** every line in scenes 1-5 was run through Emma's engine on 7 Oct with the demo data (even without the language model) and completes as written. Live, Gemini makes her more flexible, so natural variations are fine.

**Speaking tips (from the 6 Oct recordings):** say your request in one breath after "Hi" ("Hi, I'd like to…"); say phone numbers in two groups ("98450, 22222"); say dates as "Monday the 12th" rather than "the twelfth"; if Emma reads back something wrong, just correct her ("No, Monday").

---

## 1. Inbound booking (2 min)

Talk page, click the circle.

| You | What to point out |
|---|---|
| "Hi, I'd like to get my teeth cleaned on Monday afternoon." | She takes the service and the day together; no menu |
| While she asks your name: "Sorry, where is your Jayanagar branch?" | She answers from verified facts, then comes back to her question in new words |
| "Neha Kapoor." / "98450, 22222." / "Yes." | Name confirmed implicitly; number read back in groups |
| (She asks which branch) "Indiranagar." | Only branches that offer cleanings are listed |
| Talk over her once while she offers times; then "12:30, please." | She stops at once (barge-in), then takes your pick |
| At the summary: "Yes, book it." | Only a clear yes to a summary she finished saying books it |

Show: the dashboard Live tab (captions, goal, per-turn timings), then the Calendar event appearing within a few seconds.

## 2. Family booking (1.5 min)

"Hi, I'd like to book a check-up for my daughter, she's seven. A lady doctor if possible, at Jayanagar." Your name, then a new number (98450, 33333), "Yes". When she asks your daughter's name: "Her name is Diya." Then "Tomorrow morning", "The first one", "Yes, book it." She books Dr Kulkarni (a lady doctor at Jayanagar), with Diya stored as the patient and you as the caller.

## 3. Reschedule with verification (2 min)

"I need to move my appointment." Number: **98450 12345**, name **Anita Desai**.
1. When she asks the date, say the **wrong** one first: "It's on Tuesday." She can't find it and reveals nothing (no doctor, no time).
2. "Sorry, it's Monday the 12th." Verified: she reads it back.
3. "Can we make it Wednesday at 10?" She checks it's free, reads back the move ("Monday the 12th at 10 moving to Wednesday the 14th at 10"), "Yes."

Show: the appointment moved in the dashboard and in Calendar (same event, new time).

## 4. Cancel (1 min)

"I want to cancel my appointment, I'm travelling that week." (the reason, given up front, is saved with the cancellation) **98450, 67890**, "Yes", **Kiran Rao**, "Saturday." She reads it back with "no charge to cancel"; "Yes, cancel it." She offers another time; "No thanks."

Show: the Calendar event disappears.

## 5. Emergency (1 min)

"Hi, I have really bad swelling and pain since last night." Name and a new number (98450, 44444), "Yes". Branch: "Whichever is earliest." She offers the earliest slots today (within the hour); pick the first ("10:30, please" or whatever she offers), "Yes, book it."

Show: the urgent task popping up on the dashboard (toast + Tasks tab, at the top).

*(Optional, 15 s: "Wait, am I talking to a real person?" She answers truthfully in one line and carries on.)*

## 6. Doctor unavailable: recovery calls (3 min)

Dashboard, **Recovery** tab.
1. Block **Dr Rao**, **Fri 9 Oct 09:00-17:00**, reason **Illness** → "Block and preview". Priya and Rahul are listed (random DEMO bookings that day may appear too: untick everyone except Priya and Rahul).
2. **Start recovery calls (2)** → confirm. Point out: no call happens while another is on, only inside calling hours, never to do-not-call numbers.
3. Switch to the **/patient** tab: "Incoming call, Pearl Dental, to Priya". **Answer**.

| Priya (you) | What to point out |
|---|---|
| (Emma: "Hi, is this Priya?…") "Yes, speaking." | Nothing about the appointment before identity is confirmed |
| (Emma explains Dr Rao can't be in; never why) "Another doctor is fine." | Preference first, then only valid slots |
| "Yes." / "Yes." | Recap, then an atomic move; Calendar follows |

4. The patient tab rings again for **Rahul**: **Decline**.

Show: Recovery tab results (Moved / Declined the call), the new **Recovery call failed** task, Rahul's appointment as **NEEDS RESCHEDULE** in the dashboard and in Calendar.

## 7. Resilience (optional, 1 min)

Say what the drills proved rather than breaking things live: with Gemini off, 200 simulated calls still completed 97% of bookings with zero safety failures; killing the server mid-booking leaves nothing half-written; if ElevenLabs fails, a local voice takes over. If you want one live moment: turn Wi-Fi off for 20 seconds after a booking, show the System tab's Calendar outbox retrying, turn it back on.

## 8. Wrap-up (1 min)

Live tab turn timings, then System: the audit log (every staff action and export), then Appointments → **Export CSV**.

---

## If something goes wrong

| Problem | Do this |
|---|---|
| Emma doesn't hear you | Check the headset is the Chrome mic (address bar icon); reload the talk page and call again |
| The microphone won't work at all | Click **Type instead** under the error (or open http://localhost:8000/?typed=1): type the caller's lines and press Enter; Emma still answers out loud. On the patient page, Answer then **Type instead** |
| She misheard a detail | Correct her the way you would a person ("No, Monday"). That's part of the demo |
| Calls fail to start ("busy") | A previous call is still open: close the other tab, wait 5 seconds |
| The voice changes to a different one | ElevenLabs failed; Piper took over. Carry on |
| Recovery doesn't ring | Recovery tab: "Paused: outside the calling window" means after 20:00; the patient tab must be open and logged in |
| Anything else | Typed backup as above; in a normal call the browser console also takes `emma.say("I'd like to book a cleaning")` |
| Total failure | Play the screen recording of a full rehearsal (make one on 7 Oct) |

After every rehearsal: run `tools/demo_reset.py` again (Emma stopped) so the fixtures are fresh.
