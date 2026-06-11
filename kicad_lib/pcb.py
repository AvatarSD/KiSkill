"""PCB footprint staging (kicad-schematic SKILL.md §10, as a library).

Goal: place footprints in a .kicad_pcb such that KiCad's Update-PCB-from-
Schematic ADOPTS them (no duplicates) and fills their nets:
the footprint's (path "/<uuid>") must equal the symbol's tstamps uuid
from the exported netlist. Pads stay netless — the sync fills them.
"""

from __future__ import annotations

import pathlib
import subprocess
import uuid as _uuid

from . import sexp
from .sexp import Sym, QStr
from .geom import Point

KICAD_CLI = ["flatpak", "run", "--command=kicad-cli", "org.kicad.KiCad"]
SCRATCH = pathlib.Path.home() / ".cache/kx_scratch"


def netlist(sch_path: str) -> dict:
    """Export + parse the netlist: ref → {tstamps, value, footprint}.
    The netlist is the connectivity ground truth (SKILL.md §7)."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    out = SCRATCH / "kx_netlist.net"
    r = subprocess.run(KICAD_CLI + ["sch", "export", "netlist", "--output",
                                    str(out), sch_path],
                       capture_output=True, text=True)
    if not out.exists():
        raise RuntimeError(f"netlist export failed: {r.stderr or r.stdout}")
    root = sexp.load_file(str(out))
    comps = {}
    for c in sexp.walk(root, "comp"):
        ref = sexp.atoms(sexp.find(c, "ref"))[0]
        comps[ref] = {
            "tstamps": sexp.atoms(t)[0] if (t := sexp.find(c, "tstamps")) else None,
            "value": sexp.atoms(v)[0] if (v := sexp.find(c, "value")) else "",
            "footprint": sexp.atoms(f)[0] if (f := sexp.find(c, "footprint")) else "",
        }
    nets = {}
    for n in sexp.walk(root, "net"):
        name = sexp.atoms(sexp.find(n, "name"))[0]
        nets[name] = [(sexp.atoms(sexp.find(nd, "ref"))[0],
                       sexp.atoms(sexp.find(nd, "pin"))[0])
                      for nd in sexp.find_all(n, "node")]
    return {"comps": comps, "nets": nets}


def load_footprint(mod_path: str, cache_name: str) -> list:
    """Load a .kicad_mod, rename to "Lib:Name", strip file headers."""
    d = sexp.load_file(mod_path)
    if sexp.tag_of(d) != "footprint":
        raise ValueError(f"{mod_path} is not a footprint")
    d = sexp.parse(sexp.dumps(d))  # private copy
    d[1] = QStr.of(cache_name)
    for tag in ("version", "generator", "generator_version"):
        n = sexp.find(d, tag)
        if n is not None:
            d.remove(n)
    return d


def _refresh_uuids(node: list) -> None:
    for u in sexp.walk(node, "uuid"):
        u[1] = QStr.of(str(_uuid.uuid4()))


def _set_prop(fp: list, name: str, value: str, at: Point) -> None:
    for p in sexp.find_all(fp, "property"):
        if sexp.atoms(p)[0] == name:
            p[2] = QStr.of(value)
            return
    fp.append([Sym("property"), QStr.of(name), QStr.of(value),
               [Sym("at"), Sym(f"{at[0]:g}"), Sym(f"{at[1]:g}"), Sym("0")],
               [Sym("layer"), QStr.of("F.SilkS")],
               [Sym("uuid"), QStr.of(str(_uuid.uuid4()))],
               [Sym("effects"),
                [Sym("font"), [Sym("size"), Sym("1"), Sym("1")],
                 [Sym("thickness"), Sym("0.15")]]]])


def stage(board: list, fpdef: list, ref: str, value: str, at: Point,
          tstamp_uuid: str, sheetfile: str) -> None:
    """Insert one footprint instance, path-linked to its symbol."""
    fp = sexp.parse(sexp.dumps(fpdef))
    _refresh_uuids(fp)
    layer_i = next(i for i, x in enumerate(fp)
                   if isinstance(x, list) and sexp.tag_of(x) == "layer")
    fp[layer_i + 1:layer_i + 1] = [
        [Sym("uuid"), QStr.of(str(_uuid.uuid4()))],
        [Sym("at"), Sym(f"{at[0]:g}"), Sym(f"{at[1]:g}")],
        [Sym("path"), QStr.of("/" + tstamp_uuid)],
        [Sym("sheetname"), QStr.of("/")],
        [Sym("sheetfile"), QStr.of(sheetfile)],
    ]
    _set_prop(fp, "Reference", ref, (at[0], at[1] - 3))
    _set_prop(fp, "Value", value, (at[0], at[1] + 3))
    board.append(fp)


def edge_cuts_bbox(board: list) -> tuple | None:
    """Extent of Edge.Cuts graphics — stage footprints clear of it."""
    xs, ys = [], []
    for g in board:
        if not isinstance(g, list) or sexp.tag_of(g) not in (
                "gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly"):
            continue
        lay = sexp.find(g, "layer")
        if lay is None or sexp.atoms(lay)[0] != "Edge.Cuts":
            continue
        for pt_tag in ("start", "end", "center", "mid"):
            for n in sexp.find_all(g, pt_tag):
                a = sexp.atoms(n)
                xs.append(float(a[0])); ys.append(float(a[1]))
        for pts in sexp.find_all(g, "pts"):
            for xy in sexp.find_all(pts, "xy"):
                a = sexp.atoms(xy)
                xs.append(float(a[0])); ys.append(float(a[1]))
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def empty_board_from(donor_pcb: str) -> list:
    """Minimal valid board: header/layer-stack/setup taken from a donor
    .kicad_pcb (read-only), body dropped. KiCad accepts the result."""
    src = sexp.load_file(donor_pcb)
    keep = ("version", "generator", "generator_version", "general",
            "paper", "layers", "setup")
    out = [src[0]] + [x for x in src
                      if isinstance(x, list) and sexp.tag_of(x) in keep]
    # nets: net 0 "" must exist
    out.append([Sym("net"), Sym("0"), QStr.of("")])
    return sexp.parse(sexp.dumps(out))  # deep copy, detach from donor
