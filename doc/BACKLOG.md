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
- [x] kicad-pcb skill + pcb.py (netlist/load_footprint/stage/edge_cuts_bbox/empty_board_from), 14 tests, kicad-cli render gate. Follow-up: kx stage auto-flow (find mods in libs, grid placement).
- [x] kicad-layout skill + layout.py: pcbnew-python DSN/SES bridge +
      bundled freerouting 2.2.4 (.tools, no root/java needed); e2e test
      routes a 2-fp board (9 segments). Follow-up: placement heuristics,
      DRC set-diff wiring, route the full testproj board.
- [x] kicad-fab skill + fab.py: grouped BOM, JLC BOM/CPL, gerbers+drill
      (kx fab; 5 tests). Follow-up: jobsets + --variant passthrough.
- [x] kicad-emsim skill + emsim.py (docker path — daemon accessible, no
      sudo): prepare() from board incl. stackup translation, dockerized
      run(). Smoke sim VALIDATED (meander_loose: rc 0, physical S11).
      Follow-up: port placement on own boards (simulation-port fps).
- [x] Subagent e2e sweep #2: all 7 functional checks PASS cold; 16 gaps
      found and fixed (kx wrapper in ~/.local/bin, save_file order doc,
      probe import, crop formula, donor env override, freerouting glob,
      nightly-aware kx env, stale docstring, DESIGN model.py).
- [ ] KiCad 11 nightly (needs sudo PPA or source build — user action);
      then live schematic IPC via kipy in live.py.
- [ ] Forum sweep cadence: one distilled lesson per tick max into skills.
