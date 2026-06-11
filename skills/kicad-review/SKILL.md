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

Python API: `probe()` lives in `kicad_lib.cli` (NOT .source);
`diff.semantic_diff(probe_a, probe_b)` takes its dicts. `kx diff` needs
the FILE inside a git repo (baseline = revision); for two loose files
call `diff.render_png`/`pixel_diff`/`semantic_diff` directly.
Crop formula: px = mm * render_width_px / paper_width_mm (A4=297,
A3=420; render default 2400 px wide).

Gotchas baked into kicad_lib/diff.py — keep them if reimplementing:
- kicad-cli `--no-background-color` SVGs rasterize to TRANSPARENT PNGs;
  flatten onto white before grayscale or `convert("L")` maps the whole
  background to black and the diff goes blind.
- ERC report lines normalize to `type @(x mm, y mm)`; set-diff both ways.

## Reading common ERC codes (fix, don't just silence)

- `power_pin_not_driven` — a power INPUT pin has no driver on its rail.
  NOT a wiring bug: add `power:PWR_FLAG` at the rail's passive source
  (connector/battery/regulator INPUT), one per rail. PWR_FLAG ≠ PWRGND
  (the latter is only a GND graphic). `kx power-audit FILE` lists rails +
  flag coverage. The exported netlist CANNOT detect this (it drops
  PWR_FLAG/power nodes) — trust ERC, not a netlist driver scan.
- `pin_not_connected` on block I/O / undriven inputs awaiting later
  wiring = expected-benign; keep in the baseline, judge only NEW entries.
  For a GENUINELY unused pin (spare gate, NC silicon pad), the fix is a
  No-Connect flag to document intent — never lower the rule's severity.
- `no_connect_connected` — a NC flag sits on a pin/node that IS wired; the
  flag and the wiring contradict. Fix = delete the flag (pin is used) OR the
  wire (pin isn't), not both. NC means "nothing else attaches here". (KiCad
  emits one entry at the flag and one at the connected pin.)
- `missing_unit` / `missing_input_pin` — a multi-unit part (dual/quad opamp,
  logic-gate pack) has an unplaced unit. Place EVERY unit; a SPARE you don't
  use still goes on a sheet AND gets tied off (opamp: in+ → GND, in− → out;
  logic: inputs to a defined level) or its inputs throw `missing_input_pin`.
  `kx unit-audit FILE` lists each multi-unit ref's placed-vs-expected units
  and front-runs ERC (it agrees on `missing_unit`); ERC stays the authority
  for whether a placed spare is actually tied off.

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
