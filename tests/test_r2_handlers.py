"""
Global handlers (dialogue/handlers.py): red flag, urgent, abuse, closing,
repeat, wait, fragments, silence, honesty, don't-keep, person requests,
other languages and the answers to global offers.

Each turn is driven on a hand-built CallContext inside DemoClinic, the way
the engine calls handlers.handle(): an Understanding built in the test, the
confirmation already resolved, and a Runtime on the temp clinic's database.
Tasks are read back from the real tasks table, so "the task exists before
the promise" (invariant 7) is checked on the row, not on a mock. Planned
lines and notices are rendered through prompts.render. No model, no
pipeline.
"""

import asyncio
import unittest
from unittest import mock

import config
import prompts
from dialogue import handlers
from dialogue.context import Act, Emergency, FieldState, Goal, Intent, Understanding, new_context
from dialogue.runtime import Runtime
from dialogue.testing import DemoClinic

E164 = "+919876543210"


def U(text: str = "", acts=(), **kw) -> Understanding:
    return Understanding(acts=[a.value for a in acts], raw_text=text, **kw)


class Call:
    """One simulated call: handlers.handle() only, with policy's pending goal set by hand."""

    def __init__(self, clinic, call_id="call-h1"):
        self.clinic = clinic
        self.ctx = new_context(call_id)
        self.rt = Runtime(call_id=call_id, db=clinic.db, catalog=None, kb=None)
        self.out = None

    def confirmed_phone(self, phone=E164):
        c = self.ctx.caller
        c.phone_e164, c.phone_state = phone, FieldState.CONFIRMED
        return self

    def turn(self, u: Understanding, confirmation=None, pending=None):
        if pending is not None:
            self.ctx.pending = pending
        self.ctx.turn += 1
        self.out = asyncio.run(handlers.handle(self.ctx, u, confirmation, self.rt))
        return self.out

    @property
    def goal(self):
        return self.out.plan.goal if self.out and self.out.plan else None

    def rendered(self) -> str:
        parts = [prompts.render(n.line, self.ctx.prompts, n.params) for n in (self.out.notices if self.out else [])]
        if self.out and self.out.plan:
            parts.append(prompts.render(self.out.plan.line, self.ctx.prompts, self.out.plan.params))
        return " ".join(parts)

    def notice_lines(self) -> list:
        return [n.line for n in self.out.notices] if self.out else []

    def tasks(self) -> list:
        return self.clinic.query("SELECT * FROM tasks WHERE call_id = ? ORDER BY id", self.ctx.call_id)


class Screens(unittest.TestCase):
    """The deterministic word screens run before any model call."""

    def test_red_flags(self):
        for text in ("I can't breathe, my face is swelling", "my jaw is broken", "the swelling is spreading to my eye",
                     "the bleeding won't stop", "I'm having trouble swallowing"):
            self.assertTrue(handlers.red_flag_words(text), text)

    def test_not_red_flags(self):
        for text in ("no trouble breathing, just a toothache", "I want to book a cleaning",
                     "what if I can't breathe after the extraction?", ""):
            self.assertFalse(handlers.red_flag_words(text), text)

    def test_urgent(self):
        for text in ("I have severe tooth pain", "my gum is swollen", "I broke my tooth", "it's bleeding a bit"):
            self.assertTrue(handlers.urgent_words(text), text)
        for text in ("I'd like a check-up", "no pain at all", "the pain's not so bad"):
            self.assertFalse(handlers.urgent_words(text), text)


class RedFlagAndUrgent(unittest.TestCase):

    def test_red_flag_advises_tasks_and_closes(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            out = call.turn(U("I can't breathe, my face is swelling"))
            self.assertEqual(call.goal, Goal.RED_FLAG)
            self.assertTrue(out.closes_call)
            self.assertFalse(out.carry_on)
            self.assertFalse(out.plan.use_model_say)
            self.assertEqual(call.ctx.emergency, Emergency.RED_FLAG)
            text = call.rendered()
            self.assertIn("108", text)
            self.assertNotIn("?", text)               # no question asked
            tasks = call.tasks()
            self.assertEqual([(t["kind"], t["priority"]) for t in tasks], [("red_flag", "urgent")])

    def test_red_flag_from_the_model_reading(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            call.turn(U("it's getting really hard", emergency=Emergency.RED_FLAG))
            self.assertEqual(call.goal, Goal.RED_FLAG)
            self.assertEqual(len(call.tasks()), 1)

    def test_urgent_books_today_with_a_notice(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            out = call.turn(U("I have severe tooth pain"))
            self.assertIn("urgent.ack", call.notice_lines())
            self.assertIsNone(out.plan)               # the BOOK workflow carries on
            self.assertTrue(out.carry_on)
            ctx = call.ctx
            self.assertEqual(ctx.emergency, Emergency.URGENT)
            self.assertEqual(ctx.intent, Intent.BOOK)
            self.assertTrue(ctx.book.emergency)
            self.assertEqual(ctx.book.service, handlers.URGENT_SERVICE)
            self.assertIsNotNone(ctx.book.date_c)
            self.assertEqual(ctx.book.date_c.start, call.rt.now().date())
            self.assertEqual(call.tasks(), [])        # the emergency task comes at commit (book.py)

    def test_urgent_keeps_a_named_service(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            call.turn(U("my tooth is broken, I need an extraction", service="Tooth Extraction"))
            self.assertEqual(call.ctx.emergency, Emergency.URGENT)
            self.assertIsNone(call.ctx.book.service)  # apply.py fills the named one

    def test_urgent_question_is_not_an_emergency(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            out = call.turn(U("is swelling normal after a filling?", acts=(Act.QUESTION,)))
            self.assertIsNone(out)
            self.assertEqual(call.ctx.emergency, Emergency.NONE)


class TerminalActs(unittest.TestCase):

    def test_abuse_warns_once_then_closes(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            out = call.turn(U("you useless idiot", acts=(Act.ABUSE,)))
            self.assertEqual(call.goal, Goal.ABUSE_WARN)
            self.assertFalse(out.closes_call)
            out = call.turn(U("shut up", acts=(Act.ABUSE,)))
            self.assertEqual(call.goal, Goal.ABUSE_CLOSE)
            self.assertTrue(out.closes_call)

    def test_bye_closes_and_never_books(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            call.ctx.intent = Intent.BOOK
            out = call.turn(U("okay bye", acts=(Act.END,)), pending=Goal.ASK_WHEN)
            self.assertEqual(call.goal, Goal.CLOSE)
            self.assertTrue(out.closes_call)
            self.assertFalse(out.carry_on)            # the workflow never runs: nothing is booked
            self.assertEqual(out.plan.line, "close")

    def test_yes_thanks_bye_at_the_summary_still_commits(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            call.ctx.intent = Intent.BOOK
            out = call.turn(U("yes thanks bye", acts=(Act.END,), confirmation="yes"), "yes", pending=Goal.SUMMARY)
            self.assertTrue(out is None or (out.carry_on and out.plan is None))

    def test_close_after_a_booking(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            call.ctx.outcome = "booked"
            call.turn(U("that's all, bye", acts=(Act.END,)))
            self.assertEqual(call.out.plan.line, "close.booked")

    def test_repeat_wait_and_fragment(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            call.ctx.last_emma = "What day works for you?"
            out = call.turn(U("sorry?", acts=(Act.REPEAT,)), pending=Goal.ASK_WHEN)
            self.assertEqual((call.goal, out.plan.line, out.carry_on), (Goal.REPEAT, "repeat.prefix", False))
            out = call.turn(U("hold on a sec", acts=(Act.WAIT,)))
            self.assertEqual((call.goal, out.carry_on), (Goal.HOLD_ON, False))

    def test_fragment_is_stashed_not_applied(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            out = call.turn(U("Cancel the com", acts=(Act.FRAGMENT,)), pending=Goal.ASK_INTENT)
            self.assertEqual(call.goal, Goal.GO_ON)
            self.assertFalse(out.carry_on)            # apply / workflows don't run on it
            ctx = call.ctx
            self.assertEqual(ctx.fragment, "Cancel the com")
            self.assertEqual(ctx.intent, Intent.NONE)
            self.assertIsNone(ctx.caller.name)
            self.assertIsNone(ctx.book.service)
            self.assertIsNone(ctx.manage.action)


class Silence(unittest.TestCase):

    def test_three_silences_close_kindly(self):
        ctx = new_context("call-s")
        plans = [handlers.silence(ctx) for _ in range(3)]
        self.assertEqual([p.goal for p in plans], [Goal.SILENCE] * 3)
        self.assertEqual([p.closes_call for p in plans], [False, False, True])
        texts = [prompts.render(p.line, ctx.prompts, p.params) for p in plans]
        self.assertEqual(len(set(texts)), 3)          # a different line on each rung

    def test_speech_resets_the_ladder(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            handlers.silence(call.ctx)
            handlers.silence(call.ctx)
            call.turn(U("yes I'm here"))
            self.assertEqual(call.ctx.silence_level, 0)
            self.assertFalse(handlers.silence(call.ctx).closes_call)


class Notices(unittest.TestCase):

    def test_honesty_only_on_a_robot_question(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            call.ctx.intent = Intent.BOOK
            out = call.turn(U("wait, am I talking to a real person?", acts=(Act.ROBOT_QUESTION,)),
                            pending=Goal.ASK_WHEN)
            self.assertEqual(call.notice_lines(), ["honesty"])
            self.assertIn(config.HONEST_LINE, call.rendered())
            self.assertTrue(out.carry_on)             # straight back to the pending goal
            self.assertIsNone(out.plan)
            for u in (U("tomorrow evening", date_phrase="tomorrow", time_phrase="evening"),
                      U("is this a real clinic?", acts=(Act.QUESTION,)), U("hello")):
                out = call.turn(u)
                self.assertNotIn("honesty", call.notice_lines() if out else [], u.raw_text)

    def test_dont_keep(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            out = call.turn(U("please don't record this", acts=(Act.DONT_KEEP,)))
            self.assertFalse(call.ctx.keep_transcript)
            self.assertEqual(call.notice_lines(), ["dont_keep.ack"])
            self.assertTrue(out.carry_on)


class PersonRequest(unittest.TestCase):

    def test_help_first_then_callback_task_before_the_promise(self):
        with DemoClinic() as clinic:
            call = Call(clinic).confirmed_phone()
            call.turn(U("can I talk to someone at the clinic?", acts=(Act.WANTS_HUMAN,)))
            self.assertEqual(call.goal, Goal.HELP_FIRST)
            self.assertEqual(call.tasks(), [])
            call.turn(U("no, I want a real person", acts=(Act.WANTS_HUMAN,)), pending=Goal.HELP_FIRST)
            self.assertEqual(call.goal, Goal.CALLBACK_DONE)
            tasks = call.tasks()
            self.assertEqual([t["kind"] for t in tasks], ["callback"])
            self.assertEqual(tasks[0]["phone_e164"], E164)
            self.assertEqual(call.ctx.tasks_created, [tasks[0]["id"]])
            self.assertIn("98765", call.rendered().replace(" ", ""))

    def test_insisting_without_a_number_asks_for_one_first(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            call.turn(U("connect me to a human", acts=(Act.WANTS_HUMAN,)))
            call.turn(U("I said a human", acts=(Act.WANTS_HUMAN,)), pending=Goal.HELP_FIRST)
            self.assertEqual(call.goal, Goal.ASK_PHONE)
            self.assertEqual(call.tasks(), [])        # nothing promised yet
            call.turn(U("98765 43210", phone_digits="9876543210"), pending=Goal.ASK_PHONE)
            self.assertEqual(call.goal, Goal.CONFIRM_PHONE)
            self.assertEqual(call.tasks(), [])
            call.turn(U("yes", confirmation="yes"), "yes", pending=Goal.CONFIRM_PHONE)
            self.assertEqual(call.goal, Goal.CALLBACK_DONE)
            self.assertEqual([t["kind"] for t in call.tasks()], ["callback"])

    def test_no_tasks_module_means_no_promise(self):
        with DemoClinic() as clinic:
            call = Call(clinic).confirmed_phone()
            call.turn(U("a person please", acts=(Act.WANTS_HUMAN,)))
            with mock.patch.object(handlers, "optional_module", return_value=None):
                call.turn(U("a person please", acts=(Act.WANTS_HUMAN,)), pending=Goal.HELP_FIRST)
            self.assertNotEqual(call.goal, Goal.CALLBACK_DONE)
            self.assertEqual(call.tasks(), [])


class Language(unittest.TestCase):

    def test_other_script_gets_english_only_and_a_task_only_on_yes(self):
        with DemoClinic() as clinic:
            call = Call(clinic).confirmed_phone()
            call.turn(U("नमस्ते, मुझे अपॉइंटमेंट चाहिए", acts=(Act.OTHER_LANGUAGE,)))
            self.assertEqual(call.goal, Goal.ENGLISH_ONLY)
            self.assertEqual(call.tasks(), [])
            call.turn(U("yes please", confirmation="yes"), "yes", pending=Goal.ENGLISH_ONLY)
            self.assertEqual(call.goal, Goal.CALLBACK_DONE)
            self.assertEqual([t["kind"] for t in call.tasks()], ["language"])

    def test_declined_language_callback_creates_nothing(self):
        with DemoClinic() as clinic:
            call = Call(clinic).confirmed_phone()
            call.turn(U("can you speak Kannada?", acts=(Act.OTHER_LANGUAGE,)))
            self.assertEqual(call.goal, Goal.ENGLISH_ONLY)
            call.turn(U("no it's fine, English is okay", confirmation="no"), "no", pending=Goal.ENGLISH_ONLY)
            self.assertEqual(call.tasks(), [])

    def test_hinglish_is_english(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            out = call.turn(U("mujhe kal appointment chahiye", acts=(Act.OTHER_LANGUAGE,)))
            self.assertNotEqual(call.goal, Goal.ENGLISH_ONLY)
            self.assertTrue(out is None or out.carry_on)


class GlobalOffers(unittest.TestCase):

    def test_anything_else_no_closes(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            out = call.turn(U("no that's it", confirmation="no"), "no", pending=Goal.ANYTHING_ELSE)
            self.assertEqual(call.goal, Goal.CLOSE)
            self.assertTrue(out.closes_call)

    def test_anything_else_no_with_a_new_request_does_not_close(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            out = call.turn(U("no, but can I book for my son?", confirmation="no", intent=Intent.BOOK,
                              for_someone_else=True), "no", pending=Goal.ANYTHING_ELSE)
            self.assertTrue(out is None or not out.closes_call)

    def test_offer_help_yes_starts_a_booking(self):
        with DemoClinic() as clinic:
            call = Call(clinic)
            call.turn(U("yes please", confirmation="yes"), "yes", pending=Goal.OFFER_HELP)
            self.assertEqual(call.ctx.intent, Intent.BOOK)

    def test_callback_after_failed_verification(self):
        with DemoClinic() as clinic:
            call = Call(clinic).confirmed_phone()
            call.ctx.intent = Intent.CANCEL
            call.ctx.manage.verify_attempts = 2
            call.turn(U("yes", confirmation="yes"), "yes", pending=Goal.CALLBACK_OFFER)
            self.assertEqual(call.goal, Goal.CALLBACK_DONE)
            tasks = call.tasks()
            self.assertEqual([t["kind"] for t in tasks], ["callback"])
            self.assertIn("couldn't be found", tasks[0]["note"])
            self.assertTrue(call.ctx.manage.done)

    def test_callback_declined_creates_nothing(self):
        with DemoClinic() as clinic:
            call = Call(clinic).confirmed_phone()
            call.ctx.intent = Intent.CANCEL
            call.turn(U("no thanks", confirmation="no"), "no", pending=Goal.CALLBACK_OFFER)
            self.assertEqual(call.tasks(), [])
            self.assertTrue(call.ctx.manage.done)


if __name__ == "__main__":
    unittest.main()
