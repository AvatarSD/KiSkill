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
- [x] Serializer byte-fidelity COMPLETE for all four formats: sch/sym/mod
      (eeschema pts rule) and pcb (pts ≤5 inline / 4-per-line + col-72
      token wrap). Real-board soak (2.3 MB, 387k tokens) gates
      test_roundtrip via KX_DONOR_PCB.
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
- [x] KiCad 11 nightly installed (user) → LIVE SCHEMATIC IPC VALIDATED:
      kipy master bootstrap (tools/bootstrap_kipy.sh), live read/create/
      commit/remove on running eeschema (tests/test_live_ipc.py 11/11),
      handler-gap map (standalone frames lack common handler: no save/
      revert/run_action). Follow-ups: re-run gap map on nightly updates;
      try eeschema-from-PM for the full handler union (1 GUI click or
      xdotool); GUI→file persistence via get_items reconstruction if
      upstream save stays missing.
- [x] Live-IPC atomic ops: kx live snap/check/wire/junction/label/text/rm
      (live_ops.py) — net-blind verifier gate before every push, refusal
      sends nothing, push = one GUI undo step; tests/test_live_ops.py
      16/16 leaves the doc as found. Follow-ups: symbol place over IPC
      (definition is packed Any — needs unpack_any work), pin world pos
      from on-disk lib cache for net-aware live rules, update_items
      (move) op.
- [ ] Forum sweep cadence: one distilled lesson per tick max into skills.
