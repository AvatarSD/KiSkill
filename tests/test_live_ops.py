"""Verified atomic ops on the live schematic (kx live / live_ops.py).

SKIPs without a live IPC socket (launch nightly eeschema on a scratch
copy first — kicad-project skill). Leaves the live document exactly as
found: every push is removed again by id.

Usage: .venv/bin/python tests/test_live_ops.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import live  # noqa: E402

if not live.ipc_socket() or not live.ipc_ping():
    print("SKIP: no live KiCad IPC socket — launch nightly eeschema first")
    raise SystemExit(0)

try:
    from kicad_lib import live_ops  # noqa: E402
    if live_ops.KiCad is None:
        raise ImportError
except ImportError:
    print("SKIP: kipy not importable — run via the repo .venv")
    raise SystemExit(0)

FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


sch = live_ops.connect()

# --- read model --------------------------------------------------------------
snap = live_ops.snapshot(sch)
check("snapshot has our symbols", {"R1", "R2", "C1", "U1"} <
      set(snap["symbols"]))
check("snapshot wires in mm", all(len(w["a"]) == 2 for w in snap["wires"]))
check("junctions read via KOT_SCH_JUNCTION", len(snap["junctions"]) >= 4)
check("live model verifier-clean", snap["violations"] == [])

n_wires0 = len(snap["wires"])

# --- refusals never touch the document ---------------------------------------
bad = live_ops.add_wire(sch, 200, 100, 210, 110)      # diagonal
check("diagonal refused", any("diagonal" in v for v in bad))
bad = live_ops.add_wire(sch, 200.5, 100, 210.5, 100)  # off-grid
check("off-grid refused", any("off-grid" in v for v in bad))
bad = live_ops.add_junction(sch, 1.0, 1.0)            # off-grid junction
check("off-grid junction refused", any("off-grid" in v for v in bad))
check("document untouched after refusals",
      len(live_ops.snapshot(sch)["wires"]) == n_wires0)

# --- push + remove cycle ------------------------------------------------------
check("clean wire pushes",
      live_ops.add_wire(sch, 203.2, 101.6, 215.9, 101.6) == [])
snap2 = live_ops.snapshot(sch)
mine = [w for w in snap2["wires"] if w["a"] == [203.2, 101.6]]
check("pushed wire visible with id", len(mine) == 1)
check("overlap of the same span refused",
      any("double-draw" in v
          for v in live_ops.add_wire(sch, 208.28, 101.6, 212.09, 101.6)))
check("rm by id", live_ops.remove_ids(sch, [mine[0]["id"]]) == 1)
check("back to baseline",
      len(live_ops.snapshot(sch)["wires"]) == n_wires0)

# --- label push + remove ------------------------------------------------------
check("label pushes", live_ops.add_label(sch, 203.2, 106.68, "KXTEST") == [])
snap3 = live_ops.snapshot(sch)
lbl = [x for x in snap3["labels"] if x["text"] == "KXTEST"]
check("label visible", len(lbl) == 1)
check("label removed", live_ops.remove_ids(sch, [lbl[0]["id"]]) == 1)

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
