"""Schematic geometry: 1.27 mm grid, rotation/mirror, pin world positions.

World coords: mm, +Y down. Library pin coords: +Y up. Everything (symbol
origins AND wire endpoints) must sit on the 1.27 mm grid or wires will
not connect. Float hygiene: round(_, 3) everywhere — 160.02 + 7.62 is
167.64000000000001 and will fail equality against 167.64.
"""

from __future__ import annotations

GRID = 1.27

Point = tuple[float, float]


def rnd(v: float) -> float:
    return round(v, 3)


def pt(x: float, y: float) -> Point:
    return (rnd(x), rnd(y))


def snap(v: float) -> float:
    """Snap a coordinate to the 1.27 mm grid."""
    return rnd(round(v / GRID) * GRID)


def on_grid(v: float, eps: float = 1e-6) -> bool:
    return abs(v / GRID - round(v / GRID)) < eps


def rot_xy(u: float, v: float, rot: int, mirror: str = "") -> Point:
    """Library pin offset (u, v; +Y up) → world delta (+Y down) for a
    symbol at rotation `rot` (use mirror only with rot 0 — unambiguous)."""
    if mirror == "x":
        v = -v
    if rot == 0:
        return pt(u, -v)
    if rot == 90:
        return pt(-v, -u)
    if rot == 180:
        return pt(-u, v)
    if rot == 270:
        return pt(v, u)
    raise ValueError(f"rotation must be 0/90/180/270, got {rot}")


def pin_world(sym_at: Point, rot: int, pin_uv: Point, mirror: str = "") -> Point:
    """World position of a library pin for a symbol instance."""
    dx, dy = rot_xy(pin_uv[0], pin_uv[1], rot, mirror)
    return pt(sym_at[0] + dx, sym_at[1] + dy)
