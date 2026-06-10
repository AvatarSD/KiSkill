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

1. `flatpak run --command=kicad-cli org.kicad.KiCad version` — confirm env.
2. Lock files `~NAME.kicad_*.lck` (tilde = literal filename prefix, not
   `$HOME`) present ⇒ KiCad is OPEN: do not write;
   ask the user to close/reload, or work on a scratch copy.
3. Git: checkpoint-commit the project BEFORE the first modification.
4. `python3 -m kicad_lib.cli probe FILE` from the repo root — inventory
   (uuid, paper, symbols/refs, labels, sheets, cached libs).

## State machine (DESIGN.md §3)

CLEAN → PROBED → STAGED → VERIFIED (geometric verifier 0 violations) →
RENDERED (svg→png, ERC run) → REVIEWED (pixel+semantic+ERC diff judged
against rule canon) → COMMITTED. Never skip a state; never write a file
that has not passed VERIFIED; never commit one that has not been REVIEWED.

## Project hygiene

- References are global across ALL sheets — collision-check project-wide.
- Preserve root uuid and per-sheet uuids; instance paths depend on them.
- sym-lib-table / fp-lib-table: project-local libs use `${KIPRJMOD}` URIs.
- Scratch outputs (svg_out/, *.rpt, *.net, *.png) never get committed.
