# KiSkill — agent skills for KiCad

**Give your coding agent full, headless control of any KiCad project.**
KiSkill is a pack of [Claude Code](https://claude.com/claude-code) skills plus
a pure-Python engine (`kx`) that lets an agent read, edit, verify, review, and
fabricate KiCad schematics and PCBs — no GUI, no mouse, every change checked
before it lands.

Built on a lossless s-expression parser (byte-identical round-trips on real
2 MB boards), a geometric verifier that runs before every write, and visual
diffs fed back to the agent so it can *see* what it changed.

> Targets KiCad 9/10 (file-based) today; KiCad 11 IPC live-mode rides the same
> adapter when it ships. The core engine is stdlib-only.

---

## Install

**Tell your agent** (Claude Code, or any agent that reads `~/.claude/skills/`):

> Clone https://github.com/AvatarSD/KiSkill and run its `install.sh` to set up
> the KiCad skills, then verify with `kx root`.

**Or do it yourself** — one clone, one script, no pip:

```bash
git clone https://github.com/AvatarSD/KiSkill.git
cd KiSkill
./install.sh          # symlinks skills → ~/.claude/skills, kx → ~/.local/bin
```

`install.sh` is idempotent (re-run after `git pull`) and reversible
(`./install.sh --uninstall`). The engine is pure Python stdlib, so there's
nothing to compile. KiCad itself (for ERC / render / fab) is resolved
automatically: native `kicad-cli` on PATH first, then the `org.kicad.KiCad`
flatpak, then the nightly. Override with `KX_KICAD_CLI` — e.g. set it to
`kicad-cli-nightly` when you edit with the v11 nightly, so verification
evidence comes from the same engine that writes your files.

Then just ask:

> *"Use the kicad-project skill to open `~/boards/widget/widget.kicad_pro`,
> add a 3V3 LDO block to the schematic, and show me a diff."*

---

## The skills

| skill              | what it does |
|--------------------|--------------|
| **kicad-project**  | Session state machine (CLEAN→PROBED→…→COMMITTED), backend/lock detection, lib tables, git hygiene. Start here. |
| **kicad-component**| Find parts in local + official libs; fetch missing ones (LCSC via easyeda2kicad, SnapEDA/UltraLibrarian zips); register lib tables. |
| **kicad-schematic**| Generate & edit `.kicad_sch`/`.kicad_pcb` programmatically with a geometric verifier, headless ERC, and rendered visual checks. |
| **kicad-pcb**      | Stage footprints so *Update-PCB-from-Schematic* adopts them without dupes; probe geometry; run DRC; render layers. |
| **kicad-layout**   | Schematic → placed + routed board via freerouting (DSN/SES loop), gated on DRC. |
| **kicad-review**   | Pixel diff, semantic tree diff, ERC/DRC set-diff vs a baseline, and a forum-distilled rule canon. Run before every commit. |
| **kicad-fab**      | Grouped engineering BOM, JLCPCB BOM + CPL, gerbers, drill — ready for the fab house. |
| **kicad-emsim**    | Electromagnetic field sim via gerber2ems + openEMS (dockerized): S-parameters, impedance, E-field PNGs. |
| **kicad-improve**  | Self-improvement loop: each tick researches forums, tests against fixtures, and folds lessons back into the skills. |

Every skill is one self-contained `SKILL.md`. The shared engine lives in
`kicad_lib/` and is reachable from anywhere via the `kx` CLI (`kx root` prints
the repo so skills stay portable no matter where you cloned).

## Why it's safe to let an agent drive

- **Lossless parser** — token-equal round-trips proven on multi-MB boards;
  byte-identical for `.kicad_sch`/`.kicad_sym`/`.kicad_mod`.
- **Verify-before-write** — a geometric verifier (pins, overlaps, grid) must
  pass before any file is saved; nothing commits until a review diff is judged.
- **Evidence, not vibes** — renders and triple diffs flow back to the agent so
  decisions are made on what actually changed.

See [`doc/DESIGN.md`](doc/DESIGN.md) for the architecture, state machine, and
the distilled schematic rule canon.

## Requirements

- Python 3.10+ (engine is stdlib-only).
- KiCad 9 or 10 — native or flatpak (auto-resolved; `KX_KICAD_CLI` to pin) —
  for ERC, rendering, and fab output. Pure file parsing/editing works
  without it.
- Optional per-skill: `cairosvg`/Pillow (render crops), Docker (emsim),
  a bundled freerouting jar (layout).

## Contributing

Issues, fixtures, and PRs welcome — especially real-world boards that stress
the parser and forum-sourced rules worth encoding. Start with
[CONTRIBUTING.md](CONTRIBUTING.md) and the open items in
[`doc/BACKLOG.md`](doc/BACKLOG.md). The `kicad-improve` skill is itself a
working example of how the pack grows.

## License

[MIT](LICENSE) © KiSkill contributors.
