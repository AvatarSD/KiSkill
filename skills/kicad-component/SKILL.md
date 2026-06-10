---
name: kicad-component
description: >
  Find, fetch, and register KiCad components: search local/official symbol
  and footprint libraries, download missing parts from the internet
  (easyeda2kicad by LCSC id, SnapEDA/UltraLibrarian zips), and register
  them in project lib tables. Use when a schematic needs a part that is
  not already in the project.
---

# Component sourcing & indexing

## Local search order

1. Project libs (sym-lib-table next to .kicad_pro, `${KIPRJMOD}` URIs).
2. Official flatpak libs:
   `~/.local/share/flatpak/runtime/org.kicad.KiCad.Library.Symbols/x86_64/stable/active/files/symbols/*.kicad_sym`
   (footprints idem, `.../Library.Footprints/.../footprints/*.pretty`).
   Grep symbol names: `grep -l '(symbol "NAME' *.kicad_sym` then extract
   the balanced block with `kicad_lib.sexp`.
3. **Index (implemented):** `cd ~/prj/20260610_kicad-agent-skills &&
   python3 -m kicad_lib.cli find dual comparator` → ranked lib_id hits
   with description/keywords/pins. First run `kx index [PROJECT_LIB_DIR]`
   (22k official symbols ≈ 20 s once; mtime-incremental ≈ 0.1 s after).
   DB: ~/.cache/kx_scratch/symbols.sqlite; `extends` children inherit
   base description/keywords so families are searchable.

## Internet fetch

- **LCSC/JLC part known** (implemented, preferred):
  `python3 -m kicad_lib.cli fetch C25804 /path/to/project` → symbol +
  footprint + 3D (wrl+step) in PROJECT/libs/ AND both lib tables
  registered (${KIPRJMOD} URIs), idempotent. Binary lives in the repo
  .venv (PEP 668 box); fetch needs real network (sandbox blocks DNS —
  run unsandboxed). v1.0.1 quirk handled: --project-relative resolves
  against CWD, so kx runs it from the project dir.
- **SnapEDA/UltraLibrarian zip** (implemented; user downloads, no open
  API): `python3 -m kicad_lib.cli import-zip part.zip /path/to/project`
  → libs/<name>.kicad_sym + .pretty + tables; legacy .lib auto-upgraded
  via kicad-cli sym upgrade.
- Always sanity-check fetched footprints visually: render via
  `kicad-cli pcb export svg` on a scratch board, compare against the
  datasheet land pattern. Internet footprints are guilty until proven.

## Registration

Append to project `sym-lib-table`: `(lib (name "x")(type "KiCad")
(uri "${KIPRJMOD}/x.kicad_sym")(options "")(descr ""))` — same shape for
`fp-lib-table` with a `.pretty` dir URI. Missing tables: create them.
