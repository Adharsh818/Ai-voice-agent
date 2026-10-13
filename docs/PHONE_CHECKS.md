# Phone checks with the owner (Phase 3, about 20 minutes)

What only a real call can show. Claude runs the PC side and reads the logs; the owner holds the phone. Fill in the **Result** column as you go; Claude copies the results into [TELEPHONY.md](TELEPHONY.md).

**Results (12-14 Oct):** 1 greeting and "Hi": pass after fixes (instant hello; a crash at call start fixed first). 2 interrupting: pass. 3 keypad: digits don't reach Emma (Asterisk 20.6); callers say the number. 4 booking by voice: pass ("ten" picks fixed on the way). 8 "am I talking to a bot?": pass. 5 asking for a person: pass after two fixes ("No, a real person please" heard as insisting; the transfer now moves the live call with AMI Redirect because Asterisk 20.6 hangs up when AudioSocket closes); MicroSIP rang and audio flowed both ways. Line quality on a phone hotspot: 10-35% of the caller's audio lost; use a Wi-Fi router. 6 recovery call answered: Emma rang, checked identity, explained Dr Rao was away and offered other times; on the hotspot about half the caller's audio was lost, so no new time was agreed (the booking went to "needs reschedule" with a recovery_failed task, as designed); a closed-day reply fixed on the way. 7 recovery call rejected: Linphone's Reject arrives as reason "0" about 2 s in and was logged "no answer" with a retry; fixed (an early 0/1 is a decline), not yet re-tried live.

Already answered without a call (12 Oct): Asterisk 20.6 sends 8 kHz audio only (its AudioSocket module is fixed to `SLINEAR`), so there is no 16 kHz check to do.

## Before you start (5 minutes)

1. Put the phone and the PC on the same Wi-Fi or hotspot.
2. In a normal PowerShell window in `A:\Voice-Agent`, run:
   ```
   .\tools\phone_up.ps1
   ```
   If it prints firewall commands, open **PowerShell as administrator**, paste them, press Enter, then run `phone_up.ps1` again until every line says OK.
3. Start Emma from `A:\Voice-Agent`: `.\.venv\Scripts\python.exe server.py`. Wait for "Emma is listening".
4. Claude runs `tools\preflight.py`; every line should be OK (it also times Gemini today).
5. On the phone, open **Linphone** (account 1001). It should say *Connected* / registered. If not, check the account's server address is the PC's new Wi-Fi address that `phone_up` printed.
6. On the PC, open **MicroSIP** (account 1002); it should be online. This is the "front desk".
7. Open the dashboard (`http://127.0.0.1:8000/dashboard`), Recovery tab: stop any old campaign that is still running.

Emma's number from the phone is **100**.

## The checks

| # | Do this on the phone | What should happen | Result |
|---|---|---|---|
| 1 | Call **100**. Say nothing for a moment after the greeting, then say "Hi". | Greeting within about 2 s; she answers your "Hi". The call must not end at the greeting (5 calls did on 8 Oct). | |
| 2 | Ask "Do you do root canals?" and, while she is answering, start talking over her: "Sorry, sorry, how much is it?" | She stops within about half a second and answers the price. | |
| 3 | Say "I'd like to book a cleaning". When she asks for your number, **type it on the keypad** and press **#**. | She reads the number back in groups. If she doesn't react to the keys at all, keypad frames don't reach Emma (note it; that's a known Asterisk 20 limit and calls still work by voice). | |
| 4 | Finish the booking by voice, then say "Bye". | Booked; the dashboard's Appointments tab shows it; a calm goodbye and the call ends. | |
| 5 | Call 100 again. Say "I want to talk to a person." When she offers to help, say it again: "No, a real person please." | First time she offers to help; the second time she hands over and **MicroSIP (1002) rings** on the PC. Answer it on the PC: you should hear each other. | |
| 6 | (PC) On the dashboard Recovery tab, block **Dr Rao** for the day of the booking from check 4 and start the campaign. | **The phone rings** with the clinic's caller ID. **Answer**: Emma explains Dr Rao is unavailable and offers new times; pick one. | |
| 7 | (PC) Make a second booking for Dr Rao, block him again, start the campaign. | The phone rings: this time press **Reject / Decline**. The dashboard should show the call as declined (not "no answer"). Claude reads the reason code Asterisk logged and adjusts `ami.REASON_DECLINED` if needed. | |
| 8 | Call 100 and ask "Am I talking to a bot?" | "Good question! I'm Emma, the clinic's virtual receptionist." and she carries on helping. | |

## After

- Claude copies the results into [TELEPHONY.md](TELEPHONY.md) ("Still to check on the real Asterisk") and re-measures phone latency from `logs/turns.jsonl` (how often the watchdog ends the caller's turn, 46% before).
- Then Phase 7: full tests, scenarios, the four sims, crash drill, preflight READY, one browser and one phone call, push, final HANDOFF.
