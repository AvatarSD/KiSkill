"""Where is KiCad's IPC API socket, and is the API server switched on?

Stdlib only and free of package imports ON PURPOSE: `kx live connect
--host H` pipes this very file to `ssh H python3 -` so the remote side is
discovered by the same code as the local one. Run directly it prints
`report()` as JSON.

Facts (measured on KiCad 10.0.7, flatpak and native):
  - KiCad puts the socket at <wx temp dir>/kicad/api.sock, the temp dir
    being $TMPDIR or /tmp. A second instance gets api-<pid>.sock.
  - The flatpak sets TMPDIR=/var/tmp inside the sandbox, and flatpak maps
    the sandbox /var/tmp to ~/.var/app/org.kicad.KiCad/cache/tmp on the
    host — that is where the flatpak socket really lives (NOT under
    /run/user/<uid>/app/..., which older notes claimed).
  - The socket file survives a killed KiCad, so "file exists" proves
    nothing; only a successful connect() does.
  - The server runs only when kicad_common.json has api.enable_server =
    true (Preferences > Plugins > Enable KiCad API). Flatpak keeps that
    file under ~/.var/app/org.kicad.KiCad/config/kicad/<ver>/, native
    KiCad under ~/.config/kicad/<ver>/; KICAD_CONFIG_HOME overrides both.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import socket as _socket
import sys

FLATPAK_ID = "org.kicad.KiCad"


def _home(home: str | None) -> pathlib.Path:
    return pathlib.Path(home) if home else pathlib.Path.home()


def runtime_dir() -> pathlib.Path:
    """Private per-user dir for kx's forwarded sockets and state."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    d = (pathlib.Path(base) / "kx" if base and os.path.isdir(base)
         else pathlib.Path(f"/tmp/kx-{os.getuid()}"))
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _sockets_in(d: pathlib.Path) -> list[pathlib.Path]:
    """api.sock first, then api-<pid>.sock (newest first)."""
    if not d.is_dir():
        return []
    found = [p for p in d.glob("api*.sock") if re.fullmatch(
        r"api(-\d+)?\.sock", p.name)]
    return sorted(found, key=lambda p: (p.name != "api.sock",
                                        -_mtime(p)))


def _mtime(p: pathlib.Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def candidates(home: str | None = None, env: dict | None = None
               ) -> list[tuple[str, str]]:
    """(path, source) for every socket FILE that exists, in priority
    order: flatpak, then native ($TMPDIR, /tmp). Liveness not checked."""
    env = os.environ if env is None else env
    h = _home(home)
    dirs = [(h / ".var/app" / FLATPAK_ID / "cache/tmp/kicad", "flatpak")]
    tmp = env.get("TMPDIR")
    if tmp:
        dirs.append((pathlib.Path(tmp) / "kicad", "native"))
    dirs.append((pathlib.Path("/tmp/kicad"), "native"))
    out, seen = [], set()
    for d, src in dirs:
        for p in _sockets_in(d):
            if str(p) not in seen:
                seen.add(str(p))
                out.append((str(p), src))
    return out


def listening(path: str, timeout: float = 1.0) -> bool:
    """True iff something accepts connections on the unix socket."""
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def api_settings(home: str | None = None, env: dict | None = None
                 ) -> list[dict]:
    """Every kicad_common.json found: {install, version, path,
    enable_server}. Newest version first within each install."""
    env = os.environ if env is None else env
    h = _home(home)
    roots = []
    if env.get("KICAD_CONFIG_HOME"):
        roots.append((pathlib.Path(env["KICAD_CONFIG_HOME"]), "override"))
    roots.append((h / ".var/app" / FLATPAK_ID / "config/kicad", "flatpak"))
    xdg = env.get("XDG_CONFIG_HOME")
    roots.append(((pathlib.Path(xdg) if xdg else h / ".config") / "kicad",
                  "native"))
    out = []
    for root, inst in roots:
        if not root.is_dir():
            continue
        vers = [d for d in root.iterdir()
                if re.fullmatch(r"\d+\.\d+", d.name)
                and (d / "kicad_common.json").is_file()]
        vers.sort(key=lambda d: tuple(int(x) for x in d.name.split(".")),
                  reverse=True)
        for d in vers:
            f = d / "kicad_common.json"
            try:
                on = json.loads(f.read_text()).get("api", {}).get(
                    "enable_server")
            except (OSError, ValueError):
                on = None
            out.append({"install": inst, "version": d.name, "path": str(f),
                        "enable_server": bool(on) if on is not None
                        else None})
    return out


def report(home: str | None = None, env: dict | None = None) -> dict:
    """Everything discovery knows, no kipy needed."""
    cands = candidates(home, env)
    live = [(p, s) for p, s in cands if listening(p)]
    return {
        "host": _socket.gethostname(),
        "sockets": [{"path": p, "source": s, "listening": (p, s) in live}
                    for p, s in cands],
        "socket": live[0][0] if live else None,
        "source": live[0][1] if live else None,
        "stale": [p for p, s in cands if (p, s) not in live],
        "api_settings": api_settings(home, env),
    }


def why_not(rep: dict) -> str:
    """Human reason no live socket was found ("" if one was). Names every
    install whose newest config has the API server OFF — the usual cause —
    and the ones where it is ON (then KiCad is simply not running)."""
    if rep.get("socket"):
        return ""
    parts = ["no KiCad answers on an IPC socket"]
    if rep.get("stale"):
        parts.append("stale socket file(s), KiCad exited or crashed: " +
                     ", ".join(rep["stale"]))
    newest = {}
    for s in rep.get("api_settings") or []:
        newest.setdefault(s["install"], s)
    off = [s for s in newest.values() if not s["enable_server"]]
    on = [s for s in newest.values() if s["enable_server"]]
    if off:
        parts.append("API server OFF in " + ", ".join(
            f"{s['install']} {s['version']} ({s['path']})" for s in off) +
            " -> Preferences > Plugins > Enable KiCad API, then reopen the "
            "PCB editor")
    if on:
        parts.append("ON in " + ", ".join(
            f"{s['install']} {s['version']}" for s in on) +
            " -> open the PCB editor there")
    return "; ".join(parts)


if __name__ == "__main__":
    json.dump(report(), sys.stdout)
    print()
