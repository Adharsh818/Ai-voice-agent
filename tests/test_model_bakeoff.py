"""R2.7 bake-off tool: scoring, the recommendation rule and the blind ratings round trip."""

import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import model_bakeoff as mb  # noqa: E402


def turn(n, *, model=True, ok=True, ms=1000, dropped=(), fallback=False, say=False, emma="Sure.", caller="hi"):
    return {"n": n, "caller": caller, "emma": emma, "llm_called": model, "llm_ok": ok if model else None,
            "llm_error": None if ok else "timeout", "engine_ms": ms,
            "trace": {"dropped": list(dropped), "fallback": fallback, "used_model_say": say, "used_model_ask": False}}


def record(rid, turns, *, goal=True, counts=None):
    return {"id": rid, "turns": turns, "llm": {"quota_exhausted": False},
            "metrics": {"goal_met": goal, "counts": counts or {}}}


class ScoreRunTests(unittest.TestCase):
    def test_counts_only_model_turns(self):
        recs = [record("c1", [turn(1, model=False, ms=5), turn(2, ms=800, dropped=["facts"], say=True),
                              turn(3, ms=1200, fallback=True), turn(4, ok=False, ms=2600)]),
                record("c2", [turn(1, ms=1000)], goal=False, counts={"Z2": 1, "M3": 2})]
        s = mb.score_run(recs)
        self.assertEqual(s["model_turns"], 4)
        self.assertEqual(s["failed"], 1)
        self.assertEqual(s["goal_met_pct"], 50.0)
        self.assertEqual(s["zero_tolerance"], {"Z2": 1})          # M3 is not zero-tolerance
        self.assertEqual(s["drop_rules"], {"facts": 1})
        self.assertEqual(s["turns_with_drop_pct"], 25.0)
        self.assertEqual(s["fallback_pct"], 25.0)
        self.assertEqual(s["p50_ms"], 1000)                         # the failed turn's time is left out


class RecommendTests(unittest.TestCase):
    def base(self, **kw):
        s = {"model_turns": 50, "quota_exhausted": False, "zero_total": 0, "goal_met_pct": 95.0,
             "turns_with_drop_pct": 5.0, "fallback_pct": 2.0, "p50_ms": 1500}
        s.update(kw)
        return s

    def test_zero_tolerance_hits_lose_even_when_faster(self):
        model, _ = mb.recommend({"lite": self.base(zero_total=1, p50_ms=800), "flash": self.base()})
        self.assertEqual(model, "flash")

    def test_close_outcomes_then_fewer_drops_then_speed(self):
        scores = {"lite": self.base(goal_met_pct=93.0, p50_ms=900), "flash": self.base(p50_ms=1600)}
        self.assertEqual(mb.recommend(scores)[0], "lite")
        scores["lite"]["turns_with_drop_pct"] = 20.0
        self.assertEqual(mb.recommend(scores)[0], "flash")

    def test_a_clearly_worse_outcome_is_not_close(self):
        scores = {"lite": self.base(goal_met_pct=80.0, p50_ms=700), "flash": self.base()}
        self.assertEqual(mb.recommend(scores)[0], "flash")

    def test_runs_without_model_turns_are_unusable(self):
        self.assertIsNone(mb.recommend({"lite": self.base(model_turns=0)})[0])


class RatingsTests(unittest.TestCase):
    def test_blind_sheet_scores_back_to_each_model(self):
        per_model = {
            "lite": [record("a", [turn(i, say=True, emma=f"lite {i}") for i in range(1, 4)])],
            "flash": [record("b", [turn(i, say=True, emma=f"flash {i}") for i in range(1, 4)])],
        }
        items, key = mb.build_sheet(per_model, k=3, seed=1)
        self.assertEqual(len(items), 6)
        sheet = mb.ratings_md("x", items)
        self.assertNotIn("lite |", sheet.replace("| lite ", ""))    # the model column is never shown
        scored = []
        for line in sheet.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 4 and cells[0].isdigit():
                line = line.rstrip()[:-3] + (" 5 |" if cells[2].startswith("flash") else " 3 |")
            scored.append(line)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "model-bakeoff-x-ratings.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(scored))
            with open(os.path.join(tmp, "model-bakeoff-x-key.json"), "w", encoding="utf-8") as fh:
                json.dump({"key": key}, fh)
            result = mb.score(path)
        self.assertEqual(result, {"flash": {"rated": 3, "mean": 5.0}, "lite": {"rated": 3, "mean": 3.0}})

    def test_only_model_written_replies_are_sampled(self):
        recs = [record("a", [turn(1, say=False), turn(2, say=True, emma="model line"), turn(3, model=False)])]
        self.assertEqual([s["emma"] for s in mb.samples(recs, 5, random.Random(0))], ["model line"])


if __name__ == "__main__":
    unittest.main()
