"""emsim fast tests: prepare() and stackup translation only — the full
dockerized FDTD run is validated by the loop (meander_loose smoke:
rc 0, |S11|~1 with 455 ps delay on the open meander = physical) and is
too slow for the routine suite. Needs the routed scratch board; rebuilds
it via test_layout if missing.

Usage: python3 tests/test_emsim.py
"""

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import emsim, layout  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


board = layout.SCRATCH / "route_out.kicad_pcb"
if not board.exists():
    subprocess.run([sys.executable, str(HERE / "test_layout.py")],
                   capture_output=True, text=True)
check("routed board present", board.exists())

wd = layout.SCRATCH / "emsim_prep"
res = emsim.prepare(str(board), str(wd))
check("fab gerbers laid out", any((wd / "fab").glob("*.gbr")))
check("drill present", any((wd / "fab").glob("*.drl")))
check("pos csv named for gerber2ems",
      (wd / "fab" / "route_out-pos.csv").exists())

stk = json.loads((wd / "stackup.json").read_text())
cu = [l for l in stk["layers"] if l["name"].endswith(".Cu")]
check("stackup has 2 Cu layers w/ thickness",
      len(cu) == 2 and all(l["thickness"] == 0.035 for l in cu))
check("dielectric with epsilon", any(
    l.get("epsilon") for l in stk["layers"]))

sim = json.loads((wd / "simulation.json").read_text())
check("simulation template valid", sim["format_version"] == "1.2"
      and sim["frequency"]["stop"] == 6e9)

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
