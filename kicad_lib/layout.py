"""Autorouting bridge: .kicad_pcb → Specctra DSN → freerouting → SES →
back into the board. Fully headless:

  DSN export / SES import — pcbnew Python inside the KiCad flatpak
  (ExportSpecctraDSN / ImportSpecctraSES, verified present in 10.0)
  routing — freerouting 2.2.4 linux-x64 bundle (own JRE) in .tools/

The board must have an Edge.Cuts outline and netted pads, or DSN export
is meaningless. Outputs land next to the inputs in ~/.cache/kx_scratch.
"""

from __future__ import annotations

import pathlib
import subprocess

SCRATCH = pathlib.Path.home() / ".cache/kx_scratch"
FREEROUTING = (pathlib.Path(__file__).resolve().parents[1] /
               ".tools/freerouting-2.2.4-linux-x64/bin/freerouting")


def _pcbnew(code: str) -> str:
    """Run a snippet under the flatpak's pcbnew Python."""
    r = subprocess.run(["flatpak", "run", "--command=python3",
                        "org.kicad.KiCad", "-c", code],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"pcbnew python failed: {r.stderr[-800:]}")
    return r.stdout


def dsn_export(pcb_path: str, dsn_path: str) -> str:
    _pcbnew(
        "import pcbnew\n"
        f"b = pcbnew.LoadBoard({pcb_path!r})\n"
        f"ok = pcbnew.ExportSpecctraDSN(b, {dsn_path!r})\n"
        "print('dsn', ok)\n")
    if not pathlib.Path(dsn_path).exists():
        raise RuntimeError("DSN not produced")
    return dsn_path


def route(dsn_path: str, ses_path: str, passes: int = 20,
          timeout_s: int = 600) -> str:
    """Run freerouting headless. Classic CLI: -de in.dsn -do out.ses."""
    if not FREEROUTING.exists():
        raise RuntimeError(f"freerouting bundle missing at {FREEROUTING}")
    r = subprocess.run(
        [str(FREEROUTING), "-de", dsn_path, "-do", ses_path,
         "-mp", str(passes), "-da", "-dct", "1"],
        capture_output=True, text=True, timeout=timeout_s)
    if not pathlib.Path(ses_path).exists():
        raise RuntimeError(
            f"freerouting produced no SES (rc={r.returncode}): "
            f"{(r.stderr or r.stdout)[-800:]}")
    return ses_path


def ses_import(pcb_path: str, ses_path: str, out_pcb: str) -> str:
    _pcbnew(
        "import pcbnew\n"
        f"b = pcbnew.LoadBoard({pcb_path!r})\n"
        f"ok = pcbnew.ImportSpecctraSES(b, {ses_path!r})\n"
        f"pcbnew.SaveBoard({out_pcb!r}, b)\n"
        "print('ses', ok)\n")
    if not pathlib.Path(out_pcb).exists():
        raise RuntimeError("routed board not saved")
    return out_pcb


def autoroute(pcb_in: str, pcb_out: str, passes: int = 20) -> dict:
    """Full chain; returns artifact paths + routed segment/via counts."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    stem = pathlib.Path(pcb_in).stem
    dsn = str(SCRATCH / f"{stem}.dsn")
    ses = str(SCRATCH / f"{stem}.ses")
    dsn_export(pcb_in, dsn)
    route(dsn, ses, passes)
    ses_import(pcb_in, ses, pcb_out)
    from . import sexp
    board = sexp.load_file(pcb_out)
    return {"dsn": dsn, "ses": ses, "pcb": pcb_out,
            "segments": sum(1 for _ in sexp.find_all(board, "segment")),
            "vias": sum(1 for _ in sexp.find_all(board, "via"))}
