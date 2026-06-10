---
name: kicad-review
description: >
  Review KiCad schematic/PCB changes with agent-readable evidence: visual
  pixel diff between revisions, semantic tree diff, ERC/DRC set-diff vs
  baseline, and the forum-distilled rule canon. Use before committing any
  schematic/PCB change, when asked to review a design, or to compare
  revisions.
---

# Design review: triple diff + rule canon

## Triple diff (vs git baseline)

**Implemented:** `cd ~/prj/20260610_kicad-agent-skills && python3 -m
kicad_lib.cli diff REV /abs/path/FILE.kicad_sch` → JSON with `semantic`
(symbols added/removed/moved/changed, labels, count deltas), `pixel`
(changed_px + bbox_mm + composite path), `erc_new`/`erc_gone`. Artifacts
land in `~/.cache/kx_scratch/` (never /tmp — flatpak kicad-cli can't see
host /tmp): `diff.png` composite (grey = unchanged, red = removed,
green = added). Crop the bbox_mm region and Read it as evidence.

Gotchas baked into kicad_lib/diff.py — keep them if reimplementing:
- kicad-cli `--no-background-color` SVGs rasterize to TRANSPARENT PNGs;
  flatten onto white before grayscale or `convert("L")` maps the whole
  background to black and the diff goes blind.
- ERC report lines normalize to `type @(x mm, y mm)`; set-diff both ways.

## Rule canon — machine tier (verifier enforces)

grid 1.27 mm · no diagonals · junction dots at every connection ·
no cross-net touch/overlap · every pin on exactly one net · refs unique
project-wide · ERC delta ∅ · netlist members == design intent.

## Rule canon — render-judged tier (check on the PNG)

signal flow L→R · V+ up, GND down · power symbols not long wires ·
all text horizontal · decoupling caps adjacent to their IC · descriptive
UPPERCASE net names · polarity marks visible · every IC pin accounted
for (incl. explicit no-connects) · notes at non-obvious circuitry ·
title block filled · no text/symbol collisions at readable zoom.

Verdict format: PASS/FAIL per tier + per-finding file:line-equivalent
(sheet + coordinates) + cropped evidence PNG paths.
