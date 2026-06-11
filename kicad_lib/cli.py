"""kx — agent-facing CLI for KiCad file manipulation.

Subcommands grow with the atomic-op set (DESIGN.md §4). Today:
  kx probe FILE.kicad_sch   structured inventory of a schematic
  kx check FILE             parse + round-trip sanity (exit 1 on fail)
  kx power-audit FILE       PWR_FLAG / power-rail driver sanity (advisory;
                            schematic-level — netlist drops flags)
  kx diff REV FILE          triple diff (pixel/semantic/ERC) vs git REV;
                            artifacts under ~/.cache/kx_scratch
  kx index [DIR ...]        (re)build component index (incremental)
  kx find QUERY...          ranked symbol search over the index
  kx fetch LCSC_ID PROJ     download symbol+fp+3D, register lib tables
  kx import-zip ZIP PROJ    import SnapEDA/UltraLibrarian zip, register
  kx env [PROJECT_DIR]      backend detection: file/ipc/headless, locks
  kx fab SCH|- PCB|- OUTDIR fab bundle: BOM + JLC BOM/CPL + gerbers+drill
  kx live CMD [ARGS]        verified atomic ops on the RUNNING KiCad
                            (snap/refs/check/wire/junction/label/text/rm)
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


def power_audit(pr: dict) -> dict:
    """Power-driver sanity from a probe dict — SCHEMATIC-level on purpose.

    Forum lesson (kicad.info t/35552, t/57016): "Input power pin not driven
    by output power pins" is never a wiring fault — it's a missing DRIVER
    DECLARATION. Each power/ground rail needs one driver: a power_out pin
    (regulator output) OR a PWR_FLAG. Fix by adding `power:PWR_FLAG` — NOT
    a ground symbol like PWRGND (a name trap: PWRGND is just a GND graphic,
    PWR_FLAG declares the net driven) — at the rail's PASSIVE source
    (connector/battery/regulator INPUT). Do NOT flag regulator outputs.

    GOTCHA this guards against: `kicad-cli sch export netlist` DROPS
    PWR_FLAG and power-symbol-only nodes, so a rail shows power_in pins
    with no power_out and still passes ERC. Never audit drivers from the
    netlist; read PWR_FLAG instances from the schematic (this does).
    Advisory only — kicad-cli ERC is the authority."""
    power = [s for s in pr["symbols"] if s["lib_id"].startswith("power:")]
    flags = [s for s in power if s["lib_id"] == "power:PWR_FLAG"]
    rails = sorted({s["value"] for s in power
                    if s["lib_id"] != "power:PWR_FLAG"})
    return {
        "power_rails": rails,
        "pwr_flags": len(flags),
        # rails with zero flags MAY still be driven by a regulator
        # power_out pin — hence "possibly", not "definitely"
        "possibly_undriven": rails if not flags else [],
        "note": ("run kicad-cli ERC for the authoritative check; a flagged "
                 "rail here means a PWR_FLAG exists, not that it sits on "
                 "the right net"),
    }


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
    r0 = subprocess.run(["git", "-C", str(p.parent), "rev-parse",
                         "--show-toplevel"], capture_output=True, text=True)
    if r0.returncode != 0:
        raise RuntimeError(
            f"kx diff needs {p} inside a git repo (baseline = git revision); "
            "for two loose files use kicad_lib.diff primitives directly")
    top = r0.stdout.strip()
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
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "index":
        from . import index
        json.dump(index.build(argv[1:]), sys.stdout, indent=1)
        print()
        return 0
    if argv[0] == "find" and len(argv) >= 2:
        from . import index
        json.dump(index.find(" ".join(argv[1:])), sys.stdout, indent=1)
        print()
        return 0
    if argv[0] == "fetch" and len(argv) >= 3:
        from . import source
        json.dump(source.fetch_lcsc(argv[1], argv[2]), sys.stdout, indent=1)
        print()
        return 0
    if argv[0] == "import-zip" and len(argv) >= 3:
        from . import source
        json.dump(source.import_zip(argv[1], argv[2]), sys.stdout, indent=1)
        print()
        return 0
    if argv[0] == "env":
        from . import live
        json.dump(live.detect(argv[1] if len(argv) > 1 else None),
                  sys.stdout, indent=1)
        print()
        return 0
    if argv[0] == "live":
        # kipy lives in the repo .venv — re-exec there (idempotent)
        import os
        import pathlib
        import subprocess
        from . import live
        if not live.VENV_PY.exists():
            print("repo .venv missing — run tools/bootstrap_kipy.sh")
            return 2
        if pathlib.Path(sys.executable) != live.VENV_PY:
            root = pathlib.Path(__file__).resolve().parents[1]
            env = dict(os.environ, PYTHONPATH=str(root))
            return subprocess.run(
                [str(live.VENV_PY), "-m", "kicad_lib.live_ops", *argv[1:]],
                env=env).returncode
        from . import live_ops
        return live_ops.main(argv[1:])
    if argv[0] == "fab" and len(argv) >= 4:
        from . import fab
        json.dump(fab.bundle(None if argv[1] == "-" else argv[1],
                             None if argv[2] == "-" else argv[2],
                             argv[3]), sys.stdout, indent=1)
        print()
        return 0
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
    if cmd == "power-audit":
        json.dump(power_audit(probe(path)), sys.stdout, indent=1)
        print()
        return 0
    if cmd == "diff" and len(argv) >= 3:
        json.dump(diff_rev(argv[1], argv[2]), sys.stdout, indent=1)
        print()
        return 0
    print(f"unknown command {cmd!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
