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


if __name__ == "__main__":
    unittest.main()
