"""A real KiCad 10 PCB editor with no screen: Xvfb + flatpak pcbnew.

KiCad 10 cannot spawn a GUI-less API server (that lands in 11), so live
ops are tested against the actual editor on a virtual X display, never
on the operator's desktop. `kx live headless start BOARD` brings one up
and makes it the live target; `kx live headless stop` tears it down.

What it takes, each item measured on studio (KiCad 10.0.7 flatpak beta):
  - Xvfb: $KX_XVFB, PATH, or a user-local copy unpacked without sudo
    (`apt-get download xvfb && dpkg -x xvfb_*.deb ~/.local/opt/xvfb`;
    the libs it needs ship with any X desktop).
  - Everything the sandbox reads under $HOME (flatpak sees only $HOME;
    its /tmp is private): the board, and the work dir
    ~/.cache/kx_scratch/headless/headless-N.
  - A SCRATCH config (KICAD_CONFIG_HOME): a copy of the user's own config
    with api.enable_server on and the update/data-collection prompts
    marked answered — otherwise the "Welcome to KiCad 10.0" wizard blocks
    the editor forever ("KiCad is not ready to reply"). The user's real
    config is never touched.
  - A PRIVATE TMPDIR inside the sandbox, so the socket cannot collide with
    a KiCad the user runs: /var/tmp/kx-headless-N (= host
    ~/.var/app/org.kicad.KiCad/cache/tmp/kx-headless-N/kicad/api.sock).
  - A private D-Bus whose only service is the flatpak portal: on the user
    bus, GTK's portal Settings.Read calls hang 25 s each with no desktop
    session (3 of them: ~95 s to start); with the private bus, ~7 s.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import signal
import subprocess
import time

from . import ipc_discovery as disc
from . import live

FLATPAK = disc.FLATPAK_ID
HOST_TMP = pathlib.Path.home() / ".var/app" / FLATPAK / "cache/tmp"
PORTAL_SVC = "org.freedesktop.portal.Flatpak.service"
WORK_ROOT = pathlib.Path.home() / ".cache/kx_scratch/headless"


def find_xvfb() -> str | None:
    for c in (os.environ.get("KX_XVFB"), shutil.which("Xvfb"),
              str(pathlib.Path.home() / ".local/opt/xvfb/usr/bin/Xvfb")):
        if c and os.access(c, os.X_OK):
            return c
    return None


def flatpak_kicad() -> tuple[str, str] | None:
    """(branch, version) of the newest installed flatpak KiCad, or None.
    KX_KICAD_FLATPAK_BRANCH pins the branch."""
    if not shutil.which("flatpak"):
        return None
    r = subprocess.run(["flatpak", "list", "--app", "--columns=application,"
                        "branch,version"], capture_output=True, text=True)
    rows = [ln.split("\t") for ln in r.stdout.splitlines()
            if ln.startswith(FLATPAK + "\t")]
    rows = [(b, v) for _, b, v, *_ in rows]
    want = os.environ.get("KX_KICAD_FLATPAK_BRANCH")
    if want:
        rows = [x for x in rows if x[0] == want]
    if not rows:
        return None

    def key(bv):
        nums = [int(n) for n in bv[1].split("-")[0].split(".")
                if n.isdigit()]
        return nums
    return max(rows, key=key)


def missing() -> list[str]:
    """What blocks a headless run here ([] = good to go)."""
    out = []
    if not find_xvfb():
        out.append("Xvfb (no sudo: apt-get download xvfb && dpkg -x "
                   "xvfb_*.deb ~/.local/opt/xvfb)")
    if not flatpak_kicad():
        out.append(f"flatpak {FLATPAK} (KiCad 9+)")
    if not live.VENV_PY.exists():
        out.append("repo .venv with kicad-python (kipy)")
    return out


def _free_display() -> int:
    for n in range(90, 200):
        if not (pathlib.Path(f"/tmp/.X11-unix/X{n}").exists()
                or pathlib.Path(f"/tmp/.X{n}-lock").exists()):
            return n
    raise RuntimeError("no free X display number in :90..:199")


def _patch_json(path: pathlib.Path, edits: dict) -> None:
    d = json.loads(path.read_text()) if path.exists() else {}
    for dotted, val in edits.items():
        node = d
        *parents, leaf = dotted.split(".")
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = val
    path.write_text(json.dumps(d, indent=2))


def scratch_config(root: pathlib.Path, version: str) -> pathlib.Path:
    """KICAD_CONFIG_HOME for the headless editor (see module doc)."""
    mm = ".".join(version.split("-")[0].split(".")[:2])
    dst = root / mm
    if not dst.exists():
        src = pathlib.Path.home() / ".var/app" / FLATPAK / "config/kicad" / mm
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.mkdir(parents=True)
    _patch_json(dst / "kicad_common.json", {
        "api.enable_server": True,
        "do_not_show_again.update_check_prompt": True,
        "do_not_show_again.data_collection_prompt": True})
    _patch_json(dst / "kicad.json", {"pcm.check_for_updates": False,
                                     "system.check_for_kicad_updates": False})
    dbl = dst / "design-block-lib-table"
    if not dbl.exists():
        dbl.write_text("(design_block_lib_table\n  (version 7)\n)\n")
    return root


def _dbus_conf(work: pathlib.Path) -> pathlib.Path | None:
    for d in ("/usr/share/dbus-1/services", "/usr/local/share/dbus-1/services"):
        svc = pathlib.Path(d) / PORTAL_SVC
        if svc.exists():
            sd = work / "dbus-services"
            sd.mkdir(exist_ok=True)
            shutil.copy(svc, sd / PORTAL_SVC)
            conf = work / "dbus-session.conf"
            conf.write_text(
                '<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus '
                'Configuration 1.0//EN"\n "http://www.freedesktop.org/'
                'standards/dbus/1.0/busconfig.dtd">\n<busconfig>\n'
                '  <type>session</type>\n  <keep_umask/>\n'
                '  <listen>unix:tmpdir=/tmp</listen>\n'
                '  <auth>EXTERNAL</auth>\n'
                f'  <servicedir>{sd}</servicedir>\n'
                '  <policy context="default">\n'
                '    <allow send_destination="*" eavesdrop="true"/>\n'
                '    <allow eavesdrop="true"/>\n    <allow own="*"/>\n'
                '  </policy>\n</busconfig>\n')
            return conf
    return None


def _state_path() -> pathlib.Path:
    return disc.runtime_dir() / "headless.json"


def _pids_with_env(marker: str) -> list[int]:
    """Our sandboxed editor: the only processes carrying our TMPDIR."""
    out = []
    for p in pathlib.Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            if marker.encode() in (p / "environ").read_bytes().split(b"\0"):
                out.append(int(p.name))
        except OSError:
            pass
    return out


def editor_for(path: str) -> tuple[str, str]:
    """(flatpak command, open-document type) for a board, a schematic, or
    a project (.kicad_pro: the project manager, which then opens the
    schematic editor — how the operator runs KiCad; its File menu and
    handler set differ from standalone eeschema)."""
    if path.endswith(".kicad_sch"):
        return "eeschema", "schematic"
    if path.endswith(".kicad_pro"):
        return "kicad", "schematic"
    return "pcbnew", "pcb"


def _open_sch_editor(display: int) -> None:
    """Project manager: Ctrl+E = Schematic Editor (needs xdotool)."""
    env = dict(os.environ, DISPLAY=f":{display}")
    r = subprocess.run(["xdotool", "search", "--name", "KiCad"],
                       capture_output=True, text=True, env=env)
    for w in r.stdout.split():
        subprocess.run(["xdotool", "windowfocus", "--sync", w, "key",
                        "ctrl+e"], capture_output=True, env=env, timeout=10)


def start(board: str, timeout_s: float = 180.0) -> dict:
    """Open board (.kicad_pcb) or schematic (.kicad_sch) in a headless
    editor (pcbnew / eeschema); make it the live target."""
    if _state_path().exists():
        raise RuntimeError("a headless editor is already up — "
                           "`kx live headless stop` first")
    gaps = missing()
    if gaps:
        raise RuntimeError("headless KiCad needs: " + "; ".join(gaps))
    board = str(pathlib.Path(board).resolve())
    if not board.startswith(str(pathlib.Path.home()) + "/"):
        raise RuntimeError(f"{board} is outside $HOME — the flatpak sandbox "
                           "only sees $HOME (its /tmp is private); copy the "
                           "board under ~ first")
    if live.gui_lock(board):
        raise RuntimeError(f"{board} is already open in a KiCad editor")
    branch, version = flatpak_kicad()
    command, doctype = editor_for(board)
    n = _free_display()
    # under $HOME: the sandbox cannot see /run/user/... or the host /tmp,
    # and a config it cannot read brings the first-run wizard back
    work = WORK_ROOT / f"headless-{n}"
    work.mkdir(parents=True, exist_ok=True)
    cfg = scratch_config(work / "config", version)
    tmp_name = f"kx-headless-{n}"
    (HOST_TMP / tmp_name).mkdir(parents=True, exist_ok=True)
    sock = HOST_TMP / tmp_name / "kicad/api.sock"
    sock.unlink(missing_ok=True)
    log = open(work / "kicad.log", "w")
    xvfb = subprocess.Popen([find_xvfb(), f":{n}", "-screen", "0",
                             "1600x1000x24", "-nolisten", "tcp"],
                            stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    for _ in range(50):
        if pathlib.Path(f"/tmp/.X11-unix/X{n}").exists():
            break
        time.sleep(0.1)
    cmd = ["flatpak", "run", f"--branch={branch}",
           f"--env=KICAD_CONFIG_HOME={cfg}",
           f"--env=TMPDIR=/var/tmp/{tmp_name}",
           f"--command={command}", FLATPAK, board]
    conf = _dbus_conf(work)
    if conf:
        cmd = ["dbus-run-session", f"--config-file={conf}", "--"] + cmd
    env = dict(os.environ, DISPLAY=f":{n}")
    kicad = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                             env=env, start_new_session=True)
    state = {"kind": "headless", "socket": str(sock), "display": n,
             "board": board, "editor": command, "xvfb_pid": xvfb.pid, "kicad_pid": kicad.pid,
             "tmp_marker": f"TMPDIR=/var/tmp/{tmp_name}",
             "work": str(work), "flatpak_branch": branch,
             "kicad_version": version}
    _state_path().write_text(json.dumps(state))
    deadline = time.monotonic() + timeout_s
    poked = 0.0
    while time.monotonic() < deadline:
        if kicad.poll() is not None:
            stop()
            raise RuntimeError(f"{command} exited ({kicad.returncode}); "
                               f"log: {work / 'kicad.log'}")
        if disc.listening(str(sock)) and any(
                d["type"] == doctype for d in live.open_documents(str(sock))):
            live.write_target({k: state[k] for k in
                               ("kind", "socket", "display", "board")})
            return state
        if (command == "kicad" and disc.listening(str(sock))
                and shutil.which("xdotool")
                and time.monotonic() - poked > 10):
            _open_sch_editor(n)
            poked = time.monotonic()
        time.sleep(1)
    if os.environ.get("KX_HEADLESS_DEBUG") != "1":  # 1: leave it up to look
        stop()
    raise RuntimeError(f"{command} not ready after {timeout_s:.0f}s "
                       f"(a blocking dialog?) — log: {work / 'kicad.log'}")


def status() -> dict | None:
    try:
        st = json.loads(_state_path().read_text())
    except (OSError, ValueError):
        return None
    st["listening"] = disc.listening(st["socket"])
    return st


def stop() -> dict:
    """Kill the headless editor + its Xvfb, drop its locks and target.
    Kill, not quit: a modified board would raise a save prompt nobody can
    answer. The board on disk keeps its last SAVED state."""
    st = status()
    if st is None:
        return {"stopped": False, "reason": "no headless editor"}
    pids = _pids_with_env(st["tmp_marker"])
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    for pid in (st["kicad_pid"], st["xvfb_pid"]):
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(0.5)
    b = pathlib.Path(st["board"])
    for lck in [b.with_name(f"~{b.stem}.{ext}.lck")
                for ext in ("kicad_pro", "kicad_sch", "kicad_pcb")]:
        try:
            who = json.loads(lck.read_text())
            if who.get("hostname") == os.uname().nodename:
                lck.unlink()
        except (OSError, ValueError):
            pass
    pathlib.Path(st["socket"]).unlink(missing_ok=True)
    t = live.read_target()
    if t and t.get("socket") == st["socket"]:
        live.clear_target()
    _state_path().unlink(missing_ok=True)
    return {"stopped": True, "killed": pids, "display": st["display"]}
