"""Internet component sourcing + project lib-table registration.

  kx fetch LCSC_ID PROJECT_DIR    easyeda2kicad → symbol+footprint+3D in
                                  PROJECT_DIR/libs/, both lib tables
                                  registered with ${KIPRJMOD} URIs
  kx import-zip ZIP PROJECT_DIR   SnapEDA/UltraLibrarian zip → same

Fetched footprints are GUILTY UNTIL PROVEN: render and compare against
the datasheet land pattern before trusting (kicad-component skill).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import zipfile

from . import sexp
from .sexp import Sym, QStr

VENV_BIN = pathlib.Path(__file__).resolve().parents[1] / ".venv/bin"


def _lib_table(path: pathlib.Path, kind: str) -> list:
    if path.exists():
        return sexp.load_file(str(path))
    return [Sym(kind), [Sym("version"), Sym("7")]]


def register(project_dir: str, table: str, name: str, uri: str) -> bool:
    """Idempotently add a lib row to sym-lib-table / fp-lib-table.
    Returns True if added, False if the name was already registered."""
    kind = "sym_lib_table" if table == "sym" else "fp_lib_table"
    p = pathlib.Path(project_dir) / (
        "sym-lib-table" if table == "sym" else "fp-lib-table")
    t = _lib_table(p, kind)
    for row in sexp.find_all(t, "lib"):
        nm = sexp.find(row, "name")
        if nm is not None and sexp.atoms(nm)[0] == name:
            return False
    t.append([Sym("lib"),
              [Sym("name"), QStr.of(name)],
              [Sym("type"), QStr.of("KiCad")],
              [Sym("uri"), QStr.of(uri)],
              [Sym("options"), QStr.of("")],
              [Sym("descr"), QStr.of("")]])
    sexp.save_file(str(p), t)
    return True


def fetch_lcsc(lcsc_id: str, project_dir: str) -> dict:
    """Download symbol+footprint+3D for an LCSC part into
    PROJECT_DIR/libs/easyeda2kicad.* and register both lib tables."""
    proj = pathlib.Path(project_dir)
    out = proj / "libs/easyeda2kicad"
    out.parent.mkdir(parents=True, exist_ok=True)
    exe = VENV_BIN / "easyeda2kicad"
    cmd = [str(exe) if exe.exists() else "easyeda2kicad", "--full",
           f"--lcsc_id={lcsc_id}", "--output", str(out), "--overwrite",
           "--project-relative"]
    # easyeda2kicad 1.0.1 resolves --project-relative against CWD, so run
    # from the project dir or it crashes on out-of-tree outputs
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=project_dir)
    if r.returncode != 0:
        raise RuntimeError(f"easyeda2kicad failed: {r.stderr or r.stdout}")
    sym = out.with_suffix(".kicad_sym")
    sexp.load_file(str(sym))  # parse gate
    res = {
        "symbol_lib": str(sym),
        "footprint_lib": str(out) + ".pretty",
        "registered_sym": register(project_dir, "sym", "easyeda2kicad",
                                   "${KIPRJMOD}/libs/easyeda2kicad.kicad_sym"),
        "registered_fp": register(project_dir, "fp", "easyeda2kicad",
                                  "${KIPRJMOD}/libs/easyeda2kicad.pretty"),
        "log": (r.stdout + r.stderr).strip().splitlines()[-3:],
    }
    return res


def import_zip(zip_path: str, project_dir: str, name: str = "imported") -> dict:
    """SnapEDA/UltraLibrarian zip → PROJECT_DIR/libs/<name>.kicad_sym +
    <name>.pretty, registered. Legacy .lib symbols are upgraded via
    kicad-cli sym upgrade."""
    proj = pathlib.Path(project_dir)
    libs = proj / "libs"
    pretty = libs / f"{name}.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    syms, mods, legacy = [], 0, []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            fn = pathlib.Path(info.filename).name
            if fn.endswith(".kicad_mod"):
                (pretty / fn).write_bytes(z.read(info))
                mods += 1
            elif fn.endswith(".kicad_sym"):
                syms.append(z.read(info).decode("utf-8", "replace"))
            elif fn.endswith(".lib"):
                legacy.append((fn, z.read(info)))
    target = libs / f"{name}.kicad_sym"
    if syms:
        target.write_text(syms[0])
    elif legacy:
        raw = libs / legacy[0][0]
        raw.write_bytes(legacy[0][1])
        subprocess.run(["flatpak", "run", "--command=kicad-cli",
                        "org.kicad.KiCad", "sym", "upgrade", "--output",
                        str(target), str(raw)], capture_output=True)
        raw.unlink(missing_ok=True)
    out = {"symbols": int(target.exists()), "footprints": mods,
           "legacy_upgraded": len(legacy)}
    if target.exists():
        sexp.load_file(str(target))  # parse gate
        out["registered_sym"] = register(
            project_dir, "sym", name, f"${{KIPRJMOD}}/libs/{name}.kicad_sym")
    if mods:
        out["registered_fp"] = register(
            project_dir, "fp", name, f"${{KIPRJMOD}}/libs/{name}.pretty")
    return out
