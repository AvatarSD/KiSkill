"""Round-trip soak test: parse → serialize → reparse must preserve the
token stream exactly; byte diff is reported as a stability metric.

Usage: python3 tests/test_roundtrip.py [FILE ...]
Default targets: every fixture in tests/fixtures plus any *.kicad_sch /
*.kicad_pcb paths passed on argv (read-only).
"""

import sys
import os
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import sexp  # noqa: E402


def roundtrip(path: pathlib.Path, require_bytes: bool = False) -> bool:
    text = path.read_text(encoding="utf-8")
    tree = sexp.parse(text)
    out = sexp.dumps(tree)
    tree2 = sexp.parse(out)

    t1, t2 = list(sexp.tokens(tree)), list(sexp.tokens(tree2))
    if t1 != t2:
        for i, (a, b) in enumerate(zip(t1, t2)):
            if a != b:
                print(f"FAIL {path.name}: token {i}: {a!r} != {b!r}")
                return False
        print(f"FAIL {path.name}: token count {len(t1)} != {len(t2)}")
        return False

    if out == text:
        print(f"OK   {path.name}: {len(t1)} tokens, byte-identical")
        return True
    if require_bytes:
        print(f"FAIL {path.name}: byte drift (in {len(text)}B out {len(out)}B)"
              " — sch/sym/mod must be byte-stable")
        return False
    print(f"OK   {path.name}: {len(t1)} tokens, token-equal "
          f"(byte drift {len(out) - len(text):+d}B tolerated for this type)")
    return True


def mutation_check() -> bool:
    """The parser must REJECT broken input (verifier-of-the-verifier)."""
    bad = ["(a (b)", "(a))", '(a "unterminated)', "(a) (b)"]
    for s in bad:
        try:
            sexp.parse(s)
        except sexp.ParseError:
            continue
        print(f"FAIL mutation: accepted {s!r}")
        return False
    print(f"OK   mutation: {len(bad)} malformed inputs rejected")
    return True


def order_check() -> bool:
    """Atoms interleaved between list children (legacy third-party style
    like '(effects (font ...) hide)') must keep their token order."""
    src = '(a x (b 1) hide (c 2) y)'
    t = sexp.parse(src)
    same = list(sexp.tokens(t)) == list(sexp.tokens(sexp.parse(sexp.dumps(t))))
    print(("OK   " if same else "FAIL ") + "order: interleaved atoms preserved")
    return same


def main() -> int:
    here = pathlib.Path(__file__).resolve().parent
    targets = sorted(
        p for pat in ("*.kicad_sch", "*.kicad_pcb", "*.kicad_sym", "*.kicad_mod")
        for p in (here / "fixtures").rglob(pat)
    )
    extras = [pathlib.Path(a) for a in sys.argv[1:]]
    ok = mutation_check() and order_check()
    for p in targets:
        ok &= roundtrip(p, require_bytes=True)
    for p in extras:
        # argv extras may come from other generators (easyeda2kicad…)
        ok &= roundtrip(p)
    # byte-identity soak on a REAL pcbnew-saved board (read-only donor)
    donor = pathlib.Path(os.environ.get("KX_DONOR_PCB", (
        "/home/sd/prj/20250713_ig-smartgrow/gorshok/smartgrow-gorshok/"
        "hat/strawberry_1170-hat/strawberry_1170-hat.kicad_pcb")))
    if donor.exists():
        ok &= roundtrip(donor, require_bytes=True)
    else:
        print("note: donor pcb missing — set KX_DONOR_PCB for the soak")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
