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


# the raw binary under /usr/lib/kicad-nightly/bin needs LD_LIBRARY_PATH;
# the /usr/bin wrapper sources kicad-nightly.env first — always use it
NIGHTLY_CLI = pathlib.Path("/usr/bin/kicad-cli-nightly")
NIGHTLY_BIN_DIR = pathlib.Path("/usr/lib/kicad-nightly/bin")


def kicad_version() -> str | None:
    try:
        r = subprocess.run(KICAD_CLI + ["version"], capture_output=True,
                           text=True, timeout=30)
        return r.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def nightly_version() -> str | None:
    """KiCad nightly (apt kicad-nightly pkg) coexists with the flatpak;
    its kicad-cli lives outside PATH. Reports 10.99.x = the v11 nightly."""
    if not NIGHTLY_CLI.exists():
        return None
    try:
        r = subprocess.run([str(NIGHTLY_CLI), "version"],
                           capture_output=True, text=True, timeout=30)
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
    """True iff a running KiCad answers on the IPC socket (via venv kipy).

    A structured ApiError reply ("no handler available …") still proves the
    server is alive — standalone eeschema 10.99 registers the schematic
    handler but not the common one, so Ping itself can be unhandled."""
    sock = ipc_socket()
    if not VENV_PY.exists() or sock is None:
        return False
    code = ("import kipy, kipy.errors\n"
            f"k = kipy.KiCad(socket_path='ipc://{sock}')\n"
            "try:\n"
            "    k.ping()\n"
            "except kipy.errors.ApiError:\n"
            "    pass\n"
            "print('pong')")
    try:
        r = subprocess.run([str(VENV_PY), "-c", code], capture_output=True,
                           text=True, timeout=10)
        return "pong" in r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def open_documents() -> list[dict]:
    """Documents open in the live KiCad: [{type, project, path}, …].
    Empty when no socket / nothing open. Runs kipy in the venv."""
    sock = ipc_socket()
    if not VENV_PY.exists() or sock is None:
        return []
    code = (
        "import json, kipy\n"
        "from kipy.proto.common.types import DocumentType\n"
        f"k = kipy.KiCad(socket_path='ipc://{sock}')\n"
        "out = []\n"
        "for name in ('DOCTYPE_SCHEMATIC', 'DOCTYPE_PCB'):\n"
        "    try:\n"
        "        for d in k.get_open_documents(getattr(DocumentType, name)):\n"
        "            out.append({'type': name.replace('DOCTYPE_', '').lower(),\n"
        "                        'project': d.project.name,\n"
        "                        'path': d.project.path})\n"
        "    except Exception:\n"
        "        pass\n"
        "print(json.dumps(out))")
    try:
        r = subprocess.run([str(VENV_PY), "-c", code], capture_output=True,
                           text=True, timeout=10)
        import json
        return json.loads(r.stdout.strip() or "[]")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []


def detect(project_dir: str | None = None) -> dict:
    def major_of(v: str | None) -> int:
        m = re.match(r"(\d+)\.(\d+)", v or "")
        if not m:
            return 0
        # KiCad pre-release convention: X.99 is the (X+1) nightly
        return int(m.group(1)) + (1 if m.group(2) == "99" else 0)

    ver = kicad_version()
    nver = nightly_version()
    best_major = max(major_of(ver), major_of(nver))
    locks = lock_files(project_dir) if project_dir else []
    sock = ipc_socket()
    alive = ipc_ping() if sock else False
    return {
        "kicad_cli": ver,
        "kicad_nightly": nver,
        "lock_files": locks,
        "ipc_socket": sock,
        "ipc_alive": alive,
        "open_documents": open_documents() if alive else [],
        "kipy": VENV_PY.exists(),
        "backends": {
            "file": {"available": True,
                     "writable_now": not locks,
                     "note": "close/reload KiCad around edits" if locks else ""},
            "ipc": {"available": alive,
                    "scope": ("pcb+sch" if alive and best_major >= 11
                              else ("pcb" if alive else None)),
                    "note": "" if alive else
                            "launch KiCad with the IPC API enabled "
                            "(Preferences > Plugins)"},
            "headless": {"available": best_major >= 11,
                         "note": (f"use {NIGHTLY_BIN_DIR}" if nver else
                                  "needs KiCad 11+ (nightly PPA, sudo)")},
        },
        "recommended": ("ipc" if alive else "file"),
    }
