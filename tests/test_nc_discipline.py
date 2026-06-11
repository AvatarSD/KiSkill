"""No-connect-flag discipline + the inverse-trap ERC code it guards.

Forum lesson (kicad.info t/46229 "A pin with no connection flag is
connected"; t/21294): a No-Connect flag means "nothing else attaches here".
Two directions:
  - PRIMARY: an intentionally-unused pin needs a NC flag, or ERC raises
    `pin_not_connected`. Silence it by DOCUMENTING intent (add the flag),
    never by lowering severity.
  - INVERSE TRAP (proven here): a NC flag dropped on a pin/node that IS
    connected raises `no_connect_connected` — the flag and the wiring
    contradict. Fix = remove the flag (the pin is used) or the wire (it
    isn't); don't keep both.

This locks the inverse trap cold: the clean testproj has no NC markers and
no `no_connect_connected`; inject one NC on a live junction (a junction only
exists where >=3 segments meet, so a NC there is unambiguously connected)
and the code appears. Guards the skill's claim across KiCad versions.

Slow (~12 s: two ERC runs via flatpak).
Usage: python3 tests/test_nc_discipline.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import cli, diff, sexp  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "fixtures/testproj/testproj.kicad_sch"
CODE = "no_connect_connected"
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def has_code(sch_path: str, tag: str) -> bool:
    return any(v.startswith(CODE + " ") for v in diff.erc_report(sch_path, tag))


# --- clean baseline: no NC markers, no inverse-trap --------------------------
base = cli.probe(str(SRC))
check("baseline has zero no_connect markers", base["counts"]["no_connect"] == 0)
check("baseline ERC clean of no_connect_connected", not has_code(str(SRC), "ncb"))

# --- inject a NC flag on a live junction (unambiguously connected) -----------
diff.SCRATCH.mkdir(parents=True, exist_ok=True)
mut = diff.SCRATCH / "nc_on_live.kicad_sch"
root = sexp.load_file(str(SRC))
juncs = list(sexp.find_all(root, "junction"))
check("fixture has at least one junction to target", len(juncs) >= 1)
jx, jy = sexp.atoms(sexp.find(juncs[0], "at"))[:2]
nc = sexp.parse(f'(no_connect (at {jx} {jy}) '
                f'(uuid "deadbeef-0000-0000-0000-000000000001"))')
root.append(nc)
sexp.save_file(str(mut), root)

mutpr = cli.probe(str(mut))
check("mutant now has one no_connect marker",
      mutpr["counts"]["no_connect"] == 1)
check("mutant ERC raises no_connect_connected (NC on a live node)",
      has_code(str(mut), "ncm"))

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
