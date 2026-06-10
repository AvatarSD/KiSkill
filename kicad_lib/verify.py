"""Geometric verifier — run before every schematic write (rule canon,
machine tier). Input is the generator's net model: segments, pins,
junctions. Output: list of violation strings; empty list = VERIFIED.

Rules (kicad-schematic SKILL.md §6):
 1. grid 1.27 mm, no diagonal segments
 2. no contact between segments of different nets
 3. same-net collinear double-draw / crossing without shared endpoint
 4. no pin of net A on a wire of net B
 5. every pin connected to its own net's copper
 6. junction required at ≥3-item points and endpoint/pin-on-interior;
    no stray junctions
GND*/VCC* stub nets are each merged into one net (power rails).
"""

from __future__ import annotations

from dataclasses import dataclass

from .geom import Point, on_grid, pt


@dataclass(frozen=True)
class Seg:
    net: str
    a: Point
    b: Point


@dataclass(frozen=True)
class Pin:
    net: str
    p: Point
    who: str = "?"  # "R12.2" for messages


def _norm(net: str, merge_power: bool) -> str:
    if merge_power:
        if net.upper().startswith("GND"):
            return "GND"
        if net.upper().startswith("VCC"):
            return "VCC"
    return net


def _horiz(s: Seg) -> bool:
    return s.a[1] == s.b[1]


def _contains(s: Seg, p: Point) -> bool:
    """p lies on s, endpoints inclusive (segments are axis-parallel)."""
    (x, y), (x1, y1), (x2, y2) = p, s.a, s.b
    if x1 == x2:
        return x == x1 and min(y1, y2) <= y <= max(y1, y2)
    return y == y1 and min(x1, x2) <= x <= max(x1, x2)


def _interior(s: Seg, p: Point) -> bool:
    return _contains(s, p) and p != s.a and p != s.b


def _contact(s: Seg, t: Seg) -> list[Point]:
    """All shared points of two axis-parallel segments (2 pts = overlap)."""
    if _horiz(s) == _horiz(t):  # parallel
        if _horiz(s) and s.a[1] == t.a[1]:
            lo = max(min(s.a[0], s.b[0]), min(t.a[0], t.b[0]))
            hi = min(max(s.a[0], s.b[0]), max(t.a[0], t.b[0]))
            y = s.a[1]
            if lo < hi:
                return [pt(lo, y), pt(hi, y)]
            if lo == hi:
                return [pt(lo, y)]
        if not _horiz(s) and s.a[0] == t.a[0]:
            lo = max(min(s.a[1], s.b[1]), min(t.a[1], t.b[1]))
            hi = min(max(s.a[1], s.b[1]), max(t.a[1], t.b[1]))
            x = s.a[0]
            if lo < hi:
                return [pt(x, lo), pt(x, hi)]
            if lo == hi:
                return [pt(x, lo)]
        return []
    h, v = (s, t) if _horiz(s) else (t, s)
    p = pt(v.a[0], h.a[1])
    return [p] if _contains(h, p) and _contains(v, p) else []


def verify(
    segs: list[Seg],
    pins: list[Pin],
    junctions: list[Point],
    merge_power: bool = True,
) -> list[str]:
    out: list[str] = []
    segs = [Seg(_norm(s.net, merge_power), pt(*s.a), pt(*s.b)) for s in segs]
    pins = [Pin(_norm(p.net, merge_power), pt(*p.p), p.who) for p in pins]
    junctions = [pt(*j) for j in junctions]

    # rule 1: grid + orthogonal + zero length
    for s in segs:
        if s.a == s.b:
            out.append(f"zero-length segment at {s.a} [{s.net}]")
        if not (_horiz(s) or s.a[0] == s.b[0]):
            out.append(f"diagonal segment {s.a}-{s.b} [{s.net}]")
        for c in (*s.a, *s.b):
            if not on_grid(c):
                out.append(f"off-grid segment {s.a}-{s.b} [{s.net}]")
                break
    for p in pins:
        if not (on_grid(p.p[0]) and on_grid(p.p[1])):
            out.append(f"off-grid pin {p.who} at {p.p}")

    # rules 2+3: pairwise segment contact
    for i, s in enumerate(segs):
        for t in segs[i + 1 :]:
            shared = _contact(s, t)
            if not shared:
                continue
            if s.net != t.net:
                out.append(
                    f"cross-net contact [{s.net}]x[{t.net}] at {shared[0]}"
                )
            elif len(shared) == 2:
                out.append(f"same-net double-draw [{s.net}] {shared}")
            elif _interior(s, shared[0]) and _interior(t, shared[0]):
                out.append(
                    f"same-net crossing w/o shared endpoint [{s.net}] at {shared[0]}"
                )

    # rules 4+5: pins vs copper
    for p in pins:
        own, foreign = False, None
        for s in segs:
            if _contains(s, p.p):
                if s.net == p.net:
                    own = True
                else:
                    foreign = s.net
        if foreign:
            out.append(f"pin {p.who} [{p.net}] touches wire of [{foreign}]")
        if not own and not any(
            q.p == p.p and q.net == p.net for q in pins if q is not p
        ):
            out.append(f"pin {p.who} [{p.net}] not on its net's copper")

    # rule 6: junctions
    need: set[Point] = set()
    points: set[Point] = {s.a for s in segs} | {s.b for s in segs} | {
        p.p for p in pins
    }
    for q in points:
        by_net: dict[str, int] = {}
        interior_hit: dict[str, bool] = {}
        for s in segs:
            if q in (s.a, s.b):
                by_net[s.net] = by_net.get(s.net, 0) + 1
            elif _interior(s, q):
                by_net[s.net] = by_net.get(s.net, 0) + 1
                interior_hit[s.net] = True
        # coincident pins (multi-unit stacks like MCP6002 V-) are ONE item:
        # eeschema connects pin-on-pin without a junction
        for net in {p.net for p in pins if p.p == q}:
            by_net[net] = by_net.get(net, 0) + 1
        for net, n in by_net.items():
            if n >= 2 and interior_hit.get(net):
                need.add(q)
            elif n >= 3:
                need.add(q)
    for q in sorted(need):
        if q not in junctions:
            out.append(f"missing junction at {q}")
    for j in junctions:
        if j not in need:
            out.append(f"stray junction at {j}")

    return out
