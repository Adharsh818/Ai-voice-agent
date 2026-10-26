# Storylines (26 Oct 2026)

**Picked: A**, with every dashboard tab, the Live call tab once during the booking, and a narrated tab tour (owner).

## A: The booking builds (picked)
The real call a7e2b542 is re-enacted with Emma's real voice (the phone number is swapped for the demo number). Captions build up as chat bubbles, and a booking card fills in as each detail is heard. Mid-call there's a cut to the Live call tab. "Yeah." at the summary stamps BOOKED. Then a narrated tour: Today, Appointments, Tasks, Calls, Recovery (Preview open), System. The end card: "The model only talks. Python decides every booking."

## B: Missed call, then Emma
Before and after. A busy clinic phone rings out at lunch, then the same call is answered by Emma in highlights with timing tags. 45-60 s.

## C: One sentence, traced (the bold one)
One caller line travels through the system: speech → Deepgram's words → the rules decide → Python checks the slot → Emma's voice. Then it zooms out to the whole call booked. About 60 s.

## How A is made
- `build_scenes.py` writes `index.html` and `compositions/*.html` from `assets/audio/manifest.json`. Re-run it after changing any audio.
- **Audio:**
  - Emma: Sarah, her own voice and settings.
  - Caller: Will.
  - Narrator: Brian.
  - All ElevenLabs, about 1,960 characters.
- **Screens:** captured from a local demo copy (demo patients only, a throwaway password, no phone line), replaying the same call as a typed call. The test copy's own "voice off" alert was hidden, and there was no other editing.
