"""Verifier mutation tests: clean model → 0 violations; each planted
defect → exactly the expected violation class. A verifier that cannot
fail is worthless (SKILL.md §6: mutation-test the verifier once).

Usage: python3 tests/test_verify.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib.geom import pin_world, rot_xy, snap, on_grid  # noqa: E402
from kicad_lib.verify import Seg, Pin, verify  # noqa: E402

FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


# --- geom sanity ----------------------------------------------------------
check("rot_xy 0",   rot_xy(2.54, 1.27, 0) == (2.54, -1.27))
check("rot_xy 90",  rot_xy(2.54, 1.27, 90) == (-1.27, -2.54))
check("rot_xy 270", rot_xy(2.54, 1.27, 270) == (1.27, 2.54))
check("rot_xy mirror-x", rot_xy(0, 5.08, 0, "x") == (0.0, 5.08))
check("pin_world float hygiene", pin_world((160.02, 100.0), 0, (7.62, 0))
      == (167.64, 100.0))
check("snap", snap(2.6) == 2.54 and on_grid(snap(3.9)))

# --- clean baseline: T network with proper junction -----------------------
# net A: horizontal bus (0,0)-(10.16,0), stub up at (5.08,0)-(5.08,-5.08)
# pin on each open end; junction at the T point.
SEGS = [
    Seg("A", (0, 0), (10.16, 0)),
    Seg("A", (5.08, 0), (5.08, -5.08)),
    Seg("B", (0, -10.16), (10.16, -10.16)),
]
PINS = [
    Pin("A", (0, 0), "R1.1"),
    Pin("A", (10.16, 0), "R2.1"),
    Pin("A", (5.08, -5.08), "R3.1"),
    Pin("B", (0, -10.16), "R4.1"),
    Pin("B", (10.16, -10.16), "R4.2"),
]
JUNCS = [(5.08, 0)]

v = verify(SEGS, PINS, JUNCS)
check("clean model passes", v == [])

# --- mutations: each must be caught ---------------------------------------
def expect(name, needle, segs=SEGS, pins=PINS, juncs=JUNCS):
    v = verify(segs, pins, juncs)
    check(f"mutation: {name}", any(needle in x for x in v))

expect("diagonal", "diagonal",
       segs=SEGS + [Seg("A", (0, 0), (2.54, -2.54))])
expect("off-grid", "off-grid",
       segs=SEGS + [Seg("A", (10.16, 0), (11.0, 0))])
expect("cross-net touch", "cross-net",
       segs=SEGS + [Seg("B", (5.08, -5.08), (5.08, -10.16))])
expect("double-draw", "double-draw",
       segs=SEGS + [Seg("A", (2.54, 0), (7.62, 0))])
expect("pin on foreign wire", "touches wire of",
       pins=PINS + [Pin("B", (2.54, 0), "C1.1")])
expect("unconnected pin", "not on its net's copper",
       pins=PINS + [Pin("A", (20.32, 0), "C2.1")])
expect("missing junction", "missing junction", juncs=[])
expect("stray junction", "stray junction", juncs=JUNCS + [(2.54, -10.16)])

# power-net merging: VCC and VCC_3V3 stubs may touch
v = verify([Seg("VCC", (0, 0), (2.54, 0)), Seg("VCC_3V3", (2.54, 0), (5.08, 0))],
           [Pin("VCC", (0, 0), "P1"), Pin("VCC_3V3", (5.08, 0), "P2")], [])
check("power-net merge", v == [])

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
