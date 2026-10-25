# Demo video: 90 seconds for the resume

A short screen recording for recruiters who won't sign in. It also keeps working after the AWS credit runs out. It shows one real booking call on the hosted site and the dashboard that follows it. The lines below come from [DEMO_SCRIPT.md](DEMO_SCRIPT.md) scene 1, which has been run through Emma's engine.

## Before recording (5 minutes)

1. **Where:** https://pearl-emma.duckdns.org, signed in as **`demo`** (password in `secrets/demo_login.txt`).
   - The demo login masks every phone number to its last four digits, so nothing private shows on screen.
   - It can make calls; it can't change anything.
2. **Voice:** check the ElevenLabs characters on elevenlabs.io (one call uses about 30-75). If the voice switches mid-call to a flatter one, that's Piper, the backup: stop and record later.
3. **Screen:** Chrome, one window, 1920×1080, zoom 100%, bookmarks bar hidden, notifications off (Windows: Focus on).
4. **Tabs, left to right:**
   1. the talk page `/`;
   2. `/dashboard`, Live call tab;
   3. `/dashboard`, Calendar.
5. **Audio:** a headset. Record **system audio and the microphone** so both sides of the call are heard.
   - Windows Game Bar: `Win+G`, Capture settings, then "Record audio: All".
   - Or OBS: "Desktop Audio" plus "Mic/Aux".
6. **Practice once** without recording. The call books a real (demo) slot: cancel it afterwards on the dashboard if you want the slot free.

## The script

| Time | On screen | You say / do | Caption to add (optional) |
|---|---|---|---|
| 0:00-0:05 | Talk page, idle orb | (nothing) | "Emma: the voice receptionist for a dental clinic" |
| 0:05-0:10 | Click **Call Emma** | Emma greets you | "A real call: speech in, speech out" |
| 0:10-0:20 | The orb listens | "Hi, I'd like to get my teeth cleaned on Monday afternoon." | "She takes the service and the day in one go" |
| 0:20-0:30 | | While she asks your name: "Sorry, where is your Jayanagar branch?" | "Questions mid-booking: answered from verified facts, then back on track" |
| 0:30-0:45 | | Your name, then a number in two groups ("98450, 22222"), then "Yes." | "Number read back in groups, the Indian way" |
| 0:45-0:55 | | "Indiranagar." Then talk over her once while she offers times, and say "12:30, please." | "She stops when you interrupt" |
| 0:55-1:05 | | At the summary: "Yes, book it." | "Nothing is booked without a clear yes to a summary she finished" |
| 1:05-1:15 | Switch to **Live call** | Scroll the turns | "Every reply's timing, live" |
| 1:15-1:25 | Switch to **Calendar**, Monday | Point at the new booking | "The model talks; Python decides every booking" |
| 1:25-1:30 | Back to the talk page | (end) | "pearl-emma.duckdns.org · github.com/Adharsh818/Ai-voice-agent" |

**Keep it honest:** don't cut out a pause. If a reply is slow, re-record rather than speed it up, so the video shows what callers really hear.

## After recording

- **Trim** the start and end only (Windows Clipchamp or the Photos app). Add the captions above as plain white text on the lower third, at most one at a time.
- **Export** at 1080p MP4, then upload to YouTube as **Unlisted**, and put the link on the resume and in the README's first lines.
- **Optional, 10 more seconds:** the patient phone page (`/patient`) ringing with the iPhone-style call screen when a doctor is blocked (DEMO_SCRIPT scene 6). It needs a recovery booking on the server, so leave it out unless one exists.
