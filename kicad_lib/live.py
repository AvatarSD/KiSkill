"""Backend adapter: how may this machine touch a KiCad project right now?

  file      always available — sexp edits on disk; REFUSE writes while
            lock files exist (KiCad GUI has the project open)
  ipc       a running KiCad answers on its IPC socket — found by
            ipc_discovery (flatpak + native paths, liveness-checked), a
            `kx live connect --host H` ssh forward, or KICAD_API_SOCKET.
            KiCad 9/10: PCB only; schematic lands in v11
  headless  kipy can spawn its own API server (no GUI) — KiCad 11+

`kx env [PROJECT_DIR]` prints the detection result; kicad-project's
session entry protocol starts here. kipy lives in the repo .venv —
import it via venv_python() when driving IPC.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess

from . import ipc_discovery as disc
from . import kcli

VENV_PY = pathlib.Path(__file__).resolve().parents[1] / ".venv/bin/python"


# the raw binary under /usr/lib/kicad-nightly/bin needs LD_LIBRARY_PATH;
# the /usr/bin wrapper sources kicad-nightly.env first — always use it
NIGHTLY_CLI = pathlib.Path("/usr/bin/kicad-cli-nightly")
NIGHTLY_BIN_DIR = pathlib.Path("/usr/lib/kicad-nightly/bin")


def kicad_version() -> str | None:
    try:
        r = subprocess.run(kcli.cmd() + ["version"], capture_output=True,
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


class HeldByGui(RuntimeError):
    """A file write was refused because a KiCad GUI holds the file."""


def gui_lock(path: str) -> pathlib.Path | None:
    """KiCad's lock for one file: ~NAME.EXT.lck beside it (it holds
    {"hostname","username"} — a GUI on another host via sshfs shows up
    too). None when absent."""
    p = pathlib.Path(path)
    lck = p.with_name(f"~{p.name}.lck")
    return lck if lck.exists() else None


def refuse_if_gui_holds(path: str) -> None:
    """Raise HeldByGui for a .kicad_pcb whose editor is open: a file write
    under a running GUI is lost on its next save, or clobbers the user's
    unsaved work. Live mode edits the open board instead. KX_IGNORE_LOCK=1
    overrides (a stale lock after a crash)."""
    if not str(path).endswith(".kicad_pcb") or os.environ.get(
            "KX_IGNORE_LOCK") == "1":
        return
    lck = gui_lock(path)
    if lck is None:
        return
    try:
        who = json.loads(lck.read_text())
        holder = f"{who.get('username', '?')}@{who.get('hostname', '?')}"
    except (OSError, ValueError):
        holder = "unknown"
    raise HeldByGui(
        f"refusing to write {path}: the KiCad PCB editor has it open "
        f"({holder}, {lck.name}). Edit the open board with "
        "`kx live pcb ...` (one undo step per op), or close the editor. "
        "Stale lock after a crash: KX_IGNORE_LOCK=1.")


# ---------------------------------------------------------------- socket

def target_path() -> pathlib.Path:
    return disc.runtime_dir() / "target.json"


def read_target() -> dict | None:
    """The socket `kx live connect` / `kx live headless` selected."""
    try:
        return json.loads(target_path().read_text())
    except (OSError, ValueError):
        return None


def write_target(t: dict) -> None:
    target_path().write_text(json.dumps(t))


def clear_target() -> None:
    target_path().unlink(missing_ok=True)


def ipc_socket_info() -> dict:
    """Which socket kx will talk to, and why. Priority: KICAD_API_SOCKET
    (explicit) > the selected target (remote forward / headless) if it
    listens > the first LISTENING local socket (flatpak, then native).
    A socket file nobody listens on is reported as stale, never used."""
    env = os.environ.get("KICAD_API_SOCKET")
    rep = disc.report()
    if env:
        path = env[len("ipc://"):] if env.startswith("ipc://") else env
        return dict(rep, socket=path, source="env")
    t = read_target()
    if t and t.get("socket") and disc.listening(t["socket"]):
        return dict(rep, socket=t["socket"],
                    source=t.get("kind", "target"), target=t)
    if t:
        rep["stale_target"] = t
    return rep


def ipc_socket() -> str | None:
    return ipc_socket_info()["socket"]


def ipc_probe(sock: str | None = None) -> dict:
    """{alive, version} from the KiCad on the socket (via venv kipy).

    A structured ApiError reply ("no handler available …", "not ready")
    still proves the server is alive — standalone eeschema 10.99 registers
    the schematic handler but not the common one, so Ping itself can be
    unhandled; version is then None."""
    sock = sock or ipc_socket()
    if not VENV_PY.exists() or sock is None:
        return {"alive": False, "version": None}
    code = ("import json, kipy, kipy.errors\n"
            f"k = kipy.KiCad(socket_path='ipc://{sock}', timeout_ms=3000)\n"
            "v = None\n"
            "try:\n"
            "    v = str(k.get_version())\n"
            "except kipy.errors.ApiError:\n"
            "    pass\n"
            "print(json.dumps({'alive': True, 'version': v}))")
    try:
        r = subprocess.run([str(VENV_PY), "-c", code], capture_output=True,
                           text=True, timeout=15)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return {"alive": False, "version": None}


def ipc_ping() -> bool:
    return ipc_probe()["alive"]


def open_documents(sock: str | None = None) -> list[dict]:
    """Documents open in the live KiCad: [{type, project, path}, …].
    Empty when no socket / nothing open. Runs kipy in the venv."""
    sock = sock or ipc_socket()
    if not VENV_PY.exists() or sock is None:
        return []
    code = (
        "import json, kipy\n"
        "from kipy.proto.common.types import DocumentType\n"
        f"k = kipy.KiCad(socket_path='ipc://{sock}', timeout_ms=3000)\n"
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


def major_of(v: str | None) -> int:
    m = re.match(r"(\d+)\.(\d+)", v or "")
    if not m:
        return 0
    # KiCad pre-release convention: X.99 is the (X+1) nightly
    return int(m.group(1)) + (1 if m.group(2) == "99" else 0)


def ipc_scope(server_version: str | None, cli_major: int) -> str | None:
    """What the live server can edit. The SERVER's version decides (a
    KiCad 10 GUI with a v11 kicad-cli installed is still pcb-only); the
    local kicad-cli is a fallback when the server hides its version."""
    major = major_of(server_version) or cli_major
    return "pcb+sch" if major >= 11 else "pcb"


def detect(project_dir: str | None = None) -> dict:
    ver = kicad_version()
    nver = nightly_version()
    cli_major = max(major_of(ver), major_of(nver))
    locks = lock_files(project_dir) if project_dir else []
    info = ipc_socket_info()
    sock = info["socket"]
    probe = ipc_probe(sock) if sock else {"alive": False, "version": None}
    alive = probe["alive"]
    if alive:
        ipc_note = ""
    elif sock:
        ipc_note = f"socket {sock} does not answer"
    else:
        ipc_note = disc.why_not(info)
    return {
        "kicad_cli": ver,
        "kicad_nightly": nver,
        "lock_files": locks,
        "ipc_socket": sock,
        "ipc_source": info.get("source"),
        "ipc_alive": alive,
        "ipc_server_version": probe["version"],
        "ipc_stale_sockets": info.get("stale", []),
        "ipc_target": info.get("target") or info.get("stale_target"),
        "api_server_settings": info.get("api_settings", []),
        "open_documents": open_documents(sock) if alive else [],
        "kipy": VENV_PY.exists(),
        "backends": {
            "file": {"available": True,
                     "writable_now": not locks,
                     "note": ("KiCad holds this project: .kicad_pcb writes "
                              "are refused — use `kx live pcb`" if locks
                              else "")},
            "ipc": {"available": alive,
                    "scope": ipc_scope(probe["version"], cli_major)
                    if alive else None,
                    "note": ipc_note},
            "headless": {"available": cli_major >= 11,
                         "note": (f"use {NIGHTLY_BIN_DIR}" if nver else
                                  "kipy-spawned server needs KiCad 11+; on "
                                  "10 use `kx live headless` (Xvfb GUI)")},
        },
        "recommended": ("ipc" if alive else "file"),
    }
