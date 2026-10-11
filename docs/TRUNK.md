# A real phone number (SIP trunk)

Today Emma answers calls through Asterisk in WSL with two lab softphones ([TELEPHONY.md](TELEPHONY.md)). For a clinic, patients must reach her on a normal number, and recovery calls must ring each patient's own phone. Both need a **SIP trunk**: a phone line delivered over the internet to Asterisk.

## What to buy

| Option | Notes |
|---|---|
| **Exotel** (India) | Indian virtual numbers, SIP trunking, KYC needed; popular with Indian businesses |
| **Twilio Elastic SIP Trunking** | Indian numbers need regulatory documents; well documented |
| **Plivo / Knowlarity / Ozonetel** | Similar Indian offerings |

You need: one inbound number (DID) for the clinic, outbound calling to Indian mobiles, and the trunk's SIP details (host, username/password or IP authentication, the number format it expects). The clinic's existing number can simply **forward** to the new one when busy or unanswered, so nothing changes for patients.

Indian rules: calls to customers must come from a number registered to the clinic (KYC), and recovery calls must respect the patient's calling hours (Emma already does) and the do-not-call list (also done).

## Setting it up

1. **Asterisk**: add the trunk to `telephony/asterisk/pjsip.conf` as an endpoint + registration (or IP auth) named, say, `exotel`, and route its inbound calls to the `emma-inbound` context, the same one the softphone uses. `telephony_setup.py` renders the config; add the trunk's values to `.env` as `TRUNK_*` and to the template.
2. **Emma**: set how to dial a patient over the trunk:
   ```
   TELEPHONY_TRUNK_DIAL=PJSIP/{zero}@exotel
   ```
   `{zero}` is the number with a leading 0 (09845012345), `{national}` the 10 digits, `{e164}` with +91. Use the format your provider asks for. With this empty, recovery calls ring the lab softphone (`TELEPHONY_PATIENT_PHONE`).
3. **Caller ID**: set `TELEPHONY_CLINIC_CID` to the clinic's registered number so patients see who is calling.
4. **Front desk transfer**: `TELEPHONY_FRONT_DESK` can be a desk phone on the trunk (`PJSIP/<number>@exotel`).
5. **Firewall**: open SIP (5060 or the provider's port) and the RTP range only to the provider's IP addresses.
6. **Test**: call the number from a mobile (Emma answers), then block a doctor on the dashboard and start recovery calls for a test appointment booked on your own mobile (your phone rings).

## Running more than one call

Emma takes up to `MAX_CONCURRENT_CALLS` (3) at once; make sure the trunk allows as many channels, and that the Deepgram and ElevenLabs plans allow that many simultaneous streams.
