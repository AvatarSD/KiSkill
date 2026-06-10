# kicad-agent-skills

Agent-operable toolkit + Claude Code skill pack for full programmatic control
of any KiCad project (schematic + PCB), independent of the KiCad GUI.

- `kicad_lib/` — Python engine: lossless s-expression parser, geometry,
  geometric verifier, component index, internet sourcing, visual diff,
  live-mode adapter, `kx` CLI.
- `skills/` — Claude Code skills (symlinked into `~/.claude/skills/`).
- `doc/DESIGN.md` — architecture, project state machine, atomic operation
  set, distilled schematic rule canon, research sources.

Targets KiCad 10 (flatpak) file-based today; KiCad 11 IPC API (live mode)
via the same adapter when it ships.
