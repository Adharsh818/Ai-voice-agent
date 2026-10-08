"""
BOOK workflow (dialogue/book.py) on the real DEMO catalog: branch-aware
booking, doctor preferences, family bookings, the max-3 and duplicate
checks, search and holds, the summary gate and the commit.

Calls are driven turn by turn on a hand-built CallContext inside DemoClinic,
the way the engine will drive them: a small stand-in for apply.py fills the
caller's identity (name, phone read-back), then book.advance() runs and
book.next_goal() decides the reply. Every planned line and notice is
rendered through prompts.render, so a param a line can't use fails here.
"""

import asyncio
import json
import pickle
import unittest
from datetime import datetime, time, timedelta

import clock
import facts
import phones
import prompts
import scheduling
from dialogue import book
from dialogue.context import (Emergency, FieldState, Goal, Intent, Understanding, new_context)
from dialogue.runtime import Runtime
from dialogue.testing import DemoClinic

PHONE = "9876543210"
E164 = "+919876543210"
# Thursday 1 Oct 2026, 10:00: tomorrow is Friday the 2nd, Monday is the 5th.
NOW = datetime(2026, 10, 1, 10, 0)


def demo_catalog(clinic) -> facts.Catalog:
    """facts.Catalog read from the DemoClinic database (E3's load_catalog builds the same thing)."""
    branches = clinic.query("SELECT id, name, area FROM branches WHERE active = 1 ORDER BY id")
    services = clinic.query("SELECT * FROM services WHERE active = 1 ORDER BY id")
    doctors = clinic.query("SELECT d.*, b.name AS branch FROM doctors d JOIN branches b ON b.id = d.branch_id "
                           "WHERE d.active = 1 ORDER BY d.id")
    links = clinic.query("SELECT ds.doctor_id, s.name AS service FROM doctor_services ds "
                         "JOIN services s ON s.id = ds.service_id")
    does = {}
    for row in links:
        does.setdefault(row["doctor_id"], []).append(row["service"])
    branch_order = [b["name"] for b in branches]

    def offering(service):
        names = {d["branch"] for d in doctors if service in does.get(d["id"], [])}
        return tuple(n for n in branch_order if n in names)

    return facts.Catalog(
        branches=tuple(facts.Branch(b["id"], b["name"], b["area"],
                                    services=tuple(s["name"] for s in services if b["name"] in offering(s["name"])))
                       for b in branches),
        doctors=tuple(facts.Doctor(d["id"], d["name"], d["spoken_name"], d["gender"], d["branch"], d["branch_id"],
                                   tuple(does.get(d["id"], ()))) for d in doctors),
        services=tuple(facts.Service(s["id"], s["name"], prompts.speak_service(s["name"]), s["duration_min"],
                                     bool(s["is_consultation"]), tuple(json.loads(s["aliases_json"] or "[]")),
                                     offering(s["name"])) for s in services),
    )


def U(**kw) -> Understanding:
    return Understanding(**kw)


class Call:
    """One simulated call: the engine's step 6-7 for BOOK, with identity applied by a stand-in for apply.py."""

    def __init__(self, clinic, catalog, call_id="call-1"):
        self.clinic = clinic
        self.ctx = new_context(call_id)
        self.ctx.intent = Intent.BOOK
        self.progress = []
        self.rt = Runtime(call_id=call_id, db=clinic.db, catalog=catalog, kb=None,
                          progress=lambda event, **data: self.progress.append((event, data)))
        self.goals = []
        self.lines = []

    # -- the stand-in for apply.py's identity part (E1 owns the real one) ----

    def _identity(self, u: Understanding, confirmation):
        ctx, c = self.ctx, self.ctx.caller
        if u.intent is not None:
            ctx.intent = u.intent
        if u.name and ctx.pending != Goal.ASK_PATIENT:
            c.name, c.name_state = u.name, FieldState.HEARD
        if u.phone_digits:
            c.phone_buffer += u.phone_digits
            if len(c.phone_buffer) >= 10:
                c.phone_e164, c.phone_state, c.phone_buffer = phones.to_e164(c.phone_buffer), FieldState.PENDING, ""
        elif ctx.pending == Goal.CONFIRM_PHONE and confirmation == "yes":
            c.phone_state = FieldState.CONFIRMED
            ctx.book.touch()

    def given(self, name="Priya", phone=E164):
        """A caller whose name and number are already known and confirmed."""
        c = self.ctx.caller
        c.name, c.name_state = name, FieldState.HEARD
        c.phone_e164, c.phone_state = phone, FieldState.CONFIRMED
        return self

    def turn(self, u: Understanding = None, confirmation=None, heard=True):
        return asyncio.run(self.aturn(u, confirmation, heard))

    async def aturn(self, u=None, confirmation=None, heard=True):
        u = u or Understanding()
        if confirmation is None:
            confirmation = u.confirmation
        ctx = self.ctx
        ctx.turn += 1
        ctx.last_reply_heard = heard
        self.progress.clear()
        self.rt.events = []
        self._identity(u, confirmation)
        result = await book.advance(ctx, u, confirmation, self.rt)
        plan = book.next_goal(ctx)
        self.notices = [prompts.render(n.line, ctx.prompts, n.params) for n in result.notices]
        self.text = prompts.render(plan.line, ctx.prompts, plan.params) if plan else ""
        self.goals.append(plan.goal if plan else None)
        self.lines.append(" ".join(self.notices + [self.text]))
        ctx.pending = plan.goal if plan else None
        ctx.pending_params = dict(plan.params) if plan else {}
        self.result, self.plan = result, plan
        return result, plan

    # -- helpers --------------------------------------------------------------

    @property
    def goal(self):
        return self.plan.goal if self.plan else None

    def notice_ids(self):
        return [n.line for n in self.result.notices]

    def holds(self):
        return self.clinic.query("SELECT * FROM slot_holds WHERE call_id = ?", self.rt.call_id)

    def checked(self) -> bool:
        return any(event == "before_action" and data.get("phrase") for event, data in self.progress)


class BookTestCase(unittest.TestCase):
    def setUp(self):
        self.clinic = DemoClinic(now=NOW)
        self.clinic.__enter__()
        self.catalog = demo_catalog(self.clinic)

    def tearDown(self):
        self.clinic.__exit__(None, None, None)

    def call(self, **kw) -> Call:
        return Call(self.clinic, self.catalog, **kw)

    def doctor(self, spoken):
        return self.catalog.doctor(spoken)

    def book_existing(self, patient, start, doctor="Dr Rao", service="General Check-up", phone=E164, key=None):
        res = self.clinic.db.run_sync(scheduling.book, service=service, doctor_id=self.doctor(doctor).id,
                                      start=clock.localize(start), patient_name=patient, phone=phone,
                                      idem_key=key or f"seed:{patient}:{start.isoformat()}")
        self.assertTrue(res.ok, res.code)
        return res

    def to_offer(self, call, **when):
        """From a known caller to slots on offer: General Check-up at Nagarbhavi, Monday evening by default."""
        when = when or {"date_phrase": "Monday", "time_phrase": "evening"}
        call.turn(U(service="General Check-up", branch="Nagarbhavi", **when))
        self.assertEqual(call.goal, Goal.OFFER_SLOTS, call.lines)
        return call

    def to_summary(self, call, **when):
        self.to_offer(call, **when)
        call.turn(U(choice_index=1))
        self.assertEqual(call.goal, Goal.SUMMARY, call.lines)
        return call


# ---------------------------------------------------------------- branch-aware booking (Z6)


class BranchTests(BookTestCase):
    def test_braces_at_nagarbhavi_is_refused_and_names_the_branches_that_do_it(self):
        call = self.call().given()
        call.turn(U(service="Braces", branch="Nagarbhavi", date_phrase="Monday", time_phrase="8 am"))
        self.assertIn("branch.no_service", call.notice_ids())
        notice = call.notices[call.notice_ids().index("branch.no_service")]
        self.assertIn("Indiranagar", notice)
        self.assertIn("Whitefield", notice)
        self.assertNotIn("Jayanagar", notice)
        self.assertIsNone(call.ctx.book.branch)
        self.assertEqual(call.goal, Goal.ASK_BRANCH)
        self.assertIn("Indiranagar or Whitefield", call.text)
        self.assertEqual(call.holds(), [])                   # never searched there
        self.assertFalse(call.ctx.book.offered)

        call.turn(U(branch="Whitefield"))
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)
        holds = call.holds()
        self.assertTrue(holds)
        ali = self.doctor("Dr Ali")
        self.assertTrue(all(h["doctor_id"] == ali.id for h in holds))
        self.assertTrue(all(s.branch == "Whitefield" and s.hold_id for s in call.ctx.book.offered))
        self.assertEqual({h["id"] for h in holds}, {s.hold_id for s in call.ctx.book.offered})

    def test_a_mismatched_branch_put_on_the_draft_by_anyone_is_never_searched(self):
        call = self.call().given()
        call.ctx.book.service, call.ctx.book.branch = "Braces", "Nagarbhavi"
        call.ctx.book.date_c = scheduling.DateConstraint(NOW.date() + timedelta(days=4), NOW.date() + timedelta(days=4))
        call.ctx.book.any_time = True
        call.turn(U())
        self.assertIn("branch.no_service", call.notice_ids())
        self.assertEqual(call.goal, Goal.ASK_BRANCH)
        self.assertEqual(call.holds(), [])

    def test_braces_8am_three_times_gets_real_slots_then_a_callback_not_a_loop(self):
        call = self.call().given()
        call.turn(U(service="Braces", branch="Whitefield", date_phrase="Monday", time_phrase="8 am"))
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)
        for slot in call.ctx.book.offered:                    # Dr Ali starts at 2:30: real afternoon slots
            self.assertGreaterEqual(slot.start.time(), time(14, 30))
        call.turn(confirmation="no")
        self.assertEqual(call.goal, Goal.ASK_WHEN)
        self.assertEqual(call.plan.line, "ask.when.after_reject")
        self.assertEqual(call.holds(), [])
        call.turn(U(date_phrase="Wednesday", time_phrase="8 am"))
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)
        call.turn(U(reject_options=True))
        call.turn(U(date_phrase="Friday", time_phrase="8 am"))
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)
        call.turn(confirmation="no")
        self.assertEqual(call.goal, Goal.CALLBACK_OFFER)
        self.assertEqual(len(set(call.lines)), len(call.lines), call.lines)   # no line twice

    def test_a_service_only_one_branch_offers_is_said_and_settled(self):
        call = self.call().given()
        catalog = self.catalog
        # Pediatric dentistry with a lady doctor is at two branches; Dr Kulkarni narrows it to one.
        call.turn(U(service="Pediatric Dentistry", doctor="Dr Kulkarni", age=6))
        self.assertEqual(call.ctx.book.branch, "Jayanagar")
        self.assertIn("Jayanagar", " ".join(call.notices))
        self.assertTrue(catalog.branch("Jayanagar"))

    def test_branch_any_searches_every_branch_that_offers_it(self):
        call = self.call().given()
        call.turn(U(service="Braces", branch_any=True, date_phrase="Monday", time_phrase="4 pm"))
        # Monday at 4 is free somewhere: straight to the summary (owner, 7 Oct).
        self.assertEqual(call.goal, Goal.SUMMARY)
        self.assertIn(call.ctx.book.chosen.branch, {"Indiranagar", "Whitefield"})


# ---------------------------------------------------------------- doctors (criterion 5)


class DoctorTests(BookTestCase):
    def test_tomorrow_evening_around_6_with_dr_sharma(self):
        call = self.call().given()
        call.turn(U(service="General Check-up", branch="Nagarbhavi"))
        call.turn(U(date_phrase="tomorrow evening around 6", doctor_phrase="Dr Sharma"))
        b = call.ctx.book
        self.assertEqual(b.date_c.start, NOW.date() + timedelta(days=1))
        self.assertEqual((b.time_c.kind, b.time_c.start), ("exact", time(18, 0)))
        self.assertIn("doctor.unknown", call.notice_ids())
        said = call.notices[call.notice_ids().index("doctor.unknown")]
        self.assertIn("Sharma", said)
        self.assertIn("Dr Rao", said)
        self.assertIn("Dr Shetty", said)
        self.assertIsNone(b.doctor_id)
        self.assertIsNone(b.unknown_doctor)                   # said once, then cleared
        self.assertEqual(call.goal, Goal.SUMMARY)             # searched without a doctor filter; 6 is free
        self.assertEqual(b.chosen.start.time(), time(18, 0))
        call.turn(confirmation="yes")
        self.assertNotIn("doctor.unknown", call.notice_ids())

    def test_an_unknown_doctor_before_the_service_is_said_once_the_service_is_known(self):
        call = self.call().given()
        call.turn(U(doctor_phrase="Dr Sharma"))
        self.assertNotIn("doctor.unknown", call.notice_ids())
        call.turn(U(service="Root Canal Treatment"))
        said = call.notices[call.notice_ids().index("doctor.unknown")]
        for name in ("Dr Shetty", "Dr Menon", "Dr Nair"):
            self.assertIn(name, said)

    def test_a_doctor_at_another_branch_asks_before_changing_the_branch(self):
        call = self.call().given()
        call.turn(U(service="Consultation", branch="Nagarbhavi"))
        call.turn(U(doctor="Dr Menon"))
        self.assertIn("doctor.other_branch", call.notice_ids())
        self.assertIn("Indiranagar", call.notices[0])
        self.assertEqual(call.goal, Goal.CONFIRM_CHANGE)
        self.assertEqual(call.ctx.book.branch, "Nagarbhavi")
        call.turn(confirmation="yes")
        b = call.ctx.book
        self.assertEqual((b.branch, b.doctor_id), ("Indiranagar", self.doctor("Dr Menon").id))
        call.turn(U(date_phrase="Monday", time_phrase="morning"))
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)
        self.assertTrue(all(s.doctor == "Dr Menon" for s in b.offered))

    def test_a_catalog_doctor_with_no_branch_yet_settles_the_branch(self):
        call = self.call().given()
        call.turn(U(service="Braces", doctor="Dr Iyer", date_phrase="Monday", time_phrase="11 am"))
        b = call.ctx.book
        self.assertEqual(b.branch, "Indiranagar")
        self.assertTrue(b.offered and all(s.doctor == "Dr Iyer" for s in b.offered))

    def test_lady_doctor_filters_and_says_so_when_nobody_fits(self):
        call = self.call().given()
        call.turn(U(service="Teeth Cleaning", doctor_gender="female", branch="Jayanagar",
                    date_phrase="Monday", time_phrase="10 am"))
        self.assertTrue(all(s.doctor == "Dr Kulkarni" for s in call.ctx.book.offered))

        call = self.call(call_id="call-2").given()
        call.turn(U(service="Root Canal Treatment", branch="Nagarbhavi", doctor_gender="female"))
        self.assertIn("doctor.gender_none", call.notice_ids())
        said = call.notices[call.notice_ids().index("doctor.gender_none")]
        self.assertIn("lady", said)
        self.assertIn("Dr Shetty", said)
        self.assertIsNone(call.ctx.book.doctor_gender)


# ---------------------------------------------------------------- services, family, when


class DetailTests(BookTestCase):
    def test_an_unknown_treatment_books_a_consultation_and_says_why(self):
        call = self.call().given()
        call.turn(U(service_phrase="teeth whitening"))
        self.assertEqual(call.ctx.book.service, "Consultation")
        self.assertIn("service.unknown", call.notice_ids())
        self.assertIn("whitening", call.notices[0])

    def test_tooth_alone_asks_which_treatment(self):
        call = self.call().given()
        call.turn(U(service_phrase="my tooth"))
        self.assertEqual(call.goal, Goal.CLARIFY_SERVICE)
        self.assertIn("filling", call.text)
        call.turn(U(service="Tooth Filling"))
        self.assertEqual(call.ctx.book.service_options, [])
        self.assertEqual(call.goal, Goal.ASK_BRANCH)

    def test_family_booking_keeps_the_caller_and_asks_a_childs_age(self):
        call = self.call().given(name="Ravi")
        call.turn(U(service="Pediatric Dentistry", for_someone_else=True, relation="son"))
        self.assertEqual(call.goal, Goal.ASK_PATIENT)
        self.assertIn("son", call.text)
        call.turn(U(name="Aarav"))
        self.assertEqual(call.ctx.caller.name, "Ravi")
        self.assertEqual(call.ctx.book.patient_name, "Aarav")
        self.assertEqual(call.goal, Goal.ASK_AGE)
        self.assertIn("Aarav", call.text)
        call.turn(U(age=7))
        self.assertEqual(call.goal, Goal.ASK_BRANCH)
        call.turn(U(branch="Whitefield"))
        call.turn(U(date_phrase="Monday", time_phrase="10 am"))
        self.assertEqual(call.goal, Goal.SUMMARY)                   # 10 is free: straight to the summary
        self.assertIn("Aarav", call.text)
        call.turn(confirmation="yes")
        self.assertEqual(call.result.action, "booked")
        row = self.clinic.query("SELECT a.*, p.name AS patient FROM appointments a JOIN patients p "
                                "ON p.id = a.patient_id WHERE a.id = ?", call.result.appointment_id)[0]
        self.assertEqual((row["patient"], row["caller_name"], row["patient_age"]), ("Aarav", "Ravi", 7))

    def test_a_time_alone_never_becomes_today(self):
        call = self.call().given()
        call.turn(U(service="General Check-up", branch="Nagarbhavi", time_phrase="5 pm"))
        self.assertIsNone(call.ctx.book.date_c)
        self.assertEqual(call.goal, Goal.ASK_WHEN)
        call.turn(U(date_phrase="November 23"))
        self.assertEqual(call.goal, Goal.SUMMARY)
        self.assertEqual(call.ctx.book.chosen.start.time(), time(17, 0))

    def test_seven_is_resolved_before_searching(self):
        call = self.call().given()
        call.turn(U(service="General Check-up", branch="Nagarbhavi", date_phrase="Monday", time_phrase="7"))
        self.assertEqual(call.goal, Goal.RESOLVE_AMPM)
        self.assertEqual(call.holds(), [])
        call.turn(U(time_phrase="in the evening"))
        self.assertEqual(call.ctx.book.time_c.start, time(19, 0))
        self.assertEqual(call.goal, Goal.SUMMARY)                  # 7 pm is free: no separate offer

    def test_a_day_without_a_time_asks_the_time_of_day(self):
        call = self.call().given()
        call.turn(U(service="General Check-up", branch="Nagarbhavi", date_phrase="Monday"))
        self.assertEqual(call.goal, Goal.ASK_TIME)
        call.turn(U(time_phrase="any time"))
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)

    def test_a_sunday_is_explained(self):
        call = self.call().given()
        call.turn(U(service="General Check-up", branch="Nagarbhavi", date_phrase="Sunday"))
        self.assertIn("date.sunday", call.notice_ids())
        self.assertEqual(call.goal, Goal.ASK_WHEN)

    def test_lunch_time_is_explained_with_real_alternatives(self):
        call = self.call().given()
        call.turn(U(service="General Check-up", branch="Nagarbhavi", date_phrase="Monday", time_phrase="2 pm"))
        self.assertIn("time.lunch", call.notice_ids())
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)


# ---------------------------------------------------------------- offers and holds


class OfferTests(BookTestCase):
    def test_the_first_search_says_let_me_check_and_later_ones_do_not(self):
        call = self.call().given()
        self.to_offer(call)
        self.assertTrue(call.checked())
        call.turn(U(date_phrase="Tuesday"))
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)
        self.assertFalse(call.checked())

    def test_two_slots_are_held_and_picking_by_time_releases_the_other(self):
        call = self.call().given()
        self.to_offer(call)
        offered = list(call.ctx.book.offered)
        self.assertEqual(len(offered), 2)
        self.assertEqual(len(call.holds()), 2)
        second = offered[1]
        t = second.start.time()
        call.turn(U(time_phrase=f"{t.hour % 12 or 12}:{t.minute:02d} {'pm' if t.hour >= 12 else 'am'}"))
        self.assertEqual(call.ctx.book.chosen, second)
        self.assertEqual([h["id"] for h in call.holds()], [second.hold_id])
        self.assertEqual(call.goal, Goal.SUMMARY)

    def test_a_free_exact_time_goes_straight_to_the_summary(self):
        # Owner's decision, 7 Oct (T5): no "Monday at 11 is free, shall I take that?" before the summary.
        call = self.call().given()
        call.turn(U(service="General Check-up", branch="Nagarbhavi", date_phrase="Monday", time_phrase="at 11"))
        self.assertEqual(call.plan.line, "summary")
        self.assertIn("exact.free", call.notice_ids())
        self.assertEqual(call.ctx.book.chosen.start.time(), time(11, 0))
        self.assertEqual(len(call.holds()), 1)                      # only the chosen slot stays held
        call.turn(confirmation="yes")
        self.assertEqual(call.result.action, "booked")

    def test_a_new_day_while_slots_are_on_offer_searches_again(self):
        call = self.call().given()
        self.to_offer(call)
        old = {s.hold_id for s in call.ctx.book.offered}
        call.turn(U(date_phrase="Wednesday"))
        new = {h["id"] for h in call.holds()}
        self.assertTrue(new and not (new & old))
        self.assertTrue(all(s.start.weekday() == 2 for s in call.ctx.book.offered))

    def test_release_frees_holds_and_never_raises(self):
        call = self.call().given()
        self.to_offer(call)
        asyncio.run(book.release(call.ctx, call.rt))
        self.assertEqual(call.holds(), [])
        self.assertEqual(call.ctx.book.offered, [])
        broken = Runtime(call_id="x", db=None, catalog=self.catalog, kb=None)
        with self.assertLogs("dialogue.book", "ERROR"):
            asyncio.run(book.release(call.ctx, broken))       # no database at all: logged, never raised

    def test_the_context_still_pickles_with_slots_on_offer(self):
        call = self.call().given()
        self.to_offer(call)
        copy = pickle.loads(pickle.dumps(call.ctx))
        self.assertEqual(copy, call.ctx)
        self.assertEqual(book.next_goal(copy).goal, Goal.OFFER_SLOTS)


# ---------------------------------------------------------------- max-3 and duplicates (Z7)


class CheckTests(BookTestCase):
    def test_three_future_appointments_stop_the_booking_before_any_search(self):
        for i, day in enumerate((5, 6, 7)):
            self.book_existing(f"Someone {i}", datetime(2026, 10, day, 10, 0))
        call = self.call()
        call.turn(U(intent=Intent.BOOK, service="General Check-up", branch="Nagarbhavi", date_phrase="Monday",
                    time_phrase="4 pm", name="Priya"))
        self.assertEqual(call.goal, Goal.ASK_PHONE)
        call.turn(U(phone_digits=PHONE))
        self.assertEqual(call.goal, Goal.CONFIRM_PHONE)
        call.turn(confirmation="yes")
        self.assertEqual(call.goal, Goal.MAX_REACHED)
        self.assertEqual(call.holds(), [])
        self.assertFalse(call.checked())
        call.turn(confirmation="no")
        self.assertEqual(call.goal, Goal.ANYTHING_ELSE)
        self.assertEqual(self.clinic.query("SELECT COUNT(*) AS n FROM appointments")[0]["n"], 3)

    def test_the_same_patient_already_booked_is_asked_about_without_details(self):
        self.book_existing("Priya Sharma", datetime(2026, 10, 6, 11, 30), doctor="Dr Rao")
        call = self.call().given(name="Priya Sharma")
        call.turn(U(service="General Check-up", branch="Nagarbhavi"))
        self.assertEqual(call.goal, Goal.DUPLICATE_CHECK)
        said = call.lines[-1]
        self.assertIn("Priya Sharma", said)
        self.assertNotRegex(said, r"\d")
        self.assertNotRegex(said, r"(?i)\b(dr|doctor|monday|tuesday|october|morning|evening|nagarbhavi)\b")
        self.assertNotIn("11", repr(call.ctx))                # nothing about it is kept either
        call.turn(confirmation="yes")
        self.assertEqual(call.goal, Goal.ASK_WHEN)

    def test_another_family_member_on_the_same_number_is_not_a_duplicate(self):
        self.book_existing("Priya Sharma", datetime(2026, 10, 6, 11, 30))
        call = self.call().given(name="Priya Sharma")
        call.turn(U(service="General Check-up", branch="Nagarbhavi", patient_name="Meena Sharma",
                    relation="mother"))
        self.assertEqual(call.goal, Goal.ASK_WHEN)


# ---------------------------------------------------------------- summary gate and commit (Z1, Z2, M1)


class CommitTests(BookTestCase):
    def test_a_simple_booking_takes_eight_caller_turns_with_read_back_and_summary(self):
        call = self.call()
        call.turn(U(intent=Intent.BOOK, service="Teeth Cleaning"))   # 1 what they want
        call.turn(U(name="Priya"))                                    # 2 name
        call.turn(U(phone_digits=PHONE))                              # 3 number
        call.turn(confirmation="yes")                                 # 4 read-back: yes
        call.turn(U(branch="Jayanagar"))                              # 5 branch
        call.turn(U(date_phrase="Monday", time_phrase="evening"))     # 6 when
        call.turn(U(choice_index=1))                                  # 7 pick
        call.turn(confirmation="yes")                                 # 8 summary: yes
        self.assertEqual(call.goals, [Goal.ASK_NAME, Goal.ASK_PHONE, Goal.CONFIRM_PHONE, Goal.ASK_BRANCH,
                                      Goal.ASK_WHEN, Goal.OFFER_SLOTS, Goal.SUMMARY, Goal.BOOKED])
        self.assertEqual(call.result.action, "booked")
        self.assertIn("9 8 7 6 5", call.lines[2])
        self.assertIn("Jayanagar", call.lines[6])
        self.assertTrue(call.checked())                               # "let me just check" at the commit
        call.turn(U())
        self.assertIsNone(call.plan)                                  # done: the engine moves on

    def test_yes_to_a_summary_that_was_cut_off_does_not_book(self):
        call = self.call().given()
        self.to_summary(call)
        call.turn(confirmation="yes", heard=False)
        self.assertIsNone(call.result.action)
        self.assertEqual(call.goal, Goal.SUMMARY_AGAIN)
        self.assertEqual(self.clinic.query("SELECT COUNT(*) AS n FROM appointments")[0]["n"], 0)
        call.turn(confirmation="yes")
        self.assertEqual(call.result.action, "booked")

    def test_yes_after_a_detail_changed_since_the_summary_does_not_book(self):
        call = self.call().given()
        self.to_summary(call)
        call.turn(U(patient_name="Meena", relation="mother"), confirmation="yes")
        self.assertIsNone(call.result.action)
        self.assertEqual(call.goal, Goal.SUMMARY)
        self.assertIn("Meena", call.text)
        call.turn(confirmation="yes")
        self.assertEqual(call.result.action, "booked")

    def test_yes_with_a_new_time_at_the_summary_is_a_change_not_a_booking(self):
        call = self.call().given()
        self.to_summary(call)
        call.turn(U(date_phrase="Tuesday", time_phrase="5 pm"), confirmation="yes")
        self.assertIsNone(call.result.action)
        self.assertEqual(call.goal, Goal.SUMMARY)                   # a new summary for Tuesday at 5
        self.assertEqual(call.ctx.book.chosen.start.time(), time(17, 0))

    def test_yes_only_counts_as_the_answer_to_the_summary(self):
        call = self.call().given()
        self.to_summary(call)
        call.ctx.pending = Goal.HOLD_ON                       # a handler spoke instead of the summary
        call.turn(confirmation="yes")
        self.assertIsNone(call.result.action)
        self.assertEqual(call.goal, Goal.SUMMARY)

    def test_no_to_the_summary_asks_what_to_change(self):
        call = self.call().given()
        self.to_summary(call)
        call.turn(confirmation="no")
        self.assertEqual(call.goal, Goal.WHAT_TO_CHANGE)
        call.turn(U(date_phrase="Tuesday", time_phrase="5 pm"))
        self.assertEqual(call.goal, Goal.SUMMARY)

    def test_the_same_version_committed_twice_makes_one_appointment(self):
        call = self.call().given()
        self.to_summary(call)
        retry = pickle.loads(pickle.dumps(call.ctx))
        call.turn(confirmation="yes")
        first = call.result.appointment_id
        call.ctx = retry                                      # the same turn again (a retried commit)
        call.turn(confirmation="yes")
        self.assertEqual(call.result.appointment_id, first)
        self.assertEqual(self.clinic.query("SELECT COUNT(*) AS n FROM appointments")[0]["n"], 1)

    def test_a_slot_taken_at_commit_is_said_and_reoffered_never_claimed(self):
        call = self.call().given()
        self.to_summary(call)
        slot = call.ctx.book.chosen
        self.clinic.db.run_sync(scheduling.release_holds, call.rt.call_id)
        self.book_existing("Someone Else", slot.start.replace(tzinfo=None), doctor=slot.doctor,
                           phone="9123456789")
        call.turn(confirmation="yes")
        self.assertIsNone(call.result.action)
        self.assertFalse(call.result.ok)
        self.assertIn("slot.gone", call.notice_ids())
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)
        self.assertNotIn(slot.start, [s.start for s in call.ctx.book.offered if s.doctor == slot.doctor])
        self.assertNotRegex(call.lines[-1], r"(?i)\bbooked\b|\ball set\b")

    def test_every_required_detail_is_on_the_committed_row(self):
        call = self.call().given(name="Priya")
        call.turn(U(service="Braces", branch="Nagarbhavi"))
        call.turn(U(branch="Indiranagar"))
        call.turn(U(date_phrase="Monday", time_phrase="11 am"))
        call.turn(U(choice_index=1))
        call.turn(confirmation="yes")
        self.assertEqual(call.result.action, "booked")
        row = self.clinic.query(
            "SELECT a.*, p.name AS patient, s.name AS service, b.name AS branch FROM appointments a "
            "JOIN patients p ON p.id = a.patient_id JOIN services s ON s.id = a.service_id "
            "JOIN branches b ON b.id = a.branch_id WHERE a.id = ?", call.result.appointment_id)[0]
        self.assertEqual(row["service"], "Braces")
        self.assertIn(row["branch"], self.catalog.branches_offering("Braces"))
        self.assertEqual(row["patient"], "Priya")
        self.assertEqual(row["caller_phone_e164"], E164)
        self.assertEqual(row["created_by_call_id"], call.rt.call_id)
        self.assertEqual(book.missing_required(call.ctx, self.catalog), [])

    def test_an_unconfirmed_phone_never_reaches_a_commit(self):
        call = self.call().given()
        self.to_summary(call)
        call.ctx.caller.phone_state = FieldState.PENDING
        call.turn(confirmation="yes")
        self.assertIsNone(call.result.action)
        self.assertEqual(call.goal, Goal.CONFIRM_PHONE)

    def test_an_urgent_booking_is_today_and_creates_an_emergency_task(self):
        call = self.call().given()
        call.ctx.emergency = Emergency.URGENT
        call.turn(U(branch="Indiranagar"))
        b = call.ctx.book
        self.assertEqual(b.service, "Consultation")
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)
        self.assertEqual(b.offered[0].start.date(), NOW.date())
        call.turn(U(choice_index=1))
        call.turn(confirmation="yes")
        self.assertEqual(call.result.action, "booked")
        self.assertEqual(call.plan.line, "booked.emergency")
        tasks = self.clinic.query("SELECT * FROM tasks")
        self.assertEqual([(t["kind"], t["priority"]) for t in tasks], [("emergency", "urgent")])
        self.assertEqual(call.ctx.tasks_created, [tasks[0]["id"]])

    def test_nothing_is_ever_claimed_as_booked_before_scheduling_says_ok(self):
        call = self.call().given()
        self.to_summary(call)
        for line in call.lines:
            self.assertNotRegex(line, r"(?i)\byou're booked\b|\bdone\b")
        self.assertNotIn(Goal.BOOKED, call.goals)


# ---------------------------------------------------------------- with apply.py running first (the real pipeline)


class ApplyFirstTests(BookTestCase):
    """
    In the engine apply.apply() runs before book.advance() on the same
    Understanding. These mimic what apply.py leaves on the draft and check
    book still frees the right holds and never loops on a turned-down offer.
    """

    def test_a_pick_made_by_apply_still_frees_the_other_hold(self):
        call = self.call().given()
        self.to_offer(call)
        first, second = call.ctx.book.offered
        call.ctx.book.chosen = second                        # apply._apply_choice
        call.ctx.book.touch()
        call.turn(U(choice_index=2))
        self.assertEqual([h["id"] for h in call.holds()], [second.hold_id])
        self.assertEqual(call.ctx.book.offered, [second])
        self.assertEqual(call.goal, Goal.SUMMARY)

    def test_a_pick_by_time_made_by_apply_is_not_searched_again(self):
        call = self.call().given()
        self.to_offer(call)
        first, second = call.ctx.book.offered
        call.ctx.book.chosen = second                        # apply._apply_choice on "5:30"
        call.ctx.book.touch()
        t = second.start.time()
        call.turn(U(time_phrase=f"{t.hour % 12 or 12}:{t.minute:02d} {'pm' if t.hour >= 12 else 'am'}"))
        self.assertEqual(call.ctx.book.chosen, second)
        self.assertEqual([h["id"] for h in call.holds()], [second.hold_id])
        self.assertEqual(call.goal, Goal.SUMMARY)

    def test_a_change_apply_is_asking_about_is_not_taken_before_the_yes(self):
        call = self.call().given()
        self.to_offer(call)
        offered = list(call.ctx.book.offered)
        monday = call.ctx.book.date_c
        call.ctx.change_proposal = {"field": "date", "value": None, "spoken": "Tuesday"}
        call.turn(U(date_phrase="Tuesday"))
        self.assertEqual(call.ctx.book.date_c, monday)
        self.assertEqual(call.ctx.book.offered, offered)
        self.assertEqual({h["id"] for h in call.holds()}, {s.hold_id for s in offered})

    def test_a_rejection_apply_already_counted_is_counted_once_and_never_reoffered(self):
        call = self.call().given()
        call.turn(U(service="Braces", branch="Whitefield", date_phrase="Monday", time_phrase="8 am"))
        for round_ in range(1, 4):
            self.assertEqual(call.goal, Goal.OFFER_SLOTS, call.lines)
            offered = {s.start for s in call.ctx.book.offered}
            b = call.ctx.book                                # apply: rounds + 1, offers dropped, when kept
            b.offer_rounds += 1
            b.offered, b.chosen = [], None
            call.clinic.db.run_sync(scheduling.release_holds, call.rt.call_id)
            call.turn(confirmation="no")
            self.assertEqual(call.ctx.book.offer_rounds, round_)
            self.assertFalse(offered & {s.start for s in call.ctx.book.offered})
            if round_ < 3:
                self.assertEqual(call.plan.line, "ask.when.after_reject")
                call.turn(U(date_phrase=("Wednesday", "Friday")[round_ - 1], time_phrase="8 am"))
        self.assertEqual(call.goal, Goal.CALLBACK_OFFER)

    def test_details_apply_marked_as_taken_are_not_applied_twice(self):
        call = self.call().given()
        call.turn(U(service="General Check-up", branch="Nagarbhavi"))
        u = U(doctor_phrase="Dr Sharma")
        u.__dict__["_book_taken"] = True                     # apply.py already said doctor.unknown
        call.turn(u)
        self.assertNotIn("doctor.unknown", call.notice_ids())

    def test_a_doctor_left_at_another_branch_gives_way_to_the_branch(self):
        call = self.call().given()
        call.turn(U(service="Consultation", branch="Nagarbhavi"))
        b = call.ctx.book                                    # apply set Dr Menon, then the caller said no to moving
        b.doctor_id, b.doctor = self.doctor("Dr Menon").id, "Dr Menon"
        call.turn(U(date_phrase="Monday", time_phrase="morning"))
        self.assertIsNone(b.doctor_id)
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)
        self.assertTrue(all(s.branch == "Nagarbhavi" for s in b.offered))

    def test_no_search_while_a_change_question_is_open(self):
        call = self.call().given()
        call.turn(U(service="General Check-up", branch="Nagarbhavi"))
        call.ctx.change_proposal = {"field": "branch", "value": "Jayanagar", "spoken": "Jayanagar"}
        call.turn(U(date_phrase="Monday", time_phrase="evening"))
        self.assertEqual(call.holds(), [])

    def test_the_real_catalog_loader_gives_the_same_branch_rules(self):
        catalog = self.clinic.db.run_sync(facts.load_catalog)
        self.assertEqual(tuple(catalog.branches_offering("Braces")), ("Indiranagar", "Whitefield"))
        call = Call(self.clinic, catalog).given()
        call.turn(U(service="Braces", branch="Nagarbhavi", date_phrase="Monday", time_phrase="8 am"))
        self.assertIn("branch.no_service", call.notice_ids())
        call.turn(U(branch="Whitefield"))
        self.assertTrue(call.holds())


if __name__ == "__main__":
    unittest.main()
