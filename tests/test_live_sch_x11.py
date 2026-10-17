"""Save / revert a REAL open KiCad schematic editor through its window —
a headless one (kx live headless: Xvfb + flatpak KiCad), never the
operator's desktop. Runs twice: standalone eeschema (.kicad_sch) and the
project manager's schematic editor (.kicad_pro, how the operator works;
its File menu differs). Also pins what KiCad 10 serves for schematics over
IPC (live.sch_capabilities): nothing that edits, saves or reverts.

Per mode:
  1. the editor is found by title, unmodified; save is a no-op
  2. GUI edit (Ctrl+A, Delete — keyboard only) -> title gets "*";
     save -> the "*" goes and the file on disk has no symbols left
  3. the original sheet is written back to disk while the editor is
     open; revert -> the editor reloads it: a Ctrl+S dump of the
     editor's memory has every symbol again

SKIPs when Xvfb, xdotool, flatpak KiCad or the repo .venv is missing.

Usage: python3 tests/test_live_sch_x11.py   (~40 s)
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kicad_lib import live, live_headless, x11_editor as xe  # noqa: E402

FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


gaps = live_headless.missing() + ([] if shutil.which("xdotool")
                                  else ["xdotool"])
if gaps:
    print("SKIP: headless KiCad unavailable — " + "; ".join(gaps))
    raise SystemExit(0)
if live_headless.status():
    print("SKIP: a headless editor is already up (kx live headless stop) "
          "— not touching it")
    raise SystemExit(0)

FIXTURE = ROOT / "tests/fixtures/testproj/testproj.kicad_sch"
ORIG = FIXTURE.read_text()
N_SYM = ORIG.count("\n\t(symbol\n")


def n_symbols(p: pathlib.Path) -> int:
    return p.read_text().count("\n\t(symbol\n")


def dump(display: str, wid: str, path: pathlib.Path) -> None:
    """Ctrl+S writes the editor's memory even when unmodified."""
    t0 = path.stat().st_mtime_ns
    x = xe.X11(display)
    x.focus(wid)
    x.run("key", "ctrl+s")
    for _ in range(40):
        if path.stat().st_mtime_ns != t0:
            time.sleep(0.5)
            return
        time.sleep(0.25)


for mode in ("kicad_sch", "kicad_pro"):
    scratch = pathlib.Path(tempfile.mkdtemp(
        prefix="livesch_", dir=pathlib.Path.home() / ".cache/kx_scratch"))
    sch = scratch / "testproj.kicad_sch"
    sch.write_text(ORIG)
    if mode == "kicad_pro":
        (scratch / "testproj.kicad_pro").write_text("{}\n")
    st = live_headless.start(str(scratch / f"testproj.{mode}"))
    disp = f":{st['display']}"
    try:
        if live.major_of(st["kicad_version"]) == 10:
            caps = live.sch_capabilities(st["socket"]) or {}
            check(f"[{mode}] KiCad 10 IPC: the schematic is listed",
                  caps.get("documents") == ["testproj.kicad_sch"])
            check(f"[{mode}] KiCad 10 IPC: no item read/write, no save/"
                  "revert/run_action", not any(
                      caps.get(k) for k in ("get_items", "item_write",
                                            "SaveDocument", "RevertDocument",
                                            "SaveDocumentToString",
                                            "RunAction")))
            check(f"[{mode}] sch_scope list-only",
                  live.sch_scope(caps) == "list-only")
        ed = xe.find("testproj", disp)
        check(f"[{mode}] editor found, unmodified, class "
              f"{'kicad' if mode == 'kicad_pro' else 'eeschema'}",
              not ed["modified"] and ed["cls"] ==
              ("kicad" if mode == "kicad_pro" else "eeschema"))
        r = xe.save("testproj", disp)
        check(f"[{mode}] save when clean is a no-op", r["needed"] is False)

        x = xe.X11(disp)
        x.focus(ed["id"])
        x.run("mousemove", "800", "400")
        x.run("key", "Escape", "ctrl+a")
        time.sleep(0.5)
        x.run("key", "Delete")
        time.sleep(1)
        check(f"[{mode}] GUI edit marks the title modified",
              xe.find("testproj", disp)["modified"])
        r = xe.save("testproj", disp)
        check(f"[{mode}] save: verified by the title",
              r["saved"] and not r["title"].startswith("*"))
        check(f"[{mode}] save reached the disk (no symbols left)",
              n_symbols(sch) == 0)

        sch.write_text(ORIG)                      # "the generator wrote"
        r = xe.revert("testproj", disp)
        check(f"[{mode}] revert confirmed, editor clean",
              r["reverted"] and not r["title"].startswith("*"))
        dump(disp, xe.find("testproj", disp)["id"], sch)
        check(f"[{mode}] the open document is the disk version again "
              f"({N_SYM} symbols)", n_symbols(sch) == N_SYM)
        x.run("key", "ctrl+f")                    # a dialog the operator has open
        time.sleep(1.5)
        dlg = xe.dialogs(x.windows())
        if dlg:
            r = subprocess.run([sys.executable, str(ROOT / "kicad_lib/"
                                "x11_editor.py"), "revert", "testproj", disp],
                               capture_output=True, text=True)
            check(f"[{mode}] an open dialog refuses (nothing typed)",
                  r.returncode == 1 and "dialog" in r.stdout)
            x.run("key", "Escape")
        else:
            print(f"NOTE [{mode}] Find dialog did not open; dialog refusal "
                  "is covered by tests/test_live_sch.py")
    finally:
        live_headless.stop()
        shutil.rmtree(scratch, ignore_errors=True)

print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall OK")
sys.exit(1 if FAILS else 0)
