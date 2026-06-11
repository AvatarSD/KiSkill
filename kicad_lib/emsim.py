"""EM field simulation via gerber2ems (Antmicro) + openEMS, dockerized.

STATUS: runner is interface-complete; first full smoke sim pending the
docker image build (compiles openEMS at gerber2ems' pinned commit).

Workdir layout gerber2ems expects (matches kx fab naming):
  WORK/fab/NAME-<Layer>.gbr ('.'->'_' in layer), NAME-PTH.drl,
  NAME-pos.csv, stackup.json (KiCad stackup-export format),
  simulation.json (ports/frequency/grid)
Outputs: ems/ S-parameter CSVs + charts; --export-field E-field PNGs /
VTR dumps — the PNGs are the agent-readable feedback channel.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

from . import fab, sexp

IMAGE = "gerber2ems"

SIM_TEMPLATE = {
    "format_version": "1.2",
    "frequency": {"start": 2e8, "stop": 6e9},
    "max_steps": 200000,
    "pixel_size": 2.5,
    "ports": [],   # fill from positions: width/length um, impedance, layer
    "traces": [],
    "grid": {"inter_layers": 4, "optimal": 40.0, "diagonal": 40.0,
             "perpendicular": 200.0, "max": 500.0,
             "margin": {"xy": 2400.0, "z": 1000.0}},
}


def stackup_from_board(pcb_path: str) -> dict:
    """Translate the board's (setup (stackup ...)) into KiCad's
    stackup-export JSON shape that gerber2ems consumes."""
    board = sexp.load_file(pcb_path)
    setup = sexp.find(board, "setup")
    stk = sexp.find(setup, "stackup") if setup else None
    layers = []
    if stk:
        for l in sexp.find_all(stk, "layer"):
            a = sexp.atoms(l)
            ent = {"name": a[0], "type": "", "color": None,
                   "thickness": None, "material": None, "epsilon": None,
                   "lossTangent": None,
                   "user-name": a[0].replace(".", "_")}
            for k, key in (("type", "type"), ("thickness", "thickness"),
                           ("material", "material"),
                           ("epsilon_r", "epsilon"),
                           ("loss_tangent", "lossTangent")):
                n = sexp.find(l, k)
                if n is not None:
                    v = sexp.atoms(n)[0]
                    ent[key] = float(v) if key in (
                        "thickness", "epsilon", "lossTangent") else v
            layers.append(ent)
    if not layers:
        # KiCad omits (stackup) until Board Setup customizes it —
        # synthesize the default 2-layer 1.6 mm FR-4 stack
        def ent(name, typ, thick=None, eps=None, tan=None, mat=None):
            return {"name": name, "type": typ, "color": None,
                    "thickness": thick, "material": mat, "epsilon": eps,
                    "lossTangent": tan,
                    "user-name": name.replace(".", "_")}
        layers = [
            ent("F.SilkS", "Top Silk Screen"),
            ent("F.Paste", "Top Solder Paste"),
            ent("F.Mask", "Top Solder Mask", 0.01),
            ent("F.Cu", "copper", 0.035),
            ent("dielectric 1", "core", 1.51, 4.5, 0.02, "FR4"),
            ent("B.Cu", "copper", 0.035),
            ent("B.Mask", "Bottom Solder Mask", 0.01),
            ent("B.Paste", "Bottom Solder Paste"),
            ent("B.SilkS", "Bottom Silk Screen"),
        ]
    return {"layers": layers}


def prepare(pcb_path: str, workdir: str) -> dict:
    """Lay out a gerber2ems workdir from a board: fab outputs + stackup
    + simulation.json template (ports must be filled before run())."""
    wd = pathlib.Path(workdir)
    (wd / "fab").mkdir(parents=True, exist_ok=True)
    fab.gerbers(pcb_path, str(wd / "fab"))
    fab.pos_jlc(pcb_path, str(wd / "fab" /
                              (pathlib.Path(pcb_path).stem + "-pos.csv")))
    (wd / "stackup.json").write_text(
        json.dumps(stackup_from_board(pcb_path), indent=2))
    simf = wd / "simulation.json"
    if not simf.exists():
        simf.write_text(json.dumps(SIM_TEMPLATE, indent=2))
    return {"workdir": str(wd),
            "todo": "fill simulation.json ports/traces, then run()"}


def run(workdir: str, export_field: bool = False,
        timeout_s: int = 3600) -> dict:
    """Run the dockerized pipeline: geometry → FDTD sim → postprocess."""
    # mount at a SUBPATH — mounting over /home/docker would shadow the
    # image's ~/.local/bin and the openEMS venv (entrypoint vanishes)
    args = ["docker", "run", "--rm", "-v",
            f"{pathlib.Path(workdir).resolve()}:/home/docker/sim",
            "-w", "/home/docker/sim", IMAGE, "-a"]
    if export_field:
        args.append("--export-field")
    r = subprocess.run(args, capture_output=True, text=True,
                       timeout=timeout_s)
    out = pathlib.Path(workdir) / "ems"
    arts = sorted(str(p) for p in out.rglob("*")
                  if p.suffix in (".csv", ".png")) if out.exists() else []
    if r.returncode != 0 and not arts:
        raise RuntimeError(f"gerber2ems failed: {(r.stderr or r.stdout)[-800:]}")
    return {"rc": r.returncode, "artifacts": arts,
            "field_pngs": [a for a in arts if a.endswith(".png")]}
