"""Reference uniqueness + annotation audit, and the ERC-blindness it fills.

Forum lesson (kicad.info t/32585 + the Annotation docs): KiCad checks for
unannotated designators (ending '?'), duplicate designators, and multi-unit
parts whose units DON'T share a base ref. The catch — these are Annotation-
tool checks (SCH_REFERENCE_LIST), NOT ERC. This test proves the gotcha cold:

  1. ref_audit reads refs from the SCHEMATIC and is multi-unit aware — U1's
     three units (all ref 'U1') are NOT a duplicate;
  2. duplicate a ref (two R1) or unannotate one (R?) and ref_audit flags it;
  3. the GOTCHA — `kicad-cli sch erc --severity-all` stays 0/0 BLIND to both
     mutations, so a headless ERC pass cannot catch what ref_audit catches.

Refs live in TWO places (a `property "Reference"` and the authoritative
`instances` `(reference …)`); the mutation rewrites both, QStr-wrapped so
the serializer re-quotes them.

Slow (~18 s: three ERC runs via flatpak).
Usage: python3 tests/test_ref_audit.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import cli, diff, sexp  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "fixtures/testproj/testproj.kicad_sch"
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def mutate_ref(old: str, new: str, outname: str) -> pathlib.Path:
    """Rewrite a symbol's designator (both property + instances ref)."""
    diff.SCRATCH.mkdir(parents=True, exist_ok=True)
    root = sexp.load_file(str(SRC))
    for s in sexp.find_all(root, "symbol"):
        rp = next((p for p in sexp.find_all(s, "property")
                   if sexp.atoms(p)[0] == "Reference"), None)
        if rp and sexp.atoms(rp)[1] == old:
            rp[2] = sexp.QStr(new)
            for rf in sexp.walk(s, "reference"):
                rf[1] = sexp.QStr(new)
    out = diff.SCRATCH / outname
    sexp.save_file(str(out), root)
    return out


# --- clean baseline: multi-unit U1 is NOT a duplicate ------------------------
ra = cli.ref_audit(cli.probe(str(SRC)))
check("baseline clean", ra["clean"])
check("baseline no duplicates (U1's 3 units not flagged)", ra["duplicates"] == {})
check("baseline nothing unannotated", ra["unannotated"] == [])

# --- duplicate designator (two R1) -------------------------------------------
dup = mutate_ref("R2", "R1", "dupref.kicad_sch")
rad = cli.ref_audit(cli.probe(str(dup)))
check("dup mutant flags R1 as duplicate", "R1" in rad["duplicates"])
check("dup mutant R1 has two instances",
      rad["duplicates"].get("R1", {}).get("instances") == 2)
check("dup mutant not clean", not rad["clean"])

# --- unannotated designator (R?) ---------------------------------------------
un = mutate_ref("R2", "R?", "unann.kicad_sch")
rau = cli.ref_audit(cli.probe(str(un)))
check("unann mutant flags R?", rau["unannotated"] == ["R?"])
check("unann mutant not clean", not rau["clean"])

# --- the gotcha: kicad-cli ERC is BLIND to both ------------------------------
erc_base = diff.erc_report(str(SRC), "rb")
erc_dup = diff.erc_report(str(dup), "rd")
erc_un = diff.erc_report(str(un), "ru")
check("ERC blind to the duplicate (no new violations)",
      set(erc_dup) - set(erc_base) == set())
check("ERC blind to the unannotated ref (no new violations)",
      set(erc_un) - set(erc_base) == set())
check("ref_audit catches the duplicate ERC missed", "R1" in rad["duplicates"])

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
