"""Regenerate a schematic the operator has OPEN, without closing KiCad.

    save  ->  absorb + generate + snapshot  ->  revert

1. SAVE every open schematic editor of the project(s), so the operator's
   unsaved GUI edits reach the disk (x11_editor.save: Ctrl+S, verified by
   the "*" leaving the title). An editor that cannot be saved refuses the
   whole run — nothing is written while the editor holds edits we could
   not get to disk.
2. Run the generator with KX_REGEN_SAVED_BY=<user@host,...>: regen's lock
   guard lets exactly those (just-saved) editors' locks through, absorbs
   their edits into the overrides by its usual rules, generates, writes
   the snapshots.
3. REVERT each editor (File > Revert, verified) when any sheet on disk
   changed, so it reloads the result in place: same window, the new
   content on screen. Reverting cannot lose work: step 1 saved it and
   step 2 absorbed it (or refused, writing nothing).

Why not IPC: KiCad 10.0.7 serves no schematic item read/write and no
save/revert over IPC (live.sch_capabilities measures it per server). When
a KiCad serves SaveDocument + RevertDocument for schematics (v11), steps
1 and 3 can move to IPC; the flow is unchanged.

Where the editor runs: the lock file names its host. The local host
(including `kx live headless` under Xvfb) is driven directly; another
host (the operator's desktop over sshfs) through `ssh HOST python3 -`
with x11_editor.py piped in — xdotool must be installed THERE. --host
overrides the ssh target (e.g. an ssh alias).

Stdlib only (the generator side runs under system python).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import shlex
import socket
import subprocess
import sys

from . import live, x11_editor
from .regen import SAVED_ENV

X11_SRC = pathlib.Path(x11_editor.__file__)


class NotLive(RuntimeError):
    """No open editor can be driven: the caller falls back to the plain
    (refuse-on-lock) regeneration. Raised only BEFORE anything is
    written."""


class ReloadFailed(RuntimeError):
    """The sheets were regenerated but an editor could not be reverted
    onto them: the operator must File > Revert by hand BEFORE saving
    (a save would write the editor's older copy back)."""


def held(dirs) -> list[dict]:
    """Projects KiCad holds in dirs: [{dir, stem, holder, host, sch,
    locks}] — sch is True when the SCHEMATIC is open (a .kicad_sch lock),
    False when only the project manager holds the project."""
    out = {}
    for d in dirs:
        for lck in live.lock_files(str(d)):
            name = pathlib.Path(lck).name[1:-len(".lck")]   # NAME.kicad_x
            stem, ext = name.rsplit(".", 1)
            e = out.setdefault((str(d), stem), {
                "dir": str(d), "stem": stem, "holder": live.lock_holder(lck),
                "sch": False, "locks": []})
            e["locks"].append(lck)
            e["sch"] |= ext == "kicad_sch"
    for e in out.values():
        e["host"] = e["holder"].rsplit("@", 1)[-1]
    return list(out.values())


def _is_local(host: str) -> bool:
    return host in (socket.gethostname(), os.uname().nodename, "localhost")


def call(action: str, stem: str, host: str | None = None,
         display: str | None = None) -> dict:
    """x11_editor ACTION on this host, or on host via ssh."""
    args = [action, stem] + ([display] if display else [])
    if host is None:
        try:
            return {"find": x11_editor.find, "save": x11_editor.save,
                    "revert": x11_editor.revert}[action](stem, display)
        except x11_editor.EditorError as e:
            raise NotLive(str(e)) from e
    cmd = ["ssh", "-o", "BatchMode=yes",
           *shlex.split(os.environ.get("KX_SSH_OPTS", "")), host,
           "python3", "-", *map(shlex.quote, args)]
    try:
        r = subprocess.run(cmd, input=X11_SRC.read_text(),
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise NotLive(f"ssh {host}: {e}") from e
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise NotLive(f"ssh {host} {action}: {r.stderr.strip()[-300:]}")
    if r.returncode != 0:
        raise NotLive(f"{host}: {out.get('error', out)}")
    return out


def _digest(dirs) -> dict[str, str]:
    out = {}
    for d in dirs:
        for f in sorted(pathlib.Path(d).glob("*.kicad_sch")):
            out[str(f)] = hashlib.sha1(f.read_bytes()).hexdigest()
    return out


class Live:
    """The open editors of dirs and how to reach them."""

    def __init__(self, dirs, host: str | None = None,
                 display: str | None = None):
        self.dirs = [str(d) for d in dirs]
        self.projects = held(self.dirs)
        self.display = display
        for p in self.projects:
            p["via"] = host if host else (None if _is_local(p["host"])
                                          else p["host"])
        self.log: list[str] = []

    @property
    def editors(self) -> list[dict]:
        return [p for p in self.projects if p["sch"]]

    def holders(self) -> list[str]:
        return sorted({p["holder"] for p in self.projects})

    def find(self) -> list[dict]:
        return [dict(p, window=call("find", p["stem"], p["via"],
                                    self.display)) for p in self.editors]

    def save(self) -> None:
        if not self.projects:
            raise NotLive("KiCad does not hold these projects — "
                          "regenerate the plain way")
        for p in self.editors:
            r = call("save", p["stem"], p["via"], self.display)
            where = p["via"] or "local"
            self.log.append(f"{p['stem']} ({where}): " + (
                "saved the editor's unsaved edits" if r["saved"]
                else "editor had no unsaved edits"))
        for p in self.projects:
            if not p["sch"]:
                self.log.append(f"{p['stem']}: only the project manager "
                                "holds it (no schematic editor to reload)")

    def revert(self) -> None:
        for p in self.editors:
            r = call("revert", p["stem"], p["via"], self.display)
            self.log.append(f"{p['stem']}: editor reloaded ({r['title']})")


@contextlib.contextmanager
def session(dirs, host: str | None = None, display: str | None = None):
    """with session(dirs) as lv: <generate> — save before, revert after
    (only when a sheet on disk changed). Raises NotLive before anything
    is written when no editor can be driven."""
    lv = Live(dirs, host, display)
    lv.save()
    before = _digest(lv.dirs)
    old = os.environ.get(SAVED_ENV)
    os.environ[SAVED_ENV] = ",".join(lv.holders())
    try:
        yield lv
    finally:
        if old is None:
            os.environ.pop(SAVED_ENV, None)
        else:
            os.environ[SAVED_ENV] = old
        if _digest(lv.dirs) != before:
            try:
                lv.revert()
            except NotLive as e:
                raise ReloadFailed(
                    f"sheets regenerated, editor NOT reloaded ({e}) — in "
                    "KiCad do File > Revert before saving anything") from e
        else:
            lv.log.append("no sheet changed on disk — editors left as they are")


def regen(dirs, cmd: list[str], host=None, display=None) -> dict:
    """Run a generator command inside a live session."""
    try:
        with session(dirs, host, display) as lv:
            r = subprocess.run(cmd, env=dict(os.environ))
    except NotLive as e:
        return {"ok": False, "live": False, "reason": str(e)}
    except ReloadFailed as e:
        return {"ok": False, "live": True, "reason": str(e)}
    return {"ok": r.returncode == 0, "live": True, "returncode": r.returncode,
            "log": lv.log}


USE = """kx live sch status [--dir D]...           projects held, editors found
kx live sch save|revert [--dir D]...      one step on the open editors
kx live sch regen [--dir D]... [--host H] [--display :N] -- CMD...
    save the open editors, run the generator CMD (regen lets the saved
    editors' locks through), revert the editors onto the result
--dir defaults to the current directory."""


def main(argv: list[str]) -> int:
    cmd = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[:argv.index("--")] if "--" in argv else argv
    opts = {"--dir": [], "--host": [], "--display": []}
    rest, i = [], 0
    while i < len(argv):
        if argv[i] in opts and i + 1 < len(argv):
            opts[argv[i]].append(argv[i + 1])
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    dirs = opts["--dir"] or [os.getcwd()]
    host = opts["--host"][-1] if opts["--host"] else None
    display = opts["--display"][-1] if opts["--display"] else None
    act = rest[0] if rest else ""
    lv = Live(dirs, host, display)
    try:
        if act == "status":
            out = {"projects": lv.projects, "editors": lv.find()}
        elif act == "save":
            lv.save()
            out = {"log": lv.log}
        elif act == "revert":
            lv.revert()
            out = {"log": lv.log}
        elif act == "regen" and cmd:
            out = regen(dirs, cmd, host, display)
        else:
            print(USE)
            return 2
    except NotLive as e:
        out = {"ok": False, "live": False, "reason": str(e)}
    json.dump(out, sys.stdout, indent=1, default=str)
    print()
    return 0 if out.get("ok", True) and out.get("live", True) else 1
