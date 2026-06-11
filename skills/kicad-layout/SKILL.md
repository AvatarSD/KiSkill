---
name: kicad-layout
description: >
  Autoroute a KiCad board headlessly: schematic → staged footprints →
  placement → freerouting → routed .kicad_pcb, DRC-gated. Use when asked
  to route a board, do layout from a schematic, or autoroute nets.
---

# Headless layout / autorouting

Engine: `~/prj/20260610_kicad-agent-skills/kicad_lib/layout.py`.
Worked end-to-end example: `tests/test_layout.py` (scratch board with
outline + 2 netted footprints → 9 routed segments).

## The chain

    layout.autoroute(pcb_in, pcb_out, passes=20)
      = dsn_export (pcbnew python in the KiCad flatpak:
                    pcbnew.ExportSpecctraDSN — headless, verified 10.0)
      → route      (.tools/freerouting-2.2.4-linux-x64/bin/freerouting,
                    BUNDLED JRE — no system java; classic CLI
                    -de in.dsn -do out.ses -mp N -da)
      → ses_import (pcbnew.ImportSpecctraSES + SaveBoard)

Returns segment/via counts. Bootstrap if .tools missing: download
freerouting-X-linux-x64.zip from github freerouting/freerouting
releases, unzip into .tools/ (no root needed).

## Preconditions (or DSN export is meaningless)

- Edge.Cuts outline present (closed).
- Pads carry nets. Update-PCB-from-Schematic normally fills them; when
  building boards programmatically, append `(net "NAME")` to each pad —
  pcb format 20260206 (KiCad 10) keys nets BY NAME, no numeric id (old
  `(net 1 "N1")` style is normalized on import, harmless).
- Placement matters more than the router: group by schematic function,
  decoupling caps adjacent, connectors at edges (rule canon).

## After routing — never skip

1. `kicad-cli pcb drc --severity-all` set-diff vs pre-route baseline.
2. Render F.Cu/B.Cu/Edge.Cuts → PNG → look (kicad-review pixel diff).
3. pcbnew rewrites the file wholesale on SES import — commit the
   pre-route board first, diff after.
