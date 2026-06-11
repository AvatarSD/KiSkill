"""Autoroute chain test: build a routable 2-footprint board from scratch
(outline + netted pads), DSN-export via flatpak pcbnew, route with the
bundled freerouting, SES-import back, count copper. SLOW (~1-2 min).

Usage: python3 tests/test_layout.py
"""

import os
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import layout, pcb, sexp  # noqa: E402
from kicad_lib.sexp import Sym, QStr  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
DONOR = os.environ.get("KX_DONOR_PCB", "")
if not DONOR or not pathlib.Path(DONOR).exists():
    print("SKIP: donor board missing — set KX_DONOR_PCB to any .kicad_pcb")
    raise SystemExit(0)
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def gr_line(a, b):
    return [Sym("gr_line"),
            [Sym("start"), Sym(f"{a[0]:g}"), Sym(f"{a[1]:g}")],
            [Sym("end"), Sym(f"{b[0]:g}"), Sym(f"{b[1]:g}")],
            [Sym("stroke"), [Sym("width"), Sym("0.1")],
             [Sym("type"), Sym("solid")]],
            [Sym("layer"), QStr.of("Edge.Cuts")],
            [Sym("uuid"), QStr.of(str(uuid.uuid4()))]]


def net_pad(fp, pad_num, net_id, net_name):
    for p in sexp.find_all(fp, "pad"):
        if sexp.atoms(p)[0] == pad_num:
            p.append([Sym("net"), Sym(str(net_id)), QStr.of(net_name)])
            return True
    return False


# --- build a routable board -------------------------------------------------
board = pcb.empty_board_from(DONOR)
board.append([Sym("net"), Sym("1"), QStr.of("N1")])
board.append([Sym("net"), Sym("2"), QStr.of("N2")])
for seg in (((80, 80), (160, 80)), ((160, 80), (160, 120)),
            ((160, 120), (80, 120)), ((80, 120), (80, 80))):
    board.append(gr_line(*seg))

fpdef = pcb.load_footprint(str(HERE / "fixtures/WAGO_234-212.kicad_mod"),
                           "WAGO_hat:WAGO_234-212")
pcb.stage(board, fpdef, "J1", "W", (95.25, 95.25), "t" * 8 + "-1", "x.kicad_sch")
pcb.stage(board, fpdef, "J2", "W", (138.43, 95.25), "t" * 8 + "-2", "x.kicad_sch")
fps = list(sexp.find_all(board, "footprint"))
check("two staged", len(fps) == 2)
check("pads netted", net_pad(fps[0], "1", 1, "N1")
      and net_pad(fps[1], "1", 1, "N1")
      and net_pad(fps[0], "2", 2, "N2") and net_pad(fps[1], "2", 2, "N2"))

src = layout.SCRATCH / "route_in.kicad_pcb"
sexp.save_file(str(src), board)

# --- the chain ---------------------------------------------------------------
res = layout.autoroute(str(src), str(layout.SCRATCH / "route_out.kicad_pcb"),
                       passes=8)
check("dsn produced", pathlib.Path(res["dsn"]).exists())
check("ses produced", pathlib.Path(res["ses"]).exists())
check("routed copper exists", res["segments"] >= 2)
print(f"     segments={res['segments']} vias={res['vias']}")

# routed board still parses + segments carry our nets. NOTE: pcb format
# 20260206 (KiCad 10) keys nets by NAME only — (net "N1"), no numeric id;
# pcbnew normalizes old-style (net 1 "N1") on import.
routed = sexp.load_file(res["pcb"])
seg_nets = {sexp.atoms(sexp.find(s, "net"))[-1]
            for s in sexp.find_all(routed, "segment") if sexp.find(s, "net")}
check("segments carry our nets", {"N1", "N2"} <= seg_nets)

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
