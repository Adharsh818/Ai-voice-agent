"""Doctors and weekly hours from the dashboard (clinic_admin.py; REAL_CLINIC_GAPS S3)."""

import json
import unittest
from datetime import time

import clinic_admin as ca
import facts
import scheduling as sch
from test_scheduling import MON, TUE, EngineTestCase, at


class ParseTests(unittest.TestCase):
    def test_shifts(self):
        self.assertEqual(ca.parse_day("9-13, 16:00 - 20:30"), [("09:00", "13:00"), ("16:00", "20:30")])
        self.assertEqual(ca.parse_day("off"), [])
        self.assertEqual(ca.parse_day(""), [])
        for bad in ("9-25", "13-9", "9:15-12", "6-10", "9-13, 12-15", "nine to one"):
            with self.assertRaises(ca.AdminError, msg=bad):
                ca.parse_day(bad)


class AdminTests(EngineTestCase):
    def rota(self, doctor_id):
        return ca._rota(self.conn, doctor_id)

    def test_new_hours_change_what_emma_offers_and_bookings_outside_them_are_listed(self):
        self.book(at(MON, 18), doctor=1)                                  # Dr 1 works 07:00-21:00
        out = ca.set_rota(self.conn, 1, {"Mon": "09:00-13:00, 14:30-17:00"}, actor="owner")
        self.assertEqual(out["rota"]["Mon"], "09:00-13:00, 14:30-17:00")
        self.assertEqual(out["rota"]["Tue"], "07:00-21:00")               # days not given keep their hours
        self.assertEqual([a["start"][:16] for a in out["affected"]], ["2026-10-05T18:00"])
        self.assertIn("DOCTOR_OFF", sch.validate(self.conn, service="Consultation", doctor_id=1, start=at(MON, 17, 30)))
        self.assertEqual(sch.validate(self.conn, service="Consultation", doctor_id=1, start=at(MON, 16)), [])
        audit = self.conn.execute("SELECT actor, action, entity, before_json, after_json FROM audit_events "
                                  "WHERE entity = 'doctor'").fetchone()
        self.assertEqual((audit["actor"], audit["action"]), ("owner", "rota_set"))
        self.assertEqual(json.loads(audit["before_json"])["Mon"], "07:00-21:00")

    def test_a_bad_rota_changes_nothing(self):
        before = self.rota(1)
        with self.assertRaises(ca.AdminError):
            ca.set_rota(self.conn, 1, {"Mon": "09:00-13:00", "Tue": "8-25"}, actor="owner")
        self.assertEqual(self.rota(1), before)

    def test_add_a_doctor_who_can_be_booked_at_once(self):
        out = ca.add_doctor(self.conn, name="Dr Kavya Shetty", spoken_name="", gender="female", branch="Jayanagar",
                            services=["Consultation"], days={"Tue": "10:00-14:00"}, actor="owner")
        self.assertEqual(out["spoken_name"], "Dr Shetty")
        slots = sch.find_slots(self.conn, service="Consultation", dates=[TUE], doctor_id=out["id"], limit=1)
        self.assertEqual(slots[0].start.time(), time(10, 0))
        with self.assertRaises(ca.AdminError):
            ca.add_doctor(self.conn, name="Dr Kavya Shetty", spoken_name="", gender=None, branch="Jayanagar",
                          services=["Consultation"], days={}, actor="owner")
        with self.assertRaises(ca.AdminError):
            ca.add_doctor(self.conn, name="Dr X", spoken_name="", gender=None, branch="Jayanagar",
                          services=["Brain surgery"], days={}, actor="owner")

    def test_two_doctors_never_share_a_spoken_name(self):
        # Review, 12 Oct: a second "Dr Shetty" would make "book me with Dr Shetty" ambiguous.
        out = ca.add_doctor(self.conn, name="Dr Ravi Day", spoken_name="", gender=None, branch="Jayanagar",
                            services=["Consultation"], days={}, actor="owner")
        self.assertEqual(out["spoken_name"], "Dr Ravi Day")             # "Dr Day" is taken
        with self.assertRaises(ca.AdminError):
            ca.add_doctor(self.conn, name="Dr Anil Bee", spoken_name="Dr Bee", gender=None, branch="Jayanagar",
                          services=["Consultation"], days={}, actor="owner")
        with self.assertRaises(ca.AdminError) as hour:
            ca.parse_day("8-25")
        self.assertIn("isn't an hour", str(hour.exception))

    def test_services_and_switching_off(self):
        ca.set_services(self.conn, 3, ["Consultation"], actor="owner")
        self.assertIn("DOCTOR_NO_SERVICE",
                      sch.validate(self.conn, service="Root Canal Treatment", doctor_id=3, start=at(TUE, 10)))
        self.book(at(TUE, 10), doctor=3)
        with self.assertRaises(ca.AdminError):                            # upcoming bookings: move them first
            ca.set_active(self.conn, 3, False, actor="owner")
        ca.set_active(self.conn, 2, False, actor="owner")
        names = [d["id"] for d in ca.list_doctors(self.conn)["doctors"] if not d["active"]]
        self.assertEqual(names, [2])
        facts.clear_cache()


if __name__ == "__main__":
    unittest.main()
