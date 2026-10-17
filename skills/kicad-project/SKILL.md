---
name: kicad-project
description: >
  Open, inspect, and safely manipulate any KiCad project through the
  CLEAN→PROBED→STAGED→VERIFIED→RENDERED→REVIEWED→COMMITTED state machine.
  Use when starting any KiCad work session, setting up lib tables, or when
  unsure whether it is safe to write to a project.
---

# KiCad project state machine

Repo: `$(kx root)` (engine + design docs).

## Session entry protocol (always)

1. `kx env PROJECT_DIR` — one call: kicad-cli (+nightly) version, lock
   files (tilde = literal filename prefix), IPC socket/aliveness,
   recommended backend, the API-server setting of every install
   (`api_server_settings`) and stale sockets. If
   `backends.file.writable_now` is false, KiCad has the project OPEN: do
   not write files (a .kicad_pcb write is refused outright); for the
   board use live mode (`kx live pcb …`, kicad-pcb skill), otherwise ask
   the user to close/reload, or work on a scratch copy.
   `kx` = ~/.local/bin/kx → repo bin/kx (self-sets PYTHONPATH; works
   from any cwd). Python API instead: run with
   PYTHONPATH=$(kx root) (no pip install).
   kicad-cli is resolved by kicad_lib/kcli.py: KX_KICAD_CLI env →
   native on PATH → flatpak → nightly. VERSION-SKEW RULE: evidence
   (ERC/render/netlist) must come from the same engine that writes the
   files — if the user edits with the v11 nightly, set
   `KX_KICAD_CLI=kicad-cli-nightly` for the session; a stable CLI may
   misread nightly-saved files and the diff/ERC evidence silently lies.
2. Git: checkpoint-commit the project BEFORE the first modification.
3. `kx probe FILE` — inventory (uuid, paper, symbols/refs, labels,
   sheets, cached libs).

## State machine (DESIGN.md §3)

CLEAN → PROBED → STAGED → VERIFIED (geometric verifier 0 violations) →
RENDERED (svg→png, ERC run) → REVIEWED (pixel+semantic+ERC diff judged
against rule canon) → COMMITTED. Never skip a state; never write a file
that has not passed VERIFIED; never commit one that has not been REVIEWED.

## Open project: file vs live vs regen (decide from `kx env`)

| The project is…                         | Do                                                   |
|-----------------------------------------|------------------------------------------------------|
| not open (`writable_now: true`)         | file edits / plain regeneration                      |
| open, board                             | `kx live pcb …` (kicad-pcb skill), IPC, 1 undo step each |
| open, schematic, GENERATED (gen script) | `kx live sch regen -- CMD` / the script's `--live`: save → regen → revert, the change appears in the open editor (kicad-schematic §12) |
| open, schematic, hand-drawn, KiCad 10   | no live path (`backends.ipc.sch: list-only`): scratch copy, or ask the user to save + close |
| open, schematic, v11 nightly            | `kx live` item ops (kicad-schematic §11)             |

`backends.ipc.sch` is MEASURED per server (`sch_capabilities`): none /
list-only / save+revert / items. `backends.live_sch` says whether
xdotool is here (an editor on another host needs it there).

## Live IPC — board on KiCad 10 (validated 10.0.7 flatpak)

PCB editor only on 10.x: see kicad-pcb skill "Live mode" (`kx live pcb`,
`kx live connect --host H`, `kx live headless`). `kx live headless start`
also takes a .kicad_sch (standalone eeschema) or a .kicad_pro (project
manager + its schematic editor, opened with Ctrl+E — the operator's
setup; its File menu and handler set differ from standalone). The scope is decided by
the SERVER's version (`ipc_server_version`), not the local kicad-cli.

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
