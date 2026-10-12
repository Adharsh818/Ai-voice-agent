"""
The conversation regression suite (docs/SUCCESS_CRITERIA.md section 3, layer 1).

Every scripted scenario in harness/scenarios.py runs offline here: a fresh
DEMO-seeded clinic, the clock frozen at Thursday 1 Oct 2026 10:00, the fake
NLU instead of Gemini, so each one is deterministic and takes well under a
second. A scenario passes when its expected properties hold (the database
outcome, phrases that must or must not be said, metrics with no findings),
never on Emma's exact wording.

One test_<scenario id> per scenario, added below. The HANDOFF section 5 calls
(location "No" loop, cancel at the recap, braces at Nagarbhavi, the time-only
fragment, meta and price questions, fragments) are the reg_* ones. A scenario
whose `bug` names a known defect is an expected failure (none today: the 40
that named defects of the retired 12-step machine pass on R2 and were
dropped with it).

    python -m harness run scenarios --engine r2    full transcripts and a report
"""

import unittest

from harness import report, runner
from harness import scenarios as sc


class Scenarios(unittest.TestCase):
    """Every scenario on the engine, as a test."""

    def check(self, scenario_id: str):
        record = runner.run_scenario(scenario_id)
        if record["passed"]:
            return
        failed = "; ".join(report._check_text(c) for c in record["checks"] if not c["ok"])
        tail = "\n".join(f"  {t['n']:>2} caller: {t.get('caller') or '(silence)'}\n     emma:   {t['emma']}"
                         for t in record["turns"][-4:])
        self.fail(f"{scenario_id} ({record['title']}): {failed}\nlast turns:\n{tail}")


def _add_tests():
    for scenario in sc.SCENARIOS:
        def test(self, scenario_id=scenario.id):
            self.check(scenario_id)
        test.__name__ = f"test_{scenario.id}"
        test.__doc__ = scenario.title
        if scenario.bug:
            test = unittest.expectedFailure(test)
        setattr(Scenarios, f"test_{scenario.id}", test)


_add_tests()


class ScenarioBookkeeping(unittest.TestCase):
    """Keeps this file and harness/scenarios.py in step."""

    def test_every_scenario_has_a_test(self):
        for s in sc.SCENARIOS:
            with self.subTest(scenario=s.id):
                self.assertIsNotNone(getattr(Scenarios, f"test_{s.id}", None))

    def test_scenario_ids_are_unique(self):
        ids = [s.id for s in sc.SCENARIOS]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
