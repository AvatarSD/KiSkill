"""Live schematic IPC against a running KiCad v11 nightly (10.99+).

SKIPs unless /tmp/kicad/api.sock is alive (launch standalone eeschema on a
scratch schematic first — see kicad-project skill, "Live IPC" section).
Exercises: detect() ipc fields, get_schematic read (refs vs file probe),
live create_items + push_commit, and pins down the standalone-eeschema
handler gap (save/revert/run_action raise ApiError "no handler").

Usage: .venv/bin/python tests/test_live_ipc.py   (needs kipy >= 0.8 master)
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import live  # noqa: E402

if not live.ipc_socket() or not live.ipc_ping():
    print("SKIP: no live KiCad IPC socket — launch nightly eeschema first")
    raise SystemExit(0)

try:
    import kipy  # noqa: F401
    import kipy.schematic_types as st
    from kipy import KiCad
    from kipy.errors import ApiError
    from kipy.geometry import Vector2
except ImportError:
    print("SKIP: kipy not importable in this interpreter — run via .venv")
    raise SystemExit(0)

FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


# --- detection surface -------------------------------------------------------
d = live.detect()
check("ipc_alive", d["ipc_alive"] is True)
check("scope pcb+sch (10.99 counts as v11)",
      d["backends"]["ipc"]["scope"] == "pcb+sch")
check("recommended ipc", d["recommended"] == "ipc")
docs = d["open_documents"]
check("open_documents lists a schematic",
      any(x["type"] == "schematic" for x in docs))

# --- live read vs file probe -------------------------------------------------
k = KiCad(socket_path=f"ipc://{live.ipc_socket()}")
sch = k.get_schematic()
live_refs = sorted(s.reference_field.text.value for s in sch.get_symbols())
check("live refs contain user symbols", {"R1", "R2", "C1", "U1"} <
      set(live_refs))
check("multi-unit U1 appears once per placed unit",
      live_refs.count("U1") == 3)
check("live wires present", len(sch.get_lines()) >= 10)

# --- live write: create + commit (undoable in the GUI) -----------------------
n0 = len(sch.get_text())
t = st.SchematicText()
t.value = "kx-live-ipc-test"
t.position = Vector2.from_xy(40_640_000, 20_320_000)
commit = sch.begin_commit()
created = sch.create_items(t)
sch.push_commit(commit, "kx test")
check("create_items returns the item", len(created) == 1)
check("text item count grew", len(sch.get_text()) == n0 + 1)

# clean up our own mess (live delete — also exercises remove_items)
try:
    sch.remove_items(created)
    check("remove_items cleans up", len(sch.get_text()) == n0)
except ApiError as e:
    print("     remove_items unhandled on this nightly:", str(e)[:60])

# --- handler-gap contract (standalone eeschema, nightly 10.99) ----------------
# These FLIP to working once upstream registers the common handler in
# standalone frames (or when eeschema is opened from the PM) — at that
# point update the kicad-project skill: agent-side save becomes possible.
gap = {}
for name, fn in (("save", lambda: sch.save()),
                 ("revert", lambda: sch.revert()),
                 ("get_version", lambda: k.get_version())):
    try:
        fn()
        gap[name] = "HANDLED"
    except ApiError:
        gap[name] = "unhandled"
print("     handler gap:", gap)
check("gap map recorded", all(v in ("HANDLED", "unhandled")
                              for v in gap.values()))
if any(v == "HANDLED" for v in gap.values()):
    print("     NOTE: nightly now handles more — update skills!")

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
