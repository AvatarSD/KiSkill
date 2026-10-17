"""Drive a KiCad GUI on ANOTHER host: forward its IPC socket over ssh.

KiCad's API is a unix socket (nng ipc://), so an OpenSSH unix-socket
forward carries it unchanged:

    ssh -f -N -M -S CTL -o ExitOnForwardFailure=yes \\
        -o StreamLocalBindUnlink=yes -L LOCAL.sock:REMOTE.sock HOST

`kx live connect --host H` does exactly that:
  1. pipes ipc_discovery.py to `ssh H python3 -` — the remote side is
     discovered by the same code as the local one (flatpak + native
     paths, liveness, api.enable_server in kicad_common.json);
  2. refuses with the remote reason when nothing listens there;
  3. opens the forward with a ControlMaster socket (so disconnect can
     close exactly this tunnel) and waits until LOCAL accepts;
  4. records it as the live target (live.write_target) — every
     `kx live …` then talks to the remote editor until `kx live
     disconnect`.

Same user on both ends is assumed (paths are the remote $HOME's).
Extra ssh options: KX_SSH_OPTS (shell-split), e.g. a known_hosts file.
"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess
import time

from . import ipc_discovery as disc
from . import live

DISCOVERY_SRC = pathlib.Path(disc.__file__)


def _ssh_opts() -> list[str]:
    return shlex.split(os.environ.get("KX_SSH_OPTS", ""))


def paths_for(host: str) -> tuple[pathlib.Path, pathlib.Path]:
    """(local forwarded socket, ssh control socket) for host."""
    d = disc.runtime_dir()
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in host)
    return d / f"{safe}.sock", d / f"{safe}.ctl"


def forward_argv(host: str, remote_sock: str, local_sock: str,
                 ctl: str) -> list[str]:
    """The ssh command that carries the KiCad socket (documented above)."""
    return ["ssh", "-f", "-N", "-M", "-S", ctl,
            "-o", "ExitOnForwardFailure=yes",
            "-o", "StreamLocalBindUnlink=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "BatchMode=yes",
            *_ssh_opts(),
            "-L", f"{local_sock}:{remote_sock}", host]


def remote_report(host: str, timeout: int = 20) -> dict:
    """ipc_discovery.report() as seen ON host."""
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", *_ssh_opts(), host,
                        "python3", "-"],
                       input=DISCOVERY_SRC.read_text(), capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ssh {host} failed: {r.stderr.strip()[-400:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def connect(host: str, remote_sock: str | None = None,
            wait_s: float = 10.0) -> dict:
    try:
        rep = remote_report(host)
    except (RuntimeError, OSError, ValueError, IndexError,
            subprocess.TimeoutExpired) as e:
        return {"connected": False, "host": host, "reason": str(e)}
    sock = remote_sock or rep.get("socket")
    if not sock:
        return {"connected": False, "host": host,
                "reason": disc.why_not(rep), "remote": rep}
    local, ctl = paths_for(host)
    disconnect(host, quiet=True)  # a previous tunnel to the same host
    r = subprocess.run(forward_argv(host, sock, str(local), str(ctl)),
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {"connected": False, "host": host,
                "reason": f"ssh forward failed: {r.stderr.strip()[-400:]}"}
    deadline = time.monotonic() + wait_s
    while not disc.listening(str(local)):
        if time.monotonic() > deadline:
            disconnect(host, quiet=True)
            return {"connected": False, "host": host,
                    "reason": f"forward up but {local} never accepted"}
        time.sleep(0.2)
    prev = live.read_target()
    t = {"kind": "remote", "host": host, "socket": str(local),
         "remote_socket": sock, "ctl": str(ctl),
         "previous": prev if prev and prev.get("kind") != "remote" else None}
    live.write_target(t)
    return {"connected": True, **t,
            "probe": live.ipc_probe(str(local))}


def disconnect(host: str | None = None, quiet: bool = False) -> dict:
    """Close the tunnel to host (default: the current remote target)."""
    t = live.read_target()
    if host is None:
        if not t or t.get("kind") != "remote":
            return {"disconnected": False, "reason": "no remote target"}
        host = t["host"]
    local, ctl = paths_for(host)
    if ctl.exists():
        subprocess.run(["ssh", "-S", str(ctl), "-O", "exit", host],
                       capture_output=True, text=True, timeout=15)
    local.unlink(missing_ok=True)
    if t and t.get("kind") == "remote" and t.get("host") == host:
        prev = t.get("previous")  # e.g. the headless editor it replaced
        if prev and disc.listening(prev.get("socket", "")):
            live.write_target(prev)
        else:
            live.clear_target()
    return {"disconnected": True, "host": host}
