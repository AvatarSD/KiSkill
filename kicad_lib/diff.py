"""Triple diff for schematic revisions — the agent-readable evidence
behind the REVIEWED state (kicad-review skill):

  semantic_diff  probe-JSON delta: symbols added/removed/moved/changed,
                 label set changes, body-count deltas
  erc_report     headless flatpak ERC normalized to "type @(x, y)" set
  pixel_diff     render-level composite: grey = unchanged ink,
                 red = removed (only in base), green = added (only in new)

Scratch artifacts go under ~/.cache/kx_scratch — NEVER /tmp, the flatpak
kicad-cli cannot see host /tmp.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

SCRATCH = pathlib.Path.home() / ".cache/kx_scratch"
KICAD_CLI = ["flatpak", "run", "--command=kicad-cli", "org.kicad.KiCad"]
PAPER_W = {"A5": 210, "A4": 297, "A3": 420, "A2": 594, "A1": 841}


# ---------------------------------------------------------------- semantic

def semantic_diff(base: dict, new: dict) -> dict:
    """Delta between two `kx probe` dicts (same sheet, two revisions)."""
    def by_key(p):
        return {(s["ref"], s["unit"]): s for s in p["symbols"]}

    a, b = by_key(base), by_key(new)
    moved, changed = [], []
    for k in a.keys() & b.keys():
        sa, sb = a[k], b[k]
        if (sa["at"], sa["rot"], sa["mirror"]) != (sb["at"], sb["rot"], sb["mirror"]):
            moved.append({"ref": k[0], "unit": k[1],
                          "from": sa["at"], "to": sb["at"]})
        if (sa["value"], sa["lib_id"]) != (sb["value"], sb["lib_id"]):
            changed.append({"ref": k[0], "unit": k[1],
                            "value": [sa["value"], sb["value"]],
                            "lib_id": [sa["lib_id"], sb["lib_id"]]})
    return {
        "symbols_added": sorted(f"{r}.{u}" for r, u in b.keys() - a.keys()),
        "symbols_removed": sorted(f"{r}.{u}" for r, u in a.keys() - b.keys()),
        "symbols_moved": moved,
        "symbols_changed": changed,
        "labels_added": sorted(set(new["labels"]) - set(base["labels"])),
        "labels_removed": sorted(set(base["labels"]) - set(new["labels"])),
        "count_delta": {k: new["counts"][k] - base["counts"][k]
                        for k in new["counts"]
                        if new["counts"][k] != base["counts"].get(k, 0)},
    }


# ---------------------------------------------------------------- ERC

def erc_report(sch: str, tag: str) -> set[str]:
    """Run headless ERC; normalize each violation to 'type @(x, y)'."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    rpt = SCRATCH / f"erc_{tag}.rpt"
    subprocess.run(KICAD_CLI + ["sch", "erc", "--output", str(rpt),
                                "--severity-all", sch],
                   capture_output=True, text=True)
    out, vtype = set(), "?"
    for line in rpt.read_text().splitlines():
        m = re.match(r"\[(\w+)\]", line)
        if m:
            vtype = m.group(1)
        for loc in re.findall(r"@\(([\d. ]+ mm, [\d. ]+ mm)\)", line):
            out.add(f"{vtype} @({loc})")
    return out


def erc_diff(base: set[str], new: set[str]) -> dict:
    return {"erc_new": sorted(new - base), "erc_gone": sorted(base - new)}


# ---------------------------------------------------------------- pixel

def render_png(sch: str, out_png: str, width: int = 2400) -> str:
    import cairosvg

    sch_p = pathlib.Path(sch)
    svg_dir = SCRATCH / f"svg_{sch_p.stem}_{abs(hash(sch)) % 99999}"
    subprocess.run(KICAD_CLI + ["sch", "export", "svg", "--output",
                                str(svg_dir), "--no-background-color", sch],
                   capture_output=True, text=True)
    svgs = sorted(svg_dir.glob("*.svg"))
    if not svgs:
        raise RuntimeError(f"no svg produced for {sch}")
    cairosvg.svg2png(url=str(svgs[0]), write_to=out_png, output_width=width)
    return out_png


def pixel_diff(png_base: str, png_new: str, out_png: str,
               paper: str = "A4", width: int = 2400) -> dict:
    """Composite: grey = ink in both, red = only in base (removed),
    green = only in new (added). Returns change stats + bbox in mm."""
    from PIL import Image, ImageChops

    def gray(path):
        """Flatten onto white BEFORE grayscale: cairosvg output has a
        transparent background, and convert('L') maps transparent to
        BLACK — the whole page would read as ink."""
        im = Image.open(path)
        if im.mode in ("RGBA", "LA", "PA"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
        return im.convert("L")

    a, b = gray(png_base), gray(png_new)
    if a.size != b.size:
        b = b.resize(a.size)
    ink_a = a.point(lambda v: 255 if v < 250 else 0)
    ink_b = b.point(lambda v: 255 if v < 250 else 0)
    removed = ImageChops.subtract(ink_a, ink_b)   # ink only in base
    added = ImageChops.subtract(ink_b, ink_a)     # ink only in new
    common = ImageChops.darker(ink_a, ink_b)

    out = Image.new("RGB", a.size, (255, 255, 255))
    out.paste((160, 160, 160), mask=common)
    out.paste((220, 0, 0), mask=removed)
    out.paste((0, 160, 0), mask=added)
    out.save(out_png)

    delta = ImageChops.lighter(removed, added)
    changed = delta.histogram()[255]
    box = delta.getbbox()
    mm = PAPER_W.get(paper, 297) / a.size[0]
    bbox = [round(v * mm, 1) for v in box] if box else None
    return {"changed_px": changed, "bbox_mm": bbox, "composite": out_png}
