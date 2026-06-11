"""kcli resolver tests: env override wins, PATH probing picks native
over flatpak, symbols_dir mirrors the CLI preference. Pure Python, no
KiCad needed — PATH is faked per check.

Usage: python3 tests/test_kcli.py
"""

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import kcli  # noqa: E402

FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def with_env(env: dict, fn):
    old = {k: os.environ.get(k) for k in env}
    os.environ.update({k: v for k, v in env.items() if v is not None})
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
    try:
        return fn()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --- env override wins, shlex-split ------------------------------------------
got = with_env({"KX_KICAD_CLI": "kicad-cli-nightly"}, kcli.cmd)
check("env override single token", got == ["kicad-cli-nightly"])
got = with_env({"KX_KICAD_CLI": 'flatpak run "--command=kicad-cli" org.kicad.KiCad'},
               kcli.cmd)
check("env override shlex-split",
      got == ["flatpak", "run", "--command=kicad-cli", "org.kicad.KiCad"])

# --- PATH probing: native > flatpak > nightly > bare fallback ----------------
with tempfile.TemporaryDirectory() as td:
    fake = pathlib.Path(td)

    def fake_bin(name):
        p = fake / name
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
        return p

    def resolved(*names):
        for f in fake.iterdir():
            f.unlink()
        for n in names:
            fake_bin(n)
        return with_env({"KX_KICAD_CLI": None, "PATH": td}, kcli.cmd)

    check("native kicad-cli preferred",
          resolved("kicad-cli", "flatpak", "kicad-cli-nightly") == ["kicad-cli"])
    check("flatpak when no native",
          resolved("flatpak", "kicad-cli-nightly")[0] == "flatpak")
    check("nightly as last resort",
          resolved("kicad-cli-nightly") == ["kicad-cli-nightly"])
    check("bare fallback on empty PATH", resolved() == ["kicad-cli"])

    # --- symbols_dir ----------------------------------------------------------
    sym = fake / "syms"
    sym.mkdir()
    got = with_env({"KX_KICAD_SYMBOLS": str(sym)}, kcli.symbols_dir)
    check("symbols env override", got == sym)
    got = with_env({"KX_KICAD_SYMBOLS": None, "PATH": td}, kcli.symbols_dir)
    check("symbols_dir is dir-or-none",
          got is None or (isinstance(got, pathlib.Path) and got.is_dir()))

print("PASS" if not FAILS else f"FAIL ({len(FAILS)}): {FAILS}")
sys.exit(1 if FAILS else 0)
