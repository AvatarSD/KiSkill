"""Fabrication outputs via kicad-cli (forum pain point #2): grouped BOM,
JLCPCB-format BOM/CPL, gerbers + drill. All functions raise on a
non-zero kicad-cli exit and return produced file paths.
"""

from __future__ import annotations

import pathlib
import subprocess

from . import kcli


def _run(args: list[str]) -> str:
    r = subprocess.run(kcli.cmd() + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"kicad-cli {' '.join(args[:3])} failed: "
                           f"{r.stderr or r.stdout}")
    return r.stdout


def bom(sch: str, out_csv: str) -> str:
    """Grouped engineering BOM: one row per (Value, Footprint)."""
    _run(["sch", "export", "bom", "--output", out_csv,
          "--fields", "Reference,Value,Footprint,QUANTITY,DNP",
          "--labels", "Refs,Value,Footprint,Qty,DNP",
          "--group-by", "Value,Footprint", sch])
    return out_csv


def bom_jlc(sch: str, out_csv: str) -> str:
    """JLCPCB assembly BOM: Comment,Designator,Footprint,LCSC Part #.
    Parts need an 'LCSC' field on the symbol (ops.set_prop adds it)."""
    _run(["sch", "export", "bom", "--output", out_csv,
          "--fields", "Value,Reference,Footprint,LCSC",
          "--labels", "Comment,Designator,Footprint,LCSC Part #",
          "--group-by", "Value,Footprint", "--exclude-dnp", sch])
    return out_csv


def gerbers(pcb: str, out_dir: str) -> list[str]:
    """Gerbers + excellon drill into out_dir."""
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _run(["pcb", "export", "gerbers", "--output", str(out) + "/", pcb])
    _run(["pcb", "export", "drill", "--output", str(out) + "/",
          "--format", "excellon", pcb])
    return sorted(str(p) for p in out.iterdir())


def pos_jlc(pcb: str, out_csv: str) -> str:
    """Placement file (CPL), csv mm both sides."""
    _run(["pcb", "export", "pos", "--output", out_csv, "--format", "csv",
          "--units", "mm", "--side", "both", pcb])
    return out_csv


def bundle(sch: str | None, pcb: str | None, out_dir: str) -> dict:
    """Everything a fab house wants, in one directory."""
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    res: dict = {}
    if sch:
        res["bom"] = bom(sch, str(out / "bom.csv"))
        res["bom_jlc"] = bom_jlc(sch, str(out / "bom_jlc.csv"))
    if pcb:
        res["gerbers"] = gerbers(pcb, str(out / "gerbers"))
        res["cpl"] = pos_jlc(pcb, str(out / "cpl.csv"))
    return res
