---
name: kicad-improve
description: >
  Self-improvement loop for the kicad skill pack: each tick picks one
  backlog item, researches forums if needed, builds/tests against the
  fixture project, commits, and feeds lessons back into the skills.
  Use on /loop ticks or when asked to improve the kicad skills.
---

# Self-improvement loop (one tick = one slice)

Repo: `~/prj/20260610_kicad-agent-skills`. Never test against real user
projects (read-only soak targets only); use `tests/fixtures/`.

## Tick protocol

1. `git -C ~/prj/20260610_kicad-agent-skills log --oneline | head` +
   read `doc/BACKLOG.md` — pick the TOP unchecked item only.
2. If the item needs domain knowledge: ONE focused forum/web sweep
   (kicad.info, EEVblog, Hackaday, official dev-docs); distill into the
   relevant SKILL.md or doc/DESIGN.md — cite the source.
3. Build the slice; test on fixtures (`python3 tests/test_roundtrip.py`
   plus slice-specific test); mutation-test any new verifier rule.
4. Commit (one commit per step, no Co-Authored-By trailer).
5. Feed back: update the SKILL.md that the lesson belongs to; tick the
   backlog box; append new discoveries as new backlog items.
6. End the tick by suggesting the user run /compact.

## Quality bar

SOLID, zero-copy where sensible, no dead code, no legacy left behind;
every behavior provable by a command the next tick can rerun.
