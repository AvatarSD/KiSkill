"""Vendor every symbol, footprint and 3D model a project uses into the
project itself, so it opens identically on any machine (no global
library, no ${KICADn_3DMODEL_DIR}).

    kx vendor PROJECT_DIR [--check] [--config FILE.py|FILE.json]

  --check   change nothing; exit 1 if anything would change
  --config  project policy: a .json of Config fields, or a .py defining
            configure(cfg) (the hook for project-specific model builders)
            PROJECT_DIR/kx-vendor.json is read when present.

Layout (under PROJECT_DIR/<libs>, default "libs"):
  <Lib>.kicad_sym, <Lib>.pretty/      the project's own libraries (sources;
                                      footprints rewritten in place)
  kicad/<Lib>.kicad_sym, .pretty/     copies of the official libraries,
                                      ONLY the symbols/footprints used
  3d/<Lib>.3dshapes/                  every 3D model: official, fetched,
                                      generated (3d/generated.json)

Lib ids never change: the project sym-lib-table / fp-lib-table get one row
per official library under the SAME nickname ("Device", ...). KiCad reads
the project table before the global one, so the project row wins. Rows
are tagged with Config.descr; rows without that tag are never touched.
The copies live in <libs>/kicad/, not <libs>/ itself, so a generator that
reads <libs>/<Lib>.kicad_sym before the official library is not handed a
subset.

Silkscreen (Config.strip_silk): every silk item goes; Reference moves to
Fab (hidden if the footprint already has a ${REFERENCE} on Fab); one
generated polarity mark where the policy wants one — "bar" (cathode),
"plus" (polarised capacitor) or "dot" (pin 1). The transformation is
idempotent: the mark is regenerated, never accumulated.

3D models: every model path becomes ${KIPRJMOD}/<libs>/3d/... and the file
is copied there. A missing model (and every project-library footprint
without one) is GENERATED from the footprint: a placeholder box, or a
project builder (Config.special + Config.builders). STEP when a Python
with OCP (cadquery-ocp) is found, VRML otherwise. generated.json keeps
each model's input hash: a model is rebuilt only when its input changes.
Config.no_model lists hole-only footprints that get no model.

The board's placed footprints keep their own copies until Tools > Update
Footprints from Library. Stdlib only (OCP only in the model subprocess).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import uuid as _uuid
from typing import Callable

from . import kcli, sexp as sx

ROOT = pathlib.Path(__file__).resolve().parents[1]
SILK = ("F.SilkS", "B.SilkS")

# KiCad-like material colours (builders may use them)
BLACK, GOLD, GREEN = (0.1, 0.1, 0.1), (0.85, 0.65, 0.2), (0.1, 0.35, 0.15)
WHITE, GREY = (0.92, 0.92, 0.9), (0.6, 0.62, 0.65)


@dataclasses.dataclass
class Config:
    proj: pathlib.Path
    libs: str = "libs"                 # relative to proj
    kicad_sub: str = "kicad"
    d3_sub: str = "3d"
    # the project's own libraries (in <libs>/); None = every <libs>/*.pretty
    # and <libs>/*.kicad_sym stem
    project_libs: tuple | None = None
    descr: str = "vendored KiCad library (kx vendor)"
    uid_ns: str = "kx-vendor"          # uuid5 seed for generated items
    sym_dir: pathlib.Path | None = None    # official libraries; None = resolve
    fp_dir: pathlib.Path | None = None
    model_dir: pathlib.Path | None = None
    # silkscreen policy
    strip_silk: bool = True
    mark_width: float = 0.15           # JLC minimum ~0.153
    dot_r: float = 0.125
    clearance: float = 0.15            # silk to pad edge
    no_mark_libs: frozenset = frozenset({
        "Resistor_SMD", "Resistor_THT", "Inductor_SMD", "Fuse", "TestPoint",
        "Jumper", "MountingHole", "Connector_Coaxial", "Connector_USB"})
    bar_libs: frozenset = frozenset({"Diode_SMD", "Diode_THT", "LED_SMD",
                                     "LED_THT"})
    plus_libs: frozenset = frozenset({"Capacitor_SMD", "Capacitor_THT"})
    plus_prefix: str = "CP_"
    # (lib, name, pads) -> "bar"|"plus"|"dot"|None, replaces the rule above
    mark_kind: Callable | None = None
    # 3D models
    no_model: frozenset = frozenset()  # "lib:name" with no body (holes)
    special: dict = dataclasses.field(default_factory=dict)  # lid -> kind
    # kind -> builder(fab_bbox, pads, name) -> {"boxes": [...], "shapes": [...]}
    builders: dict = dataclasses.field(default_factory=dict)
    heights: list = dataclasses.field(default_factory=list)  # (fragment, mm)
    default_height: float = 1.0
    ocp_python: list = dataclasses.field(default_factory=list)
    upgrade_legacy: bool = True
    # generated model format, set by run(): ".step" with OCP, else ".wrl"
    model_ext: str = ".step"

    @property
    def libs_dir(self) -> pathlib.Path:
        return self.proj / self.libs

    @property
    def kdir(self) -> pathlib.Path:
        return self.libs_dir / self.kicad_sub

    @property
    def d3(self) -> pathlib.Path:
        return self.libs_dir / self.d3_sub

    @property
    def gen_json(self) -> pathlib.Path:
        return self.d3 / "generated.json"

    def own_libs(self) -> tuple:
        if self.project_libs is not None:
            return tuple(self.project_libs)
        d = self.libs_dir
        found = {p.stem for p in d.glob("*.pretty") if p.is_dir()}
        found |= {p.stem for p in d.glob("*.kicad_sym")}
        return tuple(sorted(found))


SET_FIELDS = ("no_mark_libs", "bar_libs", "plus_libs", "no_model")


def load_config(proj, path: str | None = None) -> Config:
    """Config for proj: defaults, then proj/kx-vendor.json, then path
    (.json fields or a .py with configure(cfg))."""
    cfg = Config(pathlib.Path(proj).resolve())
    auto = cfg.proj / "kx-vendor.json"
    for p in [auto] * auto.exists() + ([pathlib.Path(path)] if path else []):
        if p.suffix == ".py":
            import importlib.util
            spec = importlib.util.spec_from_file_location("kx_vendor_cfg", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.configure(cfg)
            continue
        for k, v in json.loads(p.read_text()).items():
            if k not in {f.name for f in dataclasses.fields(Config)} or k == "proj":
                raise ValueError(f"{p}: unknown vendor config key {k!r}")
            if k in SET_FIELDS:
                v = frozenset(v)
            elif k in ("sym_dir", "fp_dir", "model_dir"):
                v = pathlib.Path(v).expanduser()
            elif k == "project_libs":
                v = tuple(v)
            setattr(cfg, k, v)
    return cfg


def _uid(cfg: Config, *parts: str) -> str:
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, cfg.uid_ns + ":" + ":".join(parts)))


def _num(v: float) -> str:
    s = f"{round(v, 4):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# ------------------------------------------------------- official libraries

_FLATPAK_RT = pathlib.Path.home() / ".local/share/flatpak/runtime"
_LIB_KIND = {  # kind -> (env suffix, flatpak runtime, files subdir, native)
    "sym": ("SYMBOL_DIR", "Symbols", "symbols", "symbols"),
    "fp": ("FOOTPRINT_DIR", "Footprints", "footprints", "footprints"),
    "3d": ("3DMODEL_DIR", "Packages3D", "3dmodels", "3dmodels"),
}


def library_dir(kind: str) -> pathlib.Path | None:
    """Official library dir: $KICAD10_/KICAD9_/KICAD_<X>_DIR, the native
    install, then the flatpak runtime (beta before stable: the beta
    branch is the newer KiCad when both are installed)."""
    env, rt, sub, native = _LIB_KIND[kind]
    for v in ("KICAD10_", "KICAD9_", "KICAD_"):
        if os.environ.get(v + env):
            return pathlib.Path(os.environ[v + env])
    cands = [pathlib.Path("/usr/share/kicad") / native]
    flat = [_FLATPAK_RT / f"org.kicad.KiCad.Library.{rt}/x86_64/{b}/active/files/{sub}"
            for b in ("beta", "stable")]
    for c in (cands + flat if shutil.which("kicad-cli") else flat + cands):
        if c.is_dir():
            return c
    return None


# ------------------------------------------------------------ what is used

def used_ids(cfg: Config) -> tuple[dict, dict]:
    """{lib: {symbol names}}, {lib: {footprint names}} from every sheet
    and board of the project."""
    syms: dict[str, set] = {}
    fps: dict[str, set] = {}

    def add(d, lid):
        if ":" in lid:
            lib, name = lid.split(":", 1)
            d.setdefault(lib, set()).add(name)

    for f in sorted(cfg.proj.glob("*.kicad_sch")):
        t = f.read_text()
        for m in re.finditer(r'\(symbol\s*\n?\s*\(lib_id "([^"]+)"', t):
            add(syms, m.group(1))
        for m in re.finditer(r'\(property "Footprint" "([^"]+)"', t):
            add(fps, m.group(1))
    for pcb in sorted(cfg.proj.glob("*.kicad_pcb")):
        for m in re.finditer(r'^\t\(footprint "([^"]+)"', pcb.read_text(), re.M):
            add(fps, m.group(1))
    return syms, fps


# ------------------------------------------------------------------ symbols

def vendor_symbols(cfg: Config, lib: str, names: set, out: dict) -> list[str]:
    src = cfg.sym_dir / f"{lib}.kicad_sym"
    if not src.exists():
        return [f"symbol library {lib!r} not found in {cfg.sym_dir}"]
    doc = sx.load_file(str(src))
    by_name = {sx.atoms(s)[0]: s for s in sx.find_all(doc, "symbol")}
    want, todo, errs = set(), list(names), []
    while todo:
        n = todo.pop()
        if n in want:
            continue
        if n not in by_name:
            errs.append(f"{lib}:{n} not in {src.name}")
            continue
        want.add(n)
        ext = sx.find(by_name[n], "extends")
        if ext:
            todo.append(sx.atoms(ext)[0])
    root = [x for x in doc if not (sx.tag_of(x) == "symbol")]
    root += [s for s in sx.find_all(doc, "symbol") if sx.atoms(s)[0] in want]
    out[cfg.kdir / f"{lib}.kicad_sym"] = sx.dumps(root, dialect="sch")
    return errs


# --------------------------------------------------------------- footprints

def _layer(node) -> str | None:
    lay = sx.find(node, "layer")
    return sx.atoms(lay)[0] if lay else None


def pads_of(fp) -> list[dict]:
    """Pads as {name, type, x, y, w, h}: the bbox of each pad (custom
    primitives included) after its rotation, footprint coordinates."""
    pads = []
    for p in sx.find_all(fp, "pad"):
        a = sx.atoms(p)
        at = [float(v) for v in sx.atoms(sx.find(p, "at"))]
        sz = sx.find(p, "size")
        w, h = ([float(v) for v in sx.atoms(sz)] if sz else (0.0, 0.0))
        pts = [(sx_ * w / 2, sy_ * h / 2) for sx_ in (-1, 1) for sy_ in (-1, 1)]
        prim = sx.find(p, "primitives")
        for g in (prim[1:] if prim else []):
            if not isinstance(g, list):
                continue
            wd = sx.find(g, "width")
            hw = float(sx.atoms(wd)[0]) / 2 if wd else 0.0
            gp = []
            for k in ("start", "mid", "end"):
                n = sx.find(g, k)
                if n:
                    gp.append(tuple(float(v) for v in sx.atoms(n)[:2]))
            c = sx.find(g, "center")
            if c:
                cc = [float(v) for v in sx.atoms(c)[:2]]
                e = gp[-1] if gp else cc
                rr = math.hypot(e[0] - cc[0], e[1] - cc[1])
                gp += [(cc[0] - rr, cc[1] - rr), (cc[0] + rr, cc[1] + rr)]
            pl = sx.find(g, "pts")
            if pl:
                gp += [tuple(float(v) for v in sx.atoms(xy)[:2])
                       for xy in sx.find_all(pl, "xy")]
            pts += [(x + dx, y + dy) for x, y in gp for dx in (-hw, hw) for dy in (-hw, hw)]
        rot = math.radians(at[2] if len(at) > 2 else 0.0)
        # KiCad pad rotation is counter-clockwise on screen (y down)
        c, s = math.cos(rot), math.sin(rot)
        pts = [(x * c + y * s, -x * s + y * c) for x, y in pts]
        bx = bbox(pts)
        pads.append({"name": a[0] if a else "", "type": a[1] if len(a) > 1 else "",
                     "x": at[0] + (bx[0] + bx[2]) / 2, "y": at[1] + (bx[1] + bx[3]) / 2,
                     "w": bx[2] - bx[0], "h": bx[3] - bx[1]})
    return pads


def layer_points(fp, layer: str) -> list[tuple]:
    pts = []
    for tag in ("fp_line", "fp_rect", "fp_arc", "fp_circle", "fp_poly"):
        for g in sx.find_all(fp, tag):
            if _layer(g) != layer:
                continue
            if tag == "fp_circle":
                c = [float(v) for v in sx.atoms(sx.find(g, "center"))]
                e = [float(v) for v in sx.atoms(sx.find(g, "end"))]
                r = math.hypot(e[0] - c[0], e[1] - c[1])
                pts += [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
                continue
            for k in ("start", "mid", "end"):
                n = sx.find(g, k)
                if n:
                    pts.append(tuple(float(v) for v in sx.atoms(n)[:2]))
            pl = sx.find(g, "pts")
            if pl:
                pts += [tuple(float(v) for v in sx.atoms(xy)[:2])
                        for xy in sx.find_all(pl, "xy")]
    return pts


def bbox(pts):
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _clear(cfg, x, y, rad, pads) -> bool:
    for p in pads:
        dx = max(abs(x - p["x"]) - p["w"] / 2, 0.0)
        dy = max(abs(y - p["y"]) - p["h"] / 2, 0.0)
        if math.hypot(dx, dy) < rad + cfg.clearance:
            return False
    return True


def mark_kind(cfg: Config, lib: str, name: str, pads: list) -> str | None:
    """Which polarity mark a footprint gets (None = none)."""
    if not any(p["name"] == "1" for p in pads):
        return None
    if cfg.mark_kind is not None:
        return cfg.mark_kind(lib, name, pads)
    if lib in cfg.no_mark_libs:
        return None
    if lib in cfg.plus_libs:
        return "plus" if name.startswith(cfg.plus_prefix) else None
    if lib in cfg.bar_libs:
        return "bar"
    if len({p["name"] for p in pads if p["name"]}) < 2:
        return None
    return "dot"


def _line(cfg, x1, y1, x2, y2, key):
    S, Q = sx.Sym, sx.QStr
    return [S("fp_line"), [S("start"), S(_num(x1)), S(_num(y1))],
            [S("end"), S(_num(x2)), S(_num(y2))],
            [S("stroke"), [S("width"), S(_num(cfg.mark_width))], [S("type"), S("solid")]],
            [S("layer"), Q("F.SilkS")], [S("uuid"), Q(_uid(cfg, *key))]]


def polarity_mark(cfg: Config, kind: str, pads: list, key: tuple) -> list:
    S, Q = sx.Sym, sx.QStr
    W = cfg.mark_width
    p1 = next(p for p in pads if p["name"] == "1")
    others = [p for p in pads if p["name"] not in ("1", "")]
    if kind in ("bar", "plus"):
        p2 = next((p for p in others if p["name"] == "2"), others[0] if others else None)
        if p2 is None:
            return []
        ux, uy = p1["x"] - p2["x"], p1["y"] - p2["y"]
        n = math.hypot(ux, uy) or 1.0
        ux, uy = ux / n, uy / n
        e = abs(ux) * p1["w"] / 2 + abs(uy) * p1["h"] / 2
        pe = abs(uy) * p1["w"] / 2 + abs(ux) * p1["h"] / 2
        if kind == "bar":
            d = e + cfg.clearance + W / 2 + 0.05
            cx, cy = p1["x"] + ux * d, p1["y"] + uy * d
            return [_line(cfg, cx - uy * pe, cy + ux * pe, cx + uy * pe, cy - ux * pe,
                          key + ("bar",))]
        a = 0.35
        d = e + cfg.clearance + W / 2 + a + 0.05
        cx, cy = p1["x"] + ux * d, p1["y"] + uy * d
        return [_line(cfg, cx - a, cy, cx + a, cy, key + ("plus-h",)),
                _line(cfg, cx, cy - a, cx, cy + a, key + ("plus-v",))]
    # dot: walk outwards from pad 1, away from the pad centroid first
    cxs = sum(p["x"] for p in pads) / len(pads)
    cys = sum(p["y"] for p in pads) / len(pads)
    a0 = math.atan2(p1["y"] - cys, p1["x"] - cxs) if (
        abs(p1["x"] - cxs) + abs(p1["y"] - cys) > 1e-6) else math.pi
    rad = cfg.dot_r + W / 2
    angles = [a0 + k * math.pi / 4 for k in (0, -1, 1, -2, 2, -3, 3, 4)]
    for step in range(30):
        for a in angles:
            ux, uy = math.cos(a), math.sin(a)
            e = abs(ux) * p1["w"] / 2 + abs(uy) * p1["h"] / 2
            d = e + cfg.clearance + rad + 0.02 + step * 0.1
            x, y = p1["x"] + ux * d, p1["y"] + uy * d
            if _clear(cfg, x, y, rad, pads):
                return [[S("fp_circle"), [S("center"), S(_num(x)), S(_num(y))],
                         [S("end"), S(_num(x + cfg.dot_r)), S(_num(y))],
                         [S("stroke"), [S("width"), S(_num(W))], [S("type"), S("solid")]],
                         [S("fill"), S("yes")], [S("layer"), Q("F.SilkS")],
                         [S("uuid"), Q(_uid(cfg, *key, "dot"))]]]
    return []


def strip_silk(cfg: Config, fp: list, lib: str, name: str) -> list:
    """fp with no silkscreen except a generated polarity mark."""
    S, Q = sx.Sym, sx.QStr
    has_fab_ref = any(
        sx.atoms(t)[:2] == ["user", "${REFERENCE}"] and _layer(t) in ("F.Fab", "B.Fab")
        for t in sx.find_all(fp, "fp_text"))
    out = []
    for x in fp:
        t = sx.tag_of(x)
        if t in ("fp_line", "fp_rect", "fp_arc", "fp_circle", "fp_poly",
                 "fp_text", "fp_text_box", "fp_curve") and _layer(x) in SILK:
            continue
        if t == "property" and _layer(x) in SILK:
            x = [list(y) if isinstance(y, list) else y for y in x]
            for i, y in enumerate(x):
                if sx.tag_of(y) == "layer":
                    x[i] = [S("layer"), Q("B.Fab" if sx.atoms(y)[0] == "B.SilkS" else "F.Fab")]
            if sx.atoms(x)[0] == "Reference" and has_fab_ref and not sx.find(x, "hide"):
                at = next(i for i, y in enumerate(x) if sx.tag_of(y) == "layer")
                x.insert(at + 1, [S("hide"), S("yes")])
        out.append(x)
    pads = pads_of(out)
    kind = mark_kind(cfg, lib, name, pads)
    if kind:
        marks = polarity_mark(cfg, kind, pads, (lib, name))
        at = next((i for i, y in enumerate(out) if sx.tag_of(y) == "pad"), len(out))
        out[at:at] = marks
    return out


# ---------------------------------------------------------------- 3D models

_MODEL_VAR = re.compile(r"^\$\{KICAD\d*_3DMODEL_DIR\}/")


def _model_target(cfg: Config, path: str, lib: str, name: str):
    """(source file or None, vendored target) for a footprint's model path."""
    D3, LIBS = cfg.d3, cfg.libs_dir
    if _MODEL_VAR.match(path):
        rel = _MODEL_VAR.sub("", path)
        src = cfg.model_dir / rel if cfg.model_dir else None
        return (src if src and src.exists() else None), D3 / rel
    m = re.match(r"^\$\{KIPRJMOD\}/" + re.escape(cfg.libs) + r"/(?:" +
                 re.escape(cfg.d3_sub) + r"/)?([^/]+\.3dshapes)/(.+)$", path)
    if m:
        d, f = m.groups()
        stem = pathlib.Path(f).stem
        for cand in (D3 / d / f"{stem}.step", D3 / d / f, LIBS / d / f"{stem}.step",
                     LIBS / d / f):
            if cand.exists():
                return cand, D3 / d / cand.name
        return None, D3 / d / f"{stem}.step"
    return None, D3 / f"{lib}.3dshapes" / f"{name}.step"


def _model_node(path: str) -> list:
    S, Q = sx.Sym, sx.QStr
    xyz = lambda *v: [S("xyz"), *[S(_num(a)) for a in v]]  # noqa: E731
    return [S("model"), Q(path), [S("offset"), xyz(0, 0, 0)],
            [S("scale"), xyz(1, 1, 1)], [S("rotate"), xyz(0, 0, 0)]]


def _rel_prj(cfg: Config, p: pathlib.Path) -> str:
    return "${KIPRJMOD}/" + p.relative_to(cfg.proj).as_posix()


def place_models(cfg: Config, fp: list, lib: str, name: str, copies: dict,
                 gens: dict, own: tuple) -> list:
    """Point every model of fp into <libs>/3d/, queueing copies and
    generated models. A project footprint with no model gets one."""
    out, models = [], []
    for x in fp:
        (models if sx.tag_of(x) == "model" else out).append(x)
    lid = f"{lib}:{name}"
    if lid in cfg.no_model:
        return out
    if not models:
        if lid in cfg.special or lib in own:
            tgt = cfg.d3 / f"{lib}.3dshapes" / f"{name}{cfg.model_ext}"
            gens[tgt] = (cfg.special.get(lid, "box"), lib, name, out)
            models = [_model_node(_rel_prj(cfg, tgt))]
        return out + models
    generated = _generated(cfg)
    new = []
    for m in models:
        path = sx.atoms(m)[0]
        src, tgt = _model_target(cfg, path, lib, name)
        if tgt.with_suffix(cfg.model_ext).relative_to(cfg.d3).as_posix() in generated:
            src = None
        if src is not None:
            copies[tgt] = src
        else:
            tgt = tgt.with_suffix(cfg.model_ext)
            gens[tgt] = (cfg.special.get(lid, "box"), lib, name, out)
        m = list(m)
        m[1] = sx.QStr.of(_rel_prj(cfg, tgt))
        new.append(m)
    return out + new


def _generated(cfg: Config) -> dict:
    return json.loads(cfg.gen_json.read_text()) if cfg.gen_json.exists() else {}


def pad_box(p: dict, t: float = 0.05, col=GOLD, w=None, h=None) -> list:
    """A box on a pad: [x0, y0, 0, x1, y1, t, colour]."""
    w, h = w or p["w"], h or p["h"]
    return [p["x"] - w / 2, p["y"] - h / 2, 0, p["x"] + w / 2, p["y"] + h / 2, t, col]


def body_bbox(fp, pads=None):
    """Body outline: the Fab bbox, else the courtyard less 0.25 mm, else
    the pads' extent."""
    pads = pads_of(fp) if pads is None else pads
    fab = bbox(layer_points(fp, "F.Fab"))
    if fab is None:
        crt = bbox(layer_points(fp, "F.CrtYd"))
        fab = (crt[0] + .25, crt[1] + .25, crt[2] - .25, crt[3] - .25) if crt else \
            bbox([(p["x"] + sx_ * p["w"] / 2, p["y"] + sy_ * p["h"] / 2)
                  for p in pads for sx_ in (-1, 1) for sy_ in (-1, 1)])
    return fab


def model_spec(cfg: Config, kind: str, lib: str, name: str, fp: list) -> dict:
    """Boxes in footprint coordinates (mm, y down, z up from the board):
    [x0, y0, z0, x1, y1, z1, (r, g, b)], plus builder shapes."""
    pads = pads_of(fp)
    fab = body_bbox(fp, pads)
    boxes, shapes = [], []
    if kind == "box":
        x0, y0, x1, y1 = fab
        h = next((h for k, h in cfg.heights if k in name), cfg.default_height)
        boxes.append([x0, y0, 0.05, x1, y1, h, BLACK])
        boxes += [pad_box(p) for p in pads if p["w"] and p["h"]]
    else:
        if kind not in cfg.builders:
            raise KeyError(f"no model builder for kind {kind!r} ({lib}:{name})")
        r = cfg.builders[kind](fab, pads, name)
        boxes, shapes = list(r.get("boxes", [])), list(r.get("shapes", []))
    return {"kind": kind, "boxes": [[round(v, 4) if isinstance(v, float) else v
                                     for v in b[:6]] + [list(b[6])] for b in boxes],
            "shapes": _round(shapes)}


def _round(x):
    if isinstance(x, float):
        return round(x, 4)
    if isinstance(x, (list, tuple)):
        return [_round(v) for v in x]
    return x


def ocp_python(cfg: Config) -> str | None:
    cands = [os.environ.get("KX_OCP_PYTHON"), *cfg.ocp_python, sys.executable]
    for c in cands:
        if c and pathlib.Path(c).exists():
            r = subprocess.run([c, "-c", "import OCP"], capture_output=True)
            if r.returncode == 0:
                return c
    return None


def write_vrml(path: pathlib.Path, boxes: list) -> None:
    """VRML 2.0 boxes. KiCad VRML units are 0.1 inch, y up."""
    k = 1 / 2.54
    out = ["#VRML V2.0 utf8"]
    for b in boxes:
        x0, y0, z0, x1, y1, z1, col = b
        cx, cy, cz = (x0 + x1) / 2 * k, -(y0 + y1) / 2 * k, (z0 + z1) / 2 * k
        out.append(
            f"Transform {{ translation {cx:.4f} {cy:.4f} {cz:.4f} children Shape {{ "
            f"appearance Appearance {{ material Material {{ diffuseColor "
            f"{col[0]} {col[1]} {col[2]} }} }} geometry Box {{ size "
            f"{abs(x1 - x0) * k:.4f} {abs(y1 - y0) * k:.4f} {abs(z1 - z0) * k:.4f} }} }} }}")
    path.write_text("\n".join(out) + "\n")


def ocp_write(jobs_file: str) -> int:
    """Run under a Python with OCP: one coloured STEP per job. Shapes:
    ["box", x0,y0,z0,x1,y1,z1, col], ["cyl", a(3), b(3), r, col],
    ["sph", c(3), r, col], ["prism", [[x,y]...], z0, z1, col],
    ["tube", x0, x1, yc, zc, r_in, r_out, col] (half tube, +Y side)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorType
    from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_StepModelType
    from OCP.TDataStd import TDataStd_Name
    from OCP.Interface import Interface_Static

    Interface_Static.SetCVal_s("write.step.unit", "MM")
    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
    from OCP.BRepPrimAPI import (BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeSphere,
                                 BRepPrimAPI_MakePrism)
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.gp import gp_Ax2, gp_Dir, gp_Vec, gp_Trsf
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder

    P = lambda x, y, z: gp_Pnt(x, -y, z)  # noqa: E731  footprint y is down

    def solid(sh):
        k = sh[0]
        if k == "box":
            x0, y0, z0, x1, y1, z1 = sh[1:7]
            return BRepPrimAPI_MakeBox(gp_Pnt(min(x0, x1), -max(y0, y1), min(z0, z1)),
                                       gp_Pnt(max(x0, x1), -min(y0, y1), max(z0, z1))).Shape()
        if k == "cyl":
            a, b, r = sh[1:4], sh[4:7], sh[7]
            v = gp_Vec(P(*a), P(*b))
            return BRepPrimAPI_MakeCylinder(gp_Ax2(P(*a), gp_Dir(v)), r, v.Magnitude()).Shape()
        if k == "sph":
            return BRepPrimAPI_MakeSphere(P(*sh[1:4]), sh[4]).Shape()
        if k == "prism":
            poly = BRepBuilderAPI_MakePolygon()
            for x, y in sh[1]:
                poly.Add(P(x, y, sh[2]))
            poly.Close()
            face = BRepBuilderAPI_MakeFace(poly.Wire()).Face()
            return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, sh[3] - sh[2])).Shape()
        if k == "tube":   # half tube on the footprint's +Y side, axis along X
            x0, x1, yc, zc, ri, ro = sh[1:7]
            ax = gp_Ax2(P(x0, yc, zc), gp_Dir(1, 0, 0), gp_Dir(0, 0, 1))
            o = BRepPrimAPI_MakeCylinder(ax, ro, x1 - x0, math.pi).Shape()
            i = BRepPrimAPI_MakeCylinder(ax, ri, x1 - x0, math.pi).Shape()
            return BRepAlgoAPI_Cut(o, i).Shape()
        raise ValueError(k)

    for job in json.loads(pathlib.Path(jobs_file).read_text()):
        doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
        st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        ct = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
        asm = st.NewShape()
        TDataStd_Name.Set_s(asm, TCollection_ExtendedString(job["name"]))
        groups: dict = {}
        boxes: dict = {}    # identical boxes become instances of one shape
        items = [["box", *b[:6], b[6]] for b in job["boxes"]] + job.get("shapes", [])
        for sh in items:
            if sh[0] == "box":
                x0, y0, z0, x1, y1, z1 = sh[1:7]
                dims = (round(abs(x1 - x0), 4), round(abs(y1 - y0), 4), round(abs(z1 - z0), 4))
                boxes.setdefault((dims, tuple(sh[-1])), []).append(
                    (min(x0, x1), -max(y0, y1), min(z0, z1)))
            else:
                groups.setdefault(tuple(sh[-1]), []).append(sh[:-1])
        nb = 0
        for (dims, col), origins in boxes.items():
            if len(origins) < 3:
                for o in origins:
                    groups.setdefault(col, []).append(
                        ["box", o[0], -o[1] - dims[1], o[2], o[0] + dims[0], -o[1], o[2] + dims[2]])
                continue
            proto = BRepPrimAPI_MakeBox(*dims).Shape()
            lab = st.AddShape(proto, False)
            TDataStd_Name.Set_s(lab, TCollection_ExtendedString(f"{job['name']}_b{nb}"))
            ct.SetColor(lab, Quantity_Color(*col, Quantity_TypeOfColor.Quantity_TOC_RGB),
                        XCAFDoc_ColorType.XCAFDoc_ColorSurf)
            nb += 1
            for o in origins:
                tr = gp_Trsf()
                tr.SetTranslation(gp_Vec(*o))
                st.AddComponent(asm, lab, TopLoc_Location(tr))
        bb = BRep_Builder()
        for i, (col, items) in enumerate(groups.items()):
            comp = TopoDS_Compound()
            bb.MakeCompound(comp)
            for sh in items:
                bb.Add(comp, solid(sh))
            part = st.AddShape(comp, False)
            TDataStd_Name.Set_s(part, TCollection_ExtendedString(f"{job['name']}_{i}"))
            ct.SetColor(part, Quantity_Color(*col, Quantity_TypeOfColor.Quantity_TOC_RGB),
                        XCAFDoc_ColorType.XCAFDoc_ColorSurf)
            st.AddComponent(asm, part, comp.Location())
        st.UpdateAssemblies()
        w = STEPCAFControl_Writer()
        w.SetColorMode(True)
        w.SetNameMode(True)
        w.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
        pathlib.Path(job["path"]).parent.mkdir(parents=True, exist_ok=True)
        if int(w.Write(job["path"])) != 1:
            print(f"STEP write failed: {job['path']}")
            return 1
    return 0


def build_models(cfg: Config, gens: dict, check: bool) -> tuple[list, list]:
    """Generate the models whose input changed. (written, problems)."""
    D3 = cfg.d3
    rec = _generated(cfg)
    live = {t.relative_to(D3).as_posix() for t in gens}
    stale_rec = sorted(set(rec) - live)
    for k in stale_rec:
        del rec[k]
    jobs, written = [], []
    for tgt, (kind, lib, name, fp) in sorted(gens.items()):
        spec = model_spec(cfg, kind, lib, name, fp)
        h = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]
        key = tgt.relative_to(D3).as_posix()
        if rec.get(key) == h and tgt.exists():
            continue
        jobs.append({"path": str(tgt), "name": name, "boxes": spec["boxes"],
                     "shapes": spec.get("shapes", []), "hash": h, "key": key})
    if check or (not jobs and not stale_rec):
        return [j["key"] for j in jobs], []
    if not jobs:
        cfg.gen_json.write_text(json.dumps(dict(sorted(rec.items())), indent=1) + "\n")
        return [], []
    py = ocp_python(cfg) if cfg.model_ext == ".step" else None
    probs = []
    if py:
        D3.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            jf = pathlib.Path(td) / "jobs.json"
            jf.write_text(json.dumps(jobs))
            code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                    "from kicad_lib import vendor; sys.exit(vendor.ocp_write(sys.argv[2]))")
            r = subprocess.run([py, "-c", code, str(ROOT), str(jf)],
                               capture_output=True, text=True)
        if r.returncode:
            probs.append(f"OCP model writer failed: {r.stdout}{r.stderr}"[-800:])
            return [], probs
    for j in jobs:
        if not py:   # VRML fallback (boxes only; builder shapes need OCP)
            p = pathlib.Path(j["path"]).with_suffix(".wrl")
            p.parent.mkdir(parents=True, exist_ok=True)
            write_vrml(p, j["boxes"])
            probs.append(f"no OCP: {j['key']} written as VRML")
        rec[j["key"]] = j["hash"]
        written.append(j["key"])
    cfg.gen_json.write_text(json.dumps(dict(sorted(rec.items())), indent=1) + "\n")
    return written, probs


# ---------------------------------------------------------------- lib tables

def write_table(cfg: Config, kind: str, rows: list[tuple[str, str]], out: dict) -> None:
    """kind 'sym' or 'fp'. Keeps every row this module did not write."""
    S, Q = sx.Sym, sx.QStr
    path = cfg.proj / f"{kind}-lib-table"
    doc = sx.load_file(str(path)) if path.exists() else [S(f"{kind}_lib_table"),
                                                         [S("version"), S("7")]]
    keep = [x for x in doc if not (sx.tag_of(x) == "lib" and sx.atoms(sx.find(x, "descr"))
                                   == [cfg.descr])]
    names = {sx.atoms(sx.find(x, "name"))[0] for x in keep if sx.tag_of(x) == "lib"}
    for name, uri in sorted(rows):
        if name in names:
            continue
        keep.append([S("lib"), [S("name"), Q(name)], [S("type"), Q("KiCad")],
                     [S("uri"), Q(uri)], [S("options"), Q("")], [S("descr"), Q(cfg.descr)]])
    out[path] = sx.dumps(keep, dialect="sch")


# --------------------------------------------------------------------- main

def _upgrade_legacy(pretty: pathlib.Path) -> None:
    """Old (module ...) files -> current format, once, with kicad-cli."""
    legacy = [f for f in pretty.glob("*.kicad_mod") if f.read_text().startswith("(module")]
    if not legacy:
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td) / pretty.name
        subprocess.run([*kcli.cmd(), "fp", "upgrade", "-o", str(tmp), str(pretty)],
                       check=True, capture_output=True)
        for f in legacy:
            shutil.copyfile(tmp / f.name, f)


def run(cfg: Config, check: bool = False, log=print) -> int:
    """Vendor the project. check: change nothing, exit 1 if anything
    would change (or a problem exists)."""
    cfg.sym_dir = cfg.sym_dir or library_dir("sym")
    cfg.fp_dir = cfg.fp_dir or library_dir("fp")
    cfg.model_dir = cfg.model_dir or library_dir("3d")
    KDIR, D3, LIBS, PROJ = cfg.kdir, cfg.d3, cfg.libs_dir, cfg.proj
    own = cfg.own_libs()
    cfg.model_ext = ".step" if ocp_python(cfg) else ".wrl"
    syms, fps = used_ids(cfg)
    out: dict[pathlib.Path, str] = {}
    copies: dict[pathlib.Path, pathlib.Path] = {}
    gens: dict = {}
    probs: list[str] = []
    for kind, d in (("symbol", cfg.sym_dir), ("footprint", cfg.fp_dir)):
        if d is None:
            probs.append(f"no official {kind} library dir (set KICAD_"
                         f"{'SYMBOL' if kind == 'symbol' else 'FOOTPRINT'}_DIR)")
    if probs:
        for p in probs:
            log(f"vendor PROBLEM: {p}")
        return 1

    for lib in sorted(syms):
        if lib not in own:
            probs += vendor_symbols(cfg, lib, syms[lib], out)

    def do_fp(lib, name, src, dst):
        fp = sx.load_file(str(src))
        if cfg.strip_silk:
            fp = strip_silk(cfg, fp, lib, name)
        fp = place_models(cfg, fp, lib, name, copies, gens, own)
        out[dst] = sx.dumps(fp, dialect="pcb")

    for lib in own:
        pretty = LIBS / f"{lib}.pretty"
        if pretty.is_dir():
            if not check and cfg.upgrade_legacy:
                _upgrade_legacy(pretty)
            for f in sorted(pretty.glob("*.kicad_mod")):
                do_fp(lib, f.stem, f, f)
    for lib in sorted(fps):
        if lib in own:
            for n in sorted(fps[lib]):
                if not (LIBS / f"{lib}.pretty" / f"{n}.kicad_mod").exists():
                    probs.append(f"footprint {lib}:{n} missing from {cfg.libs}/{lib}.pretty")
            continue
        for n in sorted(fps[lib]):
            src = cfg.fp_dir / f"{lib}.pretty" / f"{n}.kicad_mod"
            if not src.exists():
                probs.append(f"footprint {lib}:{n} not found in {cfg.fp_dir}")
                continue
            do_fp(lib, n, src, KDIR / f"{lib}.pretty" / f"{n}.kicad_mod")

    rel = KDIR.relative_to(PROJ).as_posix()
    sym_libs = sorted(lib for lib in syms if lib not in own)
    fp_libs = sorted(lib for lib in fps if lib not in own)
    write_table(cfg, "sym", [(lib, f"${{KIPRJMOD}}/{rel}/{lib}.kicad_sym")
                             for lib in sym_libs], out)
    write_table(cfg, "fp", [(lib, f"${{KIPRJMOD}}/{rel}/{lib}.pretty")
                            for lib in fp_libs], out)

    changed = [p for p, t in out.items() if not p.exists() or p.read_text() != t]
    changed += [t for t, s in copies.items()
                if not t.exists() or t.stat().st_size != s.stat().st_size]
    # prune vendored files nothing uses any more
    keep = set(out) | set(copies) | set(gens) | {cfg.gen_json}
    keep |= {t.with_suffix(".wrl") for t in gens}
    stale = [f for d in (KDIR, D3) if d.exists() for f in d.rglob("*")
             if f.is_file() and f not in keep]
    if check:
        todo, _ = build_models(cfg, gens, check=True)
        n = len(changed) + len(stale) + len(todo)
        for p in changed + stale:
            log(f"would change: {p.relative_to(PROJ)}")
        for k in todo:
            log(f"would generate: {D3.relative_to(PROJ).as_posix()}/{k}")
        for p in probs:
            log(f"PROBLEM: {p}")
        return 1 if n or probs else 0

    for p, t in out.items():
        if not p.exists() or p.read_text() != t:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(t)
    for t, s in copies.items():
        if t.resolve() != s.resolve() and (not t.exists() or t.stat().st_size != s.stat().st_size):
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(s, t)
    for f in stale:
        f.unlink()
    for d in sorted((x for x in (KDIR, D3) if x.exists()), reverse=True):
        for sub in sorted(d.rglob("*"), reverse=True):
            if sub.is_dir() and not any(sub.iterdir()):
                sub.rmdir()
    written, mprobs = build_models(cfg, gens, check=False)
    probs += mprobs
    size = sum(f.stat().st_size for f in LIBS.rglob("*") if f.is_file())
    nfp = sum(len(v) for v in fps.values())
    log(f"vendor: {sum(len(v) for v in syms.values())} symbols from {len(sym_libs)} "
        f"official libs, {nfp} footprints, {len(copies)} copied + {len(gens)} generated "
        f"3D models; {len(changed)} files updated, {len(stale)} pruned, "
        f"{len(written)} models rebuilt; {cfg.libs}/ = {size / 1e6:.1f} MB")
    if any(p.name.endswith("lib-table") for p in changed):
        log("vendor: lib tables changed - reopen the project in KiCad")
    for p in probs:
        log(f"vendor PROBLEM: {p}")
    return 1 if any(not p.startswith("no OCP") for p in probs) else 0


def main(argv: list[str]) -> int:
    check = "--check" in argv
    argv = [a for a in argv if a != "--check"]
    conf = None
    if "--config" in argv:
        i = argv.index("--config")
        if i + 1 >= len(argv):
            print(__doc__)
            return 2
        conf = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) != 1:
        print(__doc__)
        return 2
    return run(load_config(argv[0], conf), check=check)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
