# Production phone-call path

The browser WebSocket app is the supported development test harness. The
existing `asterisk_agi.py` script is intentionally not a real-time production
path: it records a full file per caller turn before transcription.

To connect a real clinic number, deploy this architecture on Linux:

```text
SIP trunk / clinic number
          |
       Asterisk
          |
  ARI + External Media (bidirectional 8 kHz or 16 kHz PCM)
          |
  Python media bridge -> streaming STT -> state machine -> streaming TTS
```

Implementation requirements before enabling callers:

1. Configure an Asterisk SIP endpoint/trunk and a dialplan that routes the
   clinic number to an ARI application.
2. Replace file-based AGI recording with an ARI External Media bridge. Feed
   20 ms PCM frames directly to the selected streaming STT provider.
3. Stream generated TTS frames back through the media bridge, with a
   per-call playback queue and barge-in cancellation.
4. Run the Python service and Asterisk on a secured Linux VM, exposing only
   the SIP provider's required ports. Do not expose the development WebSocket
   publicly without TLS, authentication, and rate limits.
5. Set `USE_MOCK_APIS=False`, configure Google credentials and a dedicated
   clinic calendar, then verify a staging call creates exactly one event.

The browser flow can be started locally after creating a virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

Run regression checks with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
