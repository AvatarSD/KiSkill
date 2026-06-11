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
  - sweep 1 (2026-06-11, kicad.info t/35552+t/57016): `power_pin_not_driven`
    = missing DRIVER decl, not wiring; fix = PWR_FLAG (≠ PWRGND) at rail's
    passive source. GOTCHA proven: netlist export DROPS PWR_FLAG nodes →
    blind to it (ERC sees it). Shipped `kx power-audit` + test_power_audit
    (9/9) + skill knowledge in kicad-schematic §7 & kicad-review.
  - sweep 2 (2026-06-11, kicad.info unused-pin threads + KLC S4.5):
    multi-unit completeness — every unit of a dual/quad part must be placed,
    a spare still needs tie-off, else ERC `missing_unit`/`missing_input_pin`.
    Lockable cold from the existing testproj MCP6002 (dual opamp, 3 units).
    Shipped `kx unit-audit` (probe lib_unit_counts + per-ref placed-vs-
    expected) + test_unit_audit (11/11, audit agrees with ERC) + skill
    knowledge in kicad-schematic §7 & kicad-review.
  - sweep 3 (2026-06-11, kicad.info t/46229+t/21294): no-connect-flag
    discipline. Genuinely-unused pin → add a NC flag to silence
    `pin_not_connected` (document intent, don't lower severity). Inverse trap
    `no_connect_connected` (underscores — verified) = NC flag on a WIRED
    node; delete flag or wire, not both. Lockable cold after all (no new
    fixture): inject NC on a live junction → code appears. Shipped
    test_nc_discipline (6/6, pure test) + skill knowledge in kicad-schematic
    §7 & kicad-review. No new `kx` cmd: the only SOUND file-level NC check is
    the rare NC-on-junction; the valuable one (flag MISSING NC on unused
    pins, the `pin_not_connected` direction) needs pin-world-pos → blocked on
    the live-IPC "pin world pos from lib cache" follow-up above.
  - sweep 4 (2026-06-11, kicad.info t/32585 + Annotation docs): reference
    uniqueness/annotation — completes the power/unit/reference pre-flight
    trio. PROVEN gotcha: `kicad-cli sch erc --severity-all` is 0/0 BLIND to
    BOTH duplicate designators (two R1) and unannotated `?` — they're
    Annotation-tool checks (SCH_REFERENCE_LIST), not ERC. Shipped
    `kx ref-audit` (multi-unit aware: U1A/B/C sharing "U1" not a dup) +
    test_ref_audit (11/11, ERC blind while audit catches) + a "what headless
    ERC does NOT catch" block in kicad-review & kicad-schematic §7.
  - NEXT candidate (unblocked): default-DISABLED ERC checks. The erc .rpt
    footer lists "Ignored checks": global-label-appears-once (catches label
    typos), four-connection-points-joined, SPICE model issue, footprint-
    filter-mismatch. kicad-cli runs the project severity config, so these
    stay off — document how to enable (severity overrides) + which matter,
    and lock by parsing the "Ignored checks" footer. Pairs with the
    ERC-blindness theme (sweeps 1 & 4).
