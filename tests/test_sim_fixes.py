"""
Fixes from the 6 Oct simulation triage (200-call sims, model down and faked):
calls that looped or dead-ended because Emma misread a turn or kept offering
the same slot. Each test is one of those calls, reduced to its cause.
"""

import asyncio
import unittest
from datetime import date, datetime, time, timedelta

import prompts
import scheduling
import tier0
from dateparse import DateConstraint, TimeConstraint
from dialogue import book, context, engine
from dialogue.context import Expect, Goal, Intent, OfferedSlot, Tier0View, Understanding
from dialogue.match import looks_like_question
from dialogue.testing import DemoClinic

NOW = datetime(2026, 10, 1, 10, 0)          # Thursday; Monday 5 Oct is a normal working day
MONDAY = date(2026, 10, 5)


def _catalog():
    async def go():
        return (await engine.build_runtime(context.new_context("x"))).catalog
    return asyncio.run(go())


def _understand(text, pending, expect, intent=Intent.BOOK):
    view = Tier0View(expect=expect, pending=pending, intent=intent, catalog=_catalog())
    return tier0.understand(text, view, lenient=True)


class ReadingTests(unittest.TestCase):
    def test_asking_to_book_is_not_a_clinic_question(self):
        for said in ("Good morning, I need clear aligners, is it possible to get an appointment?",
                     "Would it be possible to come in on Monday?", "Can I come in tomorrow?"):
            self.assertFalse(looks_like_question(said), said)
        self.assertTrue(looks_like_question("Is it possible to get the price for braces?"))

    def test_hello_with_a_question_mark_is_not_a_question(self):
        self.assertFalse(looks_like_question("Hello? Yes, I'm still here."))
        self.assertTrue(looks_like_question("Hello? What are your timings?"))

    def test_small_talk_about_today_is_not_a_booking_day(self):
        with DemoClinic(now=NOW):
            chat = _understand("Hope you're not too busy today.", Goal.OFFER_SLOTS, Expect.CHOICE)
            real = _understand("Can I come today instead?", Goal.OFFER_SLOTS, Expect.CHOICE)
        self.assertIsNone(chat.date_phrase)
        self.assertIsNotNone(real.date_phrase)

    def test_moving_an_existing_appointment_is_a_reschedule(self):
        with DemoClinic(now=NOW):
            for said in ("No, I want to move my existing appointment.", "I'd like to change my current booking."):
                u = _understand(said, Goal.GREET, Expect.OPEN, Intent.NONE)
                self.assertEqual(u.intent, Intent.RESCHEDULE, said)

    def test_what_do_you_suggest_means_the_earliest(self):
        with DemoClinic(now=NOW):
            u = _understand("I don't know, what do you suggest?", Goal.ASK_WHEN, Expect.DATE)
            other = _understand("What do you suggest for sensitive teeth?", Goal.ASK_NAME, Expect.NAME)
        self.assertEqual(u.date_phrase, "earliest")
        self.assertIsNone(other.date_phrase)


class WordingTests(unittest.TestCase):
    def test_no_doubled_at_in_corrections(self):
        for variant in prompts.VARIANTS["correction.ack"]:
            self.assertNotIn("at at", variant.format(value="at 5"))

    def test_the_doctor_comes_last_in_two_slot_offers(self):
        # {doctor} may be "Dr Reddy for the first and Dr Ali for the second".
        for variant in prompts.VARIANTS["offer.two"]:
            self.assertNotIn("{doctor} has", variant)

    def test_two_time_offers_never_ask_shall_i_take_it(self):
        for line in ("offer.other_branch.two", "offer.other_branch.two.rephrase"):
            for variant in prompts.VARIANTS[line]:
                self.assertNotIn("take it", variant.lower(), line)


def _slot(start, branch="Indiranagar", doctor="Dr Menon"):
    return OfferedSlot(1, doctor, 2, branch, 3, "Teeth Cleaning", start, start + timedelta(minutes=30),
                       hold_id=None, spoken=start.strftime("%H:%M"))


class OfferTests(unittest.TestCase):
    def _ctx(self, starts):
        ctx = context.new_context("t")
        ctx.intent = Intent.BOOK
        ctx.pending = Goal.OFFER_SLOTS
        ctx.book.offered = [_slot(s) for s in starts]
        return ctx

    def test_yes_to_two_times_asks_which_then_takes_the_first(self):
        tz = scheduling.clock.localize(datetime(2026, 10, 5, 12, 0)).tzinfo
        ctx = self._ctx([datetime(2026, 10, 5, 12, 0, tzinfo=tz), datetime(2026, 10, 5, 12, 30, tzinfo=tz)])
        book._yes_to_two(ctx, Understanding(), "yes")
        self.assertIsNone(ctx.book.chosen)
        self.assertEqual(book._offer_plan(ctx.book).line, "offer.which")
        book._yes_to_two(ctx, Understanding(), "yes")
        self.assertEqual(ctx.book.chosen.start.hour, 12)
        self.assertEqual(ctx.book.chosen.start.minute, 0)

    def test_a_named_time_is_not_a_plain_yes(self):
        tz = scheduling.clock.localize(datetime(2026, 10, 5, 12, 0)).tzinfo
        ctx = self._ctx([datetime(2026, 10, 5, 12, 0, tzinfo=tz), datetime(2026, 10, 5, 12, 30, tzinfo=tz)])
        book._yes_to_two(ctx, Understanding(time_phrase="12:30"), "yes")
        self.assertIsNone(ctx.book.chosen)
        self.assertNotEqual(book._offer_plan(ctx.book).line, "offer.which")

    def test_same_day_times_are_said_in_clock_order(self):
        tz = scheduling.clock.localize(datetime(2026, 10, 5, 17, 0)).tzinfo
        later, earlier = datetime(2026, 10, 5, 17, 30, tzinfo=tz), datetime(2026, 10, 5, 17, 0, tzinfo=tz)
        ordered = book._in_clock_order([_slot(later), _slot(earlier)])
        self.assertEqual([s.start for s in ordered], [earlier, later])
        other_day = datetime(2026, 10, 6, 9, 0, tzinfo=tz)
        self.assertEqual([s.start for s in book._in_clock_order([_slot(later), _slot(other_day)])],
                         [later, other_day])                       # different days keep their order


class SearchTests(unittest.TestCase):
    """scheduling.suggest on the seeded clinic (no appointments)."""

    def setUp(self):
        self.clinic = DemoClinic(now=NOW)
        self.clinic.__enter__()

    def tearDown(self):
        self.clinic.__exit__(None, None, None)

    def run_db(self, fn):
        return self.clinic.db.run_sync(fn)

    def branch_id(self, name):
        return self.run_db(lambda c: c.execute("SELECT id FROM branches WHERE name = ?", (name,)).fetchone()["id"])

    def book_slot(self, slot, n):
        res = self.run_db(lambda c: scheduling.book(
            c, service=slot.service_id, doctor_id=slot.doctor_id, start=slot.start, patient_name=f"Filler {n}",
            phone=f"+9198450{n:05d}", idem_key=f"fill-{n}"))
        self.assertTrue(res.ok, res.code)

    def test_a_time_the_patient_is_already_booked_at_is_never_offered(self):
        day, at = DateConstraint(MONDAY, MONDAY), TimeConstraint("exact", time(11, 0))
        first = self.run_db(lambda c: scheduling.suggest(c, service="Teeth Cleaning", date_c=day, time_c=at))
        self.assertEqual(first.kind, "exact")
        slot = first.slots[0]
        res = self.run_db(lambda c: scheduling.book(c, service=slot.service_id, doctor_id=slot.doctor_id,
                                                     start=slot.start, patient_name="Neha Kapoor",
                                                     phone="+919845022222", idem_key="own"))
        self.assertTrue(res.ok)
        patient = ("+919845022222", scheduling.norm_name("Neha Kapoor"))
        found = self.run_db(lambda c: scheduling.suggest(c, service="Teeth Cleaning", date_c=day, time_c=at,
                                                          patient=patient))
        self.assertNotEqual(found.kind, "exact")
        for s in found.slots:
            self.assertFalse(s.start < slot.end and s.end > slot.start, s.start)
        anyone = self.run_db(lambda c: scheduling.suggest(c, service="Teeth Cleaning", date_c=day, time_c=at))
        self.assertEqual(anyone.kind, "exact")              # someone else can still have 11

    def test_asking_again_for_a_taken_time_offers_it_on_a_later_day(self):
        branch = [self.branch_id("Indiranagar")]
        day, at = DateConstraint(MONDAY, MONDAY), TimeConstraint("exact", time(11, 0))
        for n in range(1, 10):                               # fill 11:00 at Indiranagar on Monday
            found = self.run_db(lambda c: scheduling.suggest(c, service="Teeth Cleaning", date_c=day, time_c=at,
                                                              branch_ids=branch))
            if found.kind != "exact":
                break
            self.book_slot(found.slots[0], n)
        plain = self.run_db(lambda c: scheduling.suggest(c, service="Teeth Cleaning", date_c=day, time_c=at,
                                                          branch_ids=branch))
        self.assertTrue(all(s.start.date() == MONDAY for s in plain.slots))
        again = self.run_db(lambda c: scheduling.suggest(c, service="Teeth Cleaning", date_c=day, time_c=at,
                                                          branch_ids=branch, same_time_later=True))
        self.assertEqual(again.slots[0].start.date(), MONDAY)
        self.assertGreater(again.slots[1].start.date(), MONDAY)
        self.assertEqual(again.slots[1].start.time(), time(11, 0))


if __name__ == "__main__":
    unittest.main()
