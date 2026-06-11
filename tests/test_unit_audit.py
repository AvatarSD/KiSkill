"""Multi-unit completeness audit + the ERC it front-runs (kx unit-audit).

Forum lesson (kicad.info; KLC S4.5): every unit of a multi-unit part must
be placed — a dual opamp has units A, B and a power unit; leave one off and
KiCad ERC raises `missing_unit` (plus `missing_input_pin` for its dangling
inputs). The testproj's MCP6002 is exactly such a dual opamp with all three
units placed. This test locks:
  1. lib_unit_counts reads the unit count from lib_symbols (MCP6002 -> 3,
     single-unit parts -> 1), anchoring on the item name so '-'/digits in
     the name don't break the parse;
  2. unit_audit groups placed `(unit N)` by reference designator and reports
     the gap from the SCHEMATIC, before any flatpak ERC run;
  3. the audit agrees with ERC — strip unit 2 and BOTH the audit flags U1
     and ERC raises `missing_unit`, where the clean baseline has neither.

Slow (~12 s: two ERC runs via flatpak).
Usage: python3 tests/test_unit_audit.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import cli, diff, sexp  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "fixtures/testproj/testproj.kicad_sch"
MCP = "Amplifier_Operational:MCP6002-xMC"
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def erc_missing_unit(sch_path: str, tag: str) -> bool:
    return any(v.startswith("missing_unit ")
               for v in diff.erc_report(sch_path, tag))


# --- complete baseline -------------------------------------------------------
base = cli.probe(str(SRC))
check("lib_unit_counts: MCP6002 is 3 units", base["lib_unit_counts"][MCP] == 3)
check("lib_unit_counts: passives/power are 1 unit",
      all(base["lib_unit_counts"][k] == 1
          for k in ("Device:R", "Device:C", "power:GND", "power:VCC")))

ua = cli.unit_audit(base)
check("baseline sees U1 as the only multi-unit part",
      list(ua["multi_unit_parts"]) == ["U1"])
check("baseline U1 has all 3 units placed",
      ua["multi_unit_parts"]["U1"]["units_placed"] == [1, 2, 3])
check("baseline U1 missing nothing", ua["multi_unit_parts"]["U1"]["missing"] == [])
check("baseline nothing incomplete", ua["incomplete"] == [])

# --- strip unit 2 of U1 ------------------------------------------------------
diff.SCRATCH.mkdir(parents=True, exist_ok=True)
mut = diff.SCRATCH / "nounit.kicad_sch"
root = sexp.load_file(str(SRC))
removed = 0
for s in list(sexp.find_all(root, "symbol")):
    li, u = sexp.find(s, "lib_id"), sexp.find(s, "unit")
    if li and sexp.atoms(li)[0] == MCP and u and sexp.atoms(u)[0] == "2":
        root.remove(s)
        removed += 1
sexp.save_file(str(mut), root)
check("mutation removed exactly one unit-2 instance", removed == 1)

ua2 = cli.unit_audit(cli.probe(str(mut)))
check("mutant audit flags U1 missing unit 2",
      ua2["multi_unit_parts"]["U1"]["missing"] == [2])
check("mutant audit lists U1 incomplete", ua2["incomplete"] == ["U1"])

# --- the audit front-runs ERC: agree on missing_unit -------------------------
check("ERC clean baseline has no missing_unit",
      not erc_missing_unit(str(SRC), "ub"))
check("ERC trips missing_unit when a unit is absent",
      erc_missing_unit(str(mut), "um"))

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
