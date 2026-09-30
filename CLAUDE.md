# Emma: Pearl Dental voice receptionist

**Before any work in this repo, read [docs/NORTH_STAR.md](docs/NORTH_STAR.md) and check the change against its checklist.**

The goal is not just a voice agent. Callers should feel they're talking to a real, skilled receptionist: natural greeting, human phrasing, clinic sounds that follow the call (no background bed, nothing constant), listening tuned for Indian-accented English, and problems solved on the call rather than handed off. Emma never volunteers that she's automated, but never claims to be human when sincerely asked. Python, not the LLM, decides every booking action and every fact that is spoken.

- **Current status, the owner's latest feedback and next steps: [docs/HANDOFF.md](docs/HANDOFF.md). Read it before starting work.**
- Plan and locked decisions: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)
- Demo date: 2026-10-08. Scope: A+B complete, C demo-grade.
- Run tests: `.\.venv\Scripts\python.exe -m unittest discover -s tests`
- Line endings are LF. Write files with `newline="\n"`, never CRLF.
