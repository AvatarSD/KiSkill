"""Round-trip of GENERATED schematics (kicad_lib/regen.py): hand edits made
in KiCad survive the next generation, or the generation refuses.

A tiny generator below (two resistors in series, every pin a 2.54 mm stub
ending in a label, one text note) plays the part of a project's gen
script: it asks the Overrides for each symbol, places at the override
position, hangs the stubs from there, records owners + base, snapshots.
"GUI edits" are made on the file with sexp, the way KiCad would store
them; with kicad-cli present the file is also RE-SAVED by KiCad
(`sch upgrade --force`), so its normalisations are part of every check.

Covers: KiCad re-save is not an edit; move (plain and drag), rotate,
mirror, field move, value change (+ report + prune on revert), DNP, text
move; refusals: hand-added wire/label, deleted part, power symbol net;
--force discards (listed) but still absorbs; lock files; netlist
connectivity unchanged after an override regeneration and changed when
the script changes a net. Slow parts (~15 s) need kicad-cli; they skip.

Usage: python3 tests/test_regen.py
"""

import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import build, diff, geom, kcli, regen, sexp  # noqa: E402
from kicad_lib.sexp import QStr, Sym  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FIX = HERE / "fixtures/testproj/testproj.kicad_sch"
WORK = diff.SCRATCH / "regen_test"
SCH = WORK / "rt.kicad_sch"
SNAP = WORK / "snap"
OVR = WORK / "overrides.json"
ROOT_UUID = "0b5e7c1a-1111-4222-8333-444455556666"
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def has_cli() -> bool:
    try:
        return subprocess.run(kcli.cmd() + ["version"], capture_output=True,
                              timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


CLI = has_cli()
RDEF = next(s for s in sexp.find_all(sexp.find(sexp.load_file(str(FIX)), "lib_symbols"),
                                     "symbol") if sexp.atoms(s)[0] == "Device:R")
PINS = {"1": ((0, 3.81), (0, 1)), "2": ((0, -3.81), (0, -1))}   # lib pos, outward


# ---------------------------------------------------------------- generator

def generate(sess, parts=None):
    """parts: [(ref, x, y, value, net1, net2)]"""
    parts = parts or [("R1", 50.8, 50.8, "10k", "IN", "MID"),
                      ("R2", 76.2, 50.8, "1k", "MID", "OUT")]
    doc = build.empty_sheet(ROOT_UUID, "A4")
    sexp.find(doc, "lib_symbols").append(sexp.parse(sexp.dumps(RDEF)))
    owners, base = {}, {}
    key = sess.key(SCH)
    items = []
    for ref, x, y, value, n1, n2 in parts:
        k = f"{ref}/1"
        ov = sess.overrides.symbol(key, k)
        node = build.symbol("rt", ROOT_UUID, "Device:R", (x, y), 0, ref, value, ["1", "2"])
        base[k] = regen.sym_state(node)
        rot, mir = 0, ""
        if regen.geometry(ov):
            x, y, rot, mir = regen.geometry(ov)
            node = build.symbol("rt", ROOT_UUID, "Device:R", (x, y), rot, ref, value,
                                ["1", "2"])
            regen.apply_symbol(node, {"at": [x, y], "rot": rot, "mirror": mir},
                               geometry_too=True)
        regen.apply_symbol(node, ov)
        items.append(node)
        for num, net in (("1", n1), ("2", n2)):
            (u, v), (ou, ov_) = PINS[num]
            p = geom.pin_world((x, y), rot, (u, v), mir)
            d = geom.rot_xy(ou, ov_, rot, mir)
            e = geom.pt(p[0] + d[0] * 2.54, p[1] + d[1] * 2.54)
            for it in (build.wire(p, e), build.label(net, e)):
                owners[sexp.atoms(sexp.find(it, "uuid"))[0]] = k
                items.append(it)
    t = [Sym("text"), QStr.of("NOTE: divider"), [Sym("exclude_from_sim"), Sym("no")],
         [Sym("at"), Sym("30.48"), Sym("30.48"), Sym("0")],
         [Sym("effects"), [Sym("font"), [Sym("size"), Sym("1.27"), Sym("1.27")]],
          [Sym("justify"), Sym("left"), Sym("bottom")]], build.uid()]
    to = sess.overrides.text(key, "NOTE: divider")
    if to:
        t[3][1:3] = [Sym(f"{to[0]:g}"), Sym(f"{to[1]:g}")]
    items.append(t)
    for it in items:
        doc.insert(len(doc) - 2, it)
    owners = regen.restamp(doc, "rt/" + key, owners)
    sexp.save_file(str(SCH), doc)
    sess.snapshot(SCH, owners, base)


def session(force=False):
    return regen.Session(WORK, SNAP, OVR, force=force)


def regenerate(force=False, **kw):
    """The build_all flow: guard → absorb → generate → (report)."""
    s = session(force)
    s.guard_locks([WORK])
    s.absorb([SCH])
    generate(s, **kw)
    s.overrides.save(OVR)
    return s


def resave():
    if CLI:
        subprocess.run(kcli.cmd() + ["sch", "upgrade", "--force", str(SCH)],
                       capture_output=True, check=True)


def edit(fn):
    doc = sexp.load_file(str(SCH))
    fn(doc)
    sexp.save_file(str(SCH), doc)
    resave()


def sym(doc, ref):
    return next(s for s in sexp.find_all(doc, "symbol")
                if any(sexp.atoms(p)[:2] == ["Reference", ref]
                       for p in sexp.find_all(s, "property")))


def prop(s, name):
    return next(p for p in sexp.find_all(s, "property") if sexp.atoms(p)[0] == name)


def shift(node, dx, dy):
    for a in sexp.walk([None, node], "at"):
        a[1] = Sym(f"{round(float(a[1]) + dx, 3):g}")
        a[2] = Sym(f"{round(float(a[2]) + dy, 3):g}")


def state(ref):
    s = sym(sexp.load_file(str(SCH)), ref)
    return regen.sym_state(s)


def classes():
    return [e["cls"] for e in regen.diff(regen.load_json(session().snap_path(SCH)), str(SCH))]


def refused(fn, force=False):
    try:
        fn(force)
    except regen.Refused as r:
        return r
    return None


# ---------------------------------------------------------------- run

shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True)
(WORK / "rt.kicad_pro").write_text("{}\n")
generate(session())
check("fresh generation: no edits", classes() == [])
first, first_snap = SCH.read_bytes(), session().snap_path(SCH).read_bytes()
generate(session())
check("restamp: two generations are byte-identical (sheet + snapshot)",
      SCH.read_bytes() == first and session().snap_path(SCH).read_bytes() == first_snap)
R1_UUID = regen._uid(sym(sexp.load_file(str(SCH)), "R1"))
resave()
check(f"KiCad re-save is not an edit{'' if CLI else ' (no kicad-cli: skipped)'}",
      classes() == [])
if CLI:
    net0 = regen.netlist(str(SCH))
    check("netlist: R1.2 and R2.1 on MID", net0["R1.2"] == net0["R2.1"] == "/MID")

# move (plain "M": symbol + fields, stubs stay behind)
edit(lambda d: shift(sym(d, "R1"), 0, 25.4))
check("move: absorbable", classes() == ["absorb"])
s = regenerate()
st = state("R1")
check("move: survives regeneration", st["at"] == [50.8, 76.2])
wires = [w for w in sexp.find_all(sexp.load_file(str(SCH)), "wire")]
ends = {tuple(map(float, sexp.atoms(x))) for w in wires
        for x in sexp.find_all(sexp.find(w, "pts"), "xy")}
check("move: stubs re-hung at the pins' new position",
      (50.8, 72.39) in ends and (50.8, 80.01) in ends and (50.8, 46.99) not in ends)
check("move: second pass is clean", classes() == [])
check("restamp: a moved symbol keeps its uuid",
      regen._uid(sym(sexp.load_file(str(SCH)), "R1")) == R1_UUID)
if CLI:
    net = WORK / "t.net"
    subprocess.run(kcli.cmd() + ["sch", "export", "netlist", "-o", str(net), str(SCH)],
                   capture_output=True)
    comp = next(c for c in sexp.walk(sexp.load_file(str(net)), "comp")
                if sexp.atoms(sexp.find(c, "ref"))[0] == "R1")
    check("restamp: netlist tstamp = the stable uuid",
          R1_UUID in sexp.dumps(sexp.find(comp, "sheetpath")) + sexp.dumps(comp))
if CLI:
    check("connectivity unchanged after an override-only regeneration",
          regen.connectivity_diff(net0, regen.netlist(str(SCH))) == [])

# drag ("G"): symbol moves, its stub's pin end follows, KiCad inserts a
# segment back to the label that stayed
def drag(d):
    shift(sym(d, "R2"), 12.7, 0)
    for w in sexp.find_all(d, "wire"):
        xys = list(sexp.find_all(sexp.find(w, "pts"), "xy"))
        if all(float(xy[1]) == 76.2 for xy in xys):
            for xy in xys:
                xy[1] = Sym("88.9")
    for y in (44.45, 57.15):
        d.insert(len(d) - 2, build.wire((76.2, y), (88.9, y)))


edit(drag)
c = classes()
check("drag: absorbed + re-hung, nothing refused", "refuse" not in c and "absorb" in c)
regenerate()
check("drag: survives", state("R2")["at"] == [88.9, 50.8])

# rotate + mirror
edit(lambda d: (sexp.find(sym(d, "R2"), "at").__setitem__(3, Sym("90")),
                sym(d, "R2").insert(3, [Sym("mirror"), Sym("y")])))
regenerate()
st = state("R2")
check("rotate+mirror: survives (KiCad may store an equivalent pair)",
      (st["rot"], st["mirror"]) in ((90, "y"), (270, "x")))
if CLI:
    check("rotate+mirror: connectivity unchanged (pin math = KiCad's)",
          regen.connectivity_diff(net0, regen.netlist(str(SCH))) == [])

# field move
edit(lambda d: shift(prop(sym(d, "R1"), "Value"), 5.08, 0))
before = state("R1")["fields"]["Value"]["at"]
regenerate()
check("field move: survives", state("R1")["fields"]["Value"]["at"] == before)

# value change → report → revert prunes
edit(lambda d: prop(sym(d, "R1"), "Value").__setitem__(2, QStr.of("22k")))
s = regenerate()
check("value: survives", state("R1")["fields"]["Value"]["value"] == "22k")
check("value: report says move into the gen script",
      "R1/1: Value = '22k' (script: '10k')" in s.report())
edit(lambda d: prop(sym(d, "R1"), "Value").__setitem__(2, QStr.of("10k")))
regenerate()
ov = regen.load_json(OVR)["sheets"]["rt"]["symbols"]["R1/1"]
check("value: reverting to the script's value prunes the override",
      "value" not in ov.get("fields", {}).get("Value", {}))

# DNP
edit(lambda d: sexp.find(sym(d, "R2"), "dnp").__setitem__(1, Sym("yes")))
regenerate()
check("DNP: survives", state("R2")["flags"]["dnp"] is True)

# text move
edit(lambda d: shift(next(sexp.find_all(d, "text")), 0, 5.08))
regenerate()
t = next(sexp.find_all(sexp.load_file(str(SCH)), "text"))
check("text move: survives", sexp.atoms(sexp.find(t, "at"))[:2] == ["30.48", "35.56"])

# refusals
snap_before = SCH.read_text()
ovr_before = OVR.read_text()
edit(lambda d: d.insert(len(d) - 2, build.wire((120.65, 100.33), (130.81, 100.33))))
r = refused(lambda f: regenerate(force=f))
check("hand-added wire: refused", r is not None and any("hand-added wire" in x for x in r.items))
check("refused run wrote nothing", OVR.read_text() == ovr_before
      and "120.65" in SCH.read_text())
edit(lambda d: prop(sym(d, "R1"), "Value").__setitem__(2, QStr.of("33k")))
s = regenerate(force=True)
check("--force: discard listed", "DISCARDED (--force)" in s.report()
      and "hand-added wire" in s.report())
check("--force: wire gone", "120.65" not in SCH.read_text())
check("--force: absorbable edit still absorbed", state("R1")["fields"]["Value"]["value"] == "33k")

edit(lambda d: d.insert(len(d) - 2, build.label("STRAY", (120.65, 100.33))))
check("hand-added label: refused", refused(lambda f: regenerate(force=f)) is not None)
regenerate(force=True)
edit(lambda d: d.remove(sym(d, "R2")))
r = refused(lambda f: regenerate(force=f))
check("deleted generated part: refused", r is not None and any("R2/1" in x for x in r.items))
regenerate(force=True)

# lock files
lck = WORK / "~rt.kicad_sch.lck"
lck.write_text("")
r = refused(lambda f: regenerate(force=True))
check("lock file: refused even with --force", r is not None and str(lck) in r.items)
lck.unlink()

# script changes a net → connectivity reports it
if CLI:
    s = session()
    s.connectivity(SCH)
    regenerate(parts=[("R1", 50.8, 50.8, "10k", "IN", "MID2"),
                      ("R2", 76.2, 50.8, "1k", "MID2", "OUT")])
    d = session().connectivity(SCH)
    check("connectivity: script net rename reported", any("R1.2" in x for x in d))

# power symbol value = its net: refused
snap = regen.load_json(session().snap_path(SCH))
snap["symbols"]["R1/1"]["power"] = True
edit(lambda d: prop(sym(d, "R1"), "Value").__setitem__(2, QStr.of("GND")))
check("power symbol value change: refused",
      any(e["cls"] == "refuse" and "power symbol" in e["what"]
          for e in regen.diff(snap, str(SCH))))

# missing snapshot
shutil.rmtree(SNAP)
r = refused(lambda f: session().absorb([SCH]))
check("no snapshot: refused (bootstrap first)", r is not None)

print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
