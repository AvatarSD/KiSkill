"""Atomic-ops tests: mutate a fresh sheet through ops.*, gate every step
through reparse, exercise error preconditions, and prove the refactored
generator reproduces the committed testproj fixture (probe-equal modulo
random uuids).

Usage: python3 tests/test_ops.py
"""

import json
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import build, cli, ops, sexp  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def reparse(doc):
    return sexp.parse(sexp.dumps(doc))


# --- build + mutate a fresh sheet -----------------------------------------
doc = build.empty_sheet("00000000-1111-4222-8333-444444444444")
rdef = ops.extract_libdef(str(HERE / "fixtures/dac_buf.kicad_sch"),
                          "Device:R", "Device:R")
ops.place(doc, rdef, (96.52, 80.01), 90, "R1", "10k", ["1", "2"])
ops.add_wire(doc, (92.71, 80.01), (87.63, 80.01))
ops.add_label(doc, "VIN", (87.63, 80.01))
ops.add_junction(doc, (92.71, 80.01))
doc = reparse(doc)

inv = {t: len(list(sexp.find_all(doc, t)))
       for t in ("symbol", "wire", "label", "junction")}
check("place/wire/label/junction counts",
      inv == {"symbol": 1, "wire": 1, "label": 1, "junction": 1})
check("lib cached once", len(list(sexp.find_all(
    sexp.find(doc, "lib_symbols"), "symbol"))) == 1)

ops.place(doc, rdef, (110.49, 80.01), 0, "R2", "4k7", ["1", "2"])
check("lib not re-cached", len(list(sexp.find_all(
    sexp.find(doc, "lib_symbols"), "symbol"))) == 1)

n = ops.set_prop(doc, "R2", "Footprint",
                 "Resistor_SMD:R_0603_1608Metric")
check("set_prop existing", n == 1 and "R_0603_1608Metric" in sexp.dumps(doc))
ops.set_prop(doc, "R2", "MPN", "RC0603FR-074K7L")
check("set_prop new prop", "RC0603FR-074K7L" in sexp.dumps(doc))

check("delete_ref", ops.delete_ref(doc, "R2") == 1
      and ops.refs(doc) == {"R1"})
wid = sexp.atoms(sexp.find(next(sexp.find_all(doc, "wire")), "uuid"))[0]
ops.delete_uuid(doc, wid)
check("delete_uuid wire", not list(sexp.find_all(doc, "wire")))
doc = reparse(doc)
check("reparse after deletes", ops.refs(doc) == {"R1"})

# --- error preconditions ---------------------------------------------------
def raises(fn):
    try:
        fn()
        return False
    except ops.OpError:
        return True

check("dup ref other lib_id rejected", raises(lambda: ops.place(
    doc, ops.extract_libdef(str(HERE / "fixtures/dac_buf.kicad_sch"),
                            "power:GND", "power:GND"),
    (50.8, 50.8), 0, "R1", "x", ["1"])))
check("zero wire rejected", raises(lambda: ops.add_wire(doc, (0, 0), (0, 0))))
check("set_prop unknown ref", raises(lambda: ops.set_prop(doc, "Z9", "a", "b")))
check("delete unknown ref", raises(lambda: ops.delete_ref(doc, "Z9")))
check("delete unknown uuid", raises(lambda: ops.delete_uuid(doc, "nope")))

# --- regen equivalence: refactored generator == committed fixture ----------
with tempfile.TemporaryDirectory() as td:
    out = pathlib.Path(td) / "regen.kicad_sch"
    r = subprocess.run([sys.executable, str(HERE.parent / "tools/gen_testproj.py"),
                        str(out)], capture_output=True, text=True)
    check("regen runs verifier-clean", r.returncode == 0
          and "0 violations" in r.stdout)
    a = cli.probe(str(out))
    b = cli.probe(str(HERE / "fixtures/testproj/testproj.kicad_sch"))
    a.pop("file"); b.pop("file")
    check("regen probe-equal to fixture", a == b)

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
