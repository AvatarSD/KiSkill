"""Generate tests/fixtures/testproj/testproj.kicad_sch — the canonical
write-path fixture: RC filter into an MCP6002 unity follower with load.

Real lib symbols are EXTRACTED from the dac_buf fixture cache (+ PWR_FLAG
from the official flatpak power lib), placed with geom.pin_world, wired,
and the whole net model is verifier-gated before the file is written.
"""

import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import sexp
from kicad_lib.sexp import Sym, QStr
from kicad_lib.geom import pin_world
from kicad_lib.verify import Seg, Pin, verify

HERE = pathlib.Path(__file__).resolve().parents[1]
FIX = HERE / "tests/fixtures"
OUT = FIX / "testproj"
POWER_LIB = pathlib.Path.home() / (
    ".local/share/flatpak/runtime/org.kicad.KiCad.Library.Symbols/"
    "x86_64/stable/active/files/symbols/power.kicad_sym"
)
ROOT_UUID = "1a7e5f00-0000-4000-8000-20260611caf3"
PROJECT = "testproj"


def f(v: float) -> str:
    return f"{v:g}"


def uid() -> list:
    return [Sym("uuid"), QStr.of(str(uuid.uuid4()))]


def prop(name, val, x, y, hide=False, rot=0):
    # property angle is RELATIVE to symbol rotation: pass rot=90 for a
    # rot-90/270 symbol so the text renders horizontal (SKILL.md §3)
    n = [Sym("property"), QStr.of(name), QStr.of(val),
         [Sym("at"), Sym(f(x)), Sym(f(y)), Sym(str(rot))]]
    if hide:
        n.append([Sym("hide"), Sym("yes")])
    n += [[Sym("show_name"), Sym("no")], [Sym("do_not_autoplace"), Sym("no")],
          [Sym("effects"), [Sym("font"), [Sym("size"), Sym("1.27"), Sym("1.27")]]]]
    return n


def symbol(lib_id, at, rot, ref, value, pin_nums, unit=1, ref_hidden=False,
           val_at=None):
    x, y = at
    prot = 90 if rot in (90, 270) else 0
    vx, vy = val_at if val_at else (x, y + 5.08)
    n = [Sym("symbol"),
         [Sym("lib_id"), QStr.of(lib_id)],
         [Sym("at"), Sym(f(x)), Sym(f(y)), Sym(str(rot))],
         [Sym("unit"), Sym(str(unit))],
         [Sym("body_style"), Sym("1")],
         [Sym("exclude_from_sim"), Sym("no")],
         [Sym("in_bom"), Sym("yes")],
         [Sym("on_board"), Sym("yes")],
         [Sym("in_pos_files"), Sym("yes")],
         [Sym("dnp"), Sym("no")],
         uid(),
         prop("Reference", ref, x, y - 5.08, hide=ref_hidden, rot=prot),
         prop("Value", value, vx, vy, rot=prot),
         prop("Footprint", "", x, y, hide=True),
         prop("Datasheet", "", x, y, hide=True)]
    for p in pin_nums:
        n.append([Sym("pin"), QStr.of(p), uid()])
    n.append([Sym("instances"),
              [Sym("project"), QStr.of(PROJECT),
               [Sym("path"), QStr.of("/" + ROOT_UUID),
                [Sym("reference"), QStr.of(ref)],
                [Sym("unit"), Sym(str(unit))]]]])
    return n


def wire(a, b):
    return [Sym("wire"),
            [Sym("pts"),
             [Sym("xy"), Sym(f(a[0])), Sym(f(a[1]))],
             [Sym("xy"), Sym(f(b[0])), Sym(f(b[1]))]],
            [Sym("stroke"), [Sym("width"), Sym("0")],
             [Sym("type"), Sym("default")]],
            uid()]


def junction(p):
    return [Sym("junction"),
            [Sym("at"), Sym(f(p[0])), Sym(f(p[1]))],
            [Sym("diameter"), Sym("0")],
            [Sym("color"), Sym("0"), Sym("0"), Sym("0"), Sym("0")],
            uid()]


def label(text, p):
    return [Sym("label"), QStr.of(text),
            [Sym("at"), Sym(f(p[0])), Sym(f(p[1])), Sym("0")],
            [Sym("effects"),
             [Sym("font"), [Sym("size"), Sym("1.27"), Sym("1.27")]],
             [Sym("justify"), Sym("left"), Sym("bottom")]],
            uid()]


# ---- lib defs: extract from fixture cache + official power lib ----------
src = sexp.load_file(str(FIX / "dac_buf.kicad_sch"))
cache = {sexp.atoms(s)[0]: s
         for s in sexp.find_all(sexp.find(src, "lib_symbols"), "symbol")}


def official(lib_file: str, name: str, cache_name: str) -> list:
    lib = sexp.load_file(str(POWER_LIB.parent / lib_file))
    d = next(s for s in sexp.find_all(lib, "symbol")
             if sexp.atoms(s)[0] == name)
    d = sexp.parse(sexp.dumps(d))            # deep copy
    d[1] = QStr.of(cache_name)               # cache name is Lib:Name
    return d


LIBS = [cache["Device:R"], official("Device.kicad_sym", "C", "Device:C"),
        cache["Amplifier_Operational:MCP6002-xMC"],
        cache["power:GND"], cache["power:VCC"],
        official("power.kicad_sym", "PWR_FLAG", "power:PWR_FLAG")]

# ---- placement (world mm, +Y down, grid 1.27) ---------------------------
R1 = (96.52, 80.01)    # rot 90: pin1 (92.71,80.01) pin2 (100.33,80.01)
C1 = (105.41, 87.63)   # pin1 (105.41,85.09) pin2 (105.41,90.17)
U1A = (118.11, 82.55)  # in+ (110.49,80.01) in- (110.49,85.09) out (125.73,82.55)
U1P = (165.1, 82.55)   # V+ (162.56,74.93) V- x2 (162.56,90.17)
R2 = (133.35, 88.9)    # rot 0: pin1 (133.35,85.09) pin2 (133.35,92.71)

p_r1_1 = pin_world(R1, 90, (0, 3.81));   p_r1_2 = pin_world(R1, 90, (0, -3.81))
p_c1_1 = pin_world(C1, 0, (0, 3.81));    p_c1_2 = pin_world(C1, 0, (0, -3.81))
p_inp = pin_world(U1A, 0, (-7.62, 2.54)); p_inn = pin_world(U1A, 0, (-7.62, -2.54))
p_out = pin_world(U1A, 0, (7.62, 0))
p_vp = pin_world(U1P, 0, (-2.54, 7.62));  p_vn = pin_world(U1P, 0, (-2.54, -7.62))
p_r2_1 = pin_world(R2, 0, (0, 3.81));    p_r2_2 = pin_world(R2, 0, (0, -3.81))

U1B = (118.11, 99.06)  # unused unit: in+ grounded, in- tied to out
p_inpB = pin_world(U1B, 0, (-7.62, 2.54))
p_innB = pin_world(U1B, 0, (-7.62, -2.54))
p_outB = pin_world(U1B, 0, (7.62, 0))
GND4 = (107.95, 100.33)

VCC1 = (92.71, 77.47); GND1 = (105.41, 92.71); GND2 = (133.35, 95.25)
VCC2 = (162.56, 72.39); GND3 = (162.56, 92.71)
PF1 = (158.75, 72.39); PF2 = (158.75, 92.71)
T1 = (105.41, 80.01)   # N1 tap to C1
T2 = (127.0, 82.55)    # feedback tap
FB1 = (127.0, 88.9); FB2 = (110.49, 88.9)

W = [("VCC", VCC1, p_r1_1), ("N1", p_r1_2, p_inp), ("N1", T1, p_c1_1),
     ("GND", p_c1_2, GND1), ("VOUT", p_out, (133.35, 82.55)),
     ("VOUT", T2, FB1), ("VOUT", FB1, FB2), ("VOUT", FB2, p_inn),
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

# ---- assemble ------------------------------------------------------------
doc = [Sym("kicad_sch"),
       [Sym("version"), Sym("20260306")],
       [Sym("generator"), QStr.of("eeschema")],
       [Sym("generator_version"), QStr.of("10.0")],
       [Sym("uuid"), QStr.of(ROOT_UUID)],
       [Sym("paper"), QStr.of("A4")],
       [Sym("lib_symbols"), *LIBS]]
doc += [junction(p) for p in JUNCS]
doc += [wire(a, b) for _, a, b in W]
doc += [label("N1", T1), label("VOUT", (129.54, 82.55))]
doc += [
    symbol("Device:R", R1, 90, "R1", "10k", ["1", "2"]),
    symbol("Device:C", C1, 0, "C1", "100n", ["1", "2"]),
    symbol("Amplifier_Operational:MCP6002-xMC", U1A, 0, "U1", "MCP6002",
           ["1", "2", "3"], unit=1),
    symbol("Amplifier_Operational:MCP6002-xMC", U1B, 0, "U1", "MCP6002",
           ["5", "6", "7"], unit=2),
    symbol("Amplifier_Operational:MCP6002-xMC", U1P, 0, "U1", "MCP6002",
           ["4", "8", "9"], unit=3, val_at=(170.18, 82.55)),
    symbol("Device:R", R2, 0, "R2", "10k", ["1", "2"]),
    symbol("power:GND", GND4, 0, "#PWR06", "GND", ["1"], ref_hidden=True),
    symbol("power:VCC", VCC1, 0, "#PWR01", "VCC", ["1"], ref_hidden=True),
    symbol("power:GND", GND1, 0, "#PWR02", "GND", ["1"], ref_hidden=True),
    symbol("power:GND", GND2, 0, "#PWR03", "GND", ["1"], ref_hidden=True),
    symbol("power:VCC", VCC2, 0, "#PWR04", "VCC", ["1"], ref_hidden=True,
           val_at=(162.56, 68.58)),
    symbol("power:GND", GND3, 0, "#PWR05", "GND", ["1"], ref_hidden=True),
    symbol("power:PWR_FLAG", PF1, 0, "#FLG01", "PWR_FLAG", ["1"],
           ref_hidden=True, val_at=(152.4, 72.39)),
    symbol("power:PWR_FLAG", PF2, 0, "#FLG02", "PWR_FLAG", ["1"],
           ref_hidden=True, val_at=(152.4, 92.71)),
]
doc += [[Sym("sheet_instances"),
         [Sym("path"), QStr.of("/"), [Sym("page"), QStr.of("1")]]],
        [Sym("embedded_fonts"), Sym("no")]]

OUT.mkdir(parents=True, exist_ok=True)
out_path = OUT / "testproj.kicad_sch"
sexp.save_file(str(out_path), doc)
sexp.load_file(str(out_path))  # paren-balance / reparse gate
print(f"wrote {out_path} ({out_path.stat().st_size} B)")
