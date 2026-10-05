# Phone calls: Asterisk + AudioSocket

**Status:** planned for after the 8 October 2026 demo (Milestone D in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), section 13). The browser talk page is the supported path until then. This replaces the earlier `PRODUCTION_CALL_PATH.md`, which described ARI External Media and the old file-based AGI script (both removed).

## Architecture

```text
SIP softphone (MicroSIP / Zoiper) or SIP trunk
        |
   Asterisk 22 LTS (WSL2 Ubuntu for development, Linux VM for the demo)
        |  dialplan: pre-register caller ID, then AudioSocket(<uuid>, 127.0.0.1:<port>)
        v
   audiosocket_server.py  — same Transport interface as the browser WebSocket
        |
   CallSession  ->  STT  ->  dialogue engine  ->  TTS  ->  playout queue
```

`CallSession` does not know how audio arrives. The browser and AudioSocket implement the same transport methods: `send_audio`, `send_event`, `flush` and `close`.

## AudioSocket framing

TCP, one message per frame: `type (1 byte) + length (2 bytes, big-endian) + payload`.

| Type | Meaning |
|---|---|
| `0x00` | Hang-up / terminate |
| `0x01` | Call UUID (16 bytes), sent first |
| `0x10` | Audio: signed-linear 16-bit mono, 8 kHz; 20 ms = 320 bytes |
| `0xff` | Error |

Check during implementation whether this Asterisk build also supports higher-rate audio types and DTMF frames. Fall back to 8 kHz if unsure.

## Requirements

- **Pacing.** Asterisk plays frames as they arrive, so the server-side playout queue must send 20 ms frames in real time. It keeps at most ~60 ms queued so a barge-in flush is near-instant.
- **Playback events.** There are no browser `started`/`ended` reports on the phone. The playout clock supplies `played_ms`, the recap-heard rule and end-of-call timing.
- **Sample rate.**
  - Deepgram runs with `sample_rate=8000`.
  - ElevenLabs uses 8 kHz output if the account supports it; otherwise Emma resamples 16 → 8 kHz with a stateful resampler.
  - Prompt caches are kept separately per output format.
- **Caller ID.** AudioSocket only carries the UUID. The dialplan registers `CALLERID(num)` against the UUID through a local HTTP call before `AudioSocket()`, so Emma can create callback tasks and offer "Is the number you're calling from the best one?"
- **DTMF.** Phone numbers can also be keyed in.
- **Transfers and outbound.**
  - A live transfer to the escalation extension replaces "staff will call you back".
  - Outbound recovery calls are placed to the softphone extension with Originate.
- **Security.** Only the SIP and RTP ports the provider needs are exposed. The AudioSocket port listens on loopback only.
