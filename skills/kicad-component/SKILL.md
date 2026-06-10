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
3. `kicad_lib/index.py` (sqlite FTS index) — TODO, build on first need:
   scan all libs → (lib, name, pins, keywords, fp_filters, datasheet).

## Internet fetch

- **LCSC/JLC part known** (preferred): `pip install easyeda2kicad`;
  `easyeda2kicad --full --lcsc_id=C2040 --output PROJECT/easyeda2kicad`
  → symbol + footprint + 3D model; then register both lib tables.
- **SnapEDA/UltraLibrarian zip** (user downloads, no open API): unzip,
  copy .kicad_sym + .pretty into project lib dir, register, normalize
  with `kicad-cli sym upgrade` / `fp upgrade` if format is old.
- Always sanity-check fetched footprints visually: render via
  `kicad-cli pcb export svg` on a scratch board, compare against the
  datasheet land pattern. Internet footprints are guilty until proven.

## Registration

Append to project `sym-lib-table`: `(lib (name "x")(type "KiCad")
(uri "${KIPRJMOD}/x.kicad_sym")(options "")(descr ""))` — same shape for
`fp-lib-table` with a `.pretty` dir URI. Missing tables: create them.
