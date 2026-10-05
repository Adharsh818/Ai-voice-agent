"""
R2, the natural conversation engine (docs/R2_DESIGN.md).

The LLM carries the conversation; Python decides every action and every fact
that is spoken (docs/NORTH_STAR.md, principle 4). The package replaces the
12-step state machine in ai_engine.py, which stays the public facade.

    context.py   the per-call context (picklable), goals, understanding, plans
    match.py     deterministic matchers: yes/no, digits, names, services,
                 branches, doctors, fragments (used by tier0.py and apply.py)
    apply.py     applies one Understanding to the context: entities,
                 corrections, intent switches, notices
    policy.py    next_goal(): the checklist, priority order, loop breaker and
                 steer-back policy
    book.py      BOOK workflow: branch-aware search, holds, summary, commit
    manage.py    MANAGE workflow: verify, check, cancel, reschedule
    handlers.py  emergencies, honesty, person request, language, abuse,
                 repeat / wait / fragments / silence / closing
    brief.py     the per-turn brief sent to the model
    validate.py  reply validators (facts, wording, action claims, length)
    engine.py    the turn pipeline behind ai_engine.async_process_turn
    runtime.py   what a turn may touch: db thread, catalog, knowledge, progress
    testing.py   test fixtures: a DEMO-seeded throwaway clinic

Nothing in this package imports ai_engine (the facade imports us).
"""
