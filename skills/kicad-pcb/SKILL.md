---
name: kicad-pcb
description: >
  Stage footprints on a KiCad PCB so Update-PCB-from-Schematic adopts
  them without duplicates, probe board geometry, run DRC, and render
  layers for inspection, and edit the board LIVE in an open KiCad PCB
  editor over IPC (summary, move/rotate/flip, placement files, select +
  zoom, zone refill — one undo step per op). Use when asked to put
  footprints on the pcb, prepare a board for layout, verify a .kicad_pcb
  change, or change a board the user has open in KiCad.
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

## Live mode: the board is OPEN in the KiCad PCB editor

Live vs file — decide with `kx env PROJECT_DIR` first:

| situation | mode |
|---|---|
| `~NAME.kicad_pcb.lck` exists (GUI has it open, any host — sshfs too) | **live**. File writes are REFUSED (`sexp.save_file` raises `HeldByGui`): the GUI would overwrite them on its next save, or they clobber unsaved work |
| board closed, scripted/bulk generation, staging from a netlist | file (`pcb.py`, above) |
| GUI on another host (operator's screen) | live via `kx live connect --host H` |

Commands (mm, degrees, editor frame: y DOWN, as the status bar shows):

```
kx live status                  # which socket, why; probe version
kx live pcb summary             # footprints, nets, net classes, layers, zones
kx live pcb fps [REF...]        # x y rot side locked per footprint
kx live pcb move REF X Y [ROT]  # one undo step, read back
kx live pcb rotate REF DEG      # absolute orientation
kx live pcb flip REF            # other side (flip_items: pads mirrored)
kx live pcb place FILE          # JSON {REF:{x,y,rot,side}} or CSV with header
kx live pcb show REF...         # select + zoom so the user sees what you mean
kx live pcb refill              # refill all zones (KiCad's own undo step)
kx live pcb undo [N]            # take back N steps
kx live pcb save                # write the board to disk from the editor
```

Contract, every op: inputs validated BEFORE anything is sent (unknown or
LOCKED ref refuses the whole op; `--force` for locked); edits are one
commit; every change is read back (`mismatches: []` = landed exactly);
exit 1 on refusal/mismatch. Moves+rotations of any number of parts are
ONE undo step; a side change is ONE MORE (KiCad 10 `flip_items` commits
on its own; setting the layer does not mirror pads) — the result says
`undo_steps`, pass it to `undo N`. Placement CSV columns:
ref/designator, x/posx/mid x, y/posy/mid y, rot/rotation, side/layer.
Do NOT feed a kicad-cli .pos or JLC CPL back unconverted: those are y-UP
from the aux origin.

Tell the user what you changed and offer `show`; nothing is on disk
until they (or `save`) save.

### Finding the editor

- Socket discovery (`kicad_lib/ipc_discovery.py`): flatpak KiCad's
  socket is `~/.var/app/org.kicad.KiCad/cache/tmp/kicad/api.sock` (the
  sandbox's /var/tmp), native `$TMPDIR|/tmp/kicad/api.sock`; a second
  instance is `api-<pid>.sock`. A socket FILE proves nothing — kx
  connect()s; dead files are listed as `ipc_stale_sockets`.
- Server off? `kx env` / `kx live status` name the kicad_common.json with
  `api.enable_server: false` — user turns on Preferences > Plugins >
  Enable KiCad API. Never edit the user's config yourself.
- Remote GUI: `kx live connect --host book` probes the remote with the
  same discovery code over ssh, then opens
  `ssh -f -N -M -S CTL -o StreamLocalBindUnlink=yes -L LOCAL.sock:REMOTE.sock HOST`
  and makes LOCAL the target until `kx live disconnect`. Manual
  equivalent: that ssh line + `KICAD_API_SOCKET=LOCAL.sock`.
  Priority: KICAD_API_SOCKET > connect/headless target > local discovery.
- kipy: the repo `.venv` with `kicad-python==0.8.0` (API 10.0.6) for
  KiCad 10.0.x; `kx live` re-execs into it.

### Testing without the user's screen: `kx live headless`

`kx live headless start BOARD` runs the real flatpak PCB editor on Xvfb
with a scratch KICAD_CONFIG_HOME (API on, first-run wizard pre-answered),
a private sandbox TMPDIR (socket cannot collide with the user's KiCad)
and a private D-Bus (else GTK portal calls stall start-up ~95 s; now
~10 s), and makes it the live target. BOARD must be under $HOME (the
sandbox's /tmp is private). `kx live headless stop` kills it (unsaved
edits are lost by design), cleans its locks. Xvfb without sudo:
`apt-get download xvfb && dpkg -x xvfb_*.deb ~/.local/opt/xvfb`.
Tests: `tests/test_live_pcb.py` (logic, no KiCad) and
`.venv/bin/python tests/test_live_pcb_ipc.py` (e2e, headless).
