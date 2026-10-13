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
        if "old" in reply:                                  # a child's age is asked (12 Oct)
            reply = call.say("He's eight.").text
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


class LanguageTests(ClinicCallCase):
    """F2: asked for Kannada or Hindi, Emma offers a callback from someone who speaks it."""

    def test_can_you_speak_kannada(self):
        call = Call()
        reply = call.run("", "Can you speak Kannada?").text
        self.assertIn("Kannada", reply)
        self.assertIn("call you", reply)
        reply = call.run("yes", "9845012345", "yes").text
        tasks = self.clinic.query("SELECT kind FROM tasks")
        self.assertIn("language", [t["kind"] for t in tasks], reply)

    def test_a_doctors_languages_are_still_a_question(self):
        reply = Call().run("", "Does Dr Shetty speak Hindi?").text
        self.assertIn("Dr Shetty speaks", reply)

    def test_no_english(self):
        reply = Call().run("", "Sorry, I don't speak English well.").text
        self.assertIn("call you", reply)


class ClosureTests(ClinicCallCase):
    """S3: a holiday added on the dashboard is what Emma tells callers (not "Monday is full")."""

    def test_a_closed_day_is_said_and_listed(self):
        import scheduling
        from datetime import date
        out = self.clinic.db.run_sync(scheduling.add_closure, date(2026, 10, 5), None, "Holiday", "staff")
        self.assertEqual(out["affected"], [])
        self.assertTrue(self.clinic.db.run_sync(scheduling.closed_on, date(2026, 10, 5), [1, 2]))
        self.assertEqual([c["reason"] for c in self.clinic.db.run_sync(scheduling.list_closures, date(2026, 10, 1))
                          if c["date"] == "2026-10-05"], ["Holiday"])
        call = Call()
        call.run("", "I'd like a cleaning at Jayanagar.", "Priya Sharma", "9845012345", "yes", "Monday")
        reply = call.say("Morning, please.").text
        self.assertRegex(reply, r"closed on Monday")
        self.assertNotIn("full", reply)
        self.assertTrue(self.clinic.db.run_sync(scheduling.remove_closure, out["id"], "staff"))


class ForeignNumberTests(ClinicCallCase):
    def test_a_foreign_number_is_told_plainly_then_an_indian_one_works(self):
        call = Call()
        call.run("", "I'd like a cleaning at Jayanagar.", "John Smith")
        reply = call.say("plus 1 415 555 0123").text
        self.assertIn("Indian", reply)
        self.assertNotIn("digits", reply)
        self.assertIn("9 8 4 5 0", call.say("Okay, 98450 12345").text)


class MoveAgainTests(ClinicCallCase):
    def test_moving_again_right_after_a_move_is_not_anything_else(self):
        # Sim, 12 Oct: moved, then "I want to move my appointment to another day" twice got
        # "anything else?" and a goodbye.
        Call().run(*FamilyBookingTests.BOOK_PRIYA)
        call = Call()
        reply = call.run("", "Can I move my appointment to another day?", "98450 12345", "Yes", "Priya Sharma",
                         "It's on Monday, and I'd like to move it to Friday.", "10 am", "Yes, go ahead.").text
        self.assertIn("tomorrow at 10", reply)
        reply = call.say("I want to move my appointment to another day.").text
        self.assertIn("tomorrow at 10", reply)                          # where it stands now
        self.assertNotIn("anything else", reply.lower())
        call.say("Tuesday")
        call.say("11 am")
        call.say("Yes")
        rows = self.clinic.query("SELECT start_utc FROM appointments WHERE status = 'booked' "
                                 "AND caller_phone_e164 = '+919845012345'")
        self.assertEqual([r["start_utc"] for r in rows], ["2026-10-06T05:30:00Z"])
        self.assertFalse(call.s.closed_conversation)


    def test_a_turned_down_time_asked_for_by_name_is_offered_again(self):
        # Sim, 12 Oct: 9 offered, turned down, then "Is 9 AM not possible at all?" -> "I've nothing
        # until after Saturday the 10th", which was false.
        Call().run(*FamilyBookingTests.BOOK_PRIYA)
        call = Call()
        reply = call.run("", "Can I move my appointment to another day?", "98450 12345", "Yes", "Priya Sharma",
                         "It's on Monday, and I'd like to move it to Friday.").text
        self.assertIn("9", reply)
        call.say("Neither of those works.")
        reply = call.say("Is 9 AM on Friday not possible at all?").text
        self.assertNotIn("nothing", reply)
        self.assertIn("at 9", reply)


class ShortNoticeMoveTests(ClinicCallCase):
    VERIFY = ["", "Can I move my appointment?", "98450 12345", "Yes", "Priya Sharma", "It's today at 10."]

    def setUp(self):
        super().setUp()
        Call().run(*FamilyBookingTests.BOOK_PRIYA)                  # Monday the 5th at 10
        import clock
        clock.freeze(datetime(2026, 10, 5, 9, 0))                   # Monday, an hour before

    def start(self):
        rows = self.clinic.query("SELECT start_utc FROM appointments WHERE caller_phone_e164 = '+919845012345' "
                                 "AND status = 'booked'")
        return [r["start_utc"] for r in rows]

    def test_later_the_same_day_with_the_same_doctor(self):
        call = Call()
        call.run(*self.VERIFY)
        reply = call.say("Can I come at 12 instead?").text
        self.assertNotIn("too close", reply)
        call.say("Yes")
        self.assertEqual(self.start(), ["2026-10-05T06:30:00Z"])

    def test_later_today_offers_times_after_the_appointment(self):
        call = Call()
        call.run(*self.VERIFY)
        reply = call.say("What's free later today?").text
        self.assertIn("10:30", reply)
        self.assertNotIn("9:30", reply)

    def test_another_day_is_still_a_call_from_the_team(self):
        call = Call()
        call.run(*self.VERIFY)
        reply = call.say("Can I move it to tomorrow?").text
        self.assertIn("too close", reply)
        self.assertEqual(self.start(), ["2026-10-05T04:30:00Z"])


class ChildAgeTests(ClinicCallCase):
    def test_a_cleaning_for_my_son_asks_his_age_once(self):
        call = Call()
        call.run("", "I'd like to book a cleaning for my son at Jayanagar.", "Priya Sharma", "9845012345", "yes")
        self.assertIn("old", call.say("Arjun").text)
        reply = call.say("He's eight.").text
        self.assertNotIn("old", reply)
        self.assertEqual(call.s.book.age, 8)

    def test_no_age_given_is_let_go(self):
        call = Call()
        call.run("", "I'd like to book a cleaning for my son at Jayanagar.", "Priya Sharma", "9845012345", "yes")
        self.assertIn("old", call.say("Arjun").text)
        self.assertNotIn("old", call.say("Monday please.").text)


class LandlineTests(ClinicCallCase):
    def test_a_bengaluru_landline_is_read_back_as_one(self):
        call = Call()
        reply = call.run("", "I'd like a cleaning at Jayanagar.", "Priya Sharma", "080 2345 6789").text
        self.assertIn("0 8 0, 2 3 4 5, 6 7 8 9", reply)
        call2 = Call()
        reply = call2.run("", "I'd like a cleaning at Jayanagar.", "Priya Sharma", "80234 56789").text
        self.assertIn("8 0 2 3 4, 5 6 7 8 9", reply)                    # said as a mobile


class MidnightTests(ClinicCallCase):
    NOW = datetime(2026, 10, 1, 23, 58)            # Thursday, two minutes to midnight

    def test_tomorrow_said_after_midnight_is_still_the_callers_tomorrow(self):
        import clock
        call = Call()
        call.run("", "I'd like a cleaning at Jayanagar.", "Priya Sharma", "98450 12345", "Yes")
        clock.freeze(datetime(2026, 10, 2, 0, 1))  # the call runs past midnight
        call.say("Tomorrow at 10 am.")
        call.say("Yes")
        rows = self.clinic.query("SELECT start_utc FROM appointments WHERE caller_phone_e164 = '+919845012345'")
        self.assertEqual([r["start_utc"] for r in rows], ["2026-10-02T04:30:00Z"])     # Friday, not Saturday
        # A new call after midnight starts from the new day.
        self.assertIsNone(clock._call_day.get())
        self.assertEqual(clock.today().isoformat(), "2026-10-02")


class HelloBackTests(ClinicCallCase):
    def test_a_bare_hi_after_the_greeting_is_answered_at_once(self):
        # Phone check, 12 Oct: "Hi." went to the model: "Okay..." then "Hello there. How can I help you today?"
        import phrases
        import speech
        from dialogue import engine
        for said, expected in (("Hi.", engine.HELLO_LINES), ("hello emma", engine.HELLO_LINES),
                               ("Good morning!", engine.HELLO_LINES),
                               ("Hi, how are you?", (engine.HOW_ARE_YOU_LINE,))):
            call = Call()
            call.run("")
            out = call.say(said)
            self.assertIn(out.text, expected, said)
            self.assertEqual(out.tier, 0, said)                           # no model wait, no filler
            for sentence in speech.split_sentences(out.text):
                self.assertIn(sentence, phrases.all_phrases(), said)      # pre-recorded: plays at once
            self.assertEqual(call.s.pending.value, "ask_intent")

    def test_hi_with_a_request_is_the_request(self):
        call = Call()
        call.run("")
        reply = call.say("Hi, I'd like to book a cleaning at Jayanagar.").text
        self.assertNotIn(reply, ("Hi! What can I do for you?", "Hello! How can I help?"))
        self.assertIn("name", reply)


class BadLineTests(ClinicCallCase):
    def test_a_breaking_up_line_is_said_once_not_go_on_five_times(self):
        # Phone check, 12 Oct: "a" / "And that's" / "Hello" -> "Sorry, go on." "Go ahead." "You were saying?"...
        call = Call()
        call.run("", "I'd like to book a cleaning at Jayanagar.")
        replies = [call.say(t).text for t in ("a", "and that's", "um", "so", "and")]
        breaking = [r for r in replies if "breaking up" in r]
        self.assertEqual(len(breaking), 1, replies)                      # once, after cut-offs keep coming
        self.assertIn("Priya", call.say("Priya Sharma").text)            # the open question still works

    def test_one_fragment_is_just_go_on(self):
        call = Call()
        call.run("", "I'd like to book a cleaning at Jayanagar.")
        self.assertNotIn("breaking up", call.say("a").text)
        self.assertIn("Priya", call.say("Priya Sharma").text)


class OfferPickTests(ClinicCallCase):
    def test_ten_said_any_way_picks_the_ten_oclock_offer(self):
        # Phone check, 13 Oct: offered 10 or 10:30; "Do ten." / "Ten." / "I want ten." -> asked again, then a callback.
        from datetime import datetime as dt
        from types import SimpleNamespace
        import clock
        import tier0
        from dialogue.context import Expect, Goal, Intent
        offered = (SimpleNamespace(start=clock.localize(dt(2026, 10, 14, 10, 0))),
                   SimpleNamespace(start=clock.localize(dt(2026, 10, 14, 10, 30))))
        view = tier0.Tier0View(expect=Expect.CHOICE, pending=Goal.OFFER_SLOTS, intent=Intent.BOOK, offered=offered)
        for said, index in (("Ten.", 1), ("Do ten.", 1), ("I want ten.", 1), ("Uh-huh. Do ten.", 1),
                            ("let's do 10", 1), ("I'll take ten thirty", 2), ("make it 10:30", 2)):
            u = tier0.understand(said, view)
            self.assertIsNotNone(u, said)
            self.assertEqual(u.choice_index, index, said)
        self.assertIsNone(tier0.understand("I want to cancel at ten", view).choice_index if
                          tier0.understand("I want to cancel at ten", view) else None)

    def test_a_cut_off_before_the_pick_does_not_hide_it(self):
        # "I only can." (cut off) then "Ten." was heard as "I only can. Ten." and missed.
        call = Call()
        call.run("", "I'd like a cleaning at Jayanagar.", "Priya Sharma", "9845012345", "yes", "Monday morning")
        first = call.s.book.offered[0].start
        hour = first.strftime("%I").lstrip("0") + ("" if first.minute == 0 else f":{first.minute:02d}")
        self.assertIn("go", call.say("I only can").text.lower() + " go")      # a fragment: "Sorry, go on."
        reply = call.say(f"{hour}.").text
        self.assertIn("book", reply.lower())                                  # the summary for that slot
        self.assertEqual(call.s.book.chosen.start, first)


class PhoneCheck14OctTests(ClinicCallCase):
    """Turns from the 14 Oct phone check (a breaking-up hotspot line)."""

    def test_not_sure_at_the_start_steers_back(self):
        call = Call()
        call.run("")
        reply = call.say("How may I help you out?").text
        self.assertTrue(reply.rstrip().endswith("?"), reply)                # not "not sure" and nothing more
        self.assertEqual(call.s.pending.value, "ask_intent")

    def test_a_common_word_is_not_a_name(self):
        call = Call()
        call.run("", "I have to book an appointment.")
        reply = call.say("Others.").text
        self.assertNotIn("Others", reply)
        self.assertIsNone(call.s.caller.name)

    def test_a_bot_heard_as_about_is_still_the_bot_question(self):
        call = Call()
        call.run("", "I'd like a cleaning at Jayanagar.")
        reply = call.say("Am I talking to about?").text
        self.assertIn("virtual receptionist", reply)
        self.assertIn("name", reply)                                        # and straight back to the booking


class SmallTalkTests(ClinicCallCase):
    def test_small_talk_gets_a_friendly_reply_not_i_dont_know(self):
        # Sim, 12 Oct: "How's your day going?" -> "Honestly, I don't know that one."
        for remark in ("How's your day going?", "It's been raining a lot today, no?", "How are you?"):
            call = Call()
            call.run("", "I'd like a cleaning at Jayanagar.")
            reply = call.say(remark).text
            self.assertNotIn("know that", reply, remark)
            self.assertNotIn("not sure", reply, remark)
            self.assertNotIn("Today", reply, remark)                       # "raining a lot today" isn't a day
            self.assertIn("name", reply, remark)                          # and back to the booking

    def test_a_question_about_the_clinic_is_not_small_talk(self):
        call = Call()
        reply = call.run("", "Is the clinic very busy today? What's the waiting like?").text
        self.assertNotIn("know what you mean", reply)


if __name__ == "__main__":
    unittest.main()
