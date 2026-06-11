"""PCB staging tests: netlist parse on testproj, empty board from a
donor header, WAGO footprint staged path-linked, kicad-cli render gate.
Slow (~10 s: netlist export + svg export via flatpak).

Usage: python3 tests/test_pcb.py
"""

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import pcb, sexp  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
DONOR = ("/home/sd/prj/20250713_ig-smartgrow/gorshok/smartgrow-gorshok/"
         "hat/strawberry_1170-hat/strawberry_1170-hat.kicad_pcb")
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


# --- netlist ground truth on testproj --------------------------------------
nl = pcb.netlist(str(HERE / "fixtures/testproj/testproj.kicad_sch"))
check("netlist comps", {"R1", "R2", "C1", "U1"} <= nl["comps"].keys())
check("netlist tstamps present",
      all(c["tstamps"] for c in nl["comps"].values()))
# root-sheet local labels get a "/" prefix in netlists; power nets don't
check("netlist nets: /VOUT members",
      sorted(nl["nets"].get("/VOUT", [])) ==
      [("R2", "1"), ("U1", "1"), ("U1", "2")])
check("netlist nets: GND includes coincident V- pins",
      sorted(nl["nets"].get("GND", []))[-2:] == [("U1", "5"), ("U1", "9")])

# --- empty board from donor + staging ---------------------------------------
board = pcb.empty_board_from(DONOR)
check("empty board has layers", sexp.find(board, "layers") is not None)
check("donor body dropped", not list(sexp.find_all(board, "footprint")))

fpdef = pcb.load_footprint(str(HERE / "fixtures/WAGO_234-212.kicad_mod"),
                           "WAGO_hat:WAGO_234-212")
check("fp renamed", sexp.atoms(fpdef)[0] == "WAGO_hat:WAGO_234-212")
check("fp headers stripped", sexp.find(fpdef, "version") is None)

pcb.stage(board, fpdef, "J1", "WAGO_234-212", (50.8, 50.8),
          nl["comps"]["R1"]["tstamps"], "testproj.kicad_sch")
fps = list(sexp.find_all(board, "footprint"))
check("staged one footprint", len(fps) == 1)
check("path linked", sexp.atoms(sexp.find(fps[0], "path"))[0]
      == "/" + nl["comps"]["R1"]["tstamps"])
ref = next(p for p in sexp.find_all(fps[0], "property")
           if sexp.atoms(p)[0] == "Reference")
check("ref set", sexp.atoms(ref)[1] == "J1")
# .kicad_mod files carry no top-level uuid; prove the staged instance
# shares NO uuid with the source file (all refreshed)
src_uuids = {sexp.atoms(u)[0] for u in sexp.walk(
    sexp.load_file(str(HERE / "fixtures/WAGO_234-212.kicad_mod")), "uuid")}
staged_uuids = {sexp.atoms(u)[0] for u in sexp.walk(fps[0], "uuid")}
check("uuids refreshed", src_uuids and staged_uuids
      and not (src_uuids & staged_uuids))

# --- save + kicad-cli accepts the board -------------------------------------
out = pcb.SCRATCH / "staged.kicad_pcb"
sexp.save_file(str(out), board)
svg = pcb.SCRATCH / "staged.svg"
r = subprocess.run(pcb.KICAD_CLI + ["pcb", "export", "svg", "--output",
                                    str(svg), "--layers",
                                    "F.Cu,Edge.Cuts,F.SilkS", str(out)],
                   capture_output=True, text=True)
check("kicad-cli accepts staged board", r.returncode == 0 and svg.exists())

# donor untouched (read-only contract)
check("donor untouched",
      pathlib.Path(DONOR).stat().st_size > 2_000_000)

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
