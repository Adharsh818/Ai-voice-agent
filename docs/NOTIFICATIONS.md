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

## Is it free? (checked 23 Oct 2026; future work, owner's decision)

Not fully. Neither is switched on yet: Emma stays on the log-only sender.

- **WhatsApp:** Meta charges per delivered template message, by the patient's country.
  - A utility message (a confirmation or reminder) to an Indian number is about **₹0.12-0.15** each, plus 18% GST. Third-party price pages give these figures; check Meta's own pricing page before switching on.
  - For trying it out, Meta's test number and Twilio's sandbox can both message a few numbers that opted in, at no or trial-credit cost.
  - Real patients need a WhatsApp Business sender and approved templates.
- **SMS in India:**
  - The clinic, its sender ID and each template must be registered on an operator's **DLT** portal. Vendor guides give roughly ₹5,900-10,000 one-time, plus a yearly renewal.
  - Then there's a per-message charge from the provider (Twilio, MSG91, Exotel, Gupshup).
  - Twilio's international route skips DLT, but costs far more per message.
- For two messages per booking, WhatsApp is the cheaper of the two for a small clinic.

## Costs and privacy

- Check the provider's current price per message before switching on; a reminder plus a confirmation is two messages per booking.
- Messages contain the first name, service, branch, doctor and time, never the phone number in the text or anything clinical beyond the service name.
- Under India's DPDP Act, appointment confirmations and reminders are part of the service the patient asked for; keep the do-not-call list honoured (it is) and mention messages in the clinic's privacy notice.
