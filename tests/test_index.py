"""Component-index tests — hermetic: temp DB, fixture lib synthesized
from the dac_buf cache, official-libs root pointed at a void.

Usage: python3 tests/test_index.py
"""

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import index, ops, sexp  # noqa: E402
from kicad_lib.sexp import Sym, QStr  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


with tempfile.TemporaryDirectory() as td:
    tdp = pathlib.Path(td)
    index.DB = tdp / "idx.sqlite"          # hermetic DB
    # keep the big official libs out (kcli.symbols_dir env override)
    os.environ["KX_KICAD_SYMBOLS"] = str(tdp / "no-official")

    # synthesize a one-lib fixture from the dac_buf cache
    r = ops.extract_libdef(str(HERE / "fixtures/dac_buf.kicad_sch"),
                           "Device:R", "Device:R")
    op = ops.extract_libdef(str(HERE / "fixtures/dac_buf.kicad_sch"),
                            "Amplifier_Operational:MCP6002-xMC",
                            "Amplifier_Operational:MCP6002-xMC")
    r[1], op[1] = QStr.of("R"), QStr.of("MCP6002-xMC")
    libdir = tdp / "libs"
    libdir.mkdir()
    sexp.save_file(str(libdir / "testlib.kicad_sym"),
                   [Sym("kicad_symbol_lib"),
                    [Sym("version"), Sym("20241209")],
                    [Sym("generator"), QStr.of("kx")], r, op])

    st = index.build([str(libdir)], verbose=False)
    check("build counts", st["libs_rescanned"] == 1
          and st["symbols_total"] == 2)

    hit = index.find("MCP6002")
    check("find by name", any(h["lib_id"] == "testlib:MCP6002-xMC"
                              for h in hit))
    check("pin count via units", any(h["pins"] == 9 for h in hit))

    st2 = index.build([str(libdir)], verbose=False)
    check("incremental no-op", st2["libs_rescanned"] == 0)

    os.utime(libdir / "testlib.kicad_sym")
    st3 = index.build([str(libdir)], verbose=False)
    check("mtime bump rescans", st3["libs_rescanned"] == 1
          and st3["symbols_total"] == 2)

    check("find empty on garbage", index.find("zzqqxx") == [])

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
