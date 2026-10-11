"""
Calls a real clinic gets that the audit of 11 Oct found Emma mishandling
(docs/REAL_CLINIC_GAPS.md): pain when the clinic is closed, bleeding after an
extraction, sentences taken as names. Model down: the path that answered most
phone turns on 8 Oct.
"""

import unittest
from datetime import datetime

import facts
import nlu
from dialogue.handlers import next_open_day
from dialogue.testing import DemoClinic
from test_r2_engine import Call


class ClinicCallCase(unittest.TestCase):
    NOW = datetime(2026, 10, 1, 10, 0)             # a Thursday morning, the clinic open

    def setUp(self):
        self.clinic = DemoClinic(now=self.NOW)
        self.clinic.__enter__()
        facts.clear_cache()
        self.backend = nlu.use_backend(nlu.FakeNLU(usable=False))
        self.backend.__enter__()

    def tearDown(self):
        self.backend.__exit__(None, None, None)
        facts.clear_cache()
        self.clinic.__exit__(None, None, None)


class ClosedNowTests(ClinicCallCase):
    NOW = datetime(2026, 10, 3, 22, 40)            # Saturday night: closed until Monday

    def test_pain_at_night_hears_closed_now_advice_and_the_first_slot(self):
        reply = Call().run("", "My tooth is broken and it really hurts.").text
        self.assertNotIn("seen today", reply)
        self.assertIn("closed", reply)
        self.assertIn("cold pack", reply)                 # the after-hours advice (verified fact)
        self.assertIn("Monday", reply)

    def test_next_open_day(self):
        self.assertIsNone(next_open_day(datetime(2026, 10, 1, 10, 0)))
        self.assertEqual(next_open_day(datetime(2026, 10, 3, 22, 40)).isoformat(), "2026-10-05")   # Sat night
        self.assertEqual(next_open_day(datetime(2026, 10, 4, 12, 0)).isoformat(), "2026-10-05")    # Sunday
        self.assertEqual(next_open_day(datetime(2026, 10, 1, 6, 0)).isoformat(), "2026-10-01")     # before 7


class BleedingTests(ClinicCallCase):
    def test_heavy_bleeding_gets_first_aid_and_a_slot_not_a_hang_up(self):
        call = Call()
        reply = call.run("", "My tooth was taken out yesterday and it's bleeding a lot.").text
        self.assertIn("gauze", reply)
        self.assertNotIn("108", reply)
        self.assertFalse(call.s.closed_conversation, reply)

    def test_bleeding_that_wont_stop_is_still_a_red_flag(self):
        reply = Call().run("", "The bleeding won't stop, I've been pressing for an hour.").text
        self.assertIn("108", reply)


class NameTests(ClinicCallCase):
    def test_running_late_is_not_a_name(self):
        reply = Call().run("", "Hi, I'm stuck in traffic, I'll be 15 minutes late for my 11 o'clock.").text
        self.assertNotIn("Stuck", reply)

    def test_a_doctor_calling_is_not_a_patient_name(self):
        reply = Call().run("", "Hi, this is Dr Rao, I'm not coming in tomorrow.").text
        self.assertNotIn("Rao I", reply)

    def test_a_patient_introduction_still_works(self):
        reply = Call().run("", "Hi, this is Mr Arjun Sharma, I'd like to book a cleaning.").text
        self.assertIn("Arjun Sharma", reply)
        self.assertNotIn("Mr", reply)


class OtherCallerTests(ClinicCallCase):
    """Not every caller is a patient booking (C7, K4)."""

    def tasks(self):
        return self.clinic.query("SELECT kind, priority, note FROM tasks")

    def test_running_late_is_passed_on(self):
        call = Call()
        reply = call.run("", "Hi, I'm stuck in traffic, I'll be 15 minutes late for my 11 o'clock.").text
        self.assertRegex(reply, r"front desk|the team")
        self.assertTrue(any("running late" in t["note"] for t in self.tasks()))

    def test_wrong_number_is_closed_kindly(self):
        call = Call()
        reply = call.run("", "Hello, is this Domino's?").text
        self.assertIn("wrong number", reply)
        self.assertTrue(call.s.closed_conversation)

    def test_a_sales_call_goes_to_the_manager(self):
        call = Call()
        reply = call.run("", "Hi, I'm calling from a digital marketing agency about SEO services.").text
        self.assertIn("clinic manager", reply)
        self.assertTrue(any("Business" in t["note"] for t in self.tasks()))

    def test_a_doctor_calling_in(self):
        reply = Call().run("", "Hi, this is Dr Rao, I'm not coming in tomorrow.").text
        self.assertIn("Dr Rao", reply)
        self.assertIn("clinic manager", reply)
        self.assertTrue(any(t["kind"] == "escalation" and t["priority"] == "high" for t in self.tasks()))

    def test_records_request(self):
        reply = Call().run("", "I need a copy of my X-ray report from last week.").text
        self.assertIn("front desk", reply)


class DoctorAwayTests(ClinicCallCase):
    def test_a_doctor_on_leave_is_said_and_a_colleague_offered(self):
        import scheduling
        from datetime import datetime as dt
        import clock
        rao = self.clinic.query("SELECT id FROM doctors WHERE spoken_name = 'Dr Rao'")[0]["id"]
        monday = dt(2026, 10, 5)
        def block(conn):
            import db
            conn.execute("INSERT INTO blocked_times (doctor_id, start_utc, end_utc, reason_category, created_by, created_at) "
                         "VALUES (?, ?, ?, 'personal', 'test', '2026-10-01T00:00:00Z')",
                         (rao, db.utc_str(clock.localize(monday.replace(hour=0))),
                          db.utc_str(clock.localize(monday.replace(hour=23)))))
        self.clinic.db.run_sync(block)
        self.assertTrue(self.clinic.db.run_sync(scheduling.doctor_away, rao, monday.date()))
        call = Call()
        call.run("", "I'd like a cleaning with Dr Rao at Nagarbhavi.", "Priya Sharma", "9845012345", "yes")
        call.say("Monday")
        reply = call.say("Morning, please.").text
        self.assertRegex(reply, r"away|isn.t in")
        self.assertNotIn("fully booked", reply)


class FamilyBookingTests(ClinicCallCase):
    """F1 (11 Oct): a second family member in the same call, under their own name."""

    BOOK_PRIYA = ["", "I'd like to book a cleaning at Jayanagar.", "Priya Sharma", "9845012345", "yes", "Monday",
                  "10 am", "yes"]

    def people(self):
        return self.clinic.query("SELECT a.start_utc, p.name FROM appointments a JOIN patients p ON p.id = a.patient_id "
                                 "WHERE a.caller_phone_e164 = '+919845012345' ORDER BY a.start_utc")

    def test_also_book_my_son_right_after_mine(self):
        call = Call()
        call.run(*self.BOOK_PRIYA)
        reply = call.say("Can you also book my son Arjun for a check-up, same day, right after mine?").text
        self.assertIn("for Arjun", reply)
        self.assertIn("10:30", reply)
        call.say("yes")
        self.assertEqual([(r["start_utc"], r["name"]) for r in self.people()],
                         [("2026-10-05T04:30:00Z", "Priya Sharma"), ("2026-10-05T05:00:00Z", "Arjun")])

    def test_a_new_day_for_the_booking_just_made_moves_it_or_nothing(self):
        call = Call()
        call.run(*self.BOOK_PRIYA)
        reply = call.say("Actually make it Tuesday.").text
        self.assertNotIn("Got it", reply)
        call.say("yes")
        rows = self.people()
        self.assertEqual(len(rows), 1)                     # moved or still Monday, never a silent second booking


if __name__ == "__main__":
    unittest.main()
