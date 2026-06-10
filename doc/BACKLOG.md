# Backlog (self-improvement loop fuel — top item first)

- [x] Port geom.py (rot_xy, grid, pin world pos) + verify.py (rule set of
      SKILL.md §6) from gen_hiside_kicad.py; mutation-test the verifier.
- [x] Create own minimal test project in tests/fixtures/testproj/
      (RC divider + opamp, ERC-clean) generated via kicad_lib — becomes
      the canonical scratch target for all write-path tests.
- [x] kx atomic write ops (library level): place / wire / label / junction / set_prop /
      delete — DONE at lib level (ops.py); follow-up: argparse CLI surface for write ops.
- [x] diff.py: pixel composite (red/green), semantic probe-diff, ERC
      set-diff; `kx diff REV FILE` — then thicken kicad-review skill.
- [ ] Serializer byte-fidelity: match eeschema corner cases so unchanged
      files round-trip byte-identical (sch currently ~0.5% whitespace drift).
- [x] index.py: sqlite FTS5 over flatpak+project libs; kx index / kx find (22712 symbols, 21 s full / 0.09 s incremental).
- [x] source.py: kx fetch (live-tested C25804: sym+fp+3D+tables) +
      kx import-zip + idempotent register; .venv for PEP 668.
- [x] live.py adapter (file | ipc | headless) + kx env detection.
      DECISION: depend on kipy (.venv) instead of vendoring raw protos —
      kipy ships generated bindings and tracks upstream.
- [ ] kicad-pcb skill: port SKILL.md §10 footprint staging onto kicad_lib.
- [ ] kicad-layout meta-skill: schematic→board flow, freerouting DSN/SES
      loop, DRC-gated iteration.
- [ ] kicad-fab skill: BOM (grouped, JLC/LCSC columns), gerbers, pos,
      jobsets via kicad-cli.
- [ ] kicad-emsim skill: gerber2ems + openEMS install path (conda-forge or
      apt), field-slice PNG feedback to agent.
- [ ] Subagent e2e: spawn subagent per skill against testproj; record
      gaps as new backlog items.
- [ ] KiCad 11 nightly (needs sudo PPA or source build — user action);
      then live schematic IPC via kipy in live.py.
- [ ] Forum sweep cadence: one distilled lesson per tick max into skills.
