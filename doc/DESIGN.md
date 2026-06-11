# Design — KiCad agent skill pack

Goal: an agent manipulates ANY KiCad project through small, definitive,
individually-verifiable operations — with visual feedback (renders, diffs)
flowing back to the agent at every step.

## 1. Architecture

```
kicad_lib/
  sexp.py     lossless quote-aware s-expression parse/serialize (own impl;
              kiutils' fixed dataclass schema breaks on new format versions,
              kicad-skip is REPL-oriented; we need format-version tolerance)
  geom.py     1.27 mm grid, rotation/mirror math, pin world positions
  (model.py was folded into cli.probe + sexp helpers — no module)
  verify.py   geometric verifier — hard rules, run before every write
  index.py    component index: flatpak libs + project libs → sqlite FTS
  source.py   internet sourcing: easyeda2kicad (LCSC), SnapEDA/UL zip
              import, sym-lib-table/fp-lib-table registration
  diff.py     triple diff: pixel (render A/B → red/green composite),
              semantic (tree diff: symbols/nets added/moved/removed),
              ERC set-diff vs baseline
  live.py     backend adapter: file (KiCad ≤10 sch, lock-file aware) |
              kipy IPC (pcb @ KiCad 9+, sch @ KiCad 11+) | kipy headless
  cli.py      `kx` entry point
```

## 2. Skill pack (workflow-derived, forum-informed)

Pain-point ranking from forums: (1) library/part management, (2) BOM/fab
outputs, (3) review/diff confidence, (4) layout effort, (5) simulation.

| skill           | covers                                                    |
|-----------------|-----------------------------------------------------------|
| kicad-project   | state machine, env detection, lib tables, git hygiene     |
| kicad-component | index/search local libs, fetch from internet, register    |
| kicad-schematic | atomic schematic ops (evolved current skill)              |
| kicad-pcb       | footprint staging, placement, DRC                         |
| kicad-layout    | meta-skill: schematic → placed+routed board (freerouting  |
|                 | DSN/SES loop), iterate against DRC + renders              |
| kicad-review    | visual diff, ERC/DRC set-diff, rule-canon checklist       |
| kicad-fab       | BOM (grouped, LCSC/JLC columns), gerbers, pos, jobsets    |
| kicad-emsim     | openEMS via gerber2ems: EM field sim, field-slice PNGs    |
|                 | rendered back to the agent as feedback                    |

## 3. Project state machine (per manipulation session)

```
CLEAN ──probe──▶ PROBED ──ops──▶ STAGED ──verify──▶ VERIFIED
  ▲                                  │ fail: fix or revert
  │                                  ▼
COMMITTED ◀──commit── REVIEWED ◀──render+diff── RENDERED
```

- CLEAN: git clean or checkpoint-committed; lock files checked.
- PROBED: uuids, refs, occupancy, pin geometry, libs read.
- STAGED: edits exist only in memory / scratch copy.
- VERIFIED: geometric verifier 0 violations; paren balance OK.
- RENDERED: SVG→PNG produced; ERC run.
- REVIEWED: pixel+semantic+ERC diff vs baseline judged by agent
  against the rule canon; only then write/commit.
- Skills MUST NOT skip states (codifies "the loop" of the v1 skill).

## 4. Atomic operation set (the small definitive steps)

probe · find · fetch · place · move · wire · label · junction ·
set_prop · delete · annotate · erc/drc · render · diff · netcheck ·
stage_fp · route · export

Each op: precondition (state, lock, refs unique) → action → postcondition
(grid, verifier, balance). Each op is diffable and commit-sized.

## 5. Rule canon (distilled from forums; two tiers)

Machine-checked (verify.py): grid 1.27 mm; no diagonal wires; junction
dots at every T/cross connection; no cross-net touch/overlap; every pin
claimed by exactly one net; refs unique project-wide; ERC delta vs
baseline = ∅; netlist members == design intent.

Render-judged (kicad-review checklist): signal flow L→R; V+ up, GND down;
power symbols not long wires; text horizontal; decoupling caps adjacent
to their IC; descriptive UPPERCASE net names; polarity marks visible;
every IC pin accounted for; notes for non-obvious choices; title block
filled; no text collisions.

## 6. Real-time strategy (VALIDATED on nightly 10.99.0 / kipy master)

Schematic IPC is real in the v11 nightly: get_schematic → read items,
create_items/remove_items inside begin/push_commit (= GUI undo steps).
live.py picks backend by probing KICAD_API_SOCKET + `kicad-cli version`
(+ /usr/bin/kicad-cli-nightly; 10.99 counts as v11). File mode: refuse
writes while `~*.lck` present. Known nightly gap: standalone frames
lack the common handler (no save/revert/run_action over IPC) — user
saves; tests/test_live_ipc.py re-maps the gap on every run. kipy from
PyPI is broken for schematics; tools/bootstrap_kipy.sh builds master
(.tools/kicad-python + protoc 29.x + protol + .pth). Do NOT fork/patch
KiCad C++ — consume the nightly (apt PPA `ppa:kicad/kicad-dev-nightly`).

## 7. Testing policy

Everything is exercised on fixtures in tests/ and a dedicated scratch
project (tests/fixtures/testproj/). Real user projects are read-only
inputs for round-trip/parse soak tests. CI gate: round-trip token
equality, verifier mutation tests, ERC-delta on fixture edits.

## 8. Key sources

- IPC API: https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/
- kipy docs: https://docs.kicad.org/kicad-python-main/kicad.html
- kicad-skip: https://github.com/psychogenic/kicad-skip
- kiutils: https://github.com/mvnmgrx/kiutils
- KiRI (visual diff prior art): https://github.com/leoheck/kiri
- easyeda2kicad: https://github.com/uPesy/easyeda2kicad.py
- gerber2ems (openEMS PCB sim): https://github.com/antmicro/gerber2ems
- freerouting: https://github.com/freerouting/freerouting
- Rule canon sources: schemalyzer 30 rules, EEVblog #1129, Hackaday
  library-management best practices, KiCad forum threads.
