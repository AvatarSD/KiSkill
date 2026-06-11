"""Fab-output tests on testproj (sch) + the staged scratch board (pcb).
Slow (~15 s of kicad-cli). Regenerates the staged board if missing.

Usage: python3 tests/test_fab.py
"""

import csv
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import fab, pcb, sexp  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
SCH = HERE / "fixtures/testproj/testproj.kicad_sch"
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


out = fab.bundle(str(SCH), None, str(pcb.SCRATCH / "fabtest"))

rows = list(csv.DictReader(open(out["bom"])))
by_refs = {r["Refs"]: r for r in rows}
check("bom grouped: R1,R2 one row qty 2",
      by_refs.get("R1,R2", {}).get("Qty") == "2")
check("bom rows: C1 + U1 + grouped Rs + power", len(rows) >= 3)
jlc = list(csv.DictReader(open(out["bom_jlc"])))
check("jlc bom headers", set(jlc[0].keys()) ==
      {"Comment", "Designator", "Footprint", "LCSC Part #"})

# board side: reuse the staged scratch board from test_pcb (or rebuild)
board_path = pcb.SCRATCH / "staged.kicad_pcb"
if not board_path.exists():
    r = subprocess.run([sys.executable, str(HERE / "test_pcb.py")],
                       capture_output=True, text=True)
    check("staged board rebuilt", board_path.exists())

outb = fab.bundle(None, str(board_path), str(pcb.SCRATCH / "fabtest"))
gerber_exts = {pathlib.Path(p).suffix for p in outb["gerbers"]}
check("gerbers + drill produced", len(outb["gerbers"]) >= 5
      and ".drl" in gerber_exts)
cpl = (pcb.SCRATCH / "fabtest/cpl.csv").read_text()
check("cpl mentions staged J1", "J1" in cpl)

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
