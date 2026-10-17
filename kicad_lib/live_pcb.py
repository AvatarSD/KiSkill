"""Verified ops on the LIVE board in an open KiCad PCB editor (IPC, 9+).

Same contract as live_ops.py (the schematic side): every edit is ONE
commit = ONE undo step in the editor (live_ops.committed) — with one
KiCad-10 exception, side flips, which are their own step (see
apply_placement; the result says "undo_steps"). Every input is
validated before anything is sent (an unknown or locked reference refuses
the whole op, the editor sees nothing), and every edit is READ BACK after
the push and compared with what was asked. A mismatch is reported, not
hidden; `kx live pcb undo` takes the whole step back.

Units: mm and degrees, KiCad's own board frame (x right, y DOWN, the
numbers the editor's status bar shows). side is "top" or "bottom".

Pure helpers (record, plan_placement, verify_placement,
load_placement) import nothing from kipy and are unit-tested in
tests/test_live_pcb.py; the IPC-backed ops are exercised end to end by
tests/test_live_pcb_ipc.py against a headless editor (kx live headless).

kipy only imports under the repo .venv — `kx live pcb …` re-execs there.
"""

from __future__ import annotations

import csv
import json
import math
import pathlib
import sys

from . import live as live_env
from .live_ops import committed

try:
    from kipy import KiCad
    from kipy.board_types import BoardLayer
    from kipy.geometry import Angle, Vector2
    F_CU, B_CU = BoardLayer.BL_F_Cu, BoardLayer.BL_B_Cu
except ImportError:  # pragma: no cover - system python; kx re-execs venv
    KiCad = None
    F_CU, B_CU = 3, 34  # BoardLayer values in KiCad 9/10 protos

NM = 1_000_000          # nm per mm
POS_TOL_MM = 0.0005     # readback tolerance (IPC is integer nm)
ROT_TOL_DEG = 0.01
ZOOM_ACTION = "common.Control.zoomFitSelection"
UNDO_ACTION = "common.Interactive.undo"


def _mm(v: int) -> float:
    return round(v / NM, 6)


def _nm(mm: float) -> int:
    return round(mm * NM)


def norm_deg(d: float) -> float:
    """Angle in (-180, 180], rounded — KiCad's own normalisation."""
    d = math.fmod(float(d), 360.0)
    if d <= -180.0:
        d += 360.0
    elif d > 180.0:
        d -= 360.0
    return round(d, 6)


def side_of(layer: int) -> str:
    return "bottom" if layer == B_CU else "top"


# ------------------------------------------------------------ connection

def connect(socket: str | None = None):
    """(kicad, board) for the board open in the live PCB editor."""
    if KiCad is None:
        raise RuntimeError("kipy missing — run under the repo .venv "
                           "(pip install kicad-python==0.8.0 for KiCad 10)")
    sock = socket or live_env.ipc_socket()
    if not sock:
        info = live_env.ipc_socket_info()
        raise RuntimeError("no live KiCad IPC socket: " +
                           live_env.disc.why_not(info) +
                           " (remote GUI: kx live connect --host H)")
    k = KiCad(socket_path=f"ipc://{sock}", timeout_ms=20000)
    return k, k.get_board()


# ----------------------------------------------------------- pure model

def record(fp) -> dict:
    """Plain-dict view of one footprint (mm, degrees, side)."""
    return {
        "ref": fp.reference_field.text.value,
        "value": fp.value_field.text.value,
        "fpid": str(fp.definition.id) if hasattr(fp, "definition") else "",
        "x": _mm(fp.position.x),
        "y": _mm(fp.position.y),
        "rot": norm_deg(fp.orientation.degrees),
        "side": side_of(fp.layer),
        "locked": bool(fp.locked),
    }


def plan_placement(current: dict[str, dict], targets: dict[str, dict],
                   force: bool = False) -> tuple[list[dict], list[str]]:
    """Validate targets against the board BEFORE anything is sent.

    current: ref -> record(); targets: ref -> {x?, y?, rot?, side?}.
    Returns (changes, errors). Any error means the whole op is refused.
    changes: [{ref, flip, x, y, rot}] with x/y/rot None = keep."""
    errors, changes = [], []
    unknown = sorted(set(targets) - set(current))
    if unknown:
        errors.append(f"unknown references: {unknown}")
    for ref in sorted(set(targets) & set(current)):
        t, c = targets[ref], current[ref]
        if not isinstance(t, dict):
            errors.append(f"{ref}: target must be an object")
            continue
        extra = set(t) - {"x", "y", "rot", "side"}
        if extra:
            errors.append(f"{ref}: unknown keys {sorted(extra)}")
        if c["locked"] and not force:
            errors.append(f"{ref}: footprint is locked in the editor "
                          "(unlock it there, or --force)")
        side = t.get("side")
        if side is not None and side not in ("top", "bottom"):
            errors.append(f"{ref}: side must be top|bottom, not {side!r}")
        try:
            x = None if t.get("x") is None else float(t["x"])
            y = None if t.get("y") is None else float(t["y"])
            rot = None if t.get("rot") is None else norm_deg(t["rot"])
        except (TypeError, ValueError):
            errors.append(f"{ref}: x/y/rot must be numbers")
            continue
        if (x is None) != (y is None):
            errors.append(f"{ref}: give both x and y, or neither")
        changes.append({"ref": ref,
                        "flip": side is not None and side != c["side"],
                        "x": x, "y": y, "rot": rot})
    return changes, errors


def verify_placement(after: dict[str, dict], targets: dict[str, dict]
                     ) -> list[str]:
    """Readback check: every asked-for field equals what the editor now
    holds (within IPC rounding). [] = the change landed exactly."""
    bad = []
    for ref, t in sorted(targets.items()):
        a = after.get(ref)
        if a is None:
            bad.append(f"{ref}: gone from the board after the edit")
            continue
        for k in ("x", "y"):
            if t.get(k) is not None and abs(a[k] - float(t[k])) > POS_TOL_MM:
                bad.append(f"{ref}: {k} is {a[k]}, asked {t[k]}")
        if t.get("rot") is not None:
            d = abs(norm_deg(a["rot"] - norm_deg(t["rot"])))
            if d > ROT_TOL_DEG:
                bad.append(f"{ref}: rot is {a['rot']}, asked {t['rot']}")
        if t.get("side") is not None and a["side"] != t["side"]:
            bad.append(f"{ref}: side is {a['side']}, asked {t['side']}")
    return bad


_ALIASES = {
    "ref": ("ref", "reference", "designator", "refdes"),
    "x": ("x", "posx", "pos x", "mid x", "midx"),
    "y": ("y", "posy", "pos y", "mid y", "midy"),
    "rot": ("rot", "rotation", "angle", "orientation"),
    "side": ("side", "layer"),
}


def _side_word(v: str) -> str:
    v = v.strip().lower()
    if v in ("top", "t", "front", "f", "f.cu"):
        return "top"
    if v in ("bottom", "b", "back", "bot", "b.cu"):
        return "bottom"
    return v  # plan_placement rejects it with a clear message


def _num(v: str) -> float:
    return float(v.strip().lower().removesuffix("mm"))


def load_placement(path: str) -> dict[str, dict]:
    """Placement file -> {ref: {x, y, rot?, side?}}.

    JSON: {"R1": {"x": 10, "y": 20, "rot": 90, "side": "top"}, ...} or a
    list of objects with a "ref" key. CSV: a header row naming the columns
    (ref/designator, x/posx/mid x, y/posy/mid y, rot/rotation, side/layer;
    case-insensitive). Coordinates are the EDITOR frame (y down) — a
    kicad-cli .pos / JLC CPL export uses y UP from the aux origin, so do
    not feed one back without converting."""
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() == ".json" or text.lstrip().startswith(("{", "[")):
        data = json.loads(text)
        if isinstance(data, list):
            data = {d["ref"]: {k: v for k, v in d.items() if k != "ref"}
                    for d in data}
        return {str(k): v for k, v in data.items()}
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return {}
    head = [h.strip().lower() for h in rows[0]]
    col = {}
    for key, names in _ALIASES.items():
        for i, h in enumerate(head):
            if h in names:
                col[key] = i
                break
    if "ref" not in col:
        raise ValueError(f"{path}: no reference column in header {rows[0]}")
    out = {}
    for r in rows[1:]:
        if not r or not r[col["ref"]].strip():
            continue
        t = {}
        for key in ("x", "y", "rot"):
            if key in col and r[col[key]].strip():
                t[key] = _num(r[col[key]])
        if "side" in col and r[col["side"]].strip():
            t["side"] = _side_word(r[col["side"]])
        out[r[col["ref"]].strip()] = t
    return out


# ------------------------------------------------------------- live ops

def footprints(board) -> dict[str, dict]:
    return {r["ref"]: r for r in (record(f) for f in board.get_footprints())}


def _fp_objects(board) -> dict:
    return {f.reference_field.text.value: f for f in board.get_footprints()}


def summary(board) -> dict:
    """Read-only overview: footprints, nets, net classes, layers, copper."""
    fps = footprints(board)
    nets = [n for n in board.get_nets() if n.name]
    classes: dict[str, dict] = {}
    for net, nc in board.get_netclass_for_nets(nets).items():
        c = classes.setdefault(nc.name, {
            "clearance": _mm(nc.clearance) if nc.clearance else None,
            "track_width": _mm(nc.track_width) if nc.track_width else None,
            "via_diameter": _mm(nc.via_diameter) if nc.via_diameter
            else None,
            "via_drill": _mm(nc.via_drill) if nc.via_drill else None,
            "nets": []})
        c["nets"].append(net)
    for c in classes.values():
        c["nets"].sort()
    zones = board.get_zones()
    return {
        "board": board.name,
        "footprints": {"count": len(fps),
                       "top": sum(r["side"] == "top" for r in fps.values()),
                       "bottom": sum(r["side"] == "bottom"
                                     for r in fps.values()),
                       "refs": sorted(fps)},
        "nets": {"count": len(nets), "names": sorted(n.name for n in nets)},
        "netclasses": classes,
        "layers": {"copper": board.get_copper_layer_count(),
                   "enabled": [board.get_layer_name(lyr)
                               for lyr in board.get_enabled_layers()]},
        "tracks": len(board.get_tracks()),
        "vias": len(board.get_vias()),
        "zones": {"count": len(zones),
                  "filled": sum(bool(z.filled) for z in zones)},
    }


def apply_placement(board, targets: dict[str, dict], msg: str,
                    force: bool = False) -> dict:
    """Move/rotate/flip any number of footprints, then read every one
    back. Refusal (bad ref, locked, bad value) sends nothing:
    {"refused": [...]}.

    Undo steps (reported as "undo_steps"): all moves/rotations are ONE
    commit. Side changes are ONE more: on KiCad 10 flip_items commits on
    its own even inside an open commit, and setting the layer through
    update_items does not mirror the pads (measured: pads stay on F.Cu),
    so flip_items is the only correct flip. `kx live pcb undo N`."""
    changes, errors = plan_placement(footprints(board), targets, force)
    if errors:
        return {"refused": errors}
    steps = 0
    objs = _fp_objects(board)
    flips = [objs[c["ref"]] for c in changes if c["flip"]]
    if flips:
        board.flip_items(flips)  # its own undo step (see above)
        steps += 1
        objs = _fp_objects(board)  # flip changed layer + orientation
    moved = []
    for c in changes:
        if c["x"] is None and c["rot"] is None:
            continue
        fp = objs[c["ref"]]
        if c["x"] is not None:
            fp.position = Vector2.from_xy(_nm(c["x"]), _nm(c["y"]))
        if c["rot"] is not None:
            fp.orientation = Angle.from_degrees(c["rot"])
        moved.append(fp)
    if moved:
        with committed(board, msg):
            board.update_items(moved)
        steps += 1
    after = footprints(board)
    return {"applied": len(changes), "flipped": len(flips),
            "undo_steps": steps,
            "mismatches": verify_placement(after, targets),
            "after": {r: after[r] for r in sorted(targets) if r in after}}


def move(board, ref: str, x: float, y: float, rot: float | None = None,
         force: bool = False) -> dict:
    t = {"x": x, "y": y} | ({} if rot is None else {"rot": rot})
    return apply_placement(board, {ref: t}, f"kx move {ref}", force)


def rotate(board, ref: str, deg: float, force: bool = False) -> dict:
    return apply_placement(board, {ref: {"rot": deg}},
                           f"kx rotate {ref} {deg:g}", force)


def flip(board, ref: str, force: bool = False) -> dict:
    cur = footprints(board).get(ref)
    if cur is None:
        return {"refused": [f"unknown references: [{ref!r}]"]}
    other = "bottom" if cur["side"] == "top" else "top"
    return apply_placement(board, {ref: {"side": other}},
                           f"kx flip {ref}", force)


def show(kicad, board, refs: list[str]) -> dict:
    """Select refs in the editor and zoom to them, so the operator sees
    what the agent means. Selection is not an edit (no undo step)."""
    objs = _fp_objects(board)
    unknown = sorted(set(refs) - set(objs))
    if unknown:
        return {"refused": [f"unknown references: {unknown}"]}
    board.clear_selection()
    board.add_to_selection([objs[r] for r in refs])
    kicad.run_action(ZOOM_ACTION)
    sel = sorted(i.reference_field.text.value for i in board.get_selection()
                 if hasattr(i, "reference_field"))
    return {"selected": sel, "ok": sel == sorted(set(refs))}


def refill(board) -> dict:
    """Refill every zone (blocks until KiCad finishes), read back."""
    board.refill_zones(block=True, max_poll_seconds=120)
    zones = board.get_zones()
    return {"zones": len(zones),
            "filled": sum(bool(z.filled) for z in zones),
            "ok": all(z.filled for z in zones if not z.is_rule_area)}


def undo(kicad, steps: int = 1) -> dict:
    st = [str(kicad.run_action(UNDO_ACTION)).strip() for _ in range(steps)]
    return {"action": UNDO_ACTION, "steps": steps, "status": st}


# ------------------------------------------------------------------- CLI

USE = ("kx live pcb summary | fps [REF...] | move REF X Y [ROT] | "
       "rotate REF DEG | flip REF | place FILE | show REF... | refill | "
       "undo [N] | save   (--force: allow locked footprints)")


def main(argv: list[str]) -> int:
    force = "--force" in argv
    argv = [a for a in argv if a != "--force"]
    if not argv:
        print(USE)
        return 2
    cmd, args = argv[0], argv[1:]
    kicad, board = connect()
    if cmd == "summary":
        out = summary(board)
    elif cmd == "fps":
        fps = footprints(board)
        out = [fps[r] for r in (args or sorted(fps)) if r in fps]
    elif cmd == "move" and len(args) in (3, 4):
        out = move(board, args[0], float(args[1]), float(args[2]),
                   float(args[3]) if len(args) == 4 else None, force)
    elif cmd == "rotate" and len(args) == 2:
        out = rotate(board, args[0], float(args[1]), force)
    elif cmd == "flip" and len(args) == 1:
        out = flip(board, args[0], force)
    elif cmd == "place" and len(args) == 1:
        out = apply_placement(board, load_placement(args[0]),
                              f"kx place {pathlib.Path(args[0]).name}",
                              force)
    elif cmd == "show" and args:
        out = show(kicad, board, args)
    elif cmd == "refill" and not args:
        out = refill(board)  # KiCad commits the fill itself: one undo step
    elif cmd == "undo" and len(args) <= 1:
        out = undo(kicad, int(args[0]) if args else 1)
    elif cmd == "save" and not args:
        board.save()
        out = {"saved": board.name}
    else:
        print(USE)
        return 2
    json.dump(out, sys.stdout, indent=1)
    print()
    if isinstance(out, dict) and (out.get("refused") or out.get(
            "mismatches") or out.get("ok") is False):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
