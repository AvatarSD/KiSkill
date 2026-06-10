"""Atomic document operations on a parsed .kicad_sch tree.

Each op: precondition → mutation → (caller runs verify/reparse gates
before saving — see kicad-project state machine). Body items are inserted
before (sheet_instances ...) so the file keeps eeschema's section order.
"""

from __future__ import annotations

from . import build, sexp
from .geom import Point


class OpError(ValueError):
    pass


def _insert_body(doc: list, node: list) -> None:
    for i, x in enumerate(doc):
        if isinstance(x, list) and sexp.tag_of(x) == "sheet_instances":
            doc.insert(i, node)
            return
    doc.append(node)


def root_uuid(doc: list) -> str:
    return sexp.atoms(sexp.find(doc, "uuid"))[0]


def refs(doc: list) -> set[str]:
    out = set()
    for s in sexp.find_all(doc, "symbol"):
        for p in sexp.find_all(s, "property"):
            a = sexp.atoms(p)
            if len(a) >= 2 and a[0] == "Reference":
                out.add(a[1])
    return out


def extract_libdef(lib_path: str, name: str, cache_name: str) -> list:
    """Pull one symbol definition out of a .kicad_sym library OR another
    document's lib_symbols cache, renamed for the cache ("Lib:Name").
    extends-resolution TODO."""
    lib = sexp.load_file(lib_path)
    scope = sexp.find(lib, "lib_symbols") or lib
    for s in sexp.find_all(scope, "symbol"):
        if sexp.atoms(s)[0] == name:
            d = sexp.parse(sexp.dumps(s))  # deep copy
            if sexp.find(d, "extends"):
                raise OpError(f"{name} uses (extends) — not resolved yet")
            d[1] = sexp.QStr.of(cache_name)
            return d
    raise OpError(f"symbol {name!r} not in {lib_path}")


def ensure_lib(doc: list, libdef: list) -> None:
    cache = sexp.find(doc, "lib_symbols")
    if cache is None:
        raise OpError("document has no lib_symbols section")
    name = sexp.atoms(libdef)[0]
    if not any(sexp.atoms(s)[0] == name for s in sexp.find_all(cache, "symbol")):
        cache.append(libdef)


def place(doc: list, libdef: list, at: Point, rot: int, ref: str,
          value: str, pin_nums: list[str], unit: int = 1,
          project: str = "kx", **kw) -> None:
    """Place a symbol instance; caches the lib def if missing.
    Precondition: ref must be unique unless multi-unit (same lib_id)."""
    lib_id = sexp.atoms(libdef)[0]
    if ref in refs(doc):
        for s in sexp.find_all(doc, "symbol"):
            same_ref = any(sexp.atoms(p)[:2] == ["Reference", ref]
                           for p in sexp.find_all(s, "property"))
            if same_ref and sexp.atoms(sexp.find(s, "lib_id"))[0] != lib_id:
                raise OpError(f"ref {ref} already used by another lib_id")
    ensure_lib(doc, libdef)
    _insert_body(doc, build.symbol(project, root_uuid(doc), lib_id, at, rot,
                                   ref, value, pin_nums, unit=unit, **kw))


def add_wire(doc: list, a: Point, b: Point) -> None:
    if a == b:
        raise OpError("zero-length wire")
    _insert_body(doc, build.wire(a, b))


def add_junction(doc: list, p: Point) -> None:
    _insert_body(doc, build.junction(p))


def add_label(doc: list, text: str, p: Point) -> None:
    _insert_body(doc, build.label(text, p))


def set_prop(doc: list, ref: str, name: str, value: str) -> int:
    """Set a property on every unit instance of `ref`. Returns count."""
    n = 0
    for s in sexp.find_all(doc, "symbol"):
        if not any(sexp.atoms(p)[:2] == ["Reference", ref]
                   for p in sexp.find_all(s, "property")):
            continue
        hit = False
        for p in sexp.find_all(s, "property"):
            if sexp.atoms(p)[0] == name:
                p[2] = sexp.QStr.of(value)
                hit = True
        if not hit:
            at = sexp.find(s, "at")
            x, y = (float(v) for v in sexp.atoms(at)[:2])
            s.insert(len(s) - 1, build.prop(name, value, x, y, hide=True))
        n += 1
    if n == 0:
        raise OpError(f"no symbol with ref {ref}")
    return n


def delete_ref(doc: list, ref: str) -> int:
    """Remove all unit instances of `ref`. Returns count removed."""
    victims = [s for s in sexp.find_all(doc, "symbol")
               if any(sexp.atoms(p)[:2] == ["Reference", ref]
                      for p in sexp.find_all(s, "property"))]
    for v in victims:
        doc.remove(v)
    if not victims:
        raise OpError(f"no symbol with ref {ref}")
    return len(victims)


def delete_uuid(doc: list, u: str) -> None:
    """Remove a wire/junction/label/symbol by its uuid."""
    for x in doc:
        if isinstance(x, list):
            n = sexp.find(x, "uuid")
            if n is not None and sexp.atoms(n)[0] == u:
                doc.remove(x)
                return
    raise OpError(f"no body item with uuid {u}")
