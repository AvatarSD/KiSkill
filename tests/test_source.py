"""Sourcing tests — hermetic (no network): zip import + lib-table
registration. fetch_lcsc is exercised live by the loop (C25804 run,
commit message has the evidence) since it needs the EasyEDA API.

Usage: python3 tests/test_source.py
"""

import os
import pathlib
import sys
import tempfile
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import ops, sexp, source  # noqa: E402
from kicad_lib.sexp import Sym, QStr  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


with tempfile.TemporaryDirectory() as td:
    proj = pathlib.Path(td) / "proj"
    proj.mkdir()

    # registration: fresh, duplicate, second lib
    check("register fresh", source.register(str(proj), "sym", "a",
                                            "${KIPRJMOD}/libs/a.kicad_sym"))
    check("register duplicate is no-op",
          not source.register(str(proj), "sym", "a", "x"))
    check("register second", source.register(str(proj), "sym", "b", "y"))
    t = sexp.load_file(str(proj / "sym-lib-table"))
    check("table has 2 libs", len(list(sexp.find_all(t, "lib"))) == 2)

    # zip import: synthesize a SnapEDA-shaped zip from fixtures
    r = ops.extract_libdef(str(HERE / "fixtures/dac_buf.kicad_sch"),
                           "Device:R", "Device:R")
    r[1] = QStr.of("R")
    symtext = sexp.dumps([Sym("kicad_symbol_lib"),
                          [Sym("version"), Sym("20241209")],
                          [Sym("generator"), QStr.of("kx")], r])
    zp = pathlib.Path(td) / "part.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("KiCad/PART-X.kicad_sym", symtext)
        z.writestr("KiCad/PART-X.kicad_mod",
                   (HERE / "fixtures/WAGO_234-212.kicad_mod").read_text())

    res = source.import_zip(str(zp), str(proj), name="partx")
    check("zip: symbol imported", res["symbols"] == 1
          and (proj / "libs/partx.kicad_sym").exists())
    check("zip: footprint imported", res["footprints"] == 1
          and (proj / "libs/partx.pretty/PART-X.kicad_mod").exists())
    check("zip: tables registered", res.get("registered_sym")
          and res.get("registered_fp"))
    check("zip: fp-table parses", len(list(sexp.find_all(
        sexp.load_file(str(proj / "fp-lib-table")), "lib"))) == 1)

# fetch_lcsc with a stand-in easyeda2kicad that does what 1.0.1 does with
# --project-relative: Path(OUTPUT.3dshapes).relative_to(Path.cwd())
FAKE = """#!/usr/bin/env python3
import sys
from pathlib import Path
out = next(a.split("=", 1)[1] if "=" in a else sys.argv[sys.argv.index(a) + 1]
           for a in sys.argv if a.startswith("--output"))
rel = Path(f"{out}.3dshapes").relative_to(Path.cwd()).as_posix()
Path(f"{out}.kicad_sym").write_text('(kicad_symbol_lib (version 20211014))\\n')
Path(f"{out}.pretty").mkdir(exist_ok=True)
print("model", "${KIPRJMOD}/" + rel)
"""
with tempfile.TemporaryDirectory() as td:
    td = pathlib.Path(td)
    (td / "bin").mkdir()
    exe = td / "bin/easyeda2kicad"
    exe.write_text(FAKE)
    exe.chmod(0o755)
    real_bin, source.VENV_BIN = source.VENV_BIN, td / "bin"
    (td / "work").mkdir()
    (td / "far/away/proj").mkdir(parents=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(td / "work")
        for label, arg in (("relative", "../far/away/proj"),
                           ("outside cwd", str(td / "far/away/proj"))):
            try:
                res = source.fetch_lcsc("C1", arg)
                ok = (res["log"] == ["model ${KIPRJMOD}/libs/easyeda2kicad.3dshapes"]
                      and pathlib.Path(res["symbol_lib"]).is_absolute())
            except (RuntimeError, ValueError) as e:
                print("   ", e)
                ok = False
            check(f"fetch: PROJECT_DIR {label} of cwd works", ok)
        check("fetch: tables registered in the project",
              "easyeda2kicad" in (td / "far/away/proj/sym-lib-table").read_text())
    finally:
        os.chdir(old_cwd)
        source.VENV_BIN = real_bin

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
