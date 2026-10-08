# Resume here (written 8 Oct 2026, late night)

When the owner says **"continue your work"**, start from this file. The full plan is [FINISH_PLAN.md](FINISH_PLAN.md); project history is in [HANDOFF.md](HANDOFF.md).

## Where everything is

- **Code:** branch `day2-r2-engine` = `main` on GitHub up to 2bf949a; later commits are local (see "Not pushed" below). The working copy Emma runs from is the main checkout **`A:\Voice-Agent`** (it has `.env`, `data/`, `logs/`, `captures/`). Sessions may work in a git worktree under `A:\Voice-Agent\.claude\worktrees\...`; fast-forward the main checkout with `git merge --ff-only <worktree branch>` before running Emma.
- **PRs #1-#5:** all merged (8 Oct): `main` was fast-forwarded to `day2-r2-engine`; #5 merged into its base.
- **Tests:** `.\.venv\Scripts\python.exe -m unittest discover -s tests` (992, all pass; `test_dashboard ... test_lists_filter_and_search` is flaky under load: real clock).
- **Scenarios / sims:** `.\.venv\Scripts\python.exe -m harness run scenarios --engine r2 --offline`; sims `... run sim --engine r2 --offline|--nlu-down --n 200 --seed 7|11 --concurrency 2`.

## Not pushed yet (local only, push first)

- `a4aa6d0` Phase 6: common Indian first names as Deepgram keyterms.
- `7c3e71b` Phase 6: the duplicate question only for the same service.
- This file.

Push: in `A:\Voice-Agent`: `git push origin day2-r2-engine` and `git push origin day2-r2-engine:main` (owner approved push + merge into main on 8 Oct).

## What was done on 8 Oct (after the demo, which included a real phone call)

- **Phase 0 (done):** all 22 demo-day calls replayed through the engine; ~20 conversation bugs fixed with tests in `tests/test_call_fixes_8oct.py` (31 tests): phantom name changes, the duplicate loop, digits taken from dates, "I only know the name", "can you hear me?", a time said right after booking now moves that booking, AM/PM from context, requested times that can't be had are said, "change that one" no longer ends the call, "Nothing." no longer ends the call, mumbling, cancel-flow wording, insistent cancel/move callers (one more look, then the callback, then a kind goodbye). Validator: no claimed changes, no invented policies (V9), no "speech-to-text", no "Mr <name>".
- **Phase 1 (done):** pushed; PRs merged into `main`.
- **Phase 2 (done):** `tools/phone_up.ps1` (keeps Ubuntu running, re-binds SIP on a new Wi-Fi address, checks Asterisk, prints firewall commands, lists phones); Asterisk accepts SRTP or plain audio.
- **Phase 5 (partly):** TELEPHONY.md ("Every time: phone_up", what broke on 8 Oct), VIVA_NOTES (Asterisk/SIP/RTP/UDP, demo lessons), HANDOFF START HERE, FINISH_PLAN phases 0 and 2 marked done.
- **Phase 6 (code done, one check open):**
  - Duplicate question only when the same service is already booked (owner chose this). Seed-7 faked-model sim afterwards: T5 = 9.0 (target met), M3 0%, M7 97.9%, M10 1%, all Z checks 0.
  - 27 common Indian first names added as Deepgram keyterms after the clinic words (`config.DEEPGRAM_NAME_KEYTERMS`, cap 90; Deepgram allows 100). **Not yet measured.**

## Work left, in order

1. **Push** the local commits (above).
2. **Re-run the four 200-call sims and the 53 scenarios** after the Phase 6 duplicate change (only seed 7 faked-model was re-run). Targets: Z1-Z7 = 0, M3 < 1%, M7 >= 95%, M10 < 2%, T5 <= 9.
3. **Name test (Phase 6, paid, owner said yes):** replay the two recordings through Deepgram with the new name keyterms and compare with 6 Oct (17% / 18% word error; "Adharsh" heard as "Adesh"):
   `.\.venv\Scripts\python.exe tools\replay.py --show captures\20261006-092434-fee33507.wav captures\20261006-092647-e2a713c4.wav` (in `A:\Voice-Agent`). About 2 minutes of audio. The owner interrupted this run on 8 Oct, so **confirm with the owner before running it**. If the names don't help (or hurt), set `DEEPGRAM_NAME_KEYTERMS=` empty in `.env` / the default.
4. **Phase 4, deploy check in WSL (owner chose: safe test, then switch off):** run `deploy/install.sh` from a copy of the repo **without `.env` or `secrets/`** (e.g. `git archive` into Ubuntu) so it uses `.env.example`: no real API keys, no Google Calendar (a second Emma must never sync the real calendars). Check `systemctl status emma`, the backup timer and a backup `--check`, Caddy HTTPS on localhost, survival of a distro restart. Then `systemctl disable --now emma caddy emma-backup.timer` so it never takes port 8000 from Windows Emma. Downloads ~300-500 MB: check the owner isn't on a metered hotspot. Results into DEPLOY.md.
5. **Phase 5, rest:** regenerate EVALUATION.md (`tools\evaluate.py --fresh --crash`, plus `--stt` if the name test ran), add the 8 Oct phone calls; DEMO_SCRIPT note that the demo happened.
6. **Phase 3, with the owner holding the phone (next session, ~20 min):** run `tools\phone_up.ps1` first (and the firewall commands it prints, in an admin window). Then: interrupt Emma mid-sentence; key a number with `#` (do keypad frames reach Emma?); "I want to talk to a person" twice → MicroSIP 1002 rings; block Dr Rao on the dashboard → recovery call to the phone, answer once, reject once (which reason code does Reject send? adjust `ami.REASON_DECLINED`); does Asterisk send 16 kHz audio? Also watch for calls that end at the greeting with nothing heard (5 on 8 Oct). Results into TELEPHONY.md.
7. **Phase 7:** full suite, scenarios, 4 sims, crash drill, preflight READY, one browser and one phone call; push; final HANDOFF "state at the end".

## Facts a new session needs

- **Phone line:** Asterisk 20.6 in WSL Ubuntu-24.04. Linphone on the owner's phone = 1001 (Media encryption None or SRTP, transport UDP, register URI `sip:<PC Wi-Fi IP>;transport=udp`); MicroSIP on the PC = 1002 at `127.0.0.1`, source port 5070. SIP passwords are in `A:\Voice-Agent\.env` (`TELEPHONY_SIP_PASSWORD_1001/1002`), never commit them.
- **The PC's Wi-Fi address changes** with each hotspot (10.49.155.x, 10.69.54.x, 10.221.82.x on 8 Oct). `phone_up.ps1` handles it; the firewall rules (`Emma SIP (Wi-Fi only)` and Hyper-V `EmmaSIP`) need the owner's admin window.
- **WSL stops Ubuntu** a minute after its last window closes. `phone_up.ps1` starts a keep-alive via WMI. From Git Bash, run root commands as `MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/<script>.sh` (inline `bash -c` quoting from Git Bash is fragile).
- **Gemini** was slow on 8 Oct (~2.1 s against `NLU_HEAD_DEADLINE_S` 2.2 s), so many replies used the fallback path; `tools\preflight.py` suggests a value. The owner hasn't changed it.
- **Campaign 3** had recovery retries queued for the evening of 8 Oct; check the dashboard's Recovery tab and stop old campaigns before testing.
- Editing with Python heredocs mangles `\n` and backslashes: use the Edit tool for code with escapes.
- The owner prefers decisions as multiple-choice questions with a recommended option, and plain step-by-step instructions for anything they do by hand.
- VoiceLink (a real Indian number) is **dropped**.
