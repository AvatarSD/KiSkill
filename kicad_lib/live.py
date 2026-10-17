"""Backend adapter: how may this machine touch a KiCad project right now?

  file      always available — sexp edits on disk; REFUSE writes while
            lock files exist (KiCad GUI has the project open)
  ipc       a running KiCad answers on its IPC socket — found by
            ipc_discovery (flatpak + native paths, liveness-checked), a
            `kx live connect --host H` ssh forward, or KICAD_API_SOCKET.
            KiCad 9/10: PCB only. A KiCad 10.0.7 schematic editor answers
            GetOpenDocuments and nothing useful else (measured by
            sch_capabilities(): no GetItems, item create/update rejects
            every schematic type, no save/revert/run_action) — the live
            schematic path on 10 is live_sch (save, regen, revert through
            the editor's own window). Schematic items over IPC: v11
  headless  kipy can spawn its own API server (no GUI) — KiCad 11+;
            on 10 `kx live headless` runs the real editor under Xvfb

`kx env [PROJECT_DIR]` prints the detection result; kicad-project's
session entry protocol starts here. kipy lives in the repo .venv —
import it via venv_python() when driving IPC.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
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


def lock_holder(lck) -> str:
    """'user@host' recorded in a KiCad lock file ('unknown' if unreadable)."""
    try:
        who = json.loads(pathlib.Path(lck).read_text())
        return f"{who.get('username', '?')}@{who.get('hostname', '?')}"
    except (OSError, ValueError):
        return "unknown"


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
    holder = lock_holder(lck)
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


# Non-mutating probe of each schematic call: a request the server has no
# handler for fails with "no handler available"; anything else (a decode
# error, "not open", "not found") means the call is served. Write calls are
# probed with ids/documents that cannot exist, so nothing changes.
_SCH_CAPS = r"""
import json, uuid, kipy
from google.protobuf.any_pb2 import Any
from kipy.proto.common.commands import editor_commands_pb2 as ec
from kipy.proto.common.types import DocumentType, KiCadObjectType
from kipy.proto.schematic import schematic_types_pb2 as sp
k = kipy.KiCad(socket_path='ipc://' + SOCK, timeout_ms=5000)
c = k._client
docs = list(c.send(ec.GetOpenDocuments(type=DocumentType.DOCTYPE_SCHEMATIC),
                   ec.GetOpenDocumentsResponse).documents)
out = {"documents": [d.board_filename for d in docs]}
if docs:
    doc = docs[0]
    ghost = type(doc)(); ghost.CopyFrom(doc)
    ghost.board_filename = "kx-capability-probe-" + uuid.uuid4().hex + ".kicad_sch"
    def served(cmd, resp):
        try:
            c.send(cmd, resp)
            return True
        except Exception as e:
            return "no handler available" not in str(e)
    gi = ec.GetItems(); gi.header.document.CopyFrom(doc)
    gi.types.append(KiCadObjectType.KOT_SCH_SYMBOL)
    out["get_items"] = served(gi, ec.GetItemsResponse)
    ln = sp.Line(); ln.id.value = str(uuid.uuid4())
    ui = ec.UpdateItems(); ui.header.document.CopyFrom(doc)
    a = Any(); a.Pack(ln); ui.items.append(a)
    try:
        r = c.send(ui, ec.UpdateItemsResponse)
        out["item_write"] = not any("decode" in x.status.error_message
                                    for x in r.updated_items)
    except Exception as e:
        out["item_write"] = "no handler available" not in str(e)
    for name in ("SaveDocument", "RevertDocument"):
        m = getattr(ec, name)(); m.document.CopyFrom(ghost)
        from google.protobuf.empty_pb2 import Empty
        out[name] = served(m, Empty)
    s2 = ec.SaveDocumentToString(); s2.document.CopyFrom(doc)
    out["SaveDocumentToString"] = served(s2, ec.SavedDocumentResponse)
    out["RunAction"] = served(ec.RunAction(action="kx.capability.probe"),
                              ec.RunActionResponse)
print(json.dumps(out))
"""


def sch_capabilities(sock: str | None = None) -> dict | None:
    """Which schematic calls the live KiCad serves (see _SCH_CAPS); None
    when no socket / no kipy / no answer. KiCad 10.0.7 (standalone and
    project manager alike): get_items, item_write, SaveDocument,
    RevertDocument, SaveDocumentToString, RunAction all False."""
    sock = sock or ipc_socket()
    if not VENV_PY.exists() or sock is None:
        return None
    try:
        r = subprocess.run([str(VENV_PY), "-c",
                            f"SOCK = {sock!r}\n" + _SCH_CAPS],
                           capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def sch_scope(caps: dict | None) -> str:
    """One word for what IPC can do to an open schematic."""
    if not caps or not caps.get("documents"):
        return "none"
    if caps.get("get_items") and caps.get("item_write"):
        return "items"
    if caps.get("SaveDocument") and caps.get("RevertDocument"):
        return "save+revert"
    return "list-only"


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
    docs = open_documents(sock) if alive else []
    caps = (sch_capabilities(sock) if any(d["type"] == "schematic"
                                          for d in docs) else None)
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
        "open_documents": docs,
        "sch_capabilities": caps,
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
                    "sch": sch_scope(caps) if caps else None,
                    "note": ipc_note},
            "live_sch": {
                "available": bool(shutil.which("xdotool")),
                "note": ("save -> regen -> revert through the open "
                         "editor's window: `kx live sch regen -- CMD`"
                         if shutil.which("xdotool") else
                         "needs xdotool where the editor runs")},
            "headless": {"available": cli_major >= 11,
                         "note": (f"use {NIGHTLY_BIN_DIR}" if nver else
                                  "kipy-spawned server needs KiCad 11+; on "
                                  "10 use `kx live headless` (Xvfb GUI)")},
        },
        "recommended": ("ipc" if alive else "file"),
    }
