---
name: kicad-fab
description: >
  Produce fabrication outputs for a KiCad project: grouped engineering
  BOM, JLCPCB-format BOM and CPL placement file, gerbers and excellon
  drill. Use when asked to export a BOM, prepare files for JLCPCB/PCBWay,
  generate gerbers, or package a board for manufacturing.
---

# Fabrication outputs

Engine: `$(kx root)/kicad_lib/fab.py`. One call:

    python3 -m kicad_lib.cli fab SCH PCB OUTDIR     # either may be "-"

Produces: `bom.csv` (grouped by Value+Footprint, Qty column),
`bom_jlc.csv` (Comment/Designator/Footprint/LCSC Part #, DNP excluded),
`gerbers/` (+ excellon .drl), `cpl.csv` (mm, both sides).

## Notes

- JLC assembly needs an **LCSC field on each symbol** — full snippet
  (PYTHONPATH=<repo>): `doc = sexp.load_file(SCH);
  ops.set_prop(doc, "R1", "LCSC", "C25804");
  sexp.save_file(SCH, doc)` — save_file takes PATH FIRST.
  (`kx fetch` tells you the id.)
- Group key is Value+Footprint: identical parts collapse to one row with
  ranges (R1,R2 / Qty 2). DNP parts are excluded from the JLC BOM only.
- Power symbols/flags never reach the BOM (KiCad excludes # refs).
- Gerber sanity: count layers (≥ F.Cu/B.Cu/Edge.Cuts + masks/silk) and
  open one in the render if the board matters.
- Variants (KiCad 10): pass `--variant` via fab.py extension if a
  project uses them — not wired into kx yet.
