"""Sourcing tests — hermetic (no network): zip import + lib-table
registration. fetch_lcsc is exercised live by the loop (C25804 run,
commit message has the evidence) since it needs the EasyEDA API.

Usage: python3 tests/test_source.py
"""

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

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
