"""notify.py: WhatsApp/SMS confirmations and reminders, queued with the change they describe (11 Oct)."""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import clock  # noqa: E402
import config  # noqa: E402
import notify  # noqa: E402
import scheduling  # noqa: E402
from dialogue.testing import DemoClinic  # noqa: E402

NOW = datetime(2026, 10, 1, 10, 0)               # Thursday
PHONE = "9845012345"


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.clinic = DemoClinic(now=NOW)
        self.clinic.__enter__()
        self.rao = self.clinic.query("SELECT id FROM doctors WHERE spoken_name = 'Dr Rao'")[0]["id"]

    def tearDown(self):
        self.clinic.__exit__(None, None, None)

    def book(self, day=5, hour=10, phone=PHONE, actor="emma", key="k1"):
        return self.clinic.db.run_sync(
            scheduling.book, service="Teeth Cleaning", doctor_id=self.rao,
            start=clock.localize(datetime(2026, 10, day, hour, 0)), patient_name="Priya Sharma", phone=phone,
            idem_key=key, actor=actor)

    def messages(self):
        return self.clinic.query("SELECT kind, status, body, due_at FROM patient_messages ORDER BY id")

    def test_a_booking_queues_a_confirmation_and_a_reminder(self):
        res = self.book()
        self.assertTrue(res.ok, res.code)
        rows = self.messages()
        self.assertEqual([(r["kind"], r["status"]) for r in rows], [("booked", "pending"), ("reminder", "pending")])
        self.assertIn("Hi Priya, your teeth cleaning at Pearl Dental Clinic, Nagarbhavi is booked for Mon 5 Oct, "
                      "10:00 am with Dr Rao.", rows[0]["body"])
        self.assertEqual(rows[1]["due_at"], "2026-10-04T04:30:00Z")        # 24 h before, in UTC

    def test_moving_and_cancelling_keep_the_reminder_in_step(self):
        res = self.book()
        self.clinic.db.run_sync(scheduling.reschedule, res.appointment_id, doctor_id=self.rao,
                                start=clock.localize(datetime(2026, 10, 6, 11, 0)), idem_key="m1")
        self.clinic.db.run_sync(scheduling.cancel, res.appointment_id, idem_key="c1")
        kinds = [(r["kind"], r["status"]) for r in self.messages()]
        self.assertEqual(kinds, [("booked", "pending"), ("reminder", "skipped"), ("moved", "pending"),
                                 ("reminder", "skipped"), ("cancelled", "pending")])

    def test_no_reminder_for_a_visit_within_a_day_and_none_for_the_seed_or_do_not_call(self):
        self.book(day=1, hour=15, key="soon")                  # today: confirmation only
        self.assertEqual([r["kind"] for r in self.messages()], ["booked"])
        self.book(day=7, hour=10, key="seed", actor="seed")
        self.assertEqual(len(self.messages()), 1)
        self.clinic.db.run_sync(lambda conn: conn.execute(
            "INSERT INTO contact_prefs (phone_e164, do_not_call, updated_at) VALUES ('+919900000000', 1, 'x')"))
        self.book(day=8, hour=10, phone="9900000000", key="dnc")
        self.assertEqual(len(self.messages()), 1)

    def test_the_log_sender_marks_messages_logged_and_reminders_wait(self):
        self.book()
        handled = asyncio.run(notify.send_due(self.clinic.db, notify.LogSender(), now=clock.now()))
        self.assertEqual(handled, 1)                          # the reminder isn't due yet
        self.assertEqual([r["status"] for r in self.messages()], ["logged", "pending"])

    def test_a_failed_send_is_retried_then_given_up(self):
        self.book()

        class Broken:
            name = "broken"

            async def send(self, channel, to, body):
                raise RuntimeError("provider down")
        now = clock.now()
        for i in range(notify.MAX_ATTEMPTS):
            asyncio.run(notify.send_due(self.clinic.db, Broken(), now=now + timedelta(hours=i)))
        first = self.messages()[0]
        self.assertEqual(first["status"], "failed")

    def test_twilio_sender_posts_whatsapp(self):
        sent = {}

        class Response:
            status_code = 201
            text = ""

            def json(self):
                return {"sid": "SM123"}

        class Client:
            async def post(self, url, data, auth):
                sent.update(url=url, data=data, auth=auth)
                return Response()
        sender = notify.TwilioSender("AC1", "tok", "+14155238886", client=Client())
        status, sid = asyncio.run(sender.send("whatsapp", "+919845012345", "Hi"))
        self.assertEqual((status, sid), ("sent", "SM123"))
        self.assertEqual(sent["data"]["To"], "whatsapp:+919845012345")
        self.assertIn("/Accounts/AC1/Messages.json", sent["url"])

    def test_sender_choice(self):
        self.assertIsInstance(notify.build_sender()[0], notify.LogSender)
        with mock.patch.object(config, "NOTIFY_SENDER", "twilio"):
            sender, reason = notify.build_sender()
            self.assertIsInstance(sender, notify.LogSender)          # no keys yet
            self.assertIn("TWILIO", reason)


if __name__ == "__main__":
    unittest.main()
