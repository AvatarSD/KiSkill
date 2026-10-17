"""Live board ops end to end against a REAL KiCad PCB editor — a headless
one (kx live headless: Xvfb + flatpak pcbnew, scratch config), never the
operator's desktop. Each op must be one undo step and read back exactly.

SKIPs when kipy (repo .venv), Xvfb or the flatpak KiCad is missing.
KX_LIVE_TEST_SOCKET=PATH runs against an editor you started yourself
(it must have tests/fixtures/livepcb/livepcb.kicad_pcb open, unmodified).

Usage: .venv/bin/python tests/test_live_pcb_ipc.py   (~20 s)
"""

import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kicad_lib import live, live_headless, live_pcb, sexp  # noqa: E402

if live_pcb.KiCad is None:
    print("SKIP: kipy not importable — run via the repo .venv")
    raise SystemExit(0)

FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


FIXTURE = ROOT / "tests/fixtures/livepcb/livepcb.kicad_pcb"
own = None
sock = os.environ.get("KX_LIVE_TEST_SOCKET")
if not sock:
    gaps = live_headless.missing()
    if gaps:
        print("SKIP: headless KiCad unavailable — " + "; ".join(gaps))
        raise SystemExit(0)
    if live_headless.status():
        print("SKIP: a headless editor is already up (kx live headless "
              "stop) — not touching it")
        raise SystemExit(0)
    scratch = pathlib.Path(tempfile.mkdtemp(
        prefix="livepcb_", dir=pathlib.Path.home() / ".cache/kx_scratch"))
    board_path = scratch / "livepcb.kicad_pcb"
    shutil.copy(FIXTURE, board_path)
    own = live_headless.start(str(board_path))
    sock = own["socket"]

try:
    kicad, board = live_pcb.connect(sock)
    fps0 = live_pcb.footprints(board)

    # --- summary -------------------------------------------------------------
    s = live_pcb.summary(board)
    check("summary: 4 footprints", s["footprints"]["refs"]
          == ["C1", "J1", "R1", "R2"])
    check("summary: nets", s["nets"]["names"] == ["GND", "OUT", "VIN"])
    check("summary: netclass Default covers all nets",
          s["netclasses"]["Default"]["nets"] == ["GND", "OUT", "VIN"])
    check("summary: 2 copper layers, Edge.Cuts enabled",
          s["layers"]["copper"] == 2 and "Edge.Cuts" in s["layers"]["enabled"])
    check("summary: one zone", s["zones"]["count"] == 1)

    # --- move + readback, one undo step ---------------------------------------
    r = live_pcb.move(board, "R1", 112.5, 101.25, 90)
    check("move: no mismatch", r.get("mismatches") == [])
    now = live_pcb.footprints(board)["R1"]
    check("move: read back", (now["x"], now["y"], now["rot"])
          == (112.5, 101.25, 90.0))
    live_pcb.undo(kicad)
    check("move: ONE undo restores", live_pcb.footprints(board)["R1"]
          == fps0["R1"])

    # --- rotate / flip ------------------------------------------------------------
    r = live_pcb.rotate(board, "R2", -45)
    check("rotate: read back", r.get("mismatches") == []
          and live_pcb.footprints(board)["R2"]["rot"] == -45.0)
    r = live_pcb.flip(board, "C1")
    c1 = live_pcb.footprints(board)["C1"]
    check("flip: now bottom, position kept", r.get("mismatches") == []
          and c1["side"] == "bottom" and (c1["x"], c1["y"])
          == (fps0["C1"]["x"], fps0["C1"]["y"]))
    live_pcb.undo(kicad)
    live_pcb.undo(kicad)
    check("rotate+flip: two undos restore both",
          live_pcb.footprints(board) == fps0)

    # --- placement file: several refs + a flip = ONE undo step --------------------
    pf = pathlib.Path(tempfile.mkdtemp()) / "place.csv"
    pf.write_text("Ref,PosX,PosY,Rot,Side\nR2,117,103,45,bottom\n"
                  "J1,104,106,180,top\n")
    r = live_pcb.apply_placement(board, live_pcb.load_placement(str(pf)),
                                 "kx place test")
    check("place: 2 applied, 1 flipped, exact readback",
          r.get("applied") == 2 and r.get("flipped") == 1
          and r.get("mismatches") == [])
    check("place: moves are one step, the flip one more",
          r.get("undo_steps") == 2)
    live_pcb.undo(kicad, r["undo_steps"])
    check("place: undo_steps undos restore every footprint",
          live_pcb.footprints(board) == fps0)
    r = live_pcb.apply_placement(board, {"R1": {"x": 100, "y": 100},
                                         "R2": {"x": 101, "y": 100,
                                                "rot": 90},
                                         "J1": {"rot": 270}}, "kx place 3")
    check("place (same side): ONE undo step for 3 footprints",
          r.get("undo_steps") == 1 and r.get("mismatches") == [])
    live_pcb.undo(kicad)
    check("place (same side): ONE undo restores all 3",
          live_pcb.footprints(board) == fps0)

    # --- refusal sends nothing ----------------------------------------------------
    r = live_pcb.apply_placement(board, {"R1": {"x": 0, "y": 0},
                                         "NOPE": {"x": 0, "y": 0}}, "kx bad")
    check("refusal: unknown ref refuses the whole file",
          r.get("refused") and live_pcb.footprints(board) == fps0)

    # --- show: selection + zoom -------------------------------------------------
    r = live_pcb.show(kicad, board, ["R1", "C1"])
    check("show: editor selection is exactly the refs", r == {
        "selected": ["C1", "R1"], "ok": True})
    board.clear_selection()

    # --- refill ----------------------------------------------------------------------
    r = live_pcb.refill(board)
    check("refill: zone filled", r["ok"] and r["filled"] == 1)
    live_pcb.undo(kicad)
    z = board.get_zones()[0]
    print(f"     after one undo of the refill: filled={z.filled} "
          f"polys={len(z.filled_polygons)}")
    check("refill: ONE undo takes the fill back",
          not z.filled or not z.filled_polygons)
    live_pcb.refill(board)

    # --- file mode refuses while the GUI holds the board --------------------------
    bpath = pathlib.Path(board.document.project.path) / \
        board.document.board_filename
    check("lock file present while open", live.gui_lock(str(bpath)))
    try:
        sexp.save_file(str(bpath), sexp.load_file(str(bpath)))
        check("save_file refuses the open board", False)
    except live.HeldByGui as e:
        check("save_file refuses the open board", "kx live pcb" in str(e))

    # --- save over IPC lands on disk -----------------------------------------------
    if own:
        live_pcb.move(board, "R1", 111, 99)
        board.save()
        disk = sexp.load_file(str(bpath))
        at = None
        for fp in sexp.find_all(disk, "footprint"):
            refs = [p for p in sexp.find_all(fp, "property")
                    if sexp.atoms(p)[:2] == ["Reference", "R1"]]
            if refs:
                at = sexp.atoms(sexp.find(fp, "at"))[:2]
        check("save: moved R1 is on disk", at == ["111", "99"])
finally:
    if own:
        st = live_headless.stop()
        check("headless stopped, locks cleaned",
              st["stopped"] and not live.gui_lock(own["board"]))

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
