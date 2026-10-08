# Finish plan: everything left after the 8 Oct demo

Written 8 Oct 2026, after the demo (done with a real phone call through Asterisk). It replaces the "still open" parts of [FINAL_PHASES_PLAN.md](FINAL_PHASES_PLAN.md); tick items off in [HANDOFF.md](HANDOFF.md) as they're done. Decisions marked **(owner)** are asked as multiple-choice questions before that step starts.

## Owner decisions (8 Oct)

- **Save work:** push, then merge PRs #1-#5 into `main` (main becomes the finished project).
- **Phone start-up:** a small `tools/phone_up.ps1`.
- **Encryption:** Asterisk accepts both plain and encrypted (SRTP) audio from the phones.
- **Phase 6 gaps:** fewer turns per booking, and better name hearing. Not chosen: listing services, faster simple replies.

## Where things stand

- Code: `day2-r2-engine` at 9a71f99, **2 commits ahead of GitHub** (crash drill and Asterisk fixes; the date fix in the crash-drill tests). PRs #1-#5 are open and stacked; none merged.
- Checks: 961 tests, 53/53 scenarios, preflight READY, the four 200-call sims and the crash drill pass.
- Phone: WSL2 Ubuntu 24.04 + Asterisk 20.6, Linphone 1001 on the owner's phone, MicroSIP 1002 on the PC. Booking calls work end to end (Emma → database → dashboard → Google Calendar).

## Phase 1: save the work (10 min)

1. Push `day2-r2-engine` to GitHub (PR #5).
2. Merge the stack #1-#5 into `main` in order (each PR's CI and conflicts checked first); `main` then holds the finished project.
3. Note the demo in HANDOFF.md: done on 8 Oct with the phone; anything the examiners said goes into Phase 6.

## Phase 2: phone setup that survives a restart (30 min, mostly mine)

Found on demo day; each cost time.

| Problem | Fix |
|---|---|
| WSL switches Ubuntu (and Asterisk) off about a minute after its last window closes | A start-up step that keeps Ubuntu running: **(owner)** either a small `tools/phone_up.ps1` the owner runs (keep-alive + checks), or just "open Ubuntu and minimise it", documented |
| The hotspot gives the PC a new address after a restart | `phone_up.ps1` (if chosen) spots the change, re-runs `telephony_setup.py --lan`, reinstalls the configs, and prints the firewall commands to update (admin, owner runs them) |
| Linphone turned on encrypted audio (SRTP) after a re-login, and calls failed with 488 | **(owner)** either document "Media encryption: None", or let Asterisk accept SRTP too (`media_encryption=sdes` + `res_srtp`), so both work |
| MicroSIP gives up while Asterisk is down | Documented: restart MicroSIP; uses 127.0.0.1 and source port 5070 |
| SIP logging off after each restart | `phone_up.ps1` turns it on, or a `logger.conf` line |

Done when: after a PC restart, following TELEPHONY.md alone gets both phones online and a call answered, without help.

## Phase 3: the remaining live phone checks (20 min, with the owner holding the phone)

From FINAL_PHASES_PLAN Part 1; results go into TELEPHONY.md.

1. **Barge-in:** talk over Emma on the phone; she stops at once.
2. **Keypad:** key a number ending with `#`. This answers open question 1: do keypad frames reach Emma through AudioSocket? If not, set `dtmf_mode` (inband/info) or note the limit.
3. **Transfer:** "I want to talk to a person" twice → the callback task is written, then MicroSIP (1002) rings and can answer.
4. **Recovery call:** block Dr Rao on the dashboard → Emma rings the phone (AMI Originate). Answer once (rebook), reject once. This answers open question 3: the reject reason code (adjust `ami.REASON_DECLINED` if it differs from 5/8).
5. **Open question 2:** does Asterisk send 16 kHz audio to Emma? Read from the AudioSocket frames in Emma's log; keep 8 kHz if not.
6. Each fix gets a test in `tests/test_telephony.py`; full suite after.

## Phase 4: E3 deploy check in WSL (45 min, mine)

The last item of plan section 13 / FINAL_PHASES_PLAN E3. Emma on Windows is stopped while this runs (both use port 8000).

1. `deploy/install.sh` in Ubuntu (copy to `/opt/emma`, venv, the `emma` user, systemd units).
2. `systemctl status emma` is active; `emma-backup.timer` is listed; a backup runs and passes `--check`.
3. Caddy with a local certificate in front; the dashboard opens over HTTPS; WebSockets (the talk page) work through it.
4. Restart the WSL distro: Emma comes back by herself.
5. Results and any fixes into DEPLOY.md; then remove the test install, or keep it **(owner)**.

## Phase 5: docs and numbers (30 min, mine)

- TELEPHONY.md: Phases 2-3 results, keep-alive, SRTP, hotspot address, MicroSIP.
- DEMO_SCRIPT.md: what happened on 8 Oct; the phone scene as run.
- EVALUATION.md: regenerate (`tools/evaluate.py --fresh --crash`); add the phone calls.
- VIVA_NOTES.md: SIP/RTP/UDP/Asterisk answers, the 8 Oct demo, the final numbers (961 tests).
- HANDOFF.md, IMPLEMENTATION_PLAN.md ticks; the slide's "~950" becomes 961.

## Phase 6: known gaps (optional; owner picks which, each is a change to how Emma talks)

| Gap | Today | Possible fix | Risk |
|---|---|---|---|
| Simple replies are slow | ~1.6 s vs 0.9 s target | Tighter end-of-speech wait on short, complete answers | Cutting callers off more often |
| Turns per simple booking | 10 vs 9 (one sim set) | Fold the day and time questions when the caller gives both | Small |
| "Tell me the services" | Asks "check-up or a problem?" | List the services when asked to | Small |
| Names misheard ("Adharsh" → "Adesh") | Read-back catches it | Deepgram keyword boosting for common Indian names | Small; needs a list |
| Examiner feedback from the demo | n/a | As raised | n/a |

Each chosen fix: test first, then the change, then suite + scenarios + sims.

## Phase 7: final verification and hand-in (30 min)

1. Full suite, 53 scenarios, the four 200-call sims, crash drill: all at or above today.
2. Preflight READY; one browser call and one phone call end to end.
3. Push; merge per Phase 1's decision; final HANDOFF "state at the end".

## On hold

- **VoiceLink** (a real Indian phone number): not built until the owner's guide approves (memory: telephony-voicelink-pending).

## Order and time

Phase 1 → 2 → 3 (needs the owner and the phone) → 4 → 5 → 6 (only what's picked) → 7. About 4 hours without Phase 6.
