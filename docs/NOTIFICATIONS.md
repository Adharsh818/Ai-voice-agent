# Messages to patients (WhatsApp / SMS)

Emma queues a short message every time an appointment is **booked, moved or cancelled** (by Emma or on the dashboard), and a **reminder** the day before (`REMINDER_HOURS_BEFORE`, 24). The message is written in the same database transaction as the change, so it can never describe a booking that didn't happen. A worker sends what is due every 15 seconds.

Today it runs with the **log-only sender**: nothing leaves the machine; each message is written to Emma's log (phone masked) and listed on the dashboard (System → *Messages to patients*) as `logged`. Everything else (queueing, reminders kept in step with changes, retries) already works, so switching on a real provider is only settings.

| Rule | Why |
|---|---|
| A number on the do-not-call list gets nothing | The patient asked not to be contacted |
| The demo seed's bookings get nothing | They aren't real patients |
| Moving or cancelling drops the pending reminder | No reminder for a time that no longer exists |
| No reminder for a visit less than a day away | The confirmation already covers it |
| A failed send is retried 5 times (2, 4, 8, 16 minutes), then marked `failed` | Shown on the dashboard for staff |

The wording is in `notify.TEMPLATES` (short, fixed text with a few variables, which is what template approval needs).

## Switching on WhatsApp through Twilio

1. Create a Twilio account and, under *Messaging → Try it out → WhatsApp*, join the **WhatsApp sandbox** from your own phone (it gives you a sandbox number and a join code).
2. Add to `.env`:
   ```
   NOTIFY_SENDER=twilio
   NOTIFY_CHANNEL=whatsapp
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_FROM=+14155238886
   ```
   (`TWILIO_FROM` is the sandbox number at first; your own WhatsApp sender later.)
3. Restart Emma. The dashboard's Messages card says *Sending (whatsapp)*. Book a test appointment for your own number: the confirmation arrives on WhatsApp.
4. **For real patients:** register the clinic's WhatsApp Business sender with Meta through Twilio, and submit the four templates in `notify.TEMPLATES` for approval (business-initiated messages outside a 24-hour reply window must use an approved template). Until approved, only numbers that joined the sandbox receive messages.

## SMS in India

1. Commercial SMS in India must be registered on a **DLT platform** (TRAI rules) through any operator's portal (Jio, Airtel, Vodafone Idea or BSNL): the clinic as the principal entity, a 6-character **sender ID** (header, e.g. `PEARLD`), and each **template** with its variables. Unregistered messages are blocked by the operators.
2. Use an SMS provider that passes the DLT IDs (Twilio supports Indian DLT; local providers such as MSG91, Exotel and Gupshup do too).
3. With Twilio: set `NOTIFY_CHANNEL=sms` and `TWILIO_FROM` to the registered sender. Another provider needs a small sender class in `notify.py` next to `TwilioSender` (one `send(channel, to, body)` method).

## Costs and privacy

- Check the provider's current price per message before switching on; a reminder plus a confirmation is two messages per booking.
- Messages contain the first name, service, branch, doctor and time, never the phone number in the text or anything clinical beyond the service name.
- Under India's DPDP Act, appointment confirmations and reminders are part of the service the patient asked for; keep the do-not-call list honoured (it is) and mention messages in the clinic's privacy notice.
