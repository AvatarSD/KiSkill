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

1. **Pixel**: render BOTH revisions identically
   (`git show REV:FILE > $HOME/.cache/kx_scratch/...` — NEVER /tmp, the
   flatpak kicad-cli cannot see host /tmp; then `kicad-cli sch export svg`),
   rasterize at the same width (cairosvg), then composite:
   grey = unchanged, red = removed, green = added (PIL ImageChops.difference
   per revision against the common base). Crop changed regions, Read PNGs.
2. **Semantic**: `kx probe` both revisions, diff the JSON (symbols
   added/removed/moved, label set changes, count deltas).
3. **ERC set-diff**: run ERC on both, normalize `type: @location`,
   set-subtract. Judge only NEW violations (skill kicad-schematic §7).

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
