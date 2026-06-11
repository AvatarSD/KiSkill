---
name: kicad-pcb
description: >
  Stage footprints on a KiCad PCB so Update-PCB-from-Schematic adopts
  them without duplicates, probe board geometry, run DRC, and render
  layers for inspection. Use when asked to put footprints on the pcb,
  prepare a board for layout, or verify a .kicad_pcb change.
---

# PCB footprint staging & board ops

Engine: `$(kx root)/kicad_lib/pcb.py`
(python API: PYTHONPATH=<repo> or cwd=<repo>; CLI: `kx` is on PATH)
(`netlist`, `load_footprint`, `stage`, `edge_cuts_bbox`,
`empty_board_from`). Worked example: `tests/test_pcb.py`.

## Staging flow (Update-PCB adopts, no duplicates)

1. `pcb.netlist(SCH)` → `comps[ref]["tstamps"]` — that uuid IS the link;
   `nets` is the connectivity ground truth (assert designed members!).
   Root-sheet local-label nets are prefixed "/" (`/VOUT`); power nets
   (GND, VCC) are not.
2. `pcb.load_footprint(mod_path, "Lib:Name")` — renames for the board,
   strips file headers (.kicad_mod has no top-level uuid; instances do).
3. `pcb.stage(board, fpdef, ref, value, at, tstamps_uuid, sheetfile)` —
   inserts after the layer clause: uuid, at, path "/<tstamp>", sheetname,
   sheetfile; sets Reference/Value; refreshes EVERY item uuid. Pads stay
   netless — the sync fills them.
4. Stage in a grid CLEAR of the outline: `pcb.edge_cuts_bbox(board)`.
5. Gates: save → reparse → `kicad-cli pcb export svg` accepts → render
   PNG → eyeball → commit.

## Notes

- `pcb.empty_board_from(donor)` builds a minimal valid board (header +
  layer stack + setup + net 0) from any existing .kicad_pcb, read-only.
- DRC: `kicad-cli pcb drc --output drc.rpt --severity-all BOARD` —
  set-diff vs baseline exactly like ERC (kicad-review skill).
- .kicad_pcb round-trips BYTE-IDENTICAL (validated on a 2.3 MB real
  board): pcb pts dialect (≤5 points inline, then 4-per-line chunks) +
  token wrap at column 72 — sexp.dumps sniffs the root tag, or pass
  dialect="pcb" when serializing subtrees. Hand-edits diff zero-noise.
- Update-PCB direction is sch→pcb only; never edit refs on the board.
