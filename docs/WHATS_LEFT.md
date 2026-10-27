# What's left (27 Oct 2026)

The project's own finish list (Part A of [PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md)) is done: A1-A7 and the bill-callback fix (A9). The PC, GitHub and the website all run 688173e or later. This page lists what remains: a few small owner steps, then the plan for turning Emma into a product for real clinics.

## 1. Small steps for the owner

| # | What | Why | How |
|---|---|---|---|
| 1 | **Done (27 Oct).** Restart Emma on the PC | She now runs the latest code on the repaired `.venv`, in the "Emma (PC)" terminal tab. | To restart again: `.venv\Scripts\python.exe server.py` in `A:\Voice-Agent`. |
| 2 | Mark the two test tasks done | Tasks 11 and 12 on the PC dashboard came from tests. | Dashboard, Tasks tab. |
| 3 | Delete the old `.env` backups when you're sure | `.env.bak-20261025-brain` and `.env.bak-20261025-nvidia` hold keys and sit untracked in `A:\Voice-Agent`. | Delete them by hand; they are never committed. |
| 4 | Watch the free quotas (A8, ongoing) | Gemini's free daily quota runs out on busy days, and Emma then answers from rules alone. ElevenLabs has 10,000 characters a month, shared by the PC and the site. | `/health` on the site shows the day's model requests and failures. |

**Done on 27 Oct for the PC:** the Python that `A:\Voice-Agent\.venv` was built on had been moved by another tool's update. The environment now points at a Python 3.12 that `uv` manages (`%APPDATA%\uv\python`), and the old config is kept as `.venv\pyvenv.cfg.bak-20261027`. All 1132 tests pass with it.

## 2. Known limits today

- **Free services can go down for a day:** with the model's quota used up, the rules carry the call. Most bookings still work, but unusual questions get "I'm not sure".
- **The phone line is a demo line:** Asterisk in WSL with Linphone on one phone, and recovery calls ring that one phone.
- **English only:** callers in another language are offered a callback and a task.
- **Messages only log:** WhatsApp and SMS reminders are written to the log, not sent.
- **The demo video's MP4 is not in git:** it's in `demo_video/renders/` on the PC. To render it again, see [demo_video/STORYLINES.md](../demo_video/STORYLINES.md).

## 3. Future plans: from project to product

Details and the reasons are in [PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md) Part B. In order:

1. **Paid services under business agreements (B1.2):** the brain (Gemini on Vertex AI or another model), Deepgram and ElevenLabs on paid plans, so the free quotas never stop a call.
2. **A real number at one friendly clinic in Bengaluru (B1.1, B2):** a SIP trunk or virtual number (Exotel, Plivo, Twilio). The clinic forwards calls on no answer, when busy or after hours, and Emma can hand back to the front desk.
3. **Four weeks of consented, recorded calls (B1.6, B1.7):** fix what they show, and start a weekly report (calls answered, bookings, after-hours calls caught).
4. **Hosting for a real clinic (B1.3):** a standby server, off-machine backups, alerts that page a person, and a staging copy, so tests never run on the live site.
5. **Setup without Claude (B1.4, B1.5):** a wizard for a new clinic (branches, hours, doctors, services, prices, holidays, voice), and a scripted install per clinic.
6. **WhatsApp reminders, recall and a waitlist (B2, B4):** fewer no-shows and a fuller diary. This needs WhatsApp Business templates and DLT registration for SMS.
7. **Hindi and Kannada (B2):** then Tamil, Telugu and Marathi by city.
8. **India compliance (B2):** signed processor agreements, a grievance contact, a lawyer's review of the consent wording, and a decision on keeping data in India.
9. **Clinic software sync (B2, B3):** Practo Ray, Dentee or Clinicea in India; Dentrix, Open Dental and others in the US, often through NexHealth or Sikka.
10. **The US (B3):**
    - HIPAA agreements with every vendor;
    - recording consent and AI-disclosure rules by state;
    - TCPA rules for calls and texts;
    - insurance questions ("Do you take Delta Dental?");
    - Spanish;
    - then a US pilot clinic.
11. **Engineering for many clinics (B5):**
    - every clinic-specific setting out of the code;
    - cost alerts and a daily cap per clinic;
    - under 1 second to Emma's first sound;
    - release notes, rollback and feature switches per clinic.

## 4. What only the owner can decide or pay for

- Any paid plan or contract (nothing costs money today, by the owner's rule).
- A real clinic to pilot with, and its phone number.
- Legal review: the DPDP notice and consent wording in India; HIPAA, consent and AI disclosure in the US.
- The price per clinic.
