"""Generate the canonical write-path fixture via kicad_lib.ops:
RC filter into an MCP6002 unity follower with load; unused unit tied off.

Usage: python3 tools/gen_testproj.py [OUT.kicad_sch]
The net model is verifier-gated before the file is written; ERC and the
rendered-PNG review were done on the committed fixture (b3c8d82).
"""

import pathlib
import sys
from functools import partial

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import build, ops, sexp
from kicad_lib.geom import pin_world
from kicad_lib.verify import Seg, Pin, verify

HERE = pathlib.Path(__file__).resolve().parents[1]
FIX = HERE / "tests/fixtures"
SYMDIR = pathlib.Path.home() / (
    ".local/share/flatpak/runtime/org.kicad.KiCad.Library.Symbols/"
    "x86_64/stable/active/files/symbols"
)
ROOT_UUID = "1a7e5f00-0000-4000-8000-20260611caf3"

DAC = str(FIX / "dac_buf.kicad_sch")
LIB = {
    "R": ops.extract_libdef(DAC, "Device:R", "Device:R"),
    "C": ops.extract_libdef(str(SYMDIR / "Device.kicad_sym"), "C", "Device:C"),
    "OP": ops.extract_libdef(DAC, "Amplifier_Operational:MCP6002-xMC",
                             "Amplifier_Operational:MCP6002-xMC"),
    "GND": ops.extract_libdef(DAC, "power:GND", "power:GND"),
    "VCC": ops.extract_libdef(DAC, "power:VCC", "power:VCC"),
    "PF": ops.extract_libdef(str(SYMDIR / "power.kicad_sym"), "PWR_FLAG",
                             "power:PWR_FLAG"),
}

# ---- placement (world mm, +Y down, grid 1.27) ---------------------------
R1 = (96.52, 80.01); C1 = (105.41, 87.63); R2 = (133.35, 88.9)
U1A = (118.11, 82.55); U1B = (118.11, 99.06); U1P = (165.1, 82.55)

p_r1_1 = pin_world(R1, 90, (0, 3.81));   p_r1_2 = pin_world(R1, 90, (0, -3.81))
p_c1_1 = pin_world(C1, 0, (0, 3.81));    p_c1_2 = pin_world(C1, 0, (0, -3.81))
p_inp = pin_world(U1A, 0, (-7.62, 2.54)); p_inn = pin_world(U1A, 0, (-7.62, -2.54))
p_out = pin_world(U1A, 0, (7.62, 0))
p_inpB = pin_world(U1B, 0, (-7.62, 2.54)); p_innB = pin_world(U1B, 0, (-7.62, -2.54))
p_outB = pin_world(U1B, 0, (7.62, 0))
p_vp = pin_world(U1P, 0, (-2.54, 7.62));  p_vn = pin_world(U1P, 0, (-2.54, -7.62))
p_r2_1 = pin_world(R2, 0, (0, 3.81));    p_r2_2 = pin_world(R2, 0, (0, -3.81))

VCC1 = (92.71, 77.47); GND1 = (105.41, 92.71); GND2 = (133.35, 95.25)
VCC2 = (162.56, 72.39); GND3 = (162.56, 92.71); GND4 = (107.95, 100.33)
PF1 = (158.75, 72.39); PF2 = (158.75, 92.71)
T1 = (105.41, 80.01); T2 = (127.0, 82.55)

W = [("VCC", VCC1, p_r1_1), ("N1", p_r1_2, p_inp), ("N1", T1, p_c1_1),
     ("GND", p_c1_2, GND1), ("VOUT", p_out, (133.35, 82.55)),
     ("VOUT", T2, (127.0, 88.9)), ("VOUT", (127.0, 88.9), (110.49, 88.9)),
     ("VOUT", (110.49, 88.9), p_inn),
     ("VOUT", (133.35, 82.55), p_r2_1), ("GND", p_r2_2, GND2),
     ("VCC", p_vp, VCC2), ("GND", p_vn, GND3),
     ("VCC", PF1, VCC2), ("GND", PF2, GND3),
     ("GND", p_inpB, (107.95, 96.52)), ("GND", (107.95, 96.52), GND4),
     ("FB_B", p_outB, (127.0, 99.06)), ("FB_B", (127.0, 99.06), (127.0, 105.41)),
     ("FB_B", (127.0, 105.41), (110.49, 105.41)),
     ("FB_B", (110.49, 105.41), p_innB)]
JUNCS = [T1, T2, VCC2, GND3]

PINS = [Pin("VCC", VCC1, "#PWR01.1"), Pin("VCC", p_r1_1, "R1.1"),
        Pin("N1", p_r1_2, "R1.2"), Pin("N1", p_c1_1, "C1.1"),
        Pin("GND", p_c1_2, "C1.2"), Pin("N1", p_inp, "U1.3"),
        Pin("VOUT", p_inn, "U1.2"), Pin("VOUT", p_out, "U1.1"),
        Pin("VOUT", p_r2_1, "R2.1"), Pin("GND", p_r2_2, "R2.2"),
        Pin("GND", GND1, "#PWR02.1"), Pin("GND", GND2, "#PWR03.1"),
        Pin("VCC", p_vp, "U1.8"), Pin("GND", p_vn, "U1.4"),
        Pin("GND", p_vn, "U1.9"), Pin("VCC", VCC2, "#PWR04.1"),
        Pin("GND", GND3, "#PWR05.1"), Pin("VCC", PF1, "#FLG01.1"),
        Pin("GND", PF2, "#FLG02.1"), Pin("GND", p_inpB, "U1.5"),
        Pin("FB_B", p_innB, "U1.6"), Pin("FB_B", p_outB, "U1.7"),
        Pin("GND", GND4, "#PWR06.1")]

violations = verify([Seg(n, a, b) for n, a, b in W], PINS, JUNCS)
if violations:
    print("VERIFIER FAIL:\n " + "\n ".join(violations))
    raise SystemExit(1)
print(f"verifier: 0 violations ({len(W)} segs, {len(PINS)} pins)")

# ---- assemble via atomic ops ---------------------------------------------
doc = build.empty_sheet(ROOT_UUID)
place = partial(ops.place, doc, project="testproj")
for p in JUNCS:
    ops.add_junction(doc, p)
for _, a, b in W:
    ops.add_wire(doc, a, b)
ops.add_label(doc, "N1", T1)
ops.add_label(doc, "VOUT", (129.54, 82.55))
place(LIB["R"], R1, 90, "R1", "10k", ["1", "2"])
place(LIB["C"], C1, 0, "C1", "100n", ["1", "2"])
place(LIB["OP"], U1A, 0, "U1", "MCP6002", ["1", "2", "3"], unit=1)
place(LIB["OP"], U1B, 0, "U1", "MCP6002", ["5", "6", "7"], unit=2)
place(LIB["OP"], U1P, 0, "U1", "MCP6002", ["4", "8", "9"], unit=3,
      val_at=(170.18, 82.55))
place(LIB["R"], R2, 0, "R2", "10k", ["1", "2"])
place(LIB["GND"], GND4, 0, "#PWR06", "GND", ["1"], ref_hidden=True)
place(LIB["VCC"], VCC1, 0, "#PWR01", "VCC", ["1"], ref_hidden=True)
place(LIB["GND"], GND1, 0, "#PWR02", "GND", ["1"], ref_hidden=True)
place(LIB["GND"], GND2, 0, "#PWR03", "GND", ["1"], ref_hidden=True)
place(LIB["VCC"], VCC2, 0, "#PWR04", "VCC", ["1"], ref_hidden=True,
      val_at=(162.56, 68.58))
place(LIB["GND"], GND3, 0, "#PWR05", "GND", ["1"], ref_hidden=True)
place(LIB["PF"], PF1, 0, "#FLG01", "PWR_FLAG", ["1"], ref_hidden=True,
      val_at=(152.4, 72.39))
place(LIB["PF"], PF2, 0, "#FLG02", "PWR_FLAG", ["1"], ref_hidden=True,
      val_at=(152.4, 92.71))

out_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
    FIX / "testproj/testproj.kicad_sch")
out_path.parent.mkdir(parents=True, exist_ok=True)
sexp.save_file(str(out_path), doc)
sexp.load_file(str(out_path))  # paren-balance / reparse gate
print(f"wrote {out_path} ({out_path.stat().st_size} B)")
