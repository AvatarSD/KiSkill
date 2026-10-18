"""kx vendor: a throwaway project against a fake official library tree.
Subset symbols (with their `extends` base), project lib tables shadowing
the global nicknames, ${KIPRJMOD} model paths, silk stripped with the
generated polarity mark per policy, copied vs generated models (VRML
fallback without OCP; a project builder hook; NO_MODEL), idempotence,
--check, pruning, config loading. Hermetic: temp dirs, no KiCad.

Usage: python3 tests/test_vendor.py
"""

import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import sexp, vendor  # noqa: E402

FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


SYMLIB = """(kicad_symbol_lib
\t(version 20241209)
\t(generator "kicad_symbol_editor")
\t(symbol "R"
\t\t(property "Reference" "R")
\t)
\t(symbol "D"
\t\t(property "Reference" "D")
\t)
\t(symbol "D_Small"
\t\t(extends "D")
\t)
\t(symbol "Unused"
\t\t(property "Reference" "U")
\t)
)
"""


def fp(name, pads, silk=True, model=None, fab_ref=False):
    """A footprint with pads [(num, x, y)] 1 x 1 mm."""
    out = [f'(footprint "{name}"', '\t(version 20241229)',
           '\t(generator "pcbnew")', '\t(layer "F.Cu")',
           '\t(property "Reference" "REF**"\n\t\t(at 0 -2 0)\n\t\t(layer "F.SilkS")\n\t)']
    if silk:
        out.append('\t(fp_line\n\t\t(start -2 -1)\n\t\t(end 2 -1)\n\t\t(stroke\n'
                   '\t\t\t(width 0.12)\n\t\t\t(type solid)\n\t\t)\n\t\t(layer "F.SilkS")\n\t)')
    if fab_ref:
        out.append('\t(fp_text user "${REFERENCE}"\n\t\t(at 0 0 0)\n\t\t(layer "F.Fab")\n\t)')
    out.append('\t(fp_rect\n\t\t(start -1.5 -0.8)\n\t\t(end 1.5 0.8)\n\t\t(stroke\n'
               '\t\t\t(width 0.1)\n\t\t\t(type solid)\n\t\t)\n\t\t(layer "F.Fab")\n\t)')
    for n, x, y in pads:
        out.append(f'\t(pad "{n}" smd rect\n\t\t(at {x} {y})\n\t\t(size 1 1)\n'
                   '\t\t(layers "F.Cu" "F.Paste" "F.Mask")\n\t)')
    if model:
        out.append(f'\t(model "{model}"\n\t\t(offset\n\t\t\t(xyz 0 0 0)\n\t\t)\n'
                   '\t\t(scale\n\t\t\t(xyz 1 1 1)\n\t\t)\n\t\t(rotate\n\t\t\t(xyz 0 0 0)\n\t\t)\n\t)')
    return "\n".join(out) + "\n)\n"


def sheet(parts):
    body = "".join(f'\t(symbol\n\t\t(lib_id "{lid}")\n\t\t(property "Footprint" "{f}")\n\t)\n'
                   for lid, f in parts)
    return f'(kicad_sch\n\t(version 20250114)\n{body})\n'


def setup(td):
    td = pathlib.Path(td)
    off = td / "official"
    (off / "sym").mkdir(parents=True)
    (off / "sym/Device.kicad_sym").write_text(SYMLIB)
    for lib, name, pads in (("Resistor_SMD", "R_0603", [("1", -1, 0), ("2", 1, 0)]),
                            ("Diode_SMD", "D_SOD-123", [("1", -1.5, 0), ("2", 1.5, 0)]),
                            ("Package_SO", "SOIC-4", [("1", -2, -1), ("2", -2, 1),
                                                      ("3", 2, 1), ("4", 2, -1)]),
                            ("Package_SO", "SOIC-8_unused", [("1", 0, 0), ("2", 1, 0)])):
        d = off / "fp" / f"{lib}.pretty"
        d.mkdir(parents=True, exist_ok=True)
        model = f"${{KICAD10_3DMODEL_DIR}}/{lib}.3dshapes/{name}.step"
        (d / f"{name}.kicad_mod").write_text(fp(name, pads, model=model))
        m = off / "3d" / f"{lib}.3dshapes"
        m.mkdir(parents=True, exist_ok=True)
        if name != "SOIC-4":           # SOIC-4's model is missing: generated
            (m / f"{name}.step").write_text(f"STEP {name}\n")
    proj = td / "proj"
    (proj / "libs/mine.pretty").mkdir(parents=True)
    (proj / "libs/mine.pretty/Widget.kicad_mod").write_text(
        fp("Widget", [("1", -1, 0), ("2", 1, 0), ("3", 0, 1)], fab_ref=True))
    (proj / "libs/mine.pretty/Hole.kicad_mod").write_text(
        fp("Hole", [("", 0, 0)], silk=False))
    (proj / "libs/mine.kicad_sym").write_text(SYMLIB)
    (proj / "fp-lib-table").write_text(
        '(fp_lib_table\n\t(version 7)\n\t(lib\n\t\t(name "mine")\n\t\t(type "KiCad")\n'
        '\t\t(uri "${KIPRJMOD}/libs/mine.pretty")\n\t\t(options "")\n\t\t(descr "")\n\t)\n)\n')
    (proj / "proj.kicad_sch").write_text(sheet([
        ("Device:R", "Resistor_SMD:R_0603"),
        ("Device:D_Small", "Diode_SMD:D_SOD-123"),
        ("mine:R", "Package_SO:SOIC-4"),
        ("mine:R", "mine:Widget"),
        ("mine:R", "mine:Hole")]))
    return off, proj


def cfg_for(off, proj, **kw):
    c = vendor.Config(pathlib.Path(proj), sym_dir=off / "sym", fp_dir=off / "fp",
                      model_dir=off / "3d", ocp_python=[], **kw)
    return c


def snapshot(proj):
    return {str(p.relative_to(proj)): p.read_bytes()
            for p in sorted(pathlib.Path(proj).rglob("*")) if p.is_file()}


os.environ["KX_OCP_PYTHON"] = "/nonexistent/python"   # force the VRML path
built = []


def widget_builder(fab, pads, name):
    built.append(name)
    x0, y0, x1, y1 = fab
    return {"boxes": [[x0, y0, 0.0, x1, y1, 2.0, vendor.GREEN]],
            "shapes": [["sph", 0.0, 0.0, 2.0, 0.5, vendor.GOLD]]}


with tempfile.TemporaryDirectory() as td:
    off, proj = setup(td)
    has_ocp = vendor.ocp_python(cfg_for(off, proj)) is not None
    EXT = ".step" if has_ocp else ".wrl"
    mk = lambda: cfg_for(off, proj, no_model=frozenset({"mine:Hole"}),  # noqa: E731
                         special={"mine:Widget": "widget"},
                         builders={"widget": widget_builder})
    log = []
    check("--check on a fresh project reports work",
          vendor.run(mk(), check=True, log=log.append) == 1 and log)
    check("--check wrote nothing", not (proj / "libs/kicad").exists())
    rc = vendor.run(mk(), log=log.append)
    check("run succeeds (VRML fallback is not a failure)", rc == 0)
    check("own libs auto-detected", mk().own_libs() == ("mine",))

    # symbols: subset + extends base, own lib untouched
    sym = sexp.load_file(str(proj / "libs/kicad/Device.kicad_sym"))
    names = sorted(sexp.atoms(s)[0] for s in sexp.find_all(sym, "symbol"))
    check("symbol subset pulls the extends base", names == ["D", "D_Small", "R"])
    check("own symbol lib untouched", (proj / "libs/mine.kicad_sym").read_text() == SYMLIB)

    # tables: official nicknames shadowed, own row kept, tagged rows only
    st = (proj / "sym-lib-table").read_text()
    ft = (proj / "fp-lib-table").read_text()
    check("sym table row for Device", '"${KIPRJMOD}/libs/kicad/Device.kicad_sym"' in st)
    check("fp table rows for the three official libs",
          all(f'"${{KIPRJMOD}}/libs/kicad/{lib}.pretty"' in ft
              for lib in ("Resistor_SMD", "Diode_SMD", "Package_SO")))
    check("own fp row kept", '"${KIPRJMOD}/libs/mine.pretty"' in ft)
    check("no row for an own lib in the sym table", '"mine"' not in st)

    # footprints: subset, silk, marks
    kd = proj / "libs/kicad"
    check("only used footprints copied",
          sorted(p.name for p in kd.rglob("*.kicad_mod")) ==
          ["D_SOD-123.kicad_mod", "R_0603.kicad_mod", "SOIC-4.kicad_mod"])

    def silk(p):
        doc = sexp.load_file(str(p))
        return [sexp.tag_of(x) for x in doc if isinstance(x, list)
                and vendor._layer(x) in vendor.SILK and sexp.tag_of(x) != "property"]

    r = kd / "Resistor_SMD.pretty/R_0603.kicad_mod"
    d = kd / "Diode_SMD.pretty/D_SOD-123.kicad_mod"
    so = kd / "Package_SO.pretty/SOIC-4.kicad_mod"
    w = proj / "libs/mine.pretty/Widget.kicad_mod"
    check("resistor: no silk at all", silk(r) == [])
    check("diode: one cathode bar", silk(d) == ["fp_line"])
    check("IC: one pin-1 dot", silk(so) == ["fp_circle"])
    check("own footprint rewritten in place, dot kept", silk(w) == ["fp_circle"])
    rt = r.read_text()
    check("Reference moved to F.Fab", '(property "Reference" "REF**"' in rt
          and '"F.SilkS"' not in rt)
    check("Reference hidden when a Fab ${REFERENCE} exists",
          "(hide yes)" in w.read_text())
    bar = [x for x in sexp.load_file(str(d)) if sexp.tag_of(x) == "fp_line"
           and vendor._layer(x) == "F.SilkS"][0]
    xs = [float(sexp.atoms(sexp.find(bar, k))[0]) for k in ("start", "end")]
    check("cathode bar sits beyond pad 1 (x < -2)", all(x < -2.0 for x in xs))

    # models
    rm = kd / "Resistor_SMD.pretty/R_0603.kicad_mod"
    check("official model copied", (proj / "libs/3d/Resistor_SMD.3dshapes/R_0603.step")
          .read_text() == "STEP R_0603\n")
    check("model path rewritten to ${KIPRJMOD}",
          '"${KIPRJMOD}/libs/3d/Resistor_SMD.3dshapes/R_0603.step"' in rm.read_text())
    gen = json.loads((proj / "libs/3d/generated.json").read_text())
    check("missing official model generated",
          "Package_SO.3dshapes/SOIC-4" + EXT in gen)
    check("own footprint model generated by the builder hook",
          "mine.3dshapes/Widget" + EXT in gen and "Widget" in built)
    if not has_ocp:
        check("VRML fallback written",
              (proj / "libs/3d/Package_SO.3dshapes/SOIC-4.wrl").read_text()
              .startswith("#VRML V2.0"))
        check("footprint points at the VRML it got",
              "SOIC-4.wrl" in (kd / "Package_SO.pretty/SOIC-4.kicad_mod").read_text())
    check("NO_MODEL footprint gets no model",
          "(model" not in (proj / "libs/mine.pretty/Hole.kicad_mod").read_text())

    # idempotence + check
    before = snapshot(proj)
    built.clear()
    log.clear()
    check("second run succeeds", vendor.run(mk(), log=log.append) == 0)
    check("second run is byte-identical", snapshot(proj) == before)
    check("model not rebuilt when its input is unchanged",
          "0 models rebuilt" in log[0])
    check("--check clean after a run", vendor.run(mk(), check=True, log=log.append) == 0)

    # spec change -> rebuild; unused -> pruned
    vendor.run(cfg_for(off, proj, no_model=frozenset({"mine:Hole"}),
                       special={"mine:Widget": "widget"}, builders={"widget": widget_builder},
                       heights=[("SOIC", 3.0)]), log=log.append)
    check("height change regenerates the box model",
          json.loads((proj / "libs/3d/generated.json").read_text())
          ["Package_SO.3dshapes/SOIC-4" + EXT] != gen["Package_SO.3dshapes/SOIC-4" + EXT])
    (proj / "proj.kicad_sch").write_text(sheet([("Device:R", "Resistor_SMD:R_0603"),
                                                ("mine:R", "mine:Widget")]))
    vendor.run(mk(), log=log.append)
    check("unused footprint and model pruned",
          not (kd / "Diode_SMD.pretty").exists() and
          not (proj / "libs/3d/Diode_SMD.3dshapes").exists())
    check("unused table row dropped", "Diode_SMD" not in (proj / "fp-lib-table").read_text())
    check("unused symbol dropped", '"D_Small"' not in
          (proj / "libs/kicad/Device.kicad_sym").read_text())

    # policy: silk kept when disabled; custom mark rule
    off2, proj2 = setup(pathlib.Path(td) / "two")
    vendor.run(cfg_for(off2, proj2, strip_silk=False), log=log.append)
    check("strip_silk=False keeps the silk",
          silk(proj2 / "libs/kicad/Resistor_SMD.pretty/R_0603.kicad_mod") == ["fp_line"])
    off3, proj3 = setup(pathlib.Path(td) / "three")
    vendor.run(cfg_for(off3, proj3, mark_kind=lambda lib, name, pads: "dot"),
               log=log.append)
    check("mark_kind override puts a dot on the resistor",
          silk(proj3 / "libs/kicad/Resistor_SMD.pretty/R_0603.kicad_mod") == ["fp_circle"])
    try:
        vendor.model_spec(cfg_for(off3, proj3), "nope", "mine", "Widget",
                          sexp.load_file(str(proj3 / "libs/mine.pretty/Widget.kicad_mod")))
        check("unknown builder kind raises", False)
    except KeyError:
        check("unknown builder kind raises", True)

    # config loading: json fields + .py configure hook
    (proj3 / "kx-vendor.json").write_text(json.dumps(
        {"no_model": ["mine:Hole"], "uid_ns": "x", "heights": [["SOIC", 2]]}))
    hook = pathlib.Path(td) / "hook.py"
    hook.write_text("def configure(cfg):\n    cfg.descr = 'from hook'\n")
    c = vendor.load_config(proj3, str(hook))
    check("json config read from the project",
          c.no_model == frozenset({"mine:Hole"}) and c.uid_ns == "x")
    check(".py configure hook applied", c.descr == "from hook")
    (proj3 / "kx-vendor.json").write_text('{"bogus": 1}')
    try:
        vendor.load_config(proj3)
        check("unknown config key refused", False)
    except ValueError:
        check("unknown config key refused", True)
    check("kx vendor without a project prints usage", vendor.main([]) == 2)

print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all vendor tests passed'}")
sys.exit(1 if FAILS else 0)
