"""Backend adapter: how may this machine touch a KiCad project right now?

  file      always available — sexp edits on disk; REFUSE writes while
            lock files exist (KiCad GUI has the project open)
  ipc       a running KiCad exposes the IPC socket (KICAD_API_SOCKET or
            default path). KiCad 9/10: PCB only; schematic lands in v11
  headless  kipy can spawn its own API server (no GUI) — KiCad 11+

`kx env [PROJECT_DIR]` prints the detection result; kicad-project's
session entry protocol starts here. kipy lives in the repo .venv —
import it via venv_python() when driving IPC.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

VENV_PY = pathlib.Path(__file__).resolve().parents[1] / ".venv/bin/python"
KICAD_CLI = ["flatpak", "run", "--command=kicad-cli", "org.kicad.KiCad"]


def kicad_version() -> str | None:
    try:
        r = subprocess.run(KICAD_CLI + ["version"], capture_output=True,
                           text=True, timeout=30)
        return r.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def lock_files(project_dir: str) -> list[str]:
    """KiCad GUI lock files: ~NAME.kicad_*.lck (tilde = literal prefix)."""
    return sorted(str(p) for p in pathlib.Path(project_dir).glob("~*.lck"))


def ipc_socket() -> str | None:
    """Explicit env wins; else known default socket locations. Flatpak
    KiCad namespaces /tmp, so its socket surfaces under the app's XDG
    runtime dir on the host."""
    env = os.environ.get("KICAD_API_SOCKET")
    if env:
        return env
    uid = os.getuid()
    for cand in (pathlib.Path("/tmp/kicad/api.sock"),
                 pathlib.Path(f"/run/user/{uid}/app/org.kicad.KiCad/kicad/api.sock")):
        if cand.exists():
            return str(cand)
    return None


def ipc_ping() -> bool:
    """True iff a running KiCad answers on the IPC socket (via venv kipy)."""
    if not VENV_PY.exists() or ipc_socket() is None:
        return False
    code = ("import kipy\n"
            "kipy.KiCad().ping()\n"
            "print('pong')")
    try:
        r = subprocess.run([str(VENV_PY), "-c", code], capture_output=True,
                           text=True, timeout=10)
        return "pong" in r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def detect(project_dir: str | None = None) -> dict:
    ver = kicad_version()
    major = int(m.group(1)) if ver and (m := re.match(r"(\d+)\.", ver)) else 0
    locks = lock_files(project_dir) if project_dir else []
    sock = ipc_socket()
    alive = ipc_ping() if sock else False
    return {
        "kicad_cli": ver,
        "lock_files": locks,
        "ipc_socket": sock,
        "ipc_alive": alive,
        "kipy": VENV_PY.exists(),
        "backends": {
            "file": {"available": True,
                     "writable_now": not locks,
                     "note": "close/reload KiCad around edits" if locks else ""},
            "ipc": {"available": alive,
                    "scope": "pcb" if alive and major <= 10 else
                             ("pcb+sch" if alive else None)},
            "headless": {"available": major >= 11,
                         "note": "" if major >= 11 else
                                 "needs KiCad 11+ (nightly PPA, sudo)"},
        },
        "recommended": ("ipc" if alive else "file"),
    }
