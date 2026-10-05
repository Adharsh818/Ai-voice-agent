"""
prompts.py: the pre-written lines (docs/R2_DESIGN.md, section 8.3).

Every id the dialogue modules use exists, every variant only uses its own
placeholders, rendering never repeats a sentence back to back and works
through all variants before reusing one (M3, criterion 10), and the speakable
helpers say dates, times and numbers the way a receptionist would.
"""

import ast
import re
import unittest
from datetime import date, datetime, time
from pathlib import Path

import phrases
import prompts
import speech
from dateparse import DateConstraint, TimeConstraint
from dialogue.context import PromptMemory

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 1)                                   # a Thursday


def sample_params(line_id: str) -> dict:
    return {p: f"<{p}>" for p in prompts.LINES[line_id].params}


def referenced_line_ids(paths=None) -> dict:
    """
    Line ids the dialogue modules reference: `line=` keywords, Notice(...) /
    GoalPlan(goal, line, ...) / render(line_id, ...) arguments, and any other
    string shaped like a dotted id from a known family ("ask.when.rephrase").
    Returns {id: "file:line"}.
    """
    families = {k.split(".")[0] for k in prompts.LINES if "." in k}
    found = {}
    for path in paths or sorted((ROOT / "dialogue").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstrings.add(id(first.value))

        def add(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                found.setdefault(node.value, f"{path.name}:{node.lineno}")

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                for kw in node.keywords:
                    if kw.arg in ("line", "line_id"):
                        add(kw.value)
                if name in ("Notice", "render") and node.args:
                    add(node.args[0])
                if name == "GoalPlan" and len(node.args) > 1:
                    add(node.args[1])
            elif (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                  and re.fullmatch(r"[a-z_]+(\.[a-z_]+)+", node.value)
                  and node.value.split(".")[0] in families):
                add(node)
    return found


class RegistryTests(unittest.TestCase):
    def test_every_line_has_variants_and_nothing_else_does(self):
        self.assertEqual(set(prompts.VARIANTS), set(prompts.LINES))
        for line_id, variants in prompts.VARIANTS.items():
            self.assertTrue(variants, line_id)
            self.assertEqual(len(set(variants)), len(variants), f"duplicate variant in {line_id}")

    def test_placeholders_are_a_subset_of_the_spec_params(self):
        for line_id, variants in prompts.VARIANTS.items():
            allowed = set(prompts.LINES[line_id].params)
            for v in variants:
                self.assertLessEqual(prompts.placeholders(v), allowed, f"{line_id}: {v!r}")

    def test_critical_lines_state_every_fact_in_every_variant(self):
        # A slot offer, read-back, summary or outcome must never drop a detail
        # because one wording happened to leave it out.
        for line_id, spec in prompts.LINES.items():
            if spec.critical and spec.params:
                for v in prompts.VARIANTS[line_id]:
                    self.assertEqual(prompts.placeholders(v), set(spec.params), f"{line_id}: {v!r}")

    def test_fixed_policy_lines_match_the_clinic_config(self):
        # These notices state clinic rules in plain words; if config changes, they must too.
        import config
        self.assertEqual((config.CLINIC_START_HOUR, config.CLINIC_END_HOUR), (7, 21))
        self.assertEqual((config.LUNCH_START_HOUR, config.LUNCH_END_HOUR, config.LUNCH_END_MIN), (14, 14, 30))
        self.assertEqual(config.BOOKING_HORIZON_DAYS, 60)                  # "two months ahead"
        self.assertEqual(config.MAX_FUTURE_APPOINTMENTS_PER_PHONE, 3)      # "already three appointments"

    def test_frequent_lines_have_several_wordings(self):
        for line_id, spec in prompts.LINES.items():
            if spec.cache and line_id != "honesty":
                self.assertGreaterEqual(len(prompts.VARIANTS[line_id]), 2, line_id)

    def test_every_id_the_dialogue_modules_use_exists(self):
        missing = {i: where for i, where in referenced_line_ids().items() if i not in prompts.LINES}
        self.assertEqual(missing, {}, "line ids used in dialogue/ but missing from prompts.LINES")

    def test_the_reference_scanner_finds_ids(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text("\n".join([
                '"""Doc mentioning ask.docstring."""',
                'n = Notice("thanks.extra", {})',
                'p = GoalPlan(Goal.ASK_WHEN, "when.ask")',
                'q = make(line="plainword")',
                'LADDER = {1: "ask.when.sideways"}',
            ]), encoding="utf-8")
            found = referenced_line_ids([probe])
        self.assertEqual(set(found), {"thanks.extra", "when.ask", "plainword", "ask.when.sideways"})


class RenderTests(unittest.TestCase):
    def test_unknown_id_raises_key_error(self):
        with self.assertRaises(KeyError):
            prompts.render("no.such.line", PromptMemory())

    def test_thirty_renders_never_repeat_back_to_back_and_use_every_variant(self):
        for line_id in prompts.LINES:
            memory = PromptMemory()
            params = sample_params(line_id)
            seen = [prompts.render(line_id, memory, params) for _ in range(30)]
            for a, b in zip(seen, seen[1:]):
                self.assertNotEqual(a, b, f"{line_id} repeated back to back")
            n = len(prompts.VARIANTS[line_id])
            if n >= 2:
                expected = {prompts._fill(v, params) for v in prompts.VARIANTS[line_id]}
                self.assertEqual(set(seen[:n]), expected, f"{line_id} reused a variant before trying all")

    def test_never_equal_to_emmas_previous_sentence(self):
        memory = PromptMemory()
        for _ in range(10):
            memory.recent.append("Can I get your name?")
            self.assertNotEqual(prompts.render("ask.name", memory), "Can I get your name?")

    def test_render_records_into_memory(self):
        memory = PromptMemory()
        text = prompts.render("ask.when", memory)
        self.assertEqual(len(memory.used["ask.when"]), 1)
        self.assertEqual(memory.recent[-1], text)
        for _ in range(20):
            prompts.render("ask.when", memory)
        self.assertLessEqual(len(memory.recent), 10)

    def test_a_variant_needing_a_missing_param_is_skipped(self):
        for _ in range(10):
            self.assertNotIn("{", prompts.render("ask.time", PromptMemory()))
            self.assertNotIn("None", prompts.render("ask.time", PromptMemory(), {"day": None}))
        with self.assertRaises(KeyError):
            prompts.render("confirm.phone", PromptMemory())          # no read-back without the number

    def test_a_leading_placeholder_is_capitalised(self):
        memory = PromptMemory()
        texts = {prompts.render("branch.only", memory, {"service": "braces", "branch": "Whitefield"})
                 for _ in range(2)}
        self.assertIn("Braces are done at our Whitefield branch.", texts)

    def test_single_wording_lines_say_like_i_said_rather_than_repeat(self):
        memory = PromptMemory()
        fact = {"text": "A cleaning is usually between 1,000 and 1,500 rupees."}
        first = prompts.render("answer.fact", memory, fact)
        second = prompts.render("answer.fact", memory, fact)
        self.assertEqual(first, fact["text"])
        self.assertEqual(second, "Like I said, a cleaning is usually between 1,000 and 1,500 rupees.")

    def test_rng_is_respected(self):
        import random
        a = [prompts.render("ask.intent", PromptMemory(), rng=random.Random(3)) for _ in range(3)]
        b = [prompts.render("ask.intent", PromptMemory(), rng=random.Random(3)) for _ in range(3)]
        self.assertEqual(a, b)


class OpenerTests(unittest.TestCase):
    def test_opener_added_once_never_the_same_twice_running(self):
        memory = PromptMemory()
        seen = []
        for _ in range(12):
            out = prompts.with_opener("We're open till 9.", memory)
            opener = out.split(" ", 1)[0]
            self.assertIn(opener, prompts.OPENERS)
            self.assertTrue(out.endswith("we're open till 9."), out)
            seen.append(opener)
        self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])))

    def test_no_opener_on_a_reply_that_has_one_or_on_a_summary(self):
        memory = PromptMemory()
        for text in ("Sure, take your time.", "Okay, Tuesday instead.", "So that's a cleaning, shall I book it?",
                     "That's 9 8 7 6 5, 4 3 2 1 0, right?", "No worries, we'll find something."):
            self.assertEqual(prompts.with_opener(text, memory), text)

    def test_names_keep_their_capital(self):
        out = prompts.with_opener("Dr Rao is at Nagarbhavi.", PromptMemory())
        self.assertTrue(out.endswith("Dr Rao is at Nagarbhavi."))
        out = prompts.with_opener("I can do that.", PromptMemory())
        self.assertTrue(out.endswith("I can do that."))

    def test_softener_at_most_every_four_turns_and_never_on_numbers(self):
        memory = PromptMemory()
        self.assertRegex(prompts.with_softener("what would you like to do?", memory), r"^(So|Umm), ")
        self.assertEqual(prompts.with_softener("What would you like to do?", memory), "What would you like to do?")
        memory.turns_since_umm = 5
        self.assertEqual(prompts.with_softener("That's 5:30 on Monday.", memory), "That's 5:30 on Monday.")

    def test_checking_phrase_varies_and_never_repeats_back_to_back(self):
        memory = PromptMemory()
        seen = [prompts.checking_phrase(memory) for _ in range(12)]
        self.assertEqual(set(seen[:4]), set(prompts.VARIANTS["checking"]))
        self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])))
        self.assertIn(phrases.CHECKING, prompts.VARIANTS["checking"])   # the old engine's cached phrase


class SpeakableTests(unittest.TestCase):
    def test_phone_read_back_in_groups(self):
        self.assertEqual(prompts.speak_phone("+919876543210"), "9 8 7 6 5, 4 3 2 1 0")
        self.assertEqual(prompts.speak_phone("9876543210"), "9 8 7 6 5, 4 3 2 1 0")

    def test_slot_reads_like_a_person(self):
        self.assertEqual(prompts.speak_slot(datetime(2026, 10, 5, 17, 0), today=TODAY), "Monday the 5th at 5")
        self.assertEqual(prompts.speak_slot(datetime(2026, 10, 2, 17, 30), today=TODAY), "tomorrow at 5:30")
        self.assertEqual(prompts.speak_slot(datetime(2026, 10, 5, 7, 0), today=TODAY),
                         "Monday the 5th at 7 in the morning")
        self.assertEqual(prompts.speak_slot(datetime(2026, 10, 5, 13, 0), today=TODAY, with_day=False), "1")

    def test_day_names(self):
        self.assertEqual(prompts.speak_day(TODAY, today=TODAY), "today")
        self.assertEqual(prompts.speak_day(date(2026, 10, 2), today=TODAY), "tomorrow")
        self.assertEqual(prompts.speak_day(date(2026, 10, 3), today=TODAY), "Saturday the 3rd")
        self.assertEqual(prompts.speak_day(date(2026, 10, 12), today=TODAY), "Monday the 12th")
        self.assertEqual(prompts.speak_day(date(2026, 10, 22), today=TODAY), "22 October")
        self.assertEqual([prompts.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 31)],
                         ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "31st"])

    def test_times_only_say_morning_or_evening_when_it_could_confuse(self):
        self.assertEqual(prompts.speak_time(time(17, 0)), "5")
        self.assertEqual(prompts.speak_time(time(17, 30)), "5:30")
        self.assertEqual(prompts.speak_time(time(7, 0)), "7 in the morning")
        self.assertEqual(prompts.speak_time(time(19, 0)), "7 in the evening")
        self.assertEqual(prompts.speak_time(time(20, 30)), "8:30 in the evening")
        self.assertEqual(prompts.speak_time(time(12, 0)), "12")
        self.assertEqual(prompts.speak_time(time(13, 30)), "1:30")

    def test_lists(self):
        self.assertEqual(prompts.speak_list([]), "")
        self.assertEqual(prompts.speak_list(["Whitefield"]), "Whitefield")
        self.assertEqual(prompts.speak_list(["Indiranagar", "Whitefield"]), "Indiranagar or Whitefield")
        self.assertEqual(prompts.speak_list(["A", "B", "C"], "and"), "A, B and C")

    def test_the_callers_preference_said_back(self):
        day = DateConstraint(date(2026, 10, 2), date(2026, 10, 2))
        evening = TimeConstraint("window", time(16), time(21), label="evening")
        self.assertEqual(prompts.speak_when(day, evening, today=TODAY), "tomorrow evening")
        self.assertEqual(prompts.speak_when(day, TimeConstraint("exact", time(18), label="around 6"), today=TODAY),
                         "tomorrow around 6")
        monday = DateConstraint(date(2026, 10, 5), date(2026, 10, 5))
        self.assertEqual(prompts.speak_when(monday, evening, today=TODAY), "Monday the 5th, in the evening")
        self.assertEqual(prompts.speak_when(monday, None, today=TODAY), "Monday the 5th")
        next_week = DateConstraint(date(2026, 10, 5), date(2026, 10, 10), "range")
        self.assertEqual(prompts.speak_when(next_week, None, today=TODAY), "next week")
        this_week = DateConstraint(date(2026, 10, 1), date(2026, 10, 3), "range")
        self.assertEqual(prompts.speak_when(this_week, None, today=TODAY), "this week")
        self.assertEqual(prompts.speak_when(None, TimeConstraint("window", time(17), time(21), label="after 5")),
                         "after 5")
        self.assertEqual(prompts.speak_when(None, evening), "in the evening")
        self.assertEqual(prompts.speak_when(DateConstraint(TODAY, TODAY), evening, today=TODAY),
                         "this evening")
        earliest = DateConstraint(TODAY, date(2026, 11, 30), "earliest")
        self.assertEqual(prompts.speak_when(earliest, None, today=TODAY), "the earliest you can")

    def test_services_said_naturally(self):
        self.assertEqual(prompts.speak_service("Root Canal Treatment"), "a root canal")
        self.assertEqual(prompts.speak_service("Teeth Cleaning"), "a cleaning")
        self.assertEqual(prompts.speak_service("Tooth Extraction"), "an extraction")
        self.assertEqual(prompts.speak_service("Braces"), "braces")
        self.assertEqual(prompts.speak_service("Gum Surgery"), "a gum surgery")
        self.assertEqual(prompts.service_plural("Tooth Filling"), "fillings")


class CacheTests(unittest.TestCase):
    def test_static_lines_have_no_placeholders_and_are_single_sentences(self):
        for text in prompts.static_lines(cached_only=False):
            self.assertNotIn("{", text)
            self.assertEqual(speech.split_sentences(text), [text])

    def test_cached_lines_fit_the_elevenlabs_budget(self):
        before = set(_old_phrases())
        extra = [t for t in prompts.static_lines() if t not in before]
        self.assertLessEqual(sum(len(t) for t in extra), prompts.CACHE_CHAR_BUDGET)

    def test_every_cached_line_is_pre_rendered(self):
        everything = set(phrases.all_phrases())
        for text in prompts.static_lines():
            self.assertIn(text, everything)
        # the checking phrases are pre-rendered for the call session's before_action
        for text in prompts.VARIANTS["checking"]:
            self.assertIn(text, everything)


def _old_phrases():
    """What all_phrases() held before the R2 lines were added."""
    import config
    return [*config.GREETINGS, config.HONEST_LINE, *phrases.FIXED_SENTENCES, *phrases.FILLERS,
            *phrases.OPENERS, phrases.CHECKING, phrases.ERROR_REPLY]


if __name__ == "__main__":
    unittest.main()
