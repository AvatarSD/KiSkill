"""kx — agent-facing CLI for KiCad file manipulation.

Subcommands grow with the atomic-op set (DESIGN.md §4). Today:
  kx probe FILE.kicad_sch   structured inventory of a schematic
  kx check FILE             parse + round-trip sanity (exit 1 on fail)
  kx diff REV FILE          triple diff (pixel/semantic/ERC) vs git REV;
                            artifacts under ~/.cache/kx_scratch
"""

from __future__ import annotations

import json
import sys

from . import sexp


def _sym_summary(s: list) -> dict:
    props = {
        sexp.atoms(p)[0]: sexp.atoms(p)[1]
        for p in sexp.find_all(s, "property")
        if len(sexp.atoms(p)) >= 2
    }
    at = sexp.find(s, "at")
    lib = sexp.find(s, "lib_id")
    return {
        "ref": props.get("Reference", "?"),
        "value": props.get("Value", "?"),
        "lib_id": sexp.atoms(lib)[0] if lib else "?",
        "at": [float(x) for x in sexp.atoms(at)[:2]] if at else None,
        "rot": float(sexp.atoms(at)[2]) if at and len(sexp.atoms(at)) > 2 else 0.0,
        "unit": int(sexp.atoms(u)[0]) if (u := sexp.find(s, "unit")) else 1,
        "mirror": sexp.atoms(m)[0] if (m := sexp.find(s, "mirror")) else "",
    }


def probe(path: str) -> dict:
    root = sexp.load_file(path)
    body = {t: list(sexp.find_all(root, t)) for t in (
        "symbol", "wire", "label", "global_label", "hierarchical_label",
        "junction", "no_connect", "sheet", "text",
    )}
    libs = sexp.find(root, "lib_symbols")
    out = {
        "file": path,
        "version": sexp.atoms(v)[0] if (v := sexp.find(root, "version")) else "?",
        "uuid": sexp.atoms(u)[0] if (u := sexp.find(root, "uuid")) else "?",
        "paper": sexp.atoms(p)[0] if (p := sexp.find(root, "paper")) else "?",
        "lib_symbols_cached": [
            sexp.atoms(s)[0] for s in sexp.find_all(libs, "symbol")
        ] if libs else [],
        "counts": {k: len(v) for k, v in body.items()},
        "symbols": [_sym_summary(s) for s in body["symbol"]],
        "labels": sorted({
            sexp.atoms(l)[0]
            for t in ("label", "global_label", "hierarchical_label")
            for l in body[t]
        }),
        "sheets": [
            {
                "name": next((sexp.atoms(p)[1] for p in sexp.find_all(sh, "property")
                              if sexp.atoms(p)[0] == "Sheetname"), "?"),
                "file": next((sexp.atoms(p)[1] for p in sexp.find_all(sh, "property")
                              if sexp.atoms(p)[0] == "Sheetfile"), "?"),
            }
            for sh in body["sheet"]
        ],
    }
    return out


def check(path: str) -> bool:
    text = open(path, encoding="utf-8").read()
    tree = sexp.parse(text)
    return list(sexp.tokens(tree)) == list(sexp.tokens(sexp.parse(sexp.dumps(tree))))


def diff_rev(rev: str, path: str) -> dict:
    """Triple diff of working-tree `path` against git revision `rev`."""
    import pathlib
    import subprocess

    from . import diff

    p = pathlib.Path(path).resolve()
    top = subprocess.run(["git", "-C", str(p.parent), "rev-parse",
                          "--show-toplevel"], capture_output=True,
                         text=True, check=True).stdout.strip()
    rel = p.relative_to(top)
    diff.SCRATCH.mkdir(parents=True, exist_ok=True)
    base = diff.SCRATCH / f"base_{p.name}"
    blob = subprocess.run(["git", "-C", top, "show", f"{rev}:{rel}"],
                          capture_output=True, text=True, check=True).stdout
    base.write_text(blob)

    pr_a, pr_b = probe(str(base)), probe(str(p))
    png_a = diff.render_png(str(base), str(diff.SCRATCH / "base.png"))
    png_b = diff.render_png(str(p), str(diff.SCRATCH / "new.png"))
    out = {
        "semantic": diff.semantic_diff(pr_a, pr_b),
        "pixel": diff.pixel_diff(png_a, png_b,
                                 str(diff.SCRATCH / "diff.png"),
                                 paper=pr_b["paper"]),
        **diff.erc_diff(diff.erc_report(str(base), "base"),
                        diff.erc_report(str(p), "new")),
        "artifacts": {"base_png": png_a, "new_png": png_b},
    }
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, path = argv[0], argv[1]
    if cmd == "probe":
        json.dump(probe(path), sys.stdout, indent=1)
        print()
        return 0
    if cmd == "check":
        ok = check(path)
        print("OK" if ok else "FAIL", path)
        return 0 if ok else 1
    if cmd == "diff" and len(argv) >= 3:
        json.dump(diff_rev(argv[1], argv[2]), sys.stdout, indent=1)
        print()
        return 0
    print(f"unknown command {cmd!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
