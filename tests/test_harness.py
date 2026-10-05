"""
Tests for the conversation harness itself (harness/): the fake NLU, the line
reader, the metrics, the simulated caller, the isolated world, the runner and
tools/converse.py. The harness decides the fix order, so a harness bug would
send the team after the wrong problem; these pin down what it measures.
"""

import contextlib
import io
import json
import os
import random
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest import mock

import clock
import config

from harness import engine_adapter, fake_nlu, lines, metrics, report, runner, sim_caller
from harness import world as world_mod

NOW = datetime(2026, 10, 1, 10, 0)          # Thursday, the harness's default clinic moment
SUMMARY = ("So that's a Teeth Cleaning for Priya Sharma on Monday the 05 October at 10:00 AM, at our "
           "Nagarbhavi branch, and your number is 9 8 4 5 0, 1 2 3 4 5. Shall I book it?")


def turn(caller, emma, **extra):
    out = {"caller": caller, "emma": emma, "heard_previous": True, "llm_ok": None, "db_change": {}}
    out.update(extra)
    return out


def record(turns, **extra):
    rec = {"now": NOW.isoformat(), "turns": [{"n": i, **t} for i, t in enumerate(turns)], "catalog": {},
           "outcome": {"booked": [], "cancelled": [], "rescheduled": [], "tasks": []}}
    rec.update(extra)
    return rec


def booked(start="2026-10-05T10:00:00", service="Teeth Cleaning", branch="Nagarbhavi"):
    return {"id": "a1", "patient": "Priya Sharma", "phone": "+919845012345", "service": service,
            "branch": branch, "doctor": "Dr Rao", "start": start, "status": "booked"}


class FakeNLUTests(unittest.TestCase):
    def setUp(self):
        frozen = clock.frozen(NOW)
        frozen.__enter__()
        self.addCleanup(frozen.__exit__, None, None, None)

    def test_indian_and_deepgram_phone_numbers(self):
        cases = {
            "nine eight double four five zero one two three four": "9844501234",
            "(789) 937-7462": "7899377462",
            "+91 98450 12345": "9845012345",
            "nine triple zero triple zero zero one one": "9000000011",
            "It's 9 8 4 5 0, 1 2 3 4 5.": "9845012345",
        }
        for said, digits in cases.items():
            with self.subTest(said=said):
                r = fake_nlu.read(said, expect="phone")
                self.assertEqual(r.phone, digits)
                self.assertTrue(r.phone_valid)

    def test_names(self):
        self.assertEqual(fake_nlu.read("My name is Priya Sharma.").name, "Priya Sharma")
        self.assertEqual(fake_nlu.read("Priya Sharma.", expect="name").name, "Priya Sharma")
        self.assertIsNone(fake_nlu.read("Monday at 5 pm", expect="name").name)
        child = fake_nlu.read("I want to book an appointment for my son, he's 8.")
        self.assertIsNone(child.name, "a pronoun is not a name")

    def test_intents(self):
        cases = {
            "I need to cancel my appointment.": "cancel", "I want to prepone my appointment.": "reschedule",
            "Are you a bot?": "bot", "Can I talk to a real person?": "human",
            "My face is swollen and it's bleeding.": "emergency", "No, that's all, thanks. Bye!": "end",
            "Hi, I'd like to book a cleaning.": "book",
        }
        for said, intent in cases.items():
            with self.subTest(said=said):
                self.assertEqual(fake_nlu.read(said).intent, intent)

    def test_questions_are_not_bookings(self):
        price = fake_nlu.read("How much is a root canal?")
        self.assertEqual(price.intent, "question")
        self.assertNotIn("service", price.slots)
        hours = fake_nlu.read("What time do you open on Saturday?")
        self.assertEqual(hours.intent, "question")
        self.assertNotIn("date", hours.slots)
        offer = fake_nlu.read("Sunday at 10?", expect="date")
        self.assertIsNone(offer.question, "a rising 'Sunday at 10?' answers Emma, it asks nothing")
        self.assertEqual(offer.date_issue, "SUNDAY")
        self.assertIsNone(fake_nlu.read("My face is swollen and I can't open my mouth.").question)

    def test_dates_and_times_use_dateparse(self):
        r = fake_nlu.read("Tomorrow at 5 pm, please.", expect="date")
        self.assertEqual(r.date, date(2026, 10, 2))
        self.assertEqual(r.time, time(17, 0))
        self.assertIsNone(fake_nlu.read("It's been raining a lot today, no?").date)

    def test_answers_come_from_clinic_facts(self):
        text, fact_id = fake_nlu.answer("How much is a root canal?")
        self.assertEqual(fact_id, "price.root_canal")
        self.assertIn("rupees", text)
        self.assertEqual(fake_nlu.answer("Are you a bot?")[0], "@HONEST@")
        self.assertEqual(fake_nlu.answer("How can you help me?"), (None, None))


class LinesTests(unittest.TestCase):
    def test_classify_ask(self):
        cases = {
            "Sure, I can help with that. May I have your full name, please?": ("name", None),
            "So that's 9 8 4 5 0, 1 2 3 4 5, right?": ("confirm", "phone"),
            SUMMARY: ("confirm", "summary"),
            "Ah, that one's taken. I could do 10:30 AM or 11:30 AM, would either work?": ("choice", None),
            "What day would suit you?": ("date", None),
            "What time works best for you?": ("time", None),
            "Thanks. And what's the visit for?": ("service", None),
            "Great, we'll see you then. Take care, bye!": ("closing", None),
            "Hello, Pearl Dental. How can I help you?": ("open", None),
            "Anything else I can help with?": ("anything_else", None),
            "Could you say it a few digits at a time? I'm listening.": ("phone", None),
            "Sure, let's do it slowly, a few digits at a time. Go ahead.": ("phone", None),
        }
        for line, (kind, slot) in cases.items():
            with self.subTest(line=line):
                ask = lines.classify_ask(line, date(2026, 10, 1))
                self.assertEqual(ask.kind, kind)
                if slot:
                    self.assertEqual(ask.slot, slot)

    def test_claims_are_statements_not_questions(self):
        self.assertEqual(lines.claims("Done, you're booked for a Teeth Cleaning on Monday."), {"booked"})
        self.assertEqual(lines.claims("Shall I book it?"), set())
        self.assertEqual(lines.claims("Okay, I've cancelled that for you."), {"cancelled"})

    def test_yes_no_and_deflection(self):
        self.assertEqual(lines.yes_no("No, that's not right."), "no")
        self.assertEqual(lines.yes_no("No problem, go ahead."), "yes")
        self.assertFalse(lines.is_clear_yes("Yes, but change the date."))
        self.assertTrue(lines.deflection("The doctor can go through that with you at your visit."))
        self.assertIsNone(lines.deflection("A cleaning is usually 1,000 to 1,500 rupees."))


class MetricsTests(unittest.TestCase):
    def counts(self, rec):
        return metrics.evaluate(rec)["counts"]

    def test_m3_near_repeat_and_triple(self):
        line = "Sorry, on this call I can only book our Nagarbhavi branch. Would that still work?"
        rec = record([turn("", "Hello."), turn("No.", line), turn("No.", line), turn("No.", line)])
        findings = [f for f in metrics.evaluate(rec)["findings"] if f["metric"] == "M3"]
        self.assertTrue(any("near-repeat" in f["detail"] for f in findings))
        self.assertTrue(any("3 times" in f["detail"] for f in findings))
        self.assertEqual(findings[0]["turn"], 2)

    def test_m3_sentence_doubled_in_one_reply(self):
        rec = record([turn("", "Hello."), turn("Sunday?", "We're closed on Sundays. We're closed on Sundays. "
                                                          "Which other day works?")])
        self.assertEqual(self.counts(rec).get("M3"), 1)

    def test_m3_ignores_a_corrected_read_back(self):
        rec = record([turn("", "Hello."), turn("98450 12346", "So that's 9 8 4 5 0, 1 2 3 4 6, right?"),
                      turn("No, it's 98450 12345.", "So that's 9 8 4 5 0, 1 2 3 4 5, right?")])
        self.assertNotIn("M3", self.counts(rec))

    def test_m2_slot_asked_again(self):
        rec = record([turn("", "Hello."), turn("My name is Rahul Verma.", "Thanks. What's the visit for?"),
                      turn("A cleaning.", "Great. May I have your full name, please?")])
        findings = [f for f in metrics.evaluate(rec)["findings"] if f["metric"] == "M2"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["turn"], 2)

    def test_m4_deflection_unless_clinical(self):
        deflect = "The doctor can go through that with you at your visit."
        rec = record([turn("", "Hello."), turn("How much is a cleaning?", deflect),
                      turn("Do I need a root canal?", deflect)])
        found = [f for f in metrics.evaluate(rec)["findings"] if f["metric"] == "M4"]
        self.assertEqual([f["severity"] for f in found], ["fail", "info"])
        self.assertEqual(self.counts(rec).get("M4"), 1)

    def test_z2_claim_without_commit(self):
        rec = record([turn("", "Hello."), turn("Yes.", "Done, you're booked for Monday at 10.")])
        self.assertEqual(self.counts(rec).get("Z2"), 1)

    def test_z1_and_m1_for_a_booking_without_a_summary(self):
        rec = record([
            turn("", "Hello."),
            turn("Priya Sharma, 98450 12345.", "So that's 9 8 4 5 0, 1 2 3 4 5, right?"),
            turn("Yes.", "Ah, that one's taken. I could do 10:00 AM on Monday the 05 October, would that work?"),
            turn("Let's do 10.", "Done, you're booked.", db_change={"booked": [booked()]}),
        ])
        counts = self.counts(rec)
        self.assertEqual(counts.get("Z1"), 1)
        self.assertEqual(counts.get("M1"), 1)

    def test_a_clear_yes_to_a_heard_summary_is_clean(self):
        rec = record([
            turn("", "Hello."),
            turn("Priya Sharma, 98450 12345.", "So that's 9 8 4 5 0, 1 2 3 4 5, right?"),
            turn("Yes.", SUMMARY),
            turn("Yes, go ahead.", "Done, you're booked.", db_change={"booked": [booked()]}),
        ])
        counts = self.counts(rec)
        self.assertNotIn("Z1", counts)
        self.assertNotIn("M1", counts)
        talked_over = json.loads(json.dumps(rec))
        talked_over["turns"][3]["heard_previous"] = False
        self.assertEqual(self.counts(talked_over).get("Z1"), 1, "a yes over an interrupted summary is not consent")

    def test_z6_service_at_a_branch_without_it(self):
        rec = record([turn("", "Hello."), turn("Yes.", "Done.", db_change={"booked": [booked(service="Braces")]})],
                     catalog={"branch_services": {"Nagarbhavi": ["Teeth Cleaning"]}})
        self.assertEqual(self.counts(rec).get("Z6"), 1)

    def test_call_level_outcomes(self):
        cancel_got_booked = record([turn("", "Hello."), turn("Bye.", "Bye!")], goal="cancel",
                                   expected_outcome="cancelled", ended_by="caller_bye",
                                   outcome={"booked": [booked()], "cancelled": [], "rescheduled": [], "tasks": []})
        self.assertEqual(self.counts(cancel_got_booked).get("Z5"), 1)
        gave_up = record([turn("", "Hello."), turn("Forget it. Bye.", "What other time would work for you?")],
                         goal="book", expected_outcome="booked", ended_by="caller_gave_up")
        result = metrics.evaluate(gave_up)
        self.assertEqual(result["counts"].get("M7"), 1)
        self.assertEqual(result["counts"].get("M10"), 1)
        self.assertFalse(result["m7_success"])

    def test_t5_counts_turns_to_the_commit(self):
        turns = [turn("", "Hello.")] + [turn(f"answer {i}", "Okay?") for i in range(1, 8)]
        turns.append(turn("Yes.", "Done.", db_change={"booked": [booked()]}))
        rec = record(turns, simple=True, outcome={"booked": [booked()], "cancelled": [], "rescheduled": [],
                                                  "tasks": []})
        self.assertEqual(metrics.evaluate(rec)["t5_caller_turns"], 8)

    def test_degraded_turns_are_not_counted(self):
        deflect = "The doctor can go through that with you at your visit."
        rec = record([turn("", "Hello."), turn("How much is a cleaning?", deflect, llm_ok=False)])
        result = metrics.evaluate(rec)
        self.assertTrue(result["findings"][0]["llm_degraded"])
        self.assertEqual(result["counts"], {})
        self.assertTrue(result["llm_degraded_call"])


class SimCallerTests(unittest.TestCase):
    def caller(self, seed=7, **profile):
        p = dict(goal="book", name="Rahul Verma", phone="9845012345", service="Teeth Cleaning",
                 service_phrase="a cleaning", day=date(2026, 10, 5), time=time(10, 0))
        p.update(profile)
        return sim_caller.SimCaller(sim_caller.Profile(**p), random.Random(seed), today=date(2026, 10, 1))

    def test_answers_what_emma_asks(self):
        c = self.caller()
        self.assertIn("cleaning", c.respond("Hello, Pearl Dental. How can I help you?").text.lower())
        self.assertIn("Rahul Verma", c.respond("May I have your full name, please?").text)
        said = c.respond("And what's the best mobile number to reach you on?").text
        self.assertEqual(fake_nlu.phone_from(said)[0], "9845012345")
        day = c.respond("What day would suit you?").text
        self.assertEqual(fake_nlu.read(day, expect="date", today=date(2026, 10, 1)).date, date(2026, 10, 5))
        self.assertTrue(lines.is_clear_yes(c.respond(SUMMARY.replace("Priya Sharma", "Rahul Verma")).text))

    def test_corrects_a_wrong_read_back(self):
        c = self.caller()
        c.respond("Hello, Pearl Dental. How can I help you?")
        self.assertTrue(c.respond("So that's 9 8 4 5 0, 1 2 3 4 6, right?").text.startswith("No"))
        wrong_day = SUMMARY.replace("Monday the 05 October", "Tuesday the 06 October")
        self.assertTrue(c.respond(wrong_day).text.startswith("No"))

    def test_same_seed_same_call(self):
        emma = ["Hello, Pearl Dental. How can I help you?", "May I have your full name, please?",
                "And what's the best mobile number to reach you on?", "What day would suit you?",
                "What time works best for you?"]
        for seed in (1, 2, 3):
            a = self.caller(seed=seed, style="indian")
            b = self.caller(seed=seed, style="indian")
            self.assertEqual([a.respond(x).text for x in emma], [b.respond(x).text for x in emma])

    def test_hangs_up_on_goodbye_and_at_the_turn_limit(self):
        c = self.caller()
        self.assertIsNone(c.respond("Great, we'll see you then. Take care, bye!"))
        self.assertEqual(c.ended_by, "emma_closed")
        c = self.caller()
        c.max_turns = 3
        for _ in range(3):
            c.respond("Sorry, could you say that again?")
        self.assertIsNone(c.respond("Sorry, could you say that again?"))
        self.assertEqual(c.ended_by, "max_turns")

    def test_gives_up_on_a_loop(self):
        c = self.caller()
        c.respond("Hello, Pearl Dental. How can I help you?")
        last = None
        for _ in range(5):
            last = c.respond("What other time would work for you?")
            if last.final:
                break
        self.assertTrue(last.final)
        self.assertTrue(c.gave_up)

    def test_disruptions_are_seeded_and_fire(self):
        rng_a, rng_b = random.Random(5), random.Random(5)
        self.assertEqual(sim_caller.plan_disruptions(rng_a), sim_caller.plan_disruptions(rng_b))
        c = sim_caller.SimCaller(self.caller().p, random.Random(1), disruptions=["silence"], today=date(2026, 10, 1))
        texts = [c.respond("May I have your full name, please?").text for _ in range(10)]
        self.assertIn("", texts)

    def test_offers_are_not_read_from_facts(self):
        hours = ("We're open Monday to Saturday, 7 in the morning to 9 at night. "
                 "Sorry, I didn't catch the time. What time works best for you?")
        self.assertEqual(sim_caller._offered_times(hours), [])
        self.assertEqual(sim_caller._offered_times("I could do 4:30 PM. Would that work?"), [time(16, 30)])

    def test_profiles_vary(self):
        seen = set()
        for i in range(200):
            p = sim_caller.random_profile(random.Random(i), today=date(2026, 10, 1))
            seen.add((p.name, p.service, p.day, p.time, p.style))
            self.assertNotEqual(p.day.weekday(), 6, "never a Sunday")
        self.assertGreater(len(seen), 190)

    def test_moved_day_differs_and_is_open(self):
        monday = date(2026, 10, 5)
        self.assertEqual(sim_caller.moved_day(monday, -1, date(2026, 10, 1)), date(2026, 10, 3))
        self.assertNotEqual(sim_caller.moved_day(monday, 6, date(2026, 10, 1)).weekday(), 6)


class WorldTests(unittest.TestCase):
    def test_worlds_are_isolated_and_restore_everything(self):
        before = (config.DB_PATH, config.DEMO_SEED_ON_EMPTY, clock._frozen)
        with world_mod.World() as w:
            self.assertEqual(clock.now().replace(tzinfo=None), NOW)
            catalog = w.catalog()
            self.assertEqual(len(catalog["branches"]), 4)
            self.assertEqual(len(catalog["doctors"]), 8)
            appts = w.appointments()
            self.assertGreater(len(appts), 30)

            def cancel_all(conn):
                conn.execute("UPDATE appointments SET status = 'cancelled'")
            w.run(cancel_all)
        with world_mod.World() as w2:
            self.assertTrue(all(a["status"] == "booked" for a in w2.appointments()), "a fresh world each time")
        self.assertEqual((config.DB_PATH, config.DEMO_SEED_ON_EMPTY, clock._frozen), before)

    def test_card_is_a_real_future_appointment(self):
        with world_mod.World() as w:
            card = w.pick_card(index=0)
            self.assertIsNotNone(card)
            match = [a for a in w.appointments() if a["id"] == card["appointment_id"]]
            self.assertEqual(len(match), 1)
            self.assertEqual(match[0]["phone"][-10:], card["phone"])
            self.assertGreater(datetime.fromisoformat(match[0]["start"]), NOW)


class RunnerTests(unittest.TestCase):
    def test_sim_calls_are_reproducible(self):
        a = runner.run_sim_call(3, seed=11)
        b = runner.run_sim_call(3, seed=11)
        self.assertEqual([t["caller"] for t in a["turns"]], [t["caller"] for t in b["turns"]])
        self.assertEqual([t["emma"] for t in a["turns"]], [t["emma"] for t in b["turns"]])
        self.assertIn("metrics", a)
        self.assertGreater(a["caller_turns"], 0)

    def test_suite_writes_records_summary_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, summary = runner.run_suite("sim", n=3, seed=4, out_root=tmp, progress=None)
            self.assertEqual(len(os.listdir(os.path.join(run_dir, "conversations"))), 3)
            for name in ("summary.json", "report.md", "transcripts.md"):
                self.assertTrue(os.path.exists(os.path.join(run_dir, name)), name)
            self.assertEqual([r["metric"] for r in summary["metrics"]], [t["metric"] for t in metrics.TARGETS])
            self.assertTrue(summary["indicative_only"])
            with open(os.path.join(run_dir, "report.md"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("## Metrics vs targets", text)
            self.assertIn("## Failure catalogue", text)
            records, meta = report.load_run(run_dir)
            self.assertEqual(len(records), 3)
            self.assertEqual(meta["suite"], "sim")

    def test_a_scenario_reports_its_checks(self):
        rec = runner.run_scenario("reg_price_root_canal")
        self.assertTrue(rec["passed"], rec["checks"])
        self.assertEqual(rec["status"], "pass")
        self.assertTrue(all("ok" in c for c in rec["checks"]))

    def test_silence_runs_the_call_session_ladder(self):
        rec = runner.run_scenario("cat_silence")
        silent = [t for t in rec["turns"] if t.get("silence_step")]
        self.assertEqual([t["silence_step"] for t in silent], [1, 2])
        self.assertIn("still", silent[0]["emma"].lower())


class ConverseTests(unittest.TestCase):
    """tools/converse.py keeps a call going across separate invocations (state on disk)."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("converse", Path(config.BASE_DIR) / "tools" / "converse.py")
        self.converse = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.converse)
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(self.converse, "CONVERSE_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.converse.main(list(argv)), 0)
        return out.getvalue()

    def test_new_say_silence_end(self):
        started = self.run_cli("new", "--offline", "--goal", "book")
        conv_id = started.splitlines()[0].split(": ", 1)[1].split()[0]
        self.assertTrue(started.splitlines()[1].startswith("Emma: "))
        reply = self.run_cli("say", conv_id, "Hi, I'd like to book a cleaning.")
        self.assertIn("name", reply.lower())
        # Any of the call session's "are you still there?" variants (the pick is seeded per conversation id).
        still_there = engine_adapter._silence_lines()[0]
        nudge = self.run_cli("silence", conv_id)
        self.assertTrue(any(line in nudge for line in still_there), nudge)
        self.run_cli("say", conv_id, "Priya Sharma.")
        result = json.loads(self.run_cli("end", conv_id))
        self.assertEqual(result["id"], conv_id)
        self.assertEqual(len(result["transcript"]), 4)
        self.assertTrue(result["transcript"][2]["silence"])
        self.assertEqual(result["outcome"]["kind"], "none")
        self.assertFalse(result["metrics"]["goal_met"])
        self.assertTrue(os.path.exists(result["transcript_path"]))

    def test_card_is_a_seeded_appointment(self):
        started = self.run_cli("new", "--offline", "--card", "existing_appointment", "--seed", "3")
        self.assertIn("Caller card", started)
        self.assertIn("patient:", started)
        conv_id = started.splitlines()[0].split(": ", 1)[1].split()[0]
        with open(self.converse.CONVERSE_DIR / conv_id / "state.json", encoding="utf-8") as fh:
            card = json.load(fh)["card"]
        self.assertIn(card["patient"], started)
        self.assertIn(card["phone_spoken"], started)


if __name__ == "__main__":
    unittest.main()
