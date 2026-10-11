"""privacy.py: personal data beyond transcripts; a patient's export and erasure requests (DPDP, 11 Oct)."""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import clock  # noqa: E402
import privacy  # noqa: E402
import scheduling  # noqa: E402
from dialogue.testing import DemoClinic  # noqa: E402

NOW = datetime(2026, 10, 1, 10, 0)
PHONE = "9845012345"
E164 = "+919845012345"


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.clinic = DemoClinic(now=NOW)
        self.clinic.__enter__()
        self.rao = self.clinic.query("SELECT id FROM doctors WHERE spoken_name = 'Dr Rao'")[0]["id"]

    def tearDown(self):
        self.clinic.__exit__(None, None, None)

    def book(self, day, key):
        return self.clinic.db.run_sync(
            scheduling.book, service="Teeth Cleaning", doctor_id=self.rao,
            start=clock.localize(datetime(2026, 10, day, 10, 0)), patient_name="Priya Sharma", phone=PHONE,
            idem_key=key)

    def test_export_lists_what_is_held(self):
        self.book(5, "a")
        data = self.clinic.db.run_sync(privacy.export_phone, PHONE)
        self.assertEqual(data["phone"], E164)
        self.assertEqual([p["name"] for p in data["patients"]], ["Priya Sharma"])
        self.assertEqual(len(data["appointments"]), 1)
        self.assertTrue(data["messages"])                         # the confirmation queued for her
        self.assertIsNone(self.clinic.db.run_sync(privacy.export_phone, "123"))

    def test_erase_is_refused_with_an_upcoming_appointment_then_works_after_cancelling(self):
        res = self.book(5, "a")
        out = self.clinic.db.run_sync(privacy.erase_phone, PHONE, "staff")
        self.assertFalse(out["ok"])
        self.assertIn("upcoming", out["reason"])
        self.clinic.db.run_sync(scheduling.cancel, res.appointment_id, idem_key="c")
        out = self.clinic.db.run_sync(privacy.erase_phone, PHONE, "staff")
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.clinic.query("SELECT COUNT(*) AS n FROM patients WHERE phone_e164 = ?", E164)[0]["n"], 0)
        self.assertEqual(self.clinic.query("SELECT COUNT(*) AS n FROM appointments WHERE caller_phone_e164 = ?",
                                           E164)[0]["n"], 0)
        self.assertEqual(self.clinic.query("SELECT COUNT(*) AS n FROM patient_messages")[0]["n"], 0)
        self.assertEqual(self.clinic.query("SELECT COUNT(*) AS n FROM actions WHERE result_json LIKE ?",
                                           f"%{E164}%")[0]["n"], 0)
        names = {r["name"] for r in self.clinic.query("SELECT name FROM patients")}
        self.assertNotIn("Priya Sharma", names)
        audit = self.clinic.query("SELECT action, after_json FROM audit_events WHERE action = 'privacy_erase'")
        self.assertEqual(len(audit), 1)
        self.assertNotIn(E164, audit[0]["after_json"])

    def test_purge_drops_old_personal_details(self):
        self.book(5, "a")
        later = NOW + timedelta(days=200)
        out = self.clinic.db.run_sync(privacy.purge_personal, later)
        self.assertGreaterEqual(out["actions"], 1)
        self.assertGreaterEqual(out["audit_details"], 1)
        left = self.clinic.query("SELECT COUNT(*) AS n FROM audit_events WHERE after_json LIKE '%Priya%'")[0]["n"]
        self.assertEqual(left, 0)


if __name__ == "__main__":
    unittest.main()
