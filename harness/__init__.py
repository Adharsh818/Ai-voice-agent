"""
Conversation test harness for Emma (docs/SUCCESS_CRITERIA.md, section 3).

The owner's instruction is "first test all the cases, then fix", so this
package drives whole calls through the dialogue engine in text, scores every
transcript automatically, and produces the failure catalogue that decides the
fix order.

    world.py           an isolated clinic per conversation: temp SQLite, DEMO seed, frozen clock
    engine_adapter.py  the only module that knows the engine's internals
    fake_nlu.py        deterministic rule-based NLU for offline runs (no network)
    lines.py           reading Emma's lines: what is she asking, which values did she say
    sim_caller.py      adaptive rule-based simulated caller with seeded disruptions
    scenarios.py       the scripted catalogue: regressions from real calls plus edge cases
    metrics.py         the Automatic rows of SUCCESS_CRITERIA.md, per conversation
    runner.py          runs suites offline or live and writes harness_runs/<run-id>/
    report.py          metric table vs targets, scenario results, failure catalogue

    python -m harness run regression            the regression scenarios, offline
    python -m harness run sim --n 200 --seed 1  200 simulated callers
    python -m harness run catalogue --live      the catalogue against real Gemini

tools/converse.py lets an AI agent play a caller one turn at a time.
"""
