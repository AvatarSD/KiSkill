"""Power-driver audit + the netlist-drops-PWR_FLAG gotcha (kx power-audit).

Forum lesson (kicad.info t/35552, t/57016): "Input power pin not driven by
output power pins" = a missing PWR_FLAG driver declaration, not a wiring
fault. This test locks two things:
  1. power_audit reads PWR_FLAG/rails from the SCHEMATIC (probe), so it
     sees flags appear/disappear;
  2. the GOTCHA — `kicad-cli sch export netlist` drops PWR_FLAG nodes, so a
     flagged and a flag-stripped schematic have IDENTICAL netlist driver
     content (zero power_out) while ERC tells them apart. Hence: never
     audit power drivers from the netlist.

Slow (~15 s: two netlist exports + two ERC runs via flatpak).
Usage: python3 tests/test_power_audit.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import cli, diff, pcb, sexp  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "fixtures/testproj/testproj.kicad_sch"
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def raw_power_out(sch_path: str) -> list:
    """power_out nodes straight from the exported netlist."""
    nl = diff.SCRATCH / "pa.net"
    pcb.subprocess.run(pcb.KICAD_CLI + ["sch", "export", "netlist",
                                        "--output", str(nl), sch_path],
                       capture_output=True, text=True)
    r = sexp.load_file(str(nl))
    out = []
    for n in sexp.walk(r, "net"):
        for nd in sexp.find_all(n, "node"):
            pt = sexp.find(nd, "pintype")
            if pt and sexp.atoms(pt)[0] == "power_out":
                out.append(sexp.atoms(sexp.find(nd, "ref"))[0])
    return out


# --- flagged baseline --------------------------------------------------------
pa = cli.power_audit(cli.probe(str(SRC)))
check("baseline rails GND+VCC", pa["power_rails"] == ["GND", "VCC"])
check("baseline has 2 flags", pa["pwr_flags"] == 2)
check("baseline nothing possibly-undriven", pa["possibly_undriven"] == [])

# --- flag-stripped mutant ----------------------------------------------------
mut = diff.SCRATCH / "noflag.kicad_sch"
diff.SCRATCH.mkdir(parents=True, exist_ok=True)
root = sexp.load_file(str(SRC))
for f in [s for s in sexp.find_all(root, "symbol")
          if (li := sexp.find(s, "lib_id"))
          and sexp.atoms(li)[0] == "power:PWR_FLAG"]:
    root.remove(f)
sexp.save_file(str(mut), root)

pa2 = cli.power_audit(cli.probe(str(mut)))
check("mutant has 0 flags", pa2["pwr_flags"] == 0)
check("mutant flags both rails possibly-undriven",
      pa2["possibly_undriven"] == ["GND", "VCC"])

# --- the gotcha: ERC distinguishes; the netlist does NOT ---------------------
erc_base = [v for v in diff.erc_report(str(SRC), "b")
            if "not_driven" in v]
erc_mut = [v for v in diff.erc_report(str(mut), "m")
           if "not_driven" in v]
check("ERC clean with flags", erc_base == [])
check("ERC trips power_pin_not_driven without flags", len(erc_mut) == 2)

check("netlist power_out empty WITH flags (flags dropped)",
      raw_power_out(str(SRC)) == [])
check("netlist power_out empty WITHOUT flags (identical => blind)",
      raw_power_out(str(mut)) == [])

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
