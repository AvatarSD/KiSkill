"""Triple-diff tests against the testproj fixture: a mutated copy must
light up all three channels (semantic, pixel, ERC) and an identical copy
must stay dark. Slow (~20 s: 2 renders + 2 ERC runs via flatpak).

Usage: python3 tests/test_diff.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import cli, diff, ops, sexp  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FIX = HERE / "fixtures/testproj/testproj.kicad_sch"
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


diff.SCRATCH.mkdir(parents=True, exist_ok=True)

# mutate a copy: drop the R2 load and its label coverage
doc = sexp.load_file(str(FIX))
ops.delete_ref(doc, "R2")
ops.add_label(doc, "TP1", (105.41, 80.01))
mut = diff.SCRATCH / "mut.kicad_sch"
sexp.save_file(str(mut), doc)

pr_base, pr_mut = cli.probe(str(FIX)), cli.probe(str(mut))
sd = diff.semantic_diff(pr_base, pr_mut)
check("semantic: R2 removed", sd["symbols_removed"] == ["R2.1"])
check("semantic: label added", sd["labels_added"] == ["TP1"])
check("semantic: counts delta", sd["count_delta"].get("symbol") == -1)
check("semantic: self-diff empty",
      all(not v for v in diff.semantic_diff(pr_base, pr_base).values()))

png_a = diff.render_png(str(FIX), str(diff.SCRATCH / "a.png"))
png_b = diff.render_png(str(mut), str(diff.SCRATCH / "b.png"))
px = diff.pixel_diff(png_a, png_b, str(diff.SCRATCH / "ab.png"))
check("pixel: change detected", px["changed_px"] > 500
      and px["bbox_mm"] is not None)
# R2 sat near x≈133, y≈82-95 mm — bbox must cover it
check("pixel: bbox covers R2 zone", px["bbox_mm"][0] <= 134
      and px["bbox_mm"][2] >= 130)
px0 = diff.pixel_diff(png_a, png_a, str(diff.SCRATCH / "aa.png"))
check("pixel: self-diff zero", px0["changed_px"] == 0
      and px0["bbox_mm"] is None)

erc_a = diff.erc_report(str(FIX), "t_base")
erc_b = diff.erc_report(str(mut), "t_mut")
ed = diff.erc_diff(erc_a, erc_b)
check("erc: baseline clean", erc_a == set())
check("erc: mutation introduces violations", len(ed["erc_new"]) >= 1)
check("erc: self-diff empty",
      diff.erc_diff(erc_a, erc_a) == {"erc_new": [], "erc_gone": []})

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
