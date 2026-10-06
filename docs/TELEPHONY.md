# Phone calls: Asterisk + AudioSocket

**Status (6 Oct 2026):** built and tested against a simulated Asterisk (`tests/test_telephony.py`, and a live server smoke test). The real-softphone run is the last step: it needs WSL2 + Ubuntu on this PC (see *Setup*). The browser talk page is unchanged and stays the demo path; everything here is off unless `TELEPHONY_ENABLED=true`.

## Architecture

```text
Softphone 1001 (caller / patient)        Softphone 1002 (front desk)
        |  SIP + RTP, 127.0.0.1:5060              ^  live transfer
        v                                         |
Asterisk 20 LTS (WSL2 Ubuntu, mirrored networking, loopback only)
   |  dialplan: POST /telephony/register  (caller ID in the body)  -> UUID or "busy"
   |  AudioSocket(<uuid>, 127.0.0.1:9092) ................ the call's audio, 8 kHz
   |  POST /telephony/next  -> "transfer" | "hangup"
   |  AMI 127.0.0.1:5038 <- Originate (recovery calls ring 1001)
   v
Emma (Windows): audiosocket.py  ->  PhoneTransport  <->  CallSession (same as the browser)
```

`CallSession` doesn't know how audio arrives. The browser and the phone both implement `send_audio`, `send_event`, `flush` and `close`.

| Piece | File | What it does |
|---|---|---|
| AudioSocket server | `audiosocket.py` | Loopback TCP listener; reads the UUID frame and hands the connection to `server._phone_call` |
| Phone transport | `audiosocket.PhoneTransport` | Real-time playout clock, playback reports, line sounds, 16 to 8 kHz |
| Caller side | `audiosocket.PhoneCall` | Caller audio 8 to 16 kHz into the session; keypad digits; hang-up |
| Registry | `audiosocket.registry` | UUID to inbound caller ID or outbound ring; where the call goes after Emma |
| Line audio | `phone_audio.py` | Stateful anti-aliased resamplers; the server-side twin of `static/ambience.js` |
| Manager client | `ami.py` | Login, Originate, wait for answered / declined / rang out |
| Server hooks | `server.py` (phone section) | Startup, `/telephony/register`, `/telephony/next`, inbound and recovery phone calls, the recovery dialer |
| Engine | `ai_engine.phone_line`, `dialogue/handlers.py` | Caller ID instead of a read-back; live transfer instead of a callback promise |
| Asterisk configs | `telephony/asterisk/*.conf` | Templates rendered by `tools/telephony_setup.py` into `telephony/build/` (git-ignored: secrets) |

## AudioSocket framing

TCP, one message per frame: `type (1 byte) + length (2 bytes, big-endian) + payload`.

| Type | Meaning |
|---|---|
| `0x00` | Hang-up (either side) |
| `0x01` | Call UUID (16 bytes), sent first by Asterisk |
| `0x03` | Keypad digit (1 ASCII byte) |
| `0x10` | Audio: signed-linear 16-bit mono, 8 kHz; 20 ms = 320 bytes |
| `0x12` | Audio at 16 kHz (newer Asterisk builds); accepted inbound |
| `0xff` | Error |

Emma always sends `0x10` (8 kHz), which every AudioSocket build accepts.

## How the phone side behaves

- **Playout clock.** The browser plays Emma's audio itself and reports back; on the phone, `PhoneTransport` sends one 20 ms frame every 20 ms in real time (measured 49.9 frames/s on the live server). So at most a frame is queued in Asterisk, and a barge-in flush is instant. A turn starts once 60 ms is buffered, and a newer turn replaces what's left of an older one, as in `static/playback-worklet.js`. The clock reports playback `started`, `ended` and `interrupted` with `played_ms`, which drive barge-in, echo, the recap-heard rule and the silence ladder.
- **Clinic sounds.** There is still no background bed. Typing plays when Emma writes something down or checks the diary, and an occasional door, chair or footsteps only while her line is open (`phone_audio.LineSounds`, same rules as `ambience.js`). The owner-approved MP3s are decoded once with ffmpeg into `cache/phone_sounds/`. Without ffmpeg, typing is synthesised and movement is skipped. `AMBIENCE_ENABLED=false` turns both off.
- **Sample rates.** Emma works at 16 kHz everywhere. The caller's 8 kHz audio is upsampled for Deepgram, and her voice and sounds are low-passed and downsampled for the line. The filters keep state between frames, so there are no clicks and no aliasing (`tests/test_telephony.py` checks a 1 kHz tone round trip and a 6 kHz tone being removed).
- **Caller ID.** The dialplan posts `CALLERID(num)` with the registration. If it's a real phone number (an extension like `1001` or "anonymous" is ignored), Emma doesn't ask for digits. She asks "Is the number you're calling from the best one to reach you on?", and a yes confirms it. A no gets "No problem. What's the best number to reach you on?" and isn't counted as a misheard read-back. For a cancel or change, the question is "Is the appointment under the number you're calling from?".
- **Keypad.** Digits become one caller turn after `#` or `DTMF_TIMEOUT_S` (3 s) of quiet; `*` clears them. The first key stops Emma talking.
- **Live transfer.** Asked for a person, Emma still offers to help first. If the caller insists, she writes the callback task first (invariant 7), then says "Sure, I'm putting you through to the front desk now. If they can't pick up, they'll call you back on this number." Her side closes, `/telephony/next` answers `transfer`, and the dialplan dials `TELEPHONY_FRONT_DESK`. If nobody answers, the task still stands. With `TELEPHONY_FRONT_DESK` empty, she makes the usual callback promise instead. Browser calls never transfer.
- **Recovery calls.** When `AMI_USER` is set, a recovery job rings `TELEPHONY_PATIENT_PHONE` (softphone 1001) with Originate, as well as the `/patient` page. Answering on the phone runs the same recovery call; Reject on the phone (486 Busy / 603 Decline) declines it like the page's button; ringing out is "no answer" (retried per `RECOVERY_*`). Whichever answers first wins; the other side is hung up.
- **One call at a time.** Phone calls take the same call gate as the browser. A second caller hears Asterisk's "all circuits are busy" and the call ends.
- **Security.** Everything is on loopback: SIP 5060, RTP 10000-10200, AudioSocket 9092, AMI 5038, and Emma's HTTP. `/telephony/*` answers only loopback clients that send `TELEPHONY_SECRET` in the POST body, so caller numbers never appear in URLs or access logs. The rendered configs hold the secrets and are git-ignored.

## Setup (once)

1. **WSL2 + Ubuntu** (admin PowerShell, then reboot): `wsl --install -d Ubuntu-24.04`. Open Ubuntu once and create the Linux user.
2. **Mirrored networking**, so Asterisk in WSL and Emma on Windows reach each other on `127.0.0.1`: create `%UserProfile%\.wslconfig` containing
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
   then run `wsl --shutdown` (WSL restarts on next use).
3. **Settings and configs** (Windows, in the repo): `.\.venv\Scripts\python.exe tools\telephony_setup.py`. It adds the telephony settings to `.env` (only missing ones, with generated secrets), renders `telephony/build/`, and prints the two softphone passwords.
4. **Asterisk** (Ubuntu): `sudo bash /mnt/a/Voice-Agent/telephony/install_asterisk.sh`. It installs Asterisk 20 LTS from Ubuntu, checks for AudioSocket, CURL and PJSIP, and installs the configs (keeping the originals).
5. **Softphones**: MicroSIP (or Zoiper) on Windows. Account 1001, domain/server `127.0.0.1`, password from step 3, UDP. A second account or app for 1002 (front desk) is only needed to answer transfers.
6. **Restart Emma.** The log shows `AudioSocket listening on 127.0.0.1:9092`.
7. **Call:** from 1001 dial **100**.

## Checks

- `tests/test_telephony.py` (no Asterisk needed): framing, registry, resampling, line sounds, playout and playback reports, flush, keypad, a whole call over TCP against a simulated Asterisk, the dialplan endpoints (loopback, secret, busy), AMI Originate against a simulated manager (answered / declined / rang out / bad login), the recovery dialer, caller ID and transfer in the engine.
- Live smoke test without Asterisk: start Emma with `TELEPHONY_ENABLED=true` and play the Asterisk side from a script (register, connect, listen, hang up). On 6 Oct the greeting reached the line 0.07 s after connect, at 49.9 frames/s, and the call slot was free after the hang-up.
- On the real softphone: the greeting, a booking with the caller-ID question, barge-in, keying a number with `#`, "I want to talk to a person" twice (1002 rings), a recovery call answered and one rejected on 1001.

## Still to check on the real Asterisk

- Whether Ubuntu's Asterisk 20 sends keypad frames (`0x03`) through AudioSocket. If not, keyed numbers need `dtmf_mode=inband` or Asterisk 22.
- Whether it offers 16 kHz AudioSocket audio. Emma accepts `0x12` inbound, but 8 kHz is the safe default.
- The OriginateResponse reason MicroSIP's Reject produces (expected 5 or 8).
