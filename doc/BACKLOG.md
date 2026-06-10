# Backlog (self-improvement loop fuel — top item first)

- [x] Port geom.py (rot_xy, grid, pin world pos) + verify.py (rule set of
      SKILL.md §6) from gen_hiside_kicad.py; mutation-test the verifier.
- [ ] Create own minimal test project in tests/fixtures/testproj/
      (RC divider + opamp, ERC-clean) generated via kicad_lib — becomes
      the canonical scratch target for all write-path tests.
- [ ] kx atomic write ops: place / wire / label / junction / set_prop /
      delete (each: precondition → action → postcondition, verifier-gated).
- [ ] diff.py: pixel composite (red/green), semantic probe-diff, ERC
      set-diff; `kx diff REV FILE` — then thicken kicad-review skill.
- [ ] Serializer byte-fidelity: match eeschema corner cases so unchanged
      files round-trip byte-identical (sch currently ~0.5% whitespace drift).
- [ ] index.py: sqlite FTS over flatpak+project libs; `kx find "dual comparator"`.
- [ ] source.py: easyeda2kicad wrapper + zip import + lib-table register;
      `kx fetch C2040`.
- [ ] Vendor KiCad api protos (shallow clone kicad/api) + live.py adapter
      skeleton (file | ipc | headless); detect KICAD_API_SOCKET.
- [ ] kicad-pcb skill: port SKILL.md §10 footprint staging onto kicad_lib.
- [ ] kicad-layout meta-skill: schematic→board flow, freerouting DSN/SES
      loop, DRC-gated iteration.
- [ ] kicad-fab skill: BOM (grouped, JLC/LCSC columns), gerbers, pos,
      jobsets via kicad-cli.
- [ ] kicad-emsim skill: gerber2ems + openEMS install path (conda-forge or
      apt), field-slice PNG feedback to agent.
- [ ] Subagent e2e: spawn subagent per skill against testproj; record
      gaps as new backlog items.
- [ ] KiCad 11 nightly (needs sudo PPA or source build — user action);
      then live schematic IPC via kipy in live.py.
- [ ] Forum sweep cadence: one distilled lesson per tick max into skills.
