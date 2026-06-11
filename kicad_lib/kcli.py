"""Which KiCad answers the phone? Single resolver for the kicad-cli
invocation and the official symbol-library dir, shared by every module
that shells out (diff/fab/pcb/live). Resolution, first hit wins:

  KX_KICAD_CLI env        explicit command, shlex-split — set it to
                          `kicad-cli-nightly` when the project is edited
                          with the v11 nightly, so ERC/render/netlist
                          evidence comes from the SAME engine that writes
                          the files (version skew = wrong evidence)
  kicad-cli on PATH       native install (deb/rpm/brew)
  flatpak                 org.kicad.KiCad (fails loud at call time if the
                          app itself is missing)
  kicad-cli-nightly       last resort — nightly-only machines

No caching: resolution is two which() calls, and tests override the env
per-invocation.
"""

from __future__ import annotations

import os
import pathlib
import shlex
import shutil

_FLATPAK_SYMBOLS = pathlib.Path.home() / (
    ".local/share/flatpak/runtime/org.kicad.KiCad.Library.Symbols/"
    "x86_64/stable/active/files/symbols"
)
_NATIVE_SYMBOLS = (
    pathlib.Path("/usr/share/kicad/symbols"),
    pathlib.Path("/usr/lib/kicad-nightly/share/kicad/symbols"),
    pathlib.Path("/usr/share/kicad-nightly/symbols"),
)


def cmd() -> list[str]:
    """The kicad-cli invocation for subprocess: CMD + ["sch", "erc", …]."""
    env = os.environ.get("KX_KICAD_CLI")
    if env:
        return shlex.split(env)
    if shutil.which("kicad-cli"):
        return ["kicad-cli"]
    if shutil.which("flatpak"):
        return ["flatpak", "run", "--command=kicad-cli", "org.kicad.KiCad"]
    if shutil.which("kicad-cli-nightly"):
        return ["kicad-cli-nightly"]
    return ["kicad-cli"]  # let the caller's returncode check report it


def symbols_dir() -> pathlib.Path | None:
    """Official symbol-library dir matching the resolved install; None if
    no KiCad libraries are installed (index then scans only project libs)."""
    env = os.environ.get("KX_KICAD_SYMBOLS")
    if env:
        return pathlib.Path(env)
    # mirror cmd()'s preference: native install → native libs first
    native_first = bool(shutil.which("kicad-cli"))
    order = ((*_NATIVE_SYMBOLS, _FLATPAK_SYMBOLS) if native_first
             else (_FLATPAK_SYMBOLS, *_NATIVE_SYMBOLS))
    for cand in order:
        if cand.is_dir():
            return cand
    return None
