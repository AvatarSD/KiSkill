"""Round-trip for GENERATED schematics: hand edits survive regeneration.

A script that writes .kicad_sch files wipes every edit the operator makes
in the KiCad GUI the next time it runs. This module closes the loop:

  snapshot   after each generation, a JSON sidecar per sheet records what
             the generator wrote: every symbol (by "REF/UNIT") with lib_id,
             position/rotation/mirror, flags (dnp, in_bom, ...), every
             field's value and position RELATIVE to the symbol; every
             sheet block; every other item (wire, label, junction,
             no-connect, text, ...) by uuid, with the symbol/block that
             generated it ("owner") when the generator says so.
  diff       the sheet on disk against its snapshot, every difference
             classified:
               absorb   symbol moved/rotated/mirrored, field moved or
                        restyled (hide/justify), value/footprint/property
                        changed/added/removed, flag toggled, text note or
                        sheet block moved
               rehang   a generated stub/label/junction of a MOVED symbol
                        changed or vanished — the generator re-hangs it at
                        the new position, nothing is lost
               refuse   anything else: a hand-added symbol/wire/label, a
                        deleted generated part, a renamed label, a power
                        symbol's net changed, a lib_id swap, a symbol edit
                        this module cannot express as an override
  absorb     folds the absorbable edits into an overrides file (JSON,
             human-readable, committed) — and REFUSES the whole run when
             any refuse-class edit exists, unless force=True (explicit
             discard, every discarded edit listed)
  apply      the generator asks Overrides for each symbol it places and
             uses the override position BEFORE hanging stubs, then
             apply_symbol() sets fields/flags on the placed node
  netlist    kicad-cli netlist → {node: net}; compared before/after a
             regeneration that only absorbed overrides (must be equal)

Identity: symbols by reference+unit (uuids are re-rolled by generators);
other items by uuid — KiCad keeps item uuids across load/move/save. Wires
are compared GEOMETRICALLY (split at every endpoint), so KiCad merging or
splitting collinear segments on save is not an edit.

Why the operator's position is kept as an override instead of being
written back into the script: the script places by auto-grid; hard-coding
one coordinate there would freeze the grid. Value/footprint/property
overrides ARE design data, so report() lists them as "move into the gen
script" — the script stays the source of truth.

Stdlib only. kicad-cli (via kcli) only for netlist().
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import subprocess
import uuid as _uuid

from . import kcli, live, sexp
from .sexp import QStr, Sym

VERSION = 1
SAVED_ENV = "KX_REGEN_SAVED_BY"   # set by live_sch around a generator run
FLAGS = ("exclude_from_sim", "in_bom", "on_board", "in_pos_files", "dnp")
HEADER = {"version", "generator", "generator_version", "uuid", "paper",
          "title_block", "lib_symbols", "sheet_instances", "symbol_instances",
          "embedded_fonts"}
LABELS = {"label", "global_label", "hierarchical_label", "directive_label"}
POINT_ITEMS = {"junction", "no_connect"}
TEXTS = {"text"}


class Refused(RuntimeError):
    """Regeneration would lose edits (or KiCad holds the files)."""

    def __init__(self, msg: str, items: list[str] | None = None):
        super().__init__(msg)
        self.items = items or []


# ---------------------------------------------------------------- helpers

def _f(s) -> float:
    return round(float(s), 3)


def _uid(node: list) -> str | None:
    u = sexp.find(node, "uuid")
    return sexp.atoms(u)[0] if u else None


def _strip(node, drop_tags=("uuid",)):
    """Deep copy without the given child tags (any depth)."""
    if not isinstance(node, list):
        return node
    return [_strip(x, drop_tags) for x in node
            if not (isinstance(x, list) and sexp.tag_of(x) in drop_tags)]


# children KiCad adds on save when they hold their default value — a
# generator that omits them must not read as an edit
_DEFAULTS = (["show_name", "no"], ["do_not_autoplace", "no"])
_NUM = re.compile(r"-?\d+(\.\d+)?$")


def _canon(node):
    if not isinstance(node, list):
        if isinstance(node, Sym) and _NUM.match(node):
            return Sym(f"{float(node):g}")
        return node
    return [_canon(x) for x in node
            if not (isinstance(x, list) and [str(z) for z in x] in _DEFAULTS)]


def _sig(node) -> str:
    return hashlib.sha1(sexp.dumps(_canon(node)).encode()).hexdigest()[:16]


def _hidden(prop: list) -> bool:
    h = sexp.find(prop, "hide")
    if h is not None:
        return (sexp.atoms(h) or ["yes"])[0] == "yes"
    eff = sexp.find(prop, "effects")
    if eff is None:
        return False
    h = sexp.find(eff, "hide")
    if h is not None:
        return (sexp.atoms(h) or ["yes"])[0] == "yes"
    return "hide" in sexp.atoms(eff)


def _justify(prop: list) -> list[str]:
    eff = sexp.find(prop, "effects")
    j = sexp.find(eff, "justify") if eff else None
    return sexp.atoms(j) if j else []


def _key(ref: str, unit: int) -> str:
    return f"{ref}/{unit}"


# ---------------------------------------------------------------- state

def sym_state(node: list) -> dict:
    """Everything an operator can change on a placed symbol, normalised:
    field positions are RELATIVE to the symbol origin."""
    at = sexp.atoms(sexp.find(node, "at"))
    x, y = _f(at[0]), _f(at[1])
    m = sexp.find(node, "mirror")
    u = sexp.find(node, "unit")
    fields = {}
    for p in sexp.find_all(node, "property"):
        a = sexp.atoms(p)
        pat = sexp.atoms(sexp.find(p, "at")) if sexp.find(p, "at") else [x, y, 0]
        fields[a[0]] = {
            "value": a[1] if len(a) > 1 else "",
            "at": [round(_f(pat[0]) - x, 3), round(_f(pat[1]) - y, 3),
                   _f(pat[2]) if len(pat) > 2 else 0.0],
            "hide": _hidden(p),
            "justify": _justify(p),
            "style": _prop_style(p),
        }
    return {
        "lib_id": sexp.atoms(sexp.find(node, "lib_id"))[0],
        "at": [x, y],
        "rot": int(float(at[2])) % 360 if len(at) > 2 else 0,
        "mirror": sexp.atoms(m)[0] if m else "",
        "unit": int(sexp.atoms(u)[0]) if u else 1,
        "flags": {t: sexp.atoms(n)[0] == "yes" for t in FLAGS
                  if (n := sexp.find(node, t)) is not None},
        "fields": fields,
        "rest": _sym_rest_sig(node),
    }


def _sym_rest_sig(node: list) -> str:
    """Signature of what sym_state does NOT capture (body_style, ...).
    KiCad re-saves keep it stable; if it moves, the edit is outside what
    overrides can express → refuse."""
    keep = [x for x in node if not (isinstance(x, list) and sexp.tag_of(x) in (
        "at", "mirror", "uuid", "instances", "pin", "lib_id", "unit",
        "property", "fields_autoplaced", *FLAGS))]
    return _sig(keep)


def _prop_style(p: list) -> str:
    """A field's look beyond position/hide/justify (font size, bold,
    show_name, ...)."""
    keep = []
    for z in p[3:]:
        zt = sexp.tag_of(z) if isinstance(z, list) else None
        if zt in ("at", "hide", "uuid", "id"):
            continue
        if zt == "effects":
            z = [e for e in z if not (isinstance(e, list) and
                                      sexp.tag_of(e) in ("justify", "hide"))
                 and e != "hide"]
        keep.append(z)
    return _sig(keep)


def _ref_unit(node: list) -> tuple[str, int]:
    ref = next((sexp.atoms(p)[1] for p in sexp.find_all(node, "property")
                if sexp.atoms(p)[0] == "Reference"), "?")
    u = sexp.find(node, "unit")
    return ref, int(sexp.atoms(u)[0]) if u else 1


def _sheet_state(node: list) -> dict:
    at = sexp.atoms(sexp.find(node, "at"))
    x, y = _f(at[0]), _f(at[1])
    props = {sexp.atoms(p)[0]: sexp.atoms(p)[1]
             for p in sexp.find_all(node, "property")}

    def rel(n):
        if not isinstance(n, list):
            return n
        if sexp.tag_of(n) == "at":
            a = sexp.atoms(n)
            out = [Sym("at"), Sym(f"{round(_f(a[0]) - x, 3):g}"),
                   Sym(f"{round(_f(a[1]) - y, 3):g}")]
            return out + [Sym(v) for v in a[2:]]
        return [rel(z) for z in n]

    body = [z for z in node if not (isinstance(z, list) and
                                    sexp.tag_of(z) in ("at", "uuid", "instances"))]
    return {"file": props.get("Sheetfile", "?"), "name": props.get("Sheetname", "?"),
            "at": [x, y], "rel": _sig(rel(_strip(body)))}


def _item_state(node: list) -> dict:
    t = sexp.tag_of(node)
    d: dict = {"kind": t, "uuid": _uid(node)}
    if t in ("wire", "bus"):
        d["pts"] = [[_f(sexp.atoms(p)[0]), _f(sexp.atoms(p)[1])]
                    for p in sexp.find_all(sexp.find(node, "pts"), "xy")]
        return d
    a = sexp.find(node, "at")
    if a is not None:
        aa = sexp.atoms(a)
        d["at"] = [_f(aa[0]), _f(aa[1])] + ([_f(aa[2])] if len(aa) > 2 else [])
    if t in LABELS or t in TEXTS:
        d["text"] = sexp.atoms(node)[0]
    d["sig"] = _sig(_strip(node, ("uuid", "at")))
    return d


def snapshot(path: str, owners: dict[str, str] | None = None,
             base: dict[str, dict] | None = None) -> dict:
    """Inventory of the sheet on disk. owners: item uuid → owner ("REF/UNIT"
    or "sheet:FILE"); base: "REF/UNIT" → sym_state of what the SCRIPT asked
    for before overrides (enables pruning + the report)."""
    root = sexp.load_file(str(path))
    owners = owners or {}
    power = {sexp.atoms(s)[0] for s in sexp.find_all(sexp.find(root, "lib_symbols")
                                                      or [], "symbol")
             if sexp.find(s, "power") is not None}
    syms, sheets, items = {}, {}, []
    for x in root[1:]:
        t = sexp.tag_of(x) if isinstance(x, list) else None
        if t is None or t in HEADER:
            continue
        if t == "symbol":
            ref, unit = _ref_unit(x)
            st = sym_state(x)
            st["power"] = st["lib_id"] in power
            syms[_key(ref, unit)] = st
        elif t == "sheet":
            sheets[_uid(x)] = _sheet_state(x)
        else:
            d = _item_state(x)
            if d["uuid"] in owners:
                d["owner"] = owners[d["uuid"]]
            items.append(d)
    return {"version": VERSION, "symbols": syms, "sheets": sheets,
            "items": items, "base": base or {}}


def save_json(obj, path) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(p)


def save_compact(obj: dict, path) -> None:
    """Snapshot layout: sorted, one record per line — deterministic and
    small, so a no-change regeneration leaves git clean and a real change
    diffs as the lines of the records that moved."""
    def one(v):
        return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    L = ["{"]
    keys = sorted(obj)
    for i, k in enumerate(keys):
        v, end = obj[k], ("," if i < len(keys) - 1 else "")
        if isinstance(v, dict) and v:
            L.append(f" {one(k)}: {{")
            sub = sorted(v)
            L += [f"  {one(kk)}: {one(v[kk])}" + ("," if j < len(sub) - 1 else "")
                  for j, kk in enumerate(sub)]
            L.append(" }" + end)
        elif isinstance(v, list) and v:
            L.append(f" {one(k)}: [")
            L += [f"  {one(x)}" + ("," if j < len(v) - 1 else "") for j, x in enumerate(v)]
            L.append(" ]" + end)
        else:
            L.append(f" {one(k)}: {one(v)}" + end)
    L.append("}")
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("\n".join(L) + "\n")
    tmp.replace(p)


def restamp(doc: list, seed: str, owners: dict[str, str] | None = None) -> dict[str, str]:
    """Replace every item uuid in a GENERATED sheet with one derived from
    seed + the item's role, so two runs of the same script write
    byte-identical files and a symbol keeps its uuid (= the PCB footprint
    link, KiCad's netlist tstamp) across regenerations.

    Roles: symbol → its "REF/UNIT"; any other item → kind + owner +
    content signature (+ occurrence count); nested uuids (symbol pins,
    sheet pins) → parent uuid + tag + first atom. Sheet-block uuids are
    LEFT ALONE: they are part of every child symbol's instance path and the
    generator must keep them stable itself. Header uuid untouched.
    Returns the owners map re-keyed to the new uuids."""
    owners = owners or {}
    ns = _uuid.uuid5(_uuid.NAMESPACE_URL, "kiskill-regen:" + seed)
    seen: dict[str, int] = {}
    out: dict[str, str] = {}

    def nested(node: list, parent: str) -> None:
        count: dict[str, int] = {}
        for z in node:
            if not isinstance(z, list) or sexp.tag_of(z) == "uuid":
                continue
            u = sexp.find(z, "uuid")
            if u is not None:
                a = sexp.atoms(z)
                role = f"{parent}|{sexp.tag_of(z)}|{a[0] if a else ''}"
                n = count.get(role, 0)
                count[role] = n + 1
                u[1] = QStr(str(_uuid.uuid5(ns, f"{role}#{n}")))
                nested(z, sexp.atoms(u)[0])

    for x in doc[1:]:
        t = sexp.tag_of(x) if isinstance(x, list) else None
        if t is None or t in HEADER:
            continue
        u = sexp.find(x, "uuid")
        if u is None:
            continue
        old = sexp.atoms(u)[0]
        if t != "sheet":
            if t == "symbol":
                role = "symbol|" + _key(*_ref_unit(x))
            else:
                role = f"{t}|{owners.get(old, '')}|{_sig(_strip(x))}"
            n = seen.get(role, 0)
            seen[role] = n + 1
            u[1] = QStr(str(_uuid.uuid5(ns, f"{role}#{n}")))
        new = sexp.atoms(u)[0]
        if old in owners:
            out[new] = owners[old]
        nested(x, new)
    return out


def load_json(path):
    return json.loads(pathlib.Path(path).read_text())


# ---------------------------------------------------------------- diff

def _segments(wires: list[dict], extra_pts) -> dict:
    """Elementary segments: each wire split at every endpoint (of either
    revision) lying on it. {((x1,y1),(x2,y2)) sorted: wire index}."""
    pts = set(extra_pts)
    for w in wires:
        pts.update(tuple(p) for p in w["pts"])
    by_x, by_y = {}, {}
    for p in pts:
        by_x.setdefault(p[0], []).append(p)
        by_y.setdefault(p[1], []).append(p)
    out = {}
    for i, w in enumerate(wires):
        for a, b in zip(w["pts"], w["pts"][1:]):
            (ax, ay), (bx, by) = a, b
            if ay == by:
                cand = by_y.get(ay, [])
            elif ax == bx:
                cand = by_x.get(ax, [])
            else:
                cand = pts
            on = [p for p in cand
                  if min(ax, bx) - 1e-6 <= p[0] <= max(ax, bx) + 1e-6
                  and min(ay, by) - 1e-6 <= p[1] <= max(ay, by) + 1e-6
                  and abs((bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)) < 1e-4]
            on.sort(key=lambda p: (p[0] - ax) ** 2 + (p[1] - ay) ** 2)
            for p, q in zip(on, on[1:]):
                if p != q:
                    out[tuple(sorted((p, q)))] = i
    return out


def diff(snap: dict, path: str) -> list[dict]:
    """Classify every difference between the snapshot and the file on disk.
    Each entry: {"cls": absorb|rehang|refuse, "what": str, ...}."""
    cur = snapshot(path)
    out: list[dict] = []

    def E(cls, what, **kw):
        out.append({"cls": cls, "what": what, **kw})

    moved: set[str] = set()
    a, b = snap["symbols"], cur["symbols"]
    for k in sorted(a.keys() - b.keys()):
        E("refuse", f"generated symbol {k} ({a[k]['lib_id']}) deleted or re-annotated")
    for k in sorted(b.keys() - a.keys()):
        E("refuse", f"hand-added symbol {k} ({b[k]['lib_id']}) at {b[k]['at']}")
    for k in sorted(a.keys() & b.keys()):
        sa, sb = a[k], b[k]
        if sa["lib_id"] != sb["lib_id"]:
            E("refuse", f"{k}: symbol changed {sa['lib_id']} -> {sb['lib_id']}")
            continue
        if (sa["at"], sa["rot"], sa["mirror"]) != (sb["at"], sb["rot"], sb["mirror"]):
            moved.add(k)
            E("absorb", f"{k}: moved {sa['at']} r{sa['rot']}{sa['mirror']} -> "
                        f"{sb['at']} r{sb['rot']}{sb['mirror']}",
              op="geom", key=k, at=sb["at"], rot=sb["rot"], mirror=sb["mirror"])
        for fl in sorted(set(sa["flags"]) | set(sb["flags"])):
            if sa["flags"].get(fl) != sb["flags"].get(fl) and fl in sb["flags"]:
                E("absorb", f"{k}: {fl} -> {'yes' if sb['flags'][fl] else 'no'}",
                  op="flag", key=k, flag=fl, val=sb["flags"][fl])
        fa, fb = sa["fields"], sb["fields"]
        # KiCad adds its mandatory fields (Description, ...) empty on save
        for n in [n for n in fb.keys() - fa.keys() if fb[n]["value"] == ""]:
            del fb[n]
        for n in sorted(fa.keys() - fb.keys()):
            if fa[n]["value"] == "":
                continue
            E("absorb", f"{k}: property {n!r} removed", op="field", key=k,
              name=n, value=None)
        for n in sorted(fb.keys()):
            if n == "Reference":
                pass
            elif n not in fa or fa[n]["value"] != fb[n]["value"]:
                if (sa.get("power") or sb.get("power")) and n == "Value":
                    E("refuse", f"{k}: power symbol value (= its NET) "
                                f"{fa[n]['value']!r} -> {fb[n]['value']!r}")
                    continue
                E("absorb", f"{k}: {n} {fa.get(n, {}).get('value')!r} -> "
                            f"{fb[n]['value']!r}",
                  op="field", key=k, name=n, value=fb[n]["value"],
                  pos=None if n in fa else fb[n])
            if n in fa and fa[n]["style"] != fb[n]["style"]:
                E("refuse", f"{k}: field {n} font/style edited — not expressible "
                            f"as an override")
            if n in fa and any(fa[n][z] != fb[n][z] for z in ("at", "hide", "justify")):
                E("absorb", f"{k}: field {n} moved/restyled", op="fpos", key=k,
                  name=n, pos={z: fb[n][z] for z in ("at", "hide", "justify")})
        if sa["rest"] != sb["rest"]:
            E("refuse", f"{k}: symbol edited beyond position/fields/flags "
                        f"(body style ...) — not expressible as an override")

    sa, sb = snap["sheets"], cur["sheets"]
    for u in sorted(sa.keys() - sb.keys()):
        E("refuse", f"sheet block {sa[u]['file']} deleted")
    for u in sorted(sb.keys() - sa.keys()):
        E("refuse", f"hand-added sheet block {sb[u]['file']}")
    for u in sorted(sa.keys() & sb.keys()):
        if sa[u]["rel"] != sb[u]["rel"] or sa[u]["file"] != sb[u]["file"]:
            E("refuse", f"sheet block {sa[u]['file']}: pins/size/fields edited")
        if sa[u]["at"] != sb[u]["at"]:
            moved.add(f"sheet:{sa[u]['file']}")
            E("absorb", f"sheet block {sa[u]['file']} moved {sa[u]['at']} -> "
                        f"{sb[u]['at']}", op="block", file=sa[u]["file"], at=sb[u]["at"])

    # ---- items
    owner = {d["uuid"]: d.get("owner") for d in snap["items"] if d["uuid"]}
    ia = {d["uuid"]: d for d in snap["items"] if d["kind"] not in ("wire", "bus")}
    ib = {d["uuid"]: d for d in cur["items"] if d["kind"] not in ("wire", "bus")}
    wa = [d for d in snap["items"] if d["kind"] in ("wire", "bus")]
    wb = [d for d in cur["items"] if d["kind"] in ("wire", "bus")]
    # points the moved owners' items occupy NOW — a dragged symbol leaves
    # (or KiCad inserts) segments between exactly these
    hot = set()
    for d in cur["items"]:
        if owner.get(d["uuid"]) in moved:
            hot.update(tuple(p) for p in d.get("pts", []))
            if "at" in d:
                hot.add(tuple(d["at"][:2]))
    rehung: dict[str, int] = {}
    for kind in ("wire", "bus"):
        wbk = [w for w in wb if w["kind"] == kind]
        A2 = _segments([w for w in wa if w["kind"] == kind],
                       [p for w in wbk for p in map(tuple, w["pts"])])
        B2 = _segments(wbk, [p for w in wa if w["kind"] == kind
                             for p in map(tuple, w["pts"])])
        wak = [w for w in wa if w["kind"] == kind]
        for seg in sorted(A2.keys() - B2.keys()):
            o = wak[A2[seg]].get("owner")
            if o in moved:
                rehung[o] = rehung.get(o, 0) + 1
            else:
                E("refuse", f"generated {kind} {seg[0]}-{seg[1]} removed or rerouted"
                            + (f" (stub of {o})" if o else ""))
        for seg in sorted(B2.keys() - A2.keys()):
            w = wbk[B2[seg]]
            o = owner.get(w["uuid"])
            if o in moved or (w["uuid"] not in owner and seg[0] in hot and seg[1] in hot):
                rehung[o or "drag"] = rehung.get(o or "drag", 0) + 1
            else:
                E("refuse", f"hand-added {kind} {seg[0]}-{seg[1]}")
    for u in sorted(ia.keys() - ib.keys(), key=str):
        d = ia[u]
        o = d.get("owner")
        if o in moved:
            rehung[o] = rehung.get(o, 0) + 1
        else:
            E("refuse", f"generated {d['kind']} {d.get('text', '')!r} at "
                        f"{d.get('at')} deleted")
    for u in sorted(ib.keys() - ia.keys(), key=str):
        d = ib[u]
        if d["kind"] == "junction" and tuple(d["at"][:2]) in hot:
            rehung["drag"] = rehung.get("drag", 0) + 1
        else:
            E("refuse", f"hand-added {d['kind']} {d.get('text', '')!r} at {d.get('at')}")
    for u in sorted(ia.keys() & ib.keys(), key=str):
        da, db = ia[u], ib[u]
        if da.get("at") == db.get("at") and da["sig"] == db["sig"]:
            continue
        o = da.get("owner")
        if o in moved:
            rehung[o] = rehung.get(o, 0) + 1
        elif da["kind"] in TEXTS and da["sig"] == db["sig"]:
            E("absorb", f"text {da['text'][:40]!r} moved {da['at']} -> {db['at']}",
              op="text", text=da["text"], at=db["at"])
        elif da["sig"] == db["sig"]:
            E("refuse", f"generated {da['kind']} {da.get('text', '')!r} moved "
                        f"{da.get('at')} -> {db.get('at')} (its symbol did not move)")
        else:
            E("refuse", f"generated {da['kind']} {da.get('text', '')!r} at "
                        f"{da.get('at')} edited" +
              (f" -> {db.get('text')!r}" if db.get("text") != da.get("text") else ""))
    for o, n in sorted(rehung.items()):
        E("rehang", f"{o}: {n} generated stub item(s) re-hung at the new position"
              if o != "drag" else f"{n} drag segment(s)/junction(s) re-drawn by the generator")
    return out


# ---------------------------------------------------------------- overrides

class Overrides:
    """overrides.json: {"version", "sheets": {SHEET: {"symbols": {"REF/UNIT":
    {"at", "rot", "mirror", "flags", "fields"}}, "texts": {TEXT: [x,y,a]},
    "blocks": {FILE: [x, y]}}}}. SHEET is the sheet path relative to the
    project dir without extension ("rails", "ocxo/ocxo_board")."""

    def __init__(self, data: dict | None = None):
        self.data = data or {"version": VERSION, "sheets": {}}
        self.used: set[tuple] = set()

    @classmethod
    def load(cls, path) -> "Overrides":
        p = pathlib.Path(path)
        return cls(load_json(p) if p.exists() else None)

    def save(self, path) -> None:
        # drop empty containers so the file stays readable
        for sh in list(self.data["sheets"]):
            d = self.data["sheets"][sh]
            for k in list(d):
                for kk in list(d[k]):
                    if d[k][kk] in ({}, None):
                        del d[k][kk]
                if not d[k]:
                    del d[k]
            if not d:
                del self.data["sheets"][sh]
        save_json(self.data, path)

    def _sheet(self, sheet: str) -> dict:
        return self.data["sheets"].setdefault(sheet, {})

    def symbol(self, sheet: str, key: str) -> dict | None:
        self.used.add(("symbols", sheet, key))
        return self.data["sheets"].get(sheet, {}).get("symbols", {}).get(key)

    def text(self, sheet: str, text: str) -> list | None:
        self.used.add(("texts", sheet, text))
        return self.data["sheets"].get(sheet, {}).get("texts", {}).get(text)

    def block(self, sheet: str, file: str) -> list | None:
        self.used.add(("blocks", sheet, file))
        return self.data["sheets"].get(sheet, {}).get("blocks", {}).get(file)

    def stale(self) -> list[str]:
        """Overrides the last generation never asked for (part removed or
        renamed in the script) — kept, reported, never applied."""
        out = []
        for sh, d in self.data["sheets"].items():
            for kind in ("symbols", "texts", "blocks"):
                for k in d.get(kind, {}):
                    if (kind, sh, k) not in self.used:
                        out.append(f"{sh}: {kind[:-1]} {k}")
        return out

    def absorb(self, sheet: str, edits: list[dict], base: dict) -> None:
        """Fold absorb-class edits in; prune entries equal to the script's
        own value (base) so moving a part back removes its override."""
        d = self._sheet(sheet)
        for e in edits:
            if e["cls"] != "absorb":
                continue
            op = e["op"]
            if op == "text":
                d.setdefault("texts", {})[e["text"]] = e["at"]
                continue
            if op == "block":
                d.setdefault("blocks", {})[e["file"]] = e["at"]
                continue
            s = d.setdefault("symbols", {}).setdefault(e["key"], {})
            b = base.get(e["key"])
            if op == "geom":
                if b and (b["at"], b["rot"], b["mirror"]) == (e["at"], e["rot"], e["mirror"]):
                    for z in ("at", "rot", "mirror"):
                        s.pop(z, None)
                else:
                    s.update(at=e["at"], rot=e["rot"], mirror=e["mirror"])
            elif op == "flag":
                fl = s.setdefault("flags", {})
                if b and b["flags"].get(e["flag"]) == e["val"]:
                    fl.pop(e["flag"], None)
                else:
                    fl[e["flag"]] = e["val"]
            elif op == "field":
                f = s.setdefault("fields", {}).setdefault(e["name"], {})
                bf = (b or {}).get("fields", {}).get(e["name"])
                if bf is not None and e["value"] is not None and bf["value"] == e["value"]:
                    f.pop("value", None)
                else:
                    f["value"] = e["value"]
                if e.get("pos"):
                    f.update({z: e["pos"][z] for z in ("at", "hide", "justify")})
            elif op == "fpos":
                f = s.setdefault("fields", {}).setdefault(e["name"], {})
                bf = (b or {}).get("fields", {}).get(e["name"])
                for z, v in e["pos"].items():
                    if bf is not None and bf[z] == v:
                        f.pop(z, None)
                    else:
                        f[z] = v
            for k in ("fields",):
                if k in s:
                    s[k] = {n: v for n, v in s[k].items() if v}


def geometry(ov: dict | None) -> tuple | None:
    """(x, y, rot, mirror) the generator must place at, or None."""
    if not ov or "at" not in ov:
        return None
    return ov["at"][0], ov["at"][1], ov.get("rot", 0), ov.get("mirror", "")


def apply_symbol(node: list, ov: dict | None, geometry_too: bool = False) -> list:
    """Apply flags/fields of an override to a placed symbol node (in place).
    Field positions are relative to the node's CURRENT (at) — set geometry
    first (the generator does, so its stubs follow)."""
    if not ov:
        return node
    if geometry_too and "at" in ov:
        at = sexp.find(node, "at")
        at[1:] = [Sym(f"{ov['at'][0]:g}"), Sym(f"{ov['at'][1]:g}"), Sym(str(ov.get("rot", 0)))]
        m = sexp.find(node, "mirror")
        if m is not None:
            node.remove(m)
        if ov.get("mirror"):
            node.insert(node.index(at) + 1, [Sym("mirror"), Sym(ov["mirror"])])
    for fl, v in ov.get("flags", {}).items():
        n = sexp.find(node, fl)
        if n is not None:
            n[1] = Sym("yes" if v else "no")
    at = sexp.atoms(sexp.find(node, "at"))
    x, y = _f(at[0]), _f(at[1])
    props = {sexp.atoms(p)[0]: p for p in sexp.find_all(node, "property")}
    for name, f in ov.get("fields", {}).items():
        p = props.get(name)
        if "value" in f and f["value"] is None:
            if p is not None:
                node.remove(p)
            continue
        if p is None:
            p = [Sym("property"), QStr.of(name), QStr.of(""),
                 [Sym("at"), Sym(f"{x:g}"), Sym(f"{y:g}"), Sym("0")],
                 [Sym("effects"), [Sym("font"), [Sym("size"), Sym("1.27"), Sym("1.27")]]]]
            last = max(i for i, z in enumerate(node)
                       if isinstance(z, list) and sexp.tag_of(z) == "property")
            node.insert(last + 1, p)
        if "value" in f:
            p[2] = QStr.of(f["value"])
        if "at" in f:
            pa = sexp.find(p, "at")
            pa[1:] = [Sym(f"{round(x + f['at'][0], 3):g}"),
                      Sym(f"{round(y + f['at'][1], 3):g}"), Sym(f"{f['at'][2]:g}")]
        if "hide" in f:
            for h in list(sexp.find_all(p, "hide")):
                p.remove(h)
            eff = sexp.find(p, "effects")
            if eff is not None:
                eff[:] = [z for z in eff if z != "hide" and not
                          (isinstance(z, list) and sexp.tag_of(z) == "hide")]
            if f["hide"]:
                p.insert(p.index(sexp.find(p, "at")) + 1, [Sym("hide"), Sym("yes")])
        if "justify" in f:
            eff = sexp.find(p, "effects")
            if eff is None:
                eff = [Sym("effects"), [Sym("font"), [Sym("size"), Sym("1.27"), Sym("1.27")]]]
                p.append(eff)
            eff[:] = [z for z in eff if not (isinstance(z, list) and sexp.tag_of(z) == "justify")]
            if f["justify"]:
                eff.append([Sym("justify"), *map(Sym, f["justify"])])
    return node


# ---------------------------------------------------------------- netlist

def netlist(root_sch: str) -> dict[str, str]:
    """{"REF.PIN": net} from kicad-cli (the connectivity authority). Auto
    nets (Net-(...), unconnected-(...)) are named by their members so a
    renumbering is not a change."""
    out_dir = pathlib.Path.home() / ".cache/kx_scratch"
    out_dir.mkdir(parents=True, exist_ok=True)
    net = out_dir / f"regen_{_uuid.uuid4().hex[:8]}.net"
    r = subprocess.run(kcli.cmd() + ["sch", "export", "netlist", "-o", str(net),
                                     str(root_sch)], capture_output=True, text=True)
    if r.returncode != 0 or not net.exists():
        raise RuntimeError(f"netlist export failed: {r.stderr.strip()[-300:]}")
    doc = sexp.load_file(str(net))
    net.unlink()
    out = {}
    for n in sexp.walk(doc, "net"):
        name = sexp.atoms(sexp.find(n, "name"))[0]
        nodes = [f"{sexp.atoms(sexp.find(x, 'ref'))[0]}.{sexp.atoms(sexp.find(x, 'pin'))[0]}"
                 for x in sexp.find_all(n, "node")]
        if re.match(r"(Net|unconnected)-\(", name):
            name = "~(" + " ".join(sorted(nodes)) + ")"
        for nd in nodes:
            out[nd] = name
    return out


def connectivity_diff(a: dict, b: dict) -> list[str]:
    out = []
    for nd in sorted(a.keys() | b.keys()):
        if a.get(nd) != b.get(nd):
            out.append(f"{nd}: {a.get(nd)} -> {b.get(nd)}")
    return out


# ---------------------------------------------------------------- session

class Session:
    """One regeneration: guard → absorb → (generator runs, consulting
    .overrides and calling .snapshot) → connectivity → report.

    base_dir   project dir; sheet keys are paths relative to it
    snap_dir   snapshot sidecars (<sheet key>.json, _net/<root key>.json)
    ovr_path   overrides.json
    """

    def __init__(self, base_dir, snap_dir, ovr_path, force: bool = False):
        self.base = pathlib.Path(base_dir).resolve()
        self.snap_dir = pathlib.Path(snap_dir)
        self.ovr_path = pathlib.Path(ovr_path)
        self.force = force
        self.overrides = Overrides.load(self.ovr_path)
        self.log: list[dict] = []
        self.live_locks: list[str] = []
        self.net_before: dict[str, dict] = {}
        self.net_report: dict[str, list[str]] = {}

    def key(self, sch_path) -> str:
        return str(pathlib.Path(sch_path).resolve().relative_to(self.base).with_suffix(""))

    def snap_path(self, sch_path) -> pathlib.Path:
        return self.snap_dir / f"{self.key(sch_path)}.json"

    def guard_locks(self, dirs, saved_by=None) -> None:
        """Refuse while KiCad holds a lock in any of dirs — unless the
        holder is an editor live_sch has just SAVED (saved_by, or the
        KX_REGEN_SAVED_BY env it sets for a generator it runs: 'user@host'
        list). Then the disk is what that editor shows, the generator may
        write, and live_sch reverts the editor onto the result."""
        locks = [l for d in dirs for l in live.lock_files(str(d))]
        ok = set(saved_by or ()) | {
            h for h in os.environ.get(SAVED_ENV, "").split(",") if h}
        self.live_locks = [l for l in locks if live.lock_holder(l) in ok]
        locks = [l for l in locks if l not in self.live_locks]
        if locks:
            who = ", ".join(sorted({live.lock_holder(l) for l in locks}))
            raise Refused(f"KiCad has the project open ({who}) — save and "
                          "close it, or regenerate live (the editor is saved, "
                          "then reverted onto the result: kx live sch regen, "
                          "or the generator's --live)", locks)

    def absorb(self, sheets) -> list[dict]:
        """Diff every sheet against its snapshot; raise Refused (writing
        nothing) on any refuse-class edit unless force. Sheets without a
        snapshot are a refusal too — run bootstrap() once."""
        plan, missing = [], []
        for s in sheets:
            sp = self.snap_path(s)
            if not pathlib.Path(s).exists():
                continue
            if not sp.exists():
                missing.append(self.key(s))
                continue
            snap = load_json(sp)
            plan.append((self.key(s), snap, diff(snap, str(s))))
        if missing and not self.force:
            raise Refused("no snapshot for these sheets — edits there cannot be "
                          "told apart from generated content; bootstrap first "
                          "(only on pristine generator output)", missing)
        refused = [f"{k}: {e['what']}" for k, _, ed in plan for e in ed
                   if e["cls"] == "refuse"]
        if refused and not self.force:
            raise Refused("hand edits that cannot be absorbed — redo them in "
                          "the gen script, or regenerate with --force to "
                          "DISCARD them", refused)
        for k, snap, ed in plan:
            self.overrides.absorb(k, ed, snap.get("base", {}))
            for e in ed:
                self.log.append({"sheet": k, **e,
                                 "cls": "discard" if e["cls"] == "refuse" else e["cls"]})
        self.overrides.save(self.ovr_path)
        return self.log

    def snapshot(self, sch_path, owners=None, base=None) -> None:
        save_compact(snapshot(str(sch_path), owners, base), self.snap_path(sch_path))

    def net_snapshot_path(self, root_sch) -> pathlib.Path:
        return self.snap_dir / "_net" / f"{self.key(root_sch)}.json"

    def connectivity(self, root_sch) -> list[str]:
        """Compare the fresh netlist with the one recorded at the previous
        generation, then record the fresh one."""
        new = netlist(str(root_sch))
        p = self.net_snapshot_path(root_sch)
        old = load_json(p) if p.exists() else None
        d = connectivity_diff(old, new) if old is not None else ["(no previous netlist)"]
        self.net_report[self.key(root_sch)] = d
        save_compact(new, p)
        return d

    def report(self) -> str:
        L = []
        by = {}
        for e in self.log:
            by.setdefault(e["cls"], []).append(f"{e['sheet']}: {e['what']}")
        for cls, title in (("absorb", "absorbed into overrides"),
                           ("rehang", "re-hung by the generator"),
                           ("discard", "DISCARDED (--force)")):
            if by.get(cls):
                L.append(f"{title}:")
                L += [f"  {x}" for x in by[cls]]
        if not self.log:
            L.append("no hand edits since the last generation")
        todo = []
        for sh, d in sorted(self.overrides.data["sheets"].items()):
            snap = load_json(self.snap_dir / f"{sh}.json") if (self.snap_dir / f"{sh}.json").exists() else {}
            base = snap.get("base", {})
            for k, s in sorted(d.get("symbols", {}).items()):
                for n, f in sorted(s.get("fields", {}).items()):
                    if "value" in f:
                        was = base.get(k, {}).get("fields", {}).get(n, {}).get("value")
                        todo.append(f"  {sh} {k}: {n} = {f['value']!r} (script: {was!r})")
                for fl, v in sorted(s.get("flags", {}).items()):
                    todo.append(f"  {sh} {k}: {fl} = {'yes' if v else 'no'}")
        n_geo = sum("at" in s for d in self.overrides.data["sheets"].values()
                    for s in d.get("symbols", {}).values())
        L.append(f"overrides active: {n_geo} position(s), {len(todo)} design value(s)")
        if todo:
            L.append("move into the gen script (then the override prunes itself):")
            L += todo
        st = self.overrides.stale() if self.overrides.used else []
        if st:
            L.append("stale overrides (nothing generated them; kept, not applied):")
            L += [f"  {x}" for x in st]
        for root, d in self.net_report.items():
            if d == ["(no previous netlist)"]:
                L.append(f"connectivity {root}: recorded (no previous netlist)")
                continue
            L.append(f"connectivity {root}: " + ("unchanged" if not d else
                                                 f"{len(d)} node(s) CHANGED"))
            L += [f"  {x}" for x in d[:40]]
        return "\n".join(L)


# ---------------------------------------------------------------- cli

def main(argv: list[str]) -> int:
    """kx regen status|absorb BASE SNAPDIR OVERRIDES [--force]
       kx regen snapshot BASE SNAPDIR SCH...   (no owners: bootstrap only)
       kx regen netlist ROOT.kicad_sch"""
    if not argv:
        print(main.__doc__)
        return 2
    cmd, rest = argv[0], [a for a in argv[1:] if a != "--force"]
    force = "--force" in argv
    if cmd == "netlist" and rest:
        print(json.dumps(netlist(rest[0]), indent=1))
        return 0
    if len(rest) < 2:
        print(main.__doc__)
        return 2
    base, snap_dir = pathlib.Path(rest[0]), pathlib.Path(rest[1])
    if cmd == "snapshot":
        s = Session(base, snap_dir, snap_dir / "unused.json")
        for f in rest[2:]:
            s.snapshot(f)
            print("snapshot", s.snap_path(f))
        return 0
    if len(rest) < 3:
        print(main.__doc__)
        return 2
    s = Session(base, snap_dir, rest[2], force=force)
    sheets = [base / (p.relative_to(snap_dir).with_suffix(".kicad_sch"))
              for p in sorted(snap_dir.rglob("*.json")) if "_net" not in p.parts]
    if cmd == "status":
        bad = 0
        for f in sheets:
            for e in diff(load_json(s.snap_path(f)), str(f)):
                bad += e["cls"] == "refuse"
                print(f"{e['cls']:<7} {s.key(f)}: {e['what']}")
        return 1 if bad else 0
    if cmd == "absorb":
        try:
            s.guard_locks({base} | {f.parent for f in sheets})
            s.absorb(sheets)
        except Refused as r:
            print(f"REFUSED: {r}")
            for x in r.items:
                print(f"  {x}")
            return 1
        print(s.report())
        return 0
    print(main.__doc__)
    return 2
