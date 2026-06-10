"""Builders for KiCad 20260306 schematic nodes (battle-tested shapes,
cribbed from a real eeschema 10.0 file — see tests/fixtures).

All coordinates are world mm on the 1.27 grid. Property text angle is
RELATIVE to symbol rotation: builders pass rot=90 for rot-90/270 symbols
so text renders horizontal (kicad-schematic SKILL.md §3).
"""

from __future__ import annotations

import uuid as _uuid

from .sexp import Sym, QStr
from .geom import Point


def f(v: float) -> str:
    return f"{v:g}"


def uid() -> list:
    return [Sym("uuid"), QStr.of(str(_uuid.uuid4()))]


def prop(name: str, val: str, x: float, y: float,
         hide: bool = False, rot: int = 0) -> list:
    n = [Sym("property"), QStr.of(name), QStr.of(val),
         [Sym("at"), Sym(f(x)), Sym(f(y)), Sym(str(rot))]]
    if hide:
        n.append([Sym("hide"), Sym("yes")])
    n += [[Sym("show_name"), Sym("no")], [Sym("do_not_autoplace"), Sym("no")],
          [Sym("effects"), [Sym("font"), [Sym("size"), Sym("1.27"), Sym("1.27")]]]]
    return n


def symbol(project: str, root_uuid: str, lib_id: str, at: Point, rot: int,
           ref: str, value: str, pin_nums: list[str], unit: int = 1,
           ref_hidden: bool = False, val_at: Point | None = None) -> list:
    x, y = at
    prot = 90 if rot in (90, 270) else 0
    vx, vy = val_at if val_at else (x, y + 5.08)
    n = [Sym("symbol"),
         [Sym("lib_id"), QStr.of(lib_id)],
         [Sym("at"), Sym(f(x)), Sym(f(y)), Sym(str(rot))],
         [Sym("unit"), Sym(str(unit))],
         [Sym("body_style"), Sym("1")],
         [Sym("exclude_from_sim"), Sym("no")],
         [Sym("in_bom"), Sym("yes")],
         [Sym("on_board"), Sym("yes")],
         [Sym("in_pos_files"), Sym("yes")],
         [Sym("dnp"), Sym("no")],
         uid(),
         prop("Reference", ref, x, y - 5.08, hide=ref_hidden, rot=prot),
         prop("Value", value, vx, vy, rot=prot),
         prop("Footprint", "", x, y, hide=True),
         prop("Datasheet", "", x, y, hide=True)]
    for p in pin_nums:
        n.append([Sym("pin"), QStr.of(p), uid()])
    n.append([Sym("instances"),
              [Sym("project"), QStr.of(project),
               [Sym("path"), QStr.of("/" + root_uuid),
                [Sym("reference"), QStr.of(ref)],
                [Sym("unit"), Sym(str(unit))]]]])
    return n


def wire(a: Point, b: Point) -> list:
    return [Sym("wire"),
            [Sym("pts"),
             [Sym("xy"), Sym(f(a[0])), Sym(f(a[1]))],
             [Sym("xy"), Sym(f(b[0])), Sym(f(b[1]))]],
            [Sym("stroke"), [Sym("width"), Sym("0")],
             [Sym("type"), Sym("default")]],
            uid()]


def junction(p: Point) -> list:
    return [Sym("junction"),
            [Sym("at"), Sym(f(p[0])), Sym(f(p[1]))],
            [Sym("diameter"), Sym("0")],
            [Sym("color"), Sym("0"), Sym("0"), Sym("0"), Sym("0")],
            uid()]


def label(text: str, p: Point) -> list:
    return [Sym("label"), QStr.of(text),
            [Sym("at"), Sym(f(p[0])), Sym(f(p[1])), Sym("0")],
            [Sym("effects"),
             [Sym("font"), [Sym("size"), Sym("1.27"), Sym("1.27")]],
             [Sym("justify"), Sym("left"), Sym("bottom")]],
            uid()]


def empty_sheet(root_uuid: str, paper: str = "A4") -> list:
    return [Sym("kicad_sch"),
            [Sym("version"), Sym("20260306")],
            [Sym("generator"), QStr.of("eeschema")],
            [Sym("generator_version"), QStr.of("10.0")],
            [Sym("uuid"), QStr.of(root_uuid)],
            [Sym("paper"), QStr.of(paper)],
            [Sym("lib_symbols")],
            [Sym("sheet_instances"),
             [Sym("path"), QStr.of("/"), [Sym("page"), QStr.of("1")]]],
            [Sym("embedded_fonts"), Sym("no")]]
