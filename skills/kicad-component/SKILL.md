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
3. **Index (implemented):** `kx find dual comparator` → ranked lib_id hits
   with description/keywords/pins. First run `kx index [PROJECT_LIB_DIR]`
   (22k official symbols ≈ 20 s once; mtime-incremental ≈ 0.1 s after).
   DB: ~/.cache/kx_scratch/symbols.sqlite; `extends` children inherit
   base description/keywords so families are searchable.

## Internet fetch

- **LCSC/JLC part known** (implemented, preferred):
  `kx fetch C25804 /path/to/project` → symbol +
  footprint + 3D (wrl+step) in PROJECT/libs/ AND both lib tables
  registered (${KIPRJMOD} URIs), idempotent. Binary lives in the repo
  .venv (PEP 668 box); fetch needs real network (sandbox blocks DNS —
  run unsandboxed). v1.0.1 quirk handled: --project-relative resolves
  against CWD, so kx runs it from the project dir.
- **SnapEDA/UltraLibrarian zip** (implemented; user downloads, no open
  API): `kx import-zip part.zip /path/to/project`
  → libs/<name>.kicad_sym + .pretty + tables; legacy .lib auto-upgraded
  via kicad-cli sym upgrade.
  PROJECT_DIR may be relative or outside the cwd (kx resolves it).
- Always sanity-check fetched footprints visually: render via
  `kicad-cli pcb export svg` on a scratch board, compare against the
  datasheet land pattern. Internet footprints are guilty until proven.

## Vendoring — the project carries its own libraries

`kx vendor PROJ [--check] [--config gen/vendor_cfg.py]` copies ONLY the
used symbols (plus `extends` bases) and footprints of the official
libraries into PROJ/libs/kicad/, every 3D model into PROJ/libs/3d/ with
${KIPRJMOD} paths, and adds project lib-table rows under the SAME
nicknames (the project table wins over the global one; rows tagged by
`descr`, hand rows untouched). Unused copies are pruned. Footprints lose
their silkscreen except one generated polarity mark (cathode bar, "+",
pin-1 dot; policy fields `no_mark_libs`/`bar_libs`/`plus_libs` or a
`mark_kind` callable). Missing models are generated (box from Fab /
courtyard, heights by name fragment; STEP via an OCP Python —
`KX_OCP_PYTHON` — else VRML). Project-specific models: `special`
{lib:name: kind} + `builders` {kind: fn(fab_bbox, pads, name) ->
{"boxes", "shapes"}}; hole-only footprints in `no_model`. Config: a
.json of fields (PROJ/kx-vendor.json is read automatically) or a .py
with `configure(cfg)`. `--check` exits 1 if anything would change — use
it as a CI/pre-commit gate. The board is not touched (Update Footprints
from Library in the PCB editor).

## Registration

Append to project `sym-lib-table`: `(lib (name "x")(type "KiCad")
(uri "${KIPRJMOD}/x.kicad_sym")(options "")(descr ""))` — same shape for
`fp-lib-table` with a `.pretty` dir URI. Missing tables: create them.
