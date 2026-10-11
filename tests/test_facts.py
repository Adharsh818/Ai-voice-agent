"""
facts.py: the DB catalog and the verified knowledge base (docs/R2_DESIGN.md,
section 11).

Which branch offers which service comes from doctor_services, never from a
hard-coded list (Z6, the braces-at-Nagarbhavi loop). Only verified entries
are loaded, and the no-model fallback answers price and catalog questions
itself (feedback 3: no deflection to the doctor), or returns None so Emma
says honestly she isn't sure.
"""

import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

import config
import facts
from dialogue.testing import DemoClinic


def demo_catalog(clinic) -> facts.Catalog:
    return clinic.db.run_sync(facts.load_catalog)


class CatalogTests(unittest.TestCase):
    def test_branches_per_service_come_from_the_doctors(self):
        with DemoClinic() as clinic:
            cat = demo_catalog(clinic)
        self.assertEqual(cat.branches_offering("Braces"), ("Indiranagar",))
        self.assertEqual(cat.branches_offering("Pediatric Dentistry"), ("Jayanagar", "Whitefield"))
        self.assertEqual(cat.branches_offering("Root Canal Treatment"), ("Nagarbhavi", "Indiranagar", "Jayanagar"))
        self.assertNotIn("Braces", cat.branch("Nagarbhavi").services)
        self.assertEqual(len(cat.branches), 4)
        self.assertEqual(len(cat.doctors), 8)
        self.assertEqual(len(cat.services), 9)

    def test_a_rota_change_in_the_db_changes_what_is_offered(self):
        with DemoClinic() as clinic:
            def give_rao_braces(conn):
                rao = conn.execute("SELECT id FROM doctors WHERE spoken_name = 'Dr Rao'").fetchone()[0]
                braces = conn.execute("SELECT id FROM services WHERE name = 'Braces'").fetchone()[0]
                conn.execute("INSERT INTO doctor_services VALUES (?, ?)", (rao, braces))
            clinic.db.run_sync(give_rao_braces)
            cat = demo_catalog(clinic)
        self.assertEqual(cat.branches_offering("Braces"), ("Nagarbhavi", "Indiranagar"))
        self.assertIn("Braces", cat.branch("Nagarbhavi").services)

    def test_doctors_carry_gender_branch_services_and_spoken_name(self):
        with DemoClinic() as clinic:
            cat = demo_catalog(clinic)
        rao = cat.doctor("Dr Rao")
        self.assertEqual((rao.name, rao.gender, rao.branch), ("Dr. Meera Rao", "female", "Nagarbhavi"))
        self.assertIn("Teeth Cleaning", rao.services)
        self.assertEqual(rao.hours, "Monday to Saturday, 9 to 5")
        self.assertEqual(cat.doctor("Dr. Farhan Ali").hours,
                         "Monday, Wednesday, Friday and Saturday, 2:30 to 9 at night")
        self.assertEqual([d.spoken for d in cat.doctors_for("Braces")], ["Dr Iyer"])
        self.assertEqual([d.spoken for d in cat.doctors_for("Tooth Extraction", branch="Whitefield")], ["Dr Ali"])
        self.assertEqual([d.spoken for d in cat.doctors_for("Pediatric Dentistry", gender="female")],
                         ["Dr Kulkarni", "Dr Reddy"])
        self.assertTrue(all(d.gender in ("female", "male") and d.branch for d in cat.doctors))

    def test_services_spoken_forms_aliases_and_durations(self):
        with DemoClinic() as clinic:
            cat = demo_catalog(clinic)
        rct = cat.service("root canal treatment")
        self.assertEqual((rct.spoken, rct.duration_min), ("a root canal", 60))
        self.assertIn("rct", rct.aliases)
        self.assertIn("route canal", rct.aliases)             # the 1 Oct STT slip
        self.assertTrue(cat.service("Braces").is_consultation)

    def test_branches_carry_verified_address_parking_and_hours(self):
        with DemoClinic() as clinic:
            cat = demo_catalog(clinic)
        white = cat.branch("Whitefield")
        self.assertIn("ITPL Main Road", white.address)
        self.assertIn("basement", white.parking)
        self.assertTrue(white.hours.startswith("Monday, Wednesday, Friday and Saturday, 8 in the morning"))
        self.assertEqual(cat.branch("Nagarbhavi").hours, "Monday to Saturday, 9 to 9 at night")

    def test_get_catalog_is_cached_and_refreshed_after_a_minute(self):
        with DemoClinic():
            facts.clear_cache()
            first = asyncio.run(facts.get_catalog())
            self.assertIs(asyncio.run(facts.get_catalog()), first)
            with mock.patch.object(facts, "CATALOG_TTL_S", 0.0):
                self.assertIsNot(asyncio.run(facts.get_catalog()), first)
            facts.clear_cache()
        self.assertEqual(first.branches_offering("Braces"), ("Indiranagar",))


class KnowledgeTests(unittest.TestCase):
    def test_only_verified_entries_are_loaded(self):
        raw = {
            "version": "t1", "clinic_name": "Pearl Dental Clinic",
            "hours": {"verified": True, "text": "Open 9 to 5."},
            "escalation": {"verified": True, "text": "The doctor can go through that with you at your visit."},
            "facts": [
                {"id": "price.x", "topic": "x", "verified": True, "text": "X is 100 rupees.", "keywords": ["x"]},
                {"id": "price.y", "topic": "y", "verified": False, "text": "Y is 999 rupees."},
            ],
            "branches": [{"name": "Nagarbhavi", "verified": True, "address": "80 Feet Road", "phone": "PLACEHOLDER"},
                         {"name": "Secret", "verified": False, "address": "Nowhere"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "facts.json")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(raw, fh)
            kb = facts.load_knowledge(path)
        ids = {f.id for f in kb.facts}
        self.assertIn("price.x", ids)
        self.assertNotIn("price.y", ids)
        self.assertNotIn("escalation", ids)
        self.assertFalse(any("go through" in f.text for f in kb.facts))
        self.assertEqual(kb.branch_info("Nagarbhavi"), {"address": "80 Feet Road"})
        self.assertEqual(kb.branch_info("Secret"), {})
        self.assertEqual(kb.hours, "Open 9 to 5.")
        self.assertTrue(kb.unknown_line)

    def test_the_real_knowledge_base(self):
        kb = facts.load_knowledge()
        self.assertTrue(kb.overview.startswith("Pearl Dental has four branches"))
        self.assertIn("book, change or cancel", kb.get("clinic.capability").text)
        self.assertEqual(kb.get("price.braces").service, "Braces")
        self.assertEqual(kb.unknown_line, "Hmm, I'm not sure about that one.")
        self.assertFalse(any("go through that with you" in f.text for f in kb.facts))

    def test_a_missing_file_gives_an_empty_knowledge_base(self):
        kb = facts.load_knowledge(os.path.join(tempfile.gettempdir(), "no-such-facts-file.json"))
        self.assertEqual(kb.facts, ())
        self.assertTrue(kb.unknown_line)

    def test_get_knowledge_reloads_when_the_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "facts.json")

            def write(version, mtime):
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    json.dump({"version": version, "facts": []}, fh)
                os.utime(path, ns=(mtime, mtime))

            with mock.patch.object(config, "CLINIC_FACTS_PATH", path):
                facts.clear_cache()
                write("v1", 1_000_000_000_000_000_000)
                first = facts.get_knowledge()
                self.assertIs(facts.get_knowledge(), first)
                write("v2", 1_000_000_005_000_000_000)
                self.assertEqual(facts.get_knowledge().version, "v2")
            facts.clear_cache()
        self.assertEqual(first.version, "v1")

    def test_knowledge_block_is_stable_and_complete(self):
        with DemoClinic() as clinic:
            cat = demo_catalog(clinic)
        kb = facts.load_knowledge()
        block = facts.knowledge_block(cat, kb)
        later = facts.knowledge_block(cat, kb)
        self.assertEqual(block, later)
        self.assertNotIn(cat.loaded_at, block)               # nothing time-dependent: prompt cache friendly
        self.assertIn("[price.root_canal]", block)
        self.assertIn("[clinic.overview]", block)
        self.assertIn("- Dr Iyer (Dr. Kavya Iyer), female, Indiranagar: orthodontist, BDS and MDS in orthodontics, "
                      "11 years' experience, speaks English, Tamil, Kannada, Hindi, is a certified Invisalign "
                      "provider; Consultation, Braces, Invisalign", block)
        self.assertIn("- Braces (say \"braces\"), 30 min, at Indiranagar\n", block)
        nagarbhavi = next(line for line in block.splitlines() if line.startswith("- Nagarbhavi;"))
        self.assertNotIn("Braces", nagarbhavi)
        self.assertNotIn("go through", block)


class LookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with DemoClinic() as clinic:
            cls.cat = demo_catalog(clinic)
        cls.kb = facts.load_knowledge()

    def ask(self, text, faq_ids=()):
        return facts.lookup(text, self.kb, self.cat, faq_ids)

    def test_prices_without_the_model(self):
        self.assertIn("5,000 to 8,000", self.ask("How much is a root canal?"))
        self.assertIn("30,000 to 50,000", self.ask("what are the charges for braces"))
        self.assertIn("6,000 to 12,000", self.ask("how much does whitening cost"))
        self.assertIn("usual range", self.ask("how much does it cost?"))
        self.assertIn("no fee", self.ask("is there a fee to cancel?"))

    def test_which_branch_does_a_service(self):
        self.assertEqual(self.ask("Which branch does braces?"), "We do braces at our Indiranagar branch.")
        self.assertEqual(self.ask("do you do braces at nagarbhavi"),
                         "Our Nagarbhavi branch doesn't do braces, but our Indiranagar branch does.")
        self.assertIn("Jayanagar and Whitefield", self.ask("where can I get my kid's teeth checked, pediatric?"))

    def test_doctors_and_hours(self):
        doctors = self.ask("who are your doctors?")
        for d in self.cat.doctors:
            self.assertIn(d.spoken, doctors)
        self.assertEqual(self.ask("who does braces"), "For braces, it's Dr Iyer at Indiranagar.")
        self.assertEqual(self.ask("do you have a lady doctor at Jayanagar?"), "Dr Kulkarni is at our Jayanagar branch.")
        self.assertIn("Monday, Wednesday, Friday and Saturday", self.ask("when is Dr Ali in?"))
        self.assertIn("Monday to Saturday", self.ask("what are your timings?"))
        self.assertTrue(self.ask("what time is Whitefield open?").startswith("Our Whitefield branch is open"))

    def test_clinic_questions(self):
        self.assertTrue(self.ask("Tell me about the clinic").startswith("Pearl Dental has four branches"))
        self.assertIn("UPI", self.ask("can I pay by GPay or UPI?"))
        self.assertIn("basement", self.ask("is there parking at Whitefield?"))
        self.assertIn("depends on the branch", self.ask("is there parking?"))
        self.assertIn("local anaesthetic", self.ask("is a root canal painful?"))
        self.assertIn("two or three visits", self.ask("how long does a root canal take?"))
        self.assertIn("children's dentistry", self.ask("what services do you offer?"))

    def test_faq_ids_win(self):
        self.assertEqual(self.ask("anything", faq_ids=("payment.methods",)), "You can pay by UPI, any debit or credit card, net banking or cash, with no extra charge.")
        self.assertEqual(self.ask("how much is braces", faq_ids=("no.such.fact",)),
                         self.kb.get("price.braces").text)

    def test_unknown_topics_return_none(self):
        self.assertIsNone(self.ask("do you sell electric toothbrushes?"))
        self.assertIsNone(self.ask("what's the capital of France?"))
        self.assertIsNone(self.ask(""))


class ClinicKnowledgeTests(LookupTests):
    """Callers who aren't booking (owner, 11 Oct): problems, prices, doctors, family, access; complete answers."""

    def test_treatment_questions_get_the_whole_answer(self):
        wisdom = self.ask("Can you remove a wisdom tooth?")
        self.assertIn("Dr Ali", wisdom)
        self.assertIn("3,000 to 8,000", wisdom)
        self.assertIn("10,000 to 18,000", self.ask("do you do dentures for my mother"))
        self.assertIn("zirconia", self.ask("how much is a crown"))
        self.assertIn("30,000 to 55,000", self.ask("do you do implants"))
        self.assertIn("deep cleaning", self.ask("my gums bleed when I brush"))
        self.assertIn("sensitivity toothpaste", self.ask("my teeth are sensitive to cold"))

    def test_no_deflection_to_the_doctor(self):
        for q in ("how much is a crown", "do you do dentures", "how much are your prices", "is there a warranty on crowns"):
            answer = self.ask(q).lower()
            self.assertNotIn("doctor confirms", answer, q)
            self.assertNotIn("doctor will", answer, q)

    def test_doctors_profiles_children_and_languages(self):
        iyer = self.ask("tell me about Dr Iyer")
        self.assertTrue(iyer.startswith("Dr Iyer is our orthodontist at Indiranagar, with a BDS and an MDS"), iyer)
        self.assertIn("eleven years' experience", iyer)
        self.assertIn("Tamil", iyer)
        self.assertEqual(self.ask("which doctor is good for my kid"),
                         "For children's dentistry, it's Dr Kulkarni at Jayanagar and Dr Reddy at Whitefield.")
        self.assertEqual(self.ask("does anyone speak tamil"), "Yes, Dr Iyer at Indiranagar speaks Tamil.")
        self.assertEqual(self.ask("does Dr Shetty speak Hindi"), "Yes, Dr Shetty speaks English, Kannada, Tulu and Hindi.")
        self.assertNotIn("best", self.ask("is this clinic good").lower())

    def test_policies_after_care_and_access(self):
        self.assertIn("copies of your X-rays", self.ask("can I get a copy of my x-ray"))
        self.assertIn("no-cost EMI", self.ask("do you have EMI for a root canal"))
        self.assertIn("warranty", self.ask("is there a warranty on crowns"))
        self.assertIn("curd or idli", self.ask("what should I eat after extraction"))
        self.assertIn("20 to 30 minutes", self.ask("it's still bleeding after my extraction"))
        self.assertIn("medicine list", self.ask("I have diabetes, can I get an extraction"))
        self.assertIn("middle three months", self.ask("I'm pregnant, can I get a cleaning"))
        self.assertIn("stairs only", self.ask("is there a lift at Nagarbhavi"))
        self.assertIn("wheelchair", self.ask("is the clinic wheelchair friendly"))
        self.assertIn("whole families", self.ask("can my whole family come"))
        self.assertIn("cold pack", self.ask("what if it hurts at night when you're closed"))


class AllowListTests(unittest.TestCase):
    def test_allowed_numbers_cover_the_knowledge_and_catalog(self):
        with DemoClinic() as clinic:
            cat = demo_catalog(clinic)
        nums = facts.allowed_numbers(cat, facts.load_knowledge())
        for n in ("400", "1000", "1500", "5000", "8000", "30000", "70000", "30", "45", "60", "4", "8", "7", "9"):
            self.assertIn(n, nums)
        self.assertNotIn("999", nums)

    def test_price_numbers_per_service(self):
        prices = facts.price_numbers(facts.load_knowledge())
        self.assertTrue({"30000", "50000", "45000", "70000"} <= prices["braces"])
        self.assertEqual(prices["root canal treatment"], prices["root canal"])
        self.assertIn("5000", prices["root canal"])
        self.assertIn("400", prices["consultation"])


if __name__ == "__main__":
    unittest.main()
