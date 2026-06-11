---
name: kicad-emsim
description: >
  Electromagnetic field simulation of a KiCad board via gerber2ems +
  openEMS (dockerized, headless): S-parameters, impedance, and E-field
  PNG visualizations fed back to the agent. Use when asked to simulate a
  board's EM behavior, check impedance of traces, or visualize fields.
---

# EM simulation (gerber2ems + openEMS)

Engine: `$(kx root)/kicad_lib/emsim.py`.
STATUS: chain VALIDATED — meander_loose example simulated end-to-end
(rc 0; |S11|≈1 + 455 ps delay on the open meander = physically correct).
Image `gerber2ems` (6.6 GB); rebuild if missing:
`docker build -t gerber2ems $(kx root)/.tools/gerber2ems/`. NEVER mount over
/home/docker — it shadows the image toolchain; emsim.run mounts at
/home/docker/sim.

## Flow

1. `emsim.prepare(pcb, workdir)` — gerbers+drill+pos via fab.py (names
   already match), `stackup.json` translated from the board's
   `(setup (stackup ...))`, `simulation.json` template.
2. **Fill ports** in simulation.json (width/length in µm, impedance,
   layer index, excite flag) and `traces` (start/stop port indices) —
   ports sit at position-file footprint locations (use simulation-port
   footprints on the board for clean port placement).
3. `emsim.run(workdir, export_field=True)` — dockerized
   `gerber2ems -a --export-field`: geometry → FDTD → postprocess.
4. Read the outputs: `ems/*.csv` S-params/impedance; **field PNGs are
   the agent feedback** — Read them, check field concentration against
   expectations (return paths, gaps, stubs). VTR dumps open in ParaView
   for the user.

## Notes

- Frequency range/grid in SIM_TEMPLATE: 0.2–6 GHz, sane FR-4 defaults;
  finer grids explode runtime (FDTD is O(cells × steps)).
- Worked examples: `.tools/gerber2ems/examples/*` (differential pair,
  filters, stubs) — copy a simulation.json from the closest one.
- Docker daemon does its own networking — image build/pull works even
  though the agent sandbox blocks direct DNS.
