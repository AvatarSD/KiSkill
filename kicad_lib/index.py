"""Component index: every symbol in the official libraries (flatpak or native — kcli.symbols_dir) plus
any project libs, scanned into sqlite FTS5 for instant fuzzy lookup.

  kx index [DIR ...]      (re)scan — mtime-incremental, safe to rerun
  kx find QUERY...        ranked search over name/description/keywords

DB lives at ~/.cache/kx_scratch/symbols.sqlite. Derived symbols
(`extends`) inherit description/keywords from their base when they don't
override them, so "opamp" finds every family member.
"""

from __future__ import annotations

import pathlib
import sqlite3

from . import kcli, sexp

DB = pathlib.Path.home() / ".cache/kx_scratch/symbols.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL);
CREATE TABLE IF NOT EXISTS symbols(
  lib TEXT, name TEXT, description TEXT, keywords TEXT,
  fp_filters TEXT, datasheet TEXT, pins INT, is_power INT, path TEXT,
  PRIMARY KEY (lib, name));
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  lib, name, description, keywords, content=symbols);
"""


def _props(sym: list) -> dict:
    out = {}
    for p in sexp.find_all(sym, "property"):
        a = sexp.atoms(p)
        if len(a) >= 2:
            out[a[0]] = a[1]
    return out


def _scan_lib(path: pathlib.Path) -> list[dict]:
    lib = path.stem
    root = sexp.load_file(str(path))
    rows, defs = [], {}
    for s in sexp.find_all(root, "symbol"):
        defs[sexp.atoms(s)[0]] = s
    for name, s in defs.items():
        pr = _props(s)
        ext = sexp.find(s, "extends")
        base = defs.get(sexp.atoms(ext)[0]) if ext else None
        bpr = _props(base) if base else {}
        pins = sum(1 for _ in sexp.walk(s, "pin")) or (
            sum(1 for _ in sexp.walk(base, "pin")) if base else 0)
        rows.append({
            "lib": lib, "name": name,
            "description": pr.get("Description") or bpr.get("Description", ""),
            "keywords": pr.get("ki_keywords") or bpr.get("ki_keywords", ""),
            "fp_filters": pr.get("ki_fp_filters") or bpr.get("ki_fp_filters", ""),
            "datasheet": pr.get("Datasheet") or bpr.get("Datasheet", ""),
            "pins": pins, "is_power": int(sexp.find(s, "power") is not None),
            "path": str(path),
        })
    return rows


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(_SCHEMA)
    return con


def build(dirs: list[str] | None = None, verbose: bool = True) -> dict:
    """Incremental scan of the official libs (kcli.symbols_dir — flatpak
    or native, KX_KICAD_SYMBOLS override) plus any extra dirs (recursive)."""
    official = kcli.symbols_dir()
    roots = ([official] if official else []) + \
        [pathlib.Path(d) for d in (dirs or [])]
    libs = sorted({p for r in roots if r.exists()
                   for p in r.rglob("*.kicad_sym")})
    con = connect()
    seen = dict(con.execute("SELECT path, mtime FROM files"))
    fresh, n_sym = 0, 0
    for p in libs:
        mt = p.stat().st_mtime
        if seen.get(str(p)) == mt:
            continue
        rows = _scan_lib(p)
        with con:
            con.execute("DELETE FROM symbols WHERE path=?", (str(p),))
            con.executemany(
                "INSERT OR REPLACE INTO symbols VALUES "
                "(:lib,:name,:description,:keywords,:fp_filters,"
                ":datasheet,:pins,:is_power,:path)", rows)
            con.execute("INSERT OR REPLACE INTO files VALUES (?,?)",
                        (str(p), mt))
        fresh += 1
        n_sym += len(rows)
        if verbose and fresh % 40 == 0:
            print(f"  …{fresh} libs scanned")
    with con:
        con.execute("INSERT INTO fts(fts) VALUES('rebuild')")
    total = con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    return {"libs_total": len(libs), "libs_rescanned": fresh,
            "symbols_added": n_sym, "symbols_total": total}


def find(query: str, limit: int = 12) -> list[dict]:
    con = connect()
    q = " ".join(f'"{t}"' for t in query.split())
    try:
        rows = con.execute(
            "SELECT s.lib, s.name, s.description, s.keywords, s.pins,"
            " s.is_power FROM fts JOIN symbols s ON s.rowid = fts.rowid"
            " WHERE fts MATCH ? ORDER BY rank LIMIT ?",
            (q, limit)).fetchall()
    except sqlite3.OperationalError:
        like = f"%{query}%"
        rows = con.execute(
            "SELECT lib, name, description, keywords, pins, is_power"
            " FROM symbols WHERE name LIKE ? OR description LIKE ?"
            " OR keywords LIKE ? LIMIT ?", (like, like, like, limit)).fetchall()
    return [{"lib_id": f"{r[0]}:{r[1]}", "description": r[2],
             "keywords": r[3], "pins": r[4], "power": bool(r[5])}
            for r in rows]
