"""Verified atomic ops on the LIVE schematic (KiCad v11 nightly IPC).

Same contract as ops.py, different backend: each op builds items client-
side, geometric-verifies them against the live wire model, and only then
pushes a kipy commit (= ONE undo step in the GUI). Violations -> nothing
is sent at all; the GUI never sees an invalid intermediate state.

Net-blind: the live backend sees wires, not nets, so all copper is one
synthetic net. Checked here: grid 1.27 mm, diagonals, zero-length,
collinear overlap, junction-needed at T-points. NOT checked live
(file-backend rules; X-crossings and stray junctions are legal/unknowable
without nets+pins): cross-net contact, pin claims. Run kx check / kx diff
after the user saves.

kipy only imports under the repo .venv — `kx live …` re-execs itself
there. Coordinates in mm (IPC speaks nanometers).
"""

from __future__ import annotations

import json
import sys

from . import live as live_env
from .geom import on_grid, rnd
from .verify import Seg, verify

try:
    import kipy.schematic_types as st
    from kipy import KiCad
    from kipy.geometry import Vector2
    from kipy.proto.common.types import KiCadObjectType
except ImportError:  # pragma: no cover - system python; kx re-execs venv
    KiCad = None

NM = 1_000_000  # nm per mm
WIRE = 1        # SchematicLineType: 1 = wire (0 = graphic/notes)

# rules that need nets/pins the live backend doesn't have
_NET_BLIND_DROP = ("stray junction", "crossing w/o shared endpoint")


def _mm(v: int) -> float:
    return rnd(v / NM)


def _nm(mm: float) -> int:
    return round(mm * NM)


def connect(socket: str | None = None):
    if KiCad is None:
        raise RuntimeError("kipy missing — run under the repo .venv "
                           "(tools/bootstrap_kipy.sh)")
    sock = socket or live_env.ipc_socket()
    if not sock:
        raise RuntimeError("no KiCad IPC socket — launch nightly eeschema")
    return KiCad(socket_path=f"ipc://{sock}").get_schematic()


def wire_segs(sch) -> list[Seg]:
    """Live wires as one synthetic net (net-blind model)."""
    return [Seg("live", (_mm(ln.start.x), _mm(ln.start.y)),
                        (_mm(ln.end.x), _mm(ln.end.y)))
            for ln in sch.get_lines() if ln.type == WIRE]


def junction_pts(sch) -> list[tuple[float, float]]:
    return [(_mm(j.position.x), _mm(j.position.y))
            for j in sch.get_items(KiCadObjectType.KOT_SCH_JUNCTION)]


def _gate(sch, new_segs: list[Seg], new_junctions: list[tuple]) -> list[str]:
    segs = wire_segs(sch) + new_segs
    junctions = junction_pts(sch) + list(new_junctions)
    return [v for v in verify(segs, [], junctions)
            if not any(d in v for d in _NET_BLIND_DROP)]


def _push(sch, items, msg: str) -> list[str]:
    commit = sch.begin_commit()
    try:
        sch.create_items(items)
        sch.push_commit(commit, msg)
    except Exception:
        sch.drop_commit(commit)
        raise
    return []


def add_wire(sch, x1: float, y1: float, x2: float, y2: float) -> list[str]:
    """Verified wire; returns [] on push, violation strings on refusal."""
    seg = Seg("live", (rnd(x1), rnd(y1)), (rnd(x2), rnd(y2)))
    bad = _gate(sch, [seg], [])
    if bad:
        return bad
    ln = st.SchematicLine()
    ln.type = WIRE
    ln.start = Vector2.from_xy(_nm(x1), _nm(y1))
    ln.end = Vector2.from_xy(_nm(x2), _nm(y2))
    return _push(sch, ln, f"kx wire {x1},{y1} {x2},{y2}")


def add_junction(sch, x: float, y: float) -> list[str]:
    if not (on_grid(rnd(x)) and on_grid(rnd(y))):
        return [f"off-grid junction at ({x}, {y})"]
    j = st.Junction()
    j.position = Vector2.from_xy(_nm(x), _nm(y))
    return _push(sch, j, f"kx junction {x},{y}")


def add_label(sch, x: float, y: float, text: str) -> list[str]:
    from kipy.common_types import Text
    lb = st.LocalLabel()
    t = Text()
    t.value = text
    lb.text = t
    lb.position = Vector2.from_xy(_nm(x), _nm(y))
    return _push(sch, lb, f"kx label {text}")


def add_text(sch, x: float, y: float, text: str) -> list[str]:
    t = st.SchematicText()
    t.value = text
    t.position = Vector2.from_xy(_nm(x), _nm(y))
    return _push(sch, t, f"kx text {text}")


def remove_ids(sch, ids: list[str]) -> int:
    """Remove live items by KIID string; returns count requested."""
    by_id = {str(it.id.value): it
             for get in (sch.get_lines, sch.get_labels, sch.get_text,
                         sch.get_symbols)
             for it in get()}
    items = [by_id[i] for i in ids if i in by_id]
    if len(items) != len(ids):
        missing = sorted(set(ids) - set(by_id))
        raise KeyError(f"unknown live ids: {missing}")
    sch.remove_items(items)
    return len(items)


def snapshot(sch) -> dict:
    """Read-only live inventory (mm), for agents and `kx live snap`."""
    return {
        "name": sch.name,
        "symbols": sorted(s.reference_field.text.value
                          for s in sch.get_symbols()),
        "wires": [{"id": str(ln.id.value),
                   "a": [_mm(ln.start.x), _mm(ln.start.y)],
                   "b": [_mm(ln.end.x), _mm(ln.end.y)]}
                  for ln in sch.get_lines() if ln.type == WIRE],
        "labels": [{"id": str(lb.id.value), "text": str(lb.text.value),
                    "at": [_mm(lb.position.x), _mm(lb.position.y)]}
                   for lb in sch.get_labels()],
        "junctions": junction_pts(sch),
        "violations": _gate(sch, [], []),
    }


def main(argv: list[str]) -> int:
    use = ("kx live snap | refs | check | wire X1 Y1 X2 Y2 | "
           "junction X Y | label X Y TEXT | text X Y TEXT | rm ID...")
    if not argv:
        print(use)
        return 2
    sch = connect()
    cmd, args = argv[0], argv[1:]
    if cmd == "snap":
        json.dump(snapshot(sch), sys.stdout, indent=1)
    elif cmd == "refs":
        json.dump(sorted(s.reference_field.text.value
                         for s in sch.get_symbols()), sys.stdout)
    elif cmd == "check":
        bad = _gate(sch, [], [])
        json.dump(bad, sys.stdout, indent=1)
        return 1 if bad else 0
    elif cmd == "wire" and len(args) == 4:
        bad = add_wire(sch, *(float(a) for a in args))
    elif cmd == "junction" and len(args) == 2:
        bad = add_junction(sch, *(float(a) for a in args))
    elif cmd == "label" and len(args) == 3:
        bad = add_label(sch, float(args[0]), float(args[1]), args[2])
    elif cmd == "text" and len(args) == 3:
        bad = add_text(sch, float(args[0]), float(args[1]), args[2])
    elif cmd == "rm" and args:
        print(remove_ids(sch, args), "removed")
        return 0
    else:
        print(use)
        return 2
    if cmd in ("wire", "junction", "label", "text"):
        if bad:
            json.dump({"refused": bad}, sys.stdout, indent=1)
            return 1
        print("pushed (one GUI undo step)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
