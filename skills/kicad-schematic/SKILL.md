---
name: kicad-schematic
description: >
  Generate and modify KiCad schematics (.kicad_sch) and PCBs (.kicad_pcb)
  programmatically, with geometric verification, headless ERC, and rendered
  visual inspection. Use when asked to draw/implement/extend a schematic,
  inject circuit blocks into an existing KiCad project, stage footprints on
  a PCB, or verify schematic wiring/rotation. Trigger phrases: "draw in
  kicad", "create schematic", "implement schematic", "add to the schematic",
  "kicad_sch", "footprints on the pcb".
---

# KiCad schematic generation

**Engine:** `$(kx root)/kicad_lib/` — lossless sexp
parser (`sexp.py`, token-equal round-trip proven on 2.3 MB boards; byte-identity only for .kicad_mod — sch re-serializes with ~0.5% whitespace drift) and the
`kx` CLI (`python3 -m kicad_lib.cli probe|check FILE` from the repo root).
Prefer `kx probe` over ad-hoc grep for inventory; prefer `sexp.py` over
regex surgery for edits (`kx` is on PATH via ~/.local/bin). Persist with
`sexp.save_file(path, root)` — PATH FIRST. Python imports need
PYTHONPATH=<repo> or cwd=<repo>; the package is not pip-installed. Sibling skills: kicad-project (state machine),
kicad-component (find/fetch parts), kicad-review (diff/ERC), kicad-improve
(self-improvement loop). Design: `doc/DESIGN.md` in that repo.

Battle-tested workflow for writing `.kicad_sch`/`.kicad_pcb` files from
Python without KiCad GUI. The engine and worked examples ship in this repo
(`kx root`):

- `kicad_lib/ops.py` — standalone sheet, scaffold symbols, full-wire
  layout, driven by the built-in geometric verifier (`kicad_lib/verify.py`).
- `kicad_lib/live_ops.py` — inject a block into an EXISTING schematic using
  real library symbols (extends-flattening, multi-unit parts, mirrors),
  label-stitched islands.
- `kicad_lib/pcb.py` — stage footprints in the PCB, path-linked to
  schematic symbols so Update-PCB adopts them. See `tests/test_*.py` for
  runnable end-to-end usage against `tests/fixtures/`.

## 0. The loop (never skip a stage)

1. **Probe** target file + libraries (uuids, pin geometry, occupancy, refs).
2. **Generate** with explicit coordinates; run the **geometric verifier**.
3. **ERC headless**; compare against a **baseline run as a violation set**.
4. **Render SVG → PNG → look at it.** Geometry that passes checks can still
   be unreadable (text collisions, symbol overlap). Iterate.
5. Commit per slice. Checkpoint-commit the project BEFORE first modification.

## 1. Environment

KiCad 10 is a flatpak (`org.kicad.KiCad`); there is no host `kicad-cli`:

```bash
flatpak run --command=kicad-cli org.kicad.KiCad sch erc --output erc.rpt --severity-error FILE.kicad_sch
flatpak run --command=kicad-cli org.kicad.KiCad sch export svg --output OUTDIR --no-background-color FILE.kicad_sch
flatpak run --command=kicad-cli org.kicad.KiCad sch export netlist --output out.net FILE.kicad_sch
flatpak run --command=kicad-cli org.kicad.KiCad pcb export svg --output out.svg --layers F.Cu,B.Cu,Edge.Cuts,F.SilkS FILE.kicad_pcb
```

- Flatpak `/tmp` is sandboxed — outputs must land under `$HOME`.
- Official libs:
  `~/.local/share/flatpak/runtime/org.kicad.KiCad.Library.Symbols/x86_64/stable/active/files/symbols/*.kicad_sym`
  `~/.local/share/flatpak/runtime/org.kicad.KiCad.Library.Footprints/x86_64/stable/active/files/footprints/*.pretty/*.kicad_mod`
- Rasterize SVG with `cairosvg` (installed), crop with PIL, then Read the PNG.
- If a project lock file (`~NAME.kicad_pro.lck`) exists, KiCad is open —
  warn the user to close/reload before and after edits.

## 2. File format essentials (.kicad_sch, format 20260306, generator "eeschema" 10.0)

Top-level order: `(kicad_sch (version) (generator) (generator_version)
(uuid) (paper) (lib_symbols ...) <junctions/wires/labels/symbols/sheets>
(sheet_instances ...) (embedded_fonts no))`. Order of body items is loose.

- **Preserve the existing file uuid** (it's the root sheet uuid; instance
  paths reference it). For a sub-sheet keep the uuid KiCad created.
- Every placed symbol needs `(instances (project "NAME" (path "/<ROOT_UUID>"
  (reference "R1") (unit 1))))`. Sub-sheet instance paths are
  `/<root_uuid>/<sheet_symbol_uuid>`.
- `lib_symbols` is a cache of full symbol definitions named `"Lib:Name"`.
  Wires connect ONLY at exact pin endpoints — geometry comes from the cache.
- Wire: `(wire (pts (xy x1 y1) (xy x2 y2)) (stroke (width 0) (type default))
  (uuid ...))`. Junction: `(junction (at x y) (diameter 0) (color 0 0 0 0)
  (uuid ...))`.
- Labels: local `(label "net" (at x y rot) ...)`, global `(global_label
  "net" (shape input|output) ...)`, hierarchical `(hierarchical_label ...)`
  (sub-sheets only; on a root sheet use local/global).
- Paren-balance check before writing; strings may contain parens — the
  balanced extractor must be quote-aware.

## 3. Coordinates, rotation, mirror

World coords: mm, **+Y down**. Library pin coords: **+Y up**. Everything
(symbol origins AND wire endpoints) must sit on the **1.27 mm grid** or
wires will not connect.

Pin world position for a symbol at `(x, y, rot)`, lib pin offset `(u, v)`:

```python
def rot_xy(u, v, rot, mirror=""):
    if mirror == "x": v = -v        # use mirror only with rot 0 (unambiguous)
    if rot == 0:   return (u, -v)
    if rot == 90:  return (-v, -u)
    if rot == 180: return (-u, v)
    if rot == 270: return (v, u)
# world = (x + dx, y + dy)
```

- `Device:R` is **vertical** at rot 0 (pin1 top); rot 90 → pin1 left.
- KiCad PNPs draw emitter at bottom; `(mirror x)` flips it up (idiomatic).
- **Property text angle is RELATIVE to symbol rotation** — for a rot-90/270
  symbol set property angle 90 so the text renders horizontal.
- Real symbol heights differ from scratch-built ones — transistor circles
  are ~11 mm; totem pairs need ≥15.24 mm center spacing.

## 4. Sourcing symbols

**Real library symbols (preferred — "select real components"):**
- Extract `(symbol "NAME" ...)` blocks from the lib with a balanced parser.
- Resolve `(extends "BASE")`: take the base body, apply the derived
  symbol's property overrides, rename `BASE_X_Y` sub-units to `NAME_X_Y`,
  then prefix the top name to `"Lib:NAME"` for the cache.
- Cache `Value` property must equal the bare symbol name, else ERC reports
  `lib_symbol_mismatch` against the configured library.
- Parse pin positions per unit from `(pin ... (at x y a) ... (number "N"))`.
- Multi-unit parts (LM393 = unit1 A, unit2 B, unit3 power): one reference,
  several `(symbol ...)` instances each with its own `(unit N)`; all units
  carry identical properties. One dual comparator serves two channels.
- Watch coincident pins (e.g. DMP3013SFV has 3 drain pins at one point —
  all get the same net, emit all pin uuid entries).

**Scaffold symbols (standalone sheets / quick drafts):** define your own
minimal R/C/Q/M/opamp boxes in `lib_symbols` so pin geometry is fully under
your control. Sub-unit names inside a definition are bare (`"R_0_1"`, never
`"lib:R_0_1"`). Power symbol header is `(power global)`. If KiCad will open
the project standalone, also emit `scaffold.kicad_sym` + a project
`sym-lib-table` entry (URI `${KIPRJMOD}/scaffold.kicad_sym`) or ERC warns.

## 5. Placement and routing

- Describe nets as **polylines of waypoints**, where a waypoint is either a
  literal point or `("ref","pin")`; consecutive points become axis-parallel
  wire segments. Skip zero-length segments.
- **Full wires** for a self-contained sheet; **label-stitched islands**
  (driver island / power island / comparator island joined by short local
  labels) when injecting into a crowded page — labels merge nets with the
  existing drawing (`out1`, `Vin_p`, `VCC`...) for free.
- Probe free space BEFORE placing: collect `(at ...)`/`(xy ...)` coords into
  a coarse occupancy grid — but remember it misses label TEXT extents and
  sheet boxes; always confirm against a render. Probe `(sheet (at)(size))`
  blocks explicitly. Title block ≈ bottom-right 110×30 mm — keep clear.
- If the page is full, **grow the paper** (A4 → A3); existing content keeps
  coordinates.
- T-junctions: a wire endpoint or pin touching another wire's interior DOES
  connect; emit a `(junction ...)` there. Pins may sit pin-on-pin or
  pin-on-wire-interior — both connect.
- GND/VCC: one `power:GND`/`power:VCC` symbol per drop, short stub wire to
  the pin. Standalone sheets need one `PWR_FLAG` (power_out pin) on each
  passive-driven rail or ERC raises `power_pin_not_driven`.

## 6. Geometric verifier (run before every write)

**Implemented:** `kicad_lib/verify.py` (`Seg`/`Pin`/`verify()`) +
`kicad_lib/geom.py` (`rot_xy`, `pin_world`, `snap`) — mutation-tested by
`tests/test_verify.py`. Build your net model with these; do NOT rewrite
the verifier per-generator.

Mandatory checks over all generated segments + pins:

1. Everything on the 1.27 mm grid; no diagonal segments.
2. No two segments of DIFFERENT nets may touch, cross, T-touch, or overlap
   collinearly (treat all `GND*`/`VCC*` stub nets as one net each).
3. Same-net collinear overlap (double-draw) and same-net crossing without a
   shared endpoint are errors too.
4. No pin of net A lying on a wire of net B.
5. Every declared pin claimed by exactly one net; every label anchor on a
   segment of its net.
6. (Scaffold symbols) no wire through a symbol body bbox.
7. Junction emission: ≥3 connection items at a point, or endpoint/pin on a
   same-net segment interior.

Float hygiene: `round(coord, 3)` everywhere — `160.02 + 7.62` produces
`167.64000000000001` which the clash checks will flag against `167.64`.
**Mutation-test the verifier once** (plant a crossing, expect failure).

## 7. References and ERC

- **References are global across ALL sheets of a project.** Collision-check
  against every `.kicad_sch` in the project dir, not just the target file.
  Duplicate refs silently cross-merge multi-unit symbols and corrupt ERC.
- GOTCHA: `kicad-cli sch erc` does **NOT** catch duplicate designators or
  unannotated symbols (`R?`) — even at `--severity-all`. They are
  Annotation-tool checks (`SCH_REFERENCE_LIST`), not ERC violations, so a
  headless ERC pass stays 0/0 green while two `R1`s cross-merge. Verify refs
  with `kx ref-audit FILE` (multi-unit aware: U1A/U1B/U1C sharing `U1` is
  fine; a repeated unit or two lib_ids on one ref is the real dup) or the
  GUI Annotate dialog — never trust headless ERC for annotation.
- `#PWR0NNN` refs: pick an unused high block.
- ERC totals are noisy. Always: run ERC on the **pre-edit baseline**
  (`git show HEAD:file > baseline.kicad_sch`), normalize each violation to
  `type: @locations`, and **set-diff** new vs baseline. Judge only the NEW
  entries; report regrouping moves pre-existing items around.
- Expected-benign classes: `isolated_pin_label` on block I/O, undriven
  inputs awaiting later wiring, pre-existing unwired-MCU noise.
- `missing_unit`/`missing_input_pin`: EVERY unit of a multi-unit part
  (dual/quad opamp, gate pack) must be placed, AND a spare you don't use
  still goes on a sheet + tied off (opamp: in+ → GND, in− → out; logic:
  inputs to a defined level) or its inputs throw `missing_input_pin`.
  `kx unit-audit FILE` lists placed-vs-expected units per ref (reads the
  count from lib_symbols) and front-runs ERC; ERC is the authority on
  tie-off. (kicad.info unused-pin threads; KLC S4.5)
- No-connect discipline: a genuinely unused pin needs a `(no_connect …)`
  flag to silence `pin_not_connected` — DOCUMENT the intent, never lower
  severity. Inverse trap `no_connect_connected`: a NC flag on a pin/node
  that IS wired (flag and wiring contradict) — delete the flag OR the wire,
  not both. NC means "nothing else attaches here"; one ERC entry lands at
  the flag, one at the connected pin. (kicad.info t/46229, t/21294)
- The exported **netlist is the ground truth** for connectivity: parse
  `(net (name)(node (ref)(pin)))` blocks and assert each designed net has
  exactly the expected members. Do this at least once per block.
- **`power_pin_not_driven` ("Input power pin not driven by output power
  pins") is a missing DRIVER DECLARATION, not a wiring fault.** Each
  power/ground rail needs one driver: a `power_out` pin (regulator) OR a
  `power:PWR_FLAG`. Fix by adding PWR_FLAG — NOT a ground symbol like
  PWRGND (name trap: PWRGND is just a GND graphic; PWR_FLAG declares the
  net driven) — at the rail's PASSIVE source (connector / battery /
  regulator INPUT). Never flag a regulator OUTPUT (already `power_out`;
  flagging it can trip "two outputs"). One flag per rail.
- GOTCHA: `kicad-cli sch export netlist` **drops PWR_FLAG and power-symbol
  nodes**, so a flagged and a flag-stripped schematic have IDENTICAL
  netlist driver content (both zero `power_out`) — the netlist cannot see
  this class of error. NEVER audit power drivers from the netlist; use
  `kx power-audit FILE` (reads PWR_FLAG instances from the schematic) plus
  kicad-cli ERC (the authority). (kicad.info t/35552, t/57016)

## 8. Visual inspection (non-negotiable)

```bash
flatpak run --command=kicad-cli org.kicad.KiCad sch export svg --output svg_out --no-background-color FILE.kicad_sch
python3 -c "import cairosvg; cairosvg.svg2png(url='svg_out/FILE.svg', write_to='out.png', output_width=2600)"
# crop: px = x_mm/paper_w*img_w (A4 297x210, A3 420x297), then Read the PNG
```

Look for: symbol body overlap, value/ref text collisions (move per-ref via
a TEXT_POS override table), labels overlapping existing drawing text,
junction dots present, pins facing their wires. Fix and re-render until
clean. Matplotlib net-colored previews of the segment model help debug the
generator, but only the KiCad render proves the file.

## 9. Injecting into an existing schematic

- Checkpoint-commit first; idempotency = `git checkout -- file` then rerun
  the generator (uuids regenerate — re-derive anything uuid-dependent after).
- Splice lib symbols right after `(lib_symbols`, body before
  `(sheet_instances`; skip lib defs already cached.
- Reuse existing nets by label name (`out1`, `Vin_p`, `VCC`) and existing
  cached symbols (e.g. the project's own cap symbol) where possible.
- Bind MCU pins by adding a 5.08 mm stub wire + local label at the pin
  endpoint (get pin world pos from the cached MCU symbol + instance `(at)`).
  Only bind pins the design doc justifies; leave the rest to the user.

## 10. PCB footprint staging (.kicad_pcb)

Goal: place footprints so KiCad's **Update PCB from Schematic** adopts them
(no duplicates) and fills nets:

1. Export the netlist; for each ref take `(tstamps "<uuid>")` from its
   `(comp ...)` block — that uuid IS the link.
2. Load `.kicad_mod`, take the balanced `(footprint ...)` block, rename to
   `"Lib:Name"`, strip `(version)/(generator*)` headers, insert after the
   layer clause: `(uuid)`, `(at X Y)`, `(path "/<tstamp-uuid>")`,
   `(sheetname "/")`, `(sheetfile "...")`; set Reference/Value properties;
   refresh all item uuids. Leave pads netless — sync fills them.
3. Stage in a grid clear of the board outline (probe Edge.Cuts extent).
4. Parse-check via `pcb export svg`, render, eyeball, commit.

## 11. Live IPC editing (KiCad v11 nightly, GUI open)

When `kx env` reports `ipc_alive: true` + scope `pcb+sch`, edit the
schematic INSIDE the running eeschema instead of the file (the file is
LOCKED then — never write it). kipy master via repo `.venv` (bootstrap:
`tools/bootstrap_kipy.sh`); worked example `tests/test_live_ipc.py`:

    from kipy import KiCad
    import kipy.schematic_types as st
    from kipy.geometry import Vector2          # nanometers: mm * 1e6
    k = KiCad(socket_path="ipc:///tmp/kicad/api.sock")
    sch = k.get_schematic()                    # the OPEN document
    refs = [s.reference_field.text.value for s in sch.get_symbols()]
    t = st.SchematicText(); t.value = "note"
    t.position = Vector2.from_xy(50_800_000, 25_400_000)
    c = sch.begin_commit(); sch.create_items(t); sch.push_commit(c, "msg")

- Every push_commit is a single undo step in the GUI — small definitive
  steps map 1:1 onto user-visible, user-revertable edits.
- remove_items() deletes live. get_lines/get_labels/get_text mirror reads.
- PERSISTENCE GAP (nightly 10.99 standalone): save/revert are unhandled
  over IPC — the USER saves. Verify expected state on disk afterwards
  with kx probe / kx check. Full handler map: kicad-project skill.

`kx live` wraps this as VERIFIED atomic ops (kicad_lib/live_ops.py,
worked example tests/test_live_ops.py — re-execs the .venv itself):

    kx live snap                   inventory + violations (mm, with ids)
    kx live check                  geometric verifier on the live model
    kx live wire X1 Y1 X2 Y2       verify -> push or refuse (exit 1+JSON)
    kx live junction X Y | label X Y TEXT | text X Y TEXT
    kx live rm ID...               remove by KIID (from snap)

Net-blind gate: live backend sees wires not nets — grid/diagonal/zero/
overlap/T-junction checked; cross-net + pin rules only at file level
after save. A refusal sends NOTHING (GUI never sees invalid state);
a push is exactly one GUI undo step.

## 12. Done checklist

- [ ] Geometric verifier: 0 violations (and it failed when mutation-tested).
- [ ] ERC set-diff vs baseline: no unexplained NEW entries.
- [ ] Netlist spot-check: designed nets have exactly the expected members.
- [ ] Rendered PNG inspected at readable zoom; no overlaps.
- [ ] Refs unique across every sheet file in the project.
- [ ] Paren balance + file written with original uuid/paper preserved.
- [ ] Checkpoint commit before, one commit per slice after; scratch outputs
      (svg_out/, *.rpt, *.net, *.png) cleaned or ignored, never committed.
