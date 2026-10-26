# From project to product: what's left, and what a real clinic needs (26 Oct 2026)

This builds on [REAL_CLINIC_GAPS.md](REAL_CLINIC_GAPS.md), the 11 Oct audit; most of its P0 and P1 items are done. Part A finishes this project. Part B is what it takes to sell Emma to dental clinics in India and the US.

Legal points below are a checklist to take to a lawyer, not legal advice.

## Part A: finish this project

Done by Claude on 26 Oct, on `main`:
- **Pushed:** 17763c0.
- **On the website:** 9922d67.
- **Checks:** 1122 tests, 53/53 scenarios both ways, sims seed 7 and 11 clean, and 52/53 live before the callback fix.

| # | Item | Who | How |
|---|---|---|---|
| A1 | **Done.** Deploy 17763c0 (the "still there?" change) to the website | Owner's OK, then Claude | `bash tools/deploy_remote.sh` |
| A2 | **Done.** Load the 500 ms jitter buffer into Asterisk | Owner | `wsl -u root bash /mnt/a/Voice-Agent/telephony/install_asterisk.sh` (the configs are already rendered), then one phone call |
| A3 | **Done.** A call on the website, then the listening report | Owner, then Claude | A browser call on the site, then `bash tools/call_report_remote.sh` |
| A4 | **Done.** Microphone check on the talk page; Reject on a recovery call after 9 am | Owner | By hand, in the dashboard |
| A5 | **Done (27 Oct).** Demo video, `demo_video/` (render with HyperFrames; the MP4 is not in git) | Owner | [demo_video/STORYLINES.md](../demo_video/STORYLINES.md) |
| A6 | **Done.** One-click deploy from GitHub | Owner | Add the `EMMA_SSH_KEY` and `EMMA_KNOWN_HOSTS` secrets ([DEPLOY.md](DEPLOY.md)) |
| A7 | **Done (27 Oct).** The owner signed off the prices, the clinic facts (hours, branches, doctors, languages) and the payment and visit policies. Branch phone numbers are never read out: Emma says so and arranges a call back | Owner | `owner_sign_off` in `clinic_facts.json` |
| A9 | **Done (27 Oct).** With the model down, "call me back about my bill" wasn't understood by the rules, so Emma repeated "what can I do for you?". Now the rules catch a callback request (new act `callback_request`), and Emma takes the number and makes the task | Claude | `tests/test_callback_request.py` |
| A8 | Watch the free quotas | Owner | Gemini: daily, resets about 12:30 IST. ElevenLabs: 10,000 characters a month, shared by the PC and the site |

## Part B: a product for real clinics

### B1. Must have before the first paying clinic (both countries)

1. **A real phone number for each clinic** (REAL_CLINIC_GAPS T1, [TRUNK.md](TRUNK.md)).
   - The clinic keeps its number and forwards to Emma: on no answer, when busy, after hours, or always.
   - Emma hands calls back to the front desk.
   - India: a SIP trunk or virtual number (Exotel, Plivo, Twilio, Knowlarity).
   - US: Twilio or Telnyx.
   - A real trunk also fixes recovery calls ringing one fixed phone (C2).
2. **Paid services with a contract and an uptime commitment, no free tiers.**
   - The brain: Gemini on Vertex AI, or another model, under a business agreement.
   - Speech-to-text: Deepgram, paid.
   - The voice: an ElevenLabs paid plan.
   - Free quotas ran out three times this month; a clinic can't have that.
3. **Server hosting.**
   - Two servers, or one with a warm standby.
   - Off-machine backups.
   - Monitoring that pages a person.
   - A staging copy for tests: no more testing on the live site.
4. **Setup for a new clinic** without Claude: a wizard for the name, branches, hours, doctors, services, prices, holidays and the voice. Most of it is already in the dashboard's System tab (S3); the rest still sits in `.env` and JSON.
5. **One deployment per clinic, scripted** (P2 multi-tenancy): `deploy/install.sh` with a clinic folder. Shared multi-clinic hosting can come later.
6. **Call recording with consent,** so problems can be checked. The 26 Oct "two voices" hunt needed a one-off recording. India: under the DPDP notice. US: see B3.
7. **Quality gate on real calls:**
   - a growing set of recorded, consented calls (Indian and US accents, noisy lines, speakerphone);
   - scored with the harness before every release, alongside the scripted scenarios and sims.

### B2. India

- **Data protection, DPDP Act 2023 and its Rules.** Already in place (S1): notice, retention, erase-by-phone, breach runbook. Still to do:
  - a signed processor agreement with each vendor;
  - a named grievance contact;
  - consent wording reviewed by a lawyer;
  - a decision on keeping data in India (AWS Mumbai region).
- **WhatsApp and SMS.**
  - WhatsApp Business API through Meta or a provider (Gupshup, Interakt, Twilio), with approved templates.
  - SMS needs DLT registration of the sender ID and templates (TRAI).
  - `notify.py` is ready but only logs today ([NOTIFICATIONS.md](NOTIFICATIONS.md)).
- **Outbound calls** (reminders, recovery) must follow TRAI's commercial-call rules: transactional calls to a clinic's own patients, at allowed hours.
- **Languages:**
  - Hindi and Kannada first, then Tamil, Telugu and Marathi, by city.
  - Hinglish (code-mixed): out of scope for this project by the owner's decision, but most Indian callers will expect it.
  - Today non-English callers get a callback and a task (F2).
- **Clinic software** to sync appointments with: Practo Ray, Dentee, Clinicea, Oral Studio and similar, besides Google Calendar (done).
- **Payments:** UPI links for deposits or no-show fees (Razorpay, Cashfree).
- **Pricing:** a monthly price per clinic in rupees, low enough to replace part of a receptionist's salary. Shared-number plans for small single-doctor clinics.

### B3. United States

- **HIPAA:**
  - A Business Associate Agreement with every vendor that touches patient data: hosting (AWS), speech-to-text, the voice, the brain, SMS.
  - Free tiers and consumer API keys can't be used.
  - Also needed:
    - access logs (partly done: audit events);
    - encryption at rest;
    - a risk assessment;
    - breach notification;
    - minimum-necessary data in prompts and logs.
- **Recording and AI disclosure:**
  - Some states require every party's consent to record a call (California, Florida, Washington and others), so say it at the start.
  - Check state rules on telling callers they're talking to AI. Emma's policy today: never volunteer it, never deny it when sincerely asked. A US version may need a short opening line.
  - The FCC treats AI-generated voices in robocalls as artificial voices under the TCPA.
- **Outbound calls and texts (TCPA):** consent records, opt-out ("STOP") handling, quiet hours, and A2P 10DLC registration for SMS.
- **Insurance** is the most common US front-desk question:
  - "Do you take Delta Dental / MetLife / Cigna?";
  - eligibility checks (through clearinghouse APIs);
  - deductible and coverage questions handed to staff.
- **Clinic software:**
  - Dentrix, Eaglesoft and Open Dental (on the clinic's own computer);
  - Denticon and Curve (cloud);
  - often through integration companies (NexHealth, Sikka, Kolla) rather than one by one.
- **Language:** Spanish next to English.
- **Selling to US clinics and their groups (DSOs):** SOC 2 Type II, a penetration test, single sign-on for staff, uptime commitments, support hours.

### B4. Product features that sell (both countries)

- **Fill the diary:**
  - confirmations and reminders (fewer no-shows);
  - a waitlist that fills cancellations automatically;
  - recall campaigns ("your 6-month cleaning is due");
  - win-back of patients who didn't book.
- **New-patient intake:** a link by WhatsApp or SMS for history, ID and insurance before the visit.
- **Front-desk tools:**
  - live call view and takeover (exists);
  - transcripts, call summaries and tasks (exist);
  - a callback list with priority;
  - a weekly report: calls answered, bookings made, after-hours calls caught, estimated revenue.
- **Emergencies:** the on-call dentist paged for red flags after hours (today: first aid, an urgent slot and a task).
- **Brand:** the clinic picks the name, the voice and the greeting; the clinic name is already in `.env`.
- **Analytics:** where calls drop, which questions come up, which answers fail, to feed the knowledge base.

### B5. Engineering for many clinics

1. **Configuration:** everything clinic-specific out of the code; prompts and keyterms are partly hard-coded.
2. **Cost control:** today's metering is per call. Next: alerts per clinic and a cap per day.
3. **Speed:**
   - aim for under 1 s from the caller stopping to Emma's first sound;
   - more replies by rules (today about 90% of scenario turns);
   - more stored sentences;
   - a faster paid model as the brain.
4. **Phone audio:**
   - Opus with error correction (done 26 Oct);
   - jitter tuning (A2);
   - use the trunk's region (India or US) to keep the line short.
5. **Keeping it healthy:**
   - release notes;
   - rollback;
   - per-clinic feature switches;
   - logs and alerts that a support person can read.

## Suggested order

1. Part A (done 27 Oct; what's still left is in [WHATS_LEFT.md](WHATS_LEFT.md)).
2. Paid services: B1.2.
3. A real number at one friendly clinic in Bengaluru (B1.1, B2).
4. Four weeks of consented recorded calls, fixes from them, and the weekly report.
5. Setup wizard and scripted deployments (B1.4, B1.5).
6. WhatsApp reminders and recall (B2, B4).
7. Hindi and Kannada.
8. For the US: HIPAA agreements, consent and disclosure, insurance questions, one practice-software integration, then a US pilot clinic.
