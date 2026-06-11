"""Backend-adapter tests — hermetic except one real kicad-cli version
probe (cheap, no project touched).

Usage: python3 tests/test_live.py
"""

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import live  # noqa: E402

FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


with tempfile.TemporaryDirectory() as td:
    check("no locks in empty dir", live.lock_files(td) == [])
    lck = pathlib.Path(td) / "~x.kicad_sch.lck"
    lck.touch()
    (pathlib.Path(td) / "x.kicad_sch").touch()  # non-lock must not match
    check("lock detected", live.lock_files(td) == [str(lck)])

    os.environ["KICAD_API_SOCKET"] = "/nonexistent/api.sock"
    check("env socket wins", live.ipc_socket() == "/nonexistent/api.sock")
    del os.environ["KICAD_API_SOCKET"]

    d = live.detect(td)
    check("detect shape", {"kicad_cli", "backends", "recommended"} <= d.keys())
    check("file backend blocked by lock",
          d["backends"]["file"]["available"]
          and not d["backends"]["file"]["writable_now"])
    # with a live KiCad up, ipc legitimately wins; file otherwise
    check("recommended matches ipc liveness",
          d["recommended"] == ("ipc" if d["ipc_alive"] else "file"))
    check("kicad-cli probed", (d["kicad_cli"] or "").startswith("10."))

    lck.unlink()
    d2 = live.detect(td)
    check("writable when unlocked", d2["backends"]["file"]["writable_now"])

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
