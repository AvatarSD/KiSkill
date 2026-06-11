---
name: kicad-project
description: >
  Open, inspect, and safely manipulate any KiCad project through the
  CLEAN→PROBED→STAGED→VERIFIED→RENDERED→REVIEWED→COMMITTED state machine.
  Use when starting any KiCad work session, setting up lib tables, or when
  unsure whether it is safe to write to a project.
---

# KiCad project state machine

Repo: `~/prj/20260610_kicad-agent-skills` (engine + design docs).

## Session entry protocol (always)

1. `kx env PROJECT_DIR` — one call: kicad-cli (+nightly) version, lock
   files (tilde = literal filename prefix), IPC socket/aliveness,
   recommended backend. If `backends.file.writable_now` is false, KiCad
   has the project OPEN: do not write; ask the user to close/reload, or
   work on a scratch copy.
   `kx` = ~/.local/bin/kx → repo bin/kx (self-sets PYTHONPATH; works
   from any cwd). Python API instead: run with
   PYTHONPATH=~/prj/20260610_kicad-agent-skills (no pip install).
2. Git: checkpoint-commit the project BEFORE the first modification.
3. `kx probe FILE` — inventory (uuid, paper, symbols/refs, labels,
   sheets, cached libs).

## State machine (DESIGN.md §3)

CLEAN → PROBED → STAGED → VERIFIED (geometric verifier 0 violations) →
RENDERED (svg→png, ERC run) → REVIEWED (pixel+semantic+ERC diff judged
against rule canon) → COMMITTED. Never skip a state; never write a file
that has not passed VERIFIED; never commit one that has not been REVIEWED.

## Live IPC against the v11 nightly (validated 10.99.0 build 87de73b)

- Enable once (KiCad closed): set `api.enable_server: true` in
  `~/.config/kicad/10.99/kicad_common.json`.
- Launch on a SCRATCH COPY (the nightly upgrades file formats on save):
  `DISPLAY=:1 sh -c '. /usr/share/kicad-nightly/kicad-nightly.env &&
  /usr/lib/kicad-nightly/bin/eeschema COPY.kicad_sch'` in background.
  GUI needs X11 — pick the `DISPLAY` where `xset q` answers. Kill it
  later with pkill; then rm stale `~*.lck` files.
- Socket appears at `/tmp/kicad/api.sock` within ~1 s; verify with
  `kx env` → `ipc_alive: true`, `open_documents` lists the schematic.
- kipy MASTER is required (PyPI 0.7.1's schematic module is broken
  against its own protos). Bootstrap: `tools/bootstrap_kipy.sh`
  (protoc 29.x in ~/.local + protol; .pth-installs .tools/kicad-python).
- HANDLER MAP (each frame registers its own handlers in-process):
  - standalone eeschema HANDLES: get_open_documents, get_schematic,
    get_items/symbols/lines/labels/text, begin/push commit,
    create_items, remove_items — full live editing, undoable in GUI.
  - standalone eeschema LACKS (ApiError "no handler"): ping,
    get_version, save, save_as, revert, run_action — the agent CANNOT
    persist from IPC; the USER saves (Ctrl+S), or do at-rest edits via
    the file backend instead.
  - kicad PM process handles ONLY ping + get_version (no frames).
    Opening eeschema FROM the PM should merge both sets — needs one GUI
    click (no xdotool on this box). Re-test on newer nightlies:
    tests/test_live_ipc.py prints the gap map and flags improvements.
- live.py's ipc_ping treats a structured ApiError as alive (transport
  answered). `kicad-cli-nightly` (wrapper in /usr/bin) is the only way
  to run the nightly CLI — the raw binary fails on LD_LIBRARY_PATH.

## Project hygiene

- References are global across ALL sheets — collision-check project-wide.
- Preserve root uuid and per-sheet uuids; instance paths depend on them.
- sym-lib-table / fp-lib-table: project-local libs use `${KIPRJMOD}` URIs.
- Scratch outputs (svg_out/, *.rpt, *.net, *.png) never get committed.
