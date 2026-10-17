"""Live schematic regeneration without a KiCad: editor-window recognition
(titles, "*" = unsaved, dialogs, which editor class), lock-file holders,
regen's lock guard letting exactly the saved editors through, and the
save -> generate -> revert session with a fake editor driver (revert only
when a sheet changed; an editor that cannot be saved writes nothing; a
failed reload is reported, never swallowed). Hermetic: temp dirs only.

Usage: python3 tests/test_live_sch.py
"""

import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import live, live_sch, regen, x11_editor as xe  # noqa: E402

FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def raises(exc, fn):
    try:
        fn()
    except exc as e:
        return e
    return None


# --- titles ----------------------------------------------------------------
t = xe.parse_title("*power_in [rev3/power_in] — Schematic Editor")
check("title: sub-sheet, modified",
      t == {"project": "rev3", "sheet": "power_in", "modified": True})
t = xe.parse_title("rev3 — Schematic Editor")
check("title: root, clean",
      t == {"project": "rev3", "sheet": "rev3", "modified": False})
check("title: nested sheet path keeps the project",
      xe.parse_title("osc [ocxo/a/osc] — Schematic Editor")["project"]
      == "ocxo")
check("title: other frames are not editors",
      xe.parse_title("rev3 — KiCad 10.0") is None
      and xe.parse_title("rev3 — PCB Editor") is None)

W = [{"id": "1", "title": "rev3 — KiCad 10.0", "cls": "kicad"},
     {"id": "2", "title": "*rev3 — Schematic Editor", "cls": "kicad"},
     {"id": "3", "title": "ocxo — Schematic Editor", "cls": "eeschema"}]
ed = xe.pick_editor(W, "rev3")
check("pick: the project's editor", ed["id"] == "2" and ed["modified"])
check("pick: none for an unknown project",
      raises(xe.EditorError, lambda: xe.pick_editor(W, "nope")) is not None)
check("pick: two editors of one project refuse",
      raises(xe.EditorError, lambda: xe.pick_editor(
          W + [dict(W[1], id="9")], "rev3")) is not None)
check("dialogs: main frames are not dialogs", xe.dialogs(W) == [])
check("dialogs: a question window is",
      xe.dialogs(W + [{"id": "5", "title": "Confirmation", "cls": "kicad"}])
      == ["Confirmation"])
check("revert menu index: standalone 5, project manager 2",
      xe.REVERT_DOWNS == {"eeschema": 5, "kicad": 2})

# --- locks -----------------------------------------------------------------
tmp = pathlib.Path(tempfile.mkdtemp(prefix="kx_livesch_"))
proj, other = tmp / "rev3", tmp / "ocxo"
for d in (proj, other):
    d.mkdir()
(proj / "rev3.kicad_sch").write_text("(kicad_sch)\n")
(other / "ocxo.kicad_sch").write_text("(kicad_sch)\n")


def lock(path, user, host):
    path.write_text(json.dumps({"hostname": host, "username": user}))


lock(proj / "~rev3.kicad_sch.lck", "sd", "book")
lock(proj / "~rev3.kicad_pro.lck", "sd", "book")
lock(other / "~ocxo.kicad_pro.lck", "sd", "studio")
check("lock_holder reads user@host",
      live.lock_holder(proj / "~rev3.kicad_sch.lck") == "sd@book")
check("lock_holder: unreadable -> unknown",
      live.lock_holder(tmp / "missing.lck") == "unknown")
h = {p["stem"]: p for p in live_sch.held([proj, other])}
check("held: schematic open on book",
      h["rev3"]["sch"] and h["rev3"]["host"] == "book"
      and len(h["rev3"]["locks"]) == 2)
check("held: project manager only (no editor)", not h["ocxo"]["sch"])

lv = live_sch.Live([proj, other])
check("Live: a remote holder is reached over ssh by its hostname",
      {p["stem"]: p["via"] for p in lv.projects}["rev3"] == "book")
check("Live: --host overrides the ssh target",
      all(p["via"] == "alias" for p in
          live_sch.Live([proj], host="alias").projects))
check("Live: only real editors are driven",
      [p["stem"] for p in lv.editors] == ["rev3"])

# --- regen lock guard --------------------------------------------------------
s = regen.Session(proj, proj / "snap", proj / "ov.json")
e = raises(regen.Refused, lambda: s.guard_locks([proj]))
check("guard: refuses an open project", e is not None and len(e.items) == 2)
check("guard: the refusal names the holder and the live way",
      "sd@book" in str(e) and "live" in str(e))
s.guard_locks([proj], saved_by=["sd@book"])
check("guard: a saved editor's locks pass", len(s.live_locks) == 2)
check("guard: another holder still refuses",
      raises(regen.Refused, lambda: s.guard_locks(
          [proj, other], saved_by=["sd@book"])) is not None)
os.environ[regen.SAVED_ENV] = "sd@book,sd@studio"
s.guard_locks([proj, other])
check("guard: KX_REGEN_SAVED_BY lets a generator subprocess through",
      len(s.live_locks) == 3)
del os.environ[regen.SAVED_ENV]

# --- the session, with a fake editor driver --------------------------------
calls = []
behaviour = {}


def fake_call(action, stem, host=None, display=None):
    calls.append((action, stem, host))
    err = behaviour.get(action)
    if err:
        raise live_sch.NotLive(err)
    return {"saved": True, "title": f"{stem} — Schematic Editor"}


live_sch.call = fake_call
sheet = proj / "rev3.kicad_sch"

with live_sch.session([proj, other]) as lv:
    env_inside = os.environ.get(regen.SAVED_ENV)
    sheet.write_text("(kicad_sch (generated))\n")
check("session: editor saved before the generator, reverted after",
      [c[0] for c in calls] == ["save", "revert"]
      and calls[0][1:] == ("rev3", "book"))
check("session: the generator sees both holders",
      env_inside == "sd@book,sd@studio")
check("session: env restored afterwards",
      regen.SAVED_ENV not in os.environ)

calls.clear()
with live_sch.session([proj]) as lv:
    pass
check("session: nothing changed on disk -> no revert",
      [c[0] for c in calls] == ["save"]
      and any("no sheet changed" in x for x in lv.log))

calls.clear()
behaviour["save"] = "still shows unsaved changes after Ctrl+S"
ran = []


def body():
    with live_sch.session([proj]):
        ran.append(1)
        sheet.write_text("(kicad_sch (must not happen))\n")


e = raises(live_sch.NotLive, body)
check("session: an editor that cannot be saved refuses before writing",
      e is not None and not ran and "must not" not in sheet.read_text())
behaviour.clear()

behaviour["revert"] = "no revert confirmation appeared"


def body2():
    with live_sch.session([proj]):
        sheet.write_text("(kicad_sch (regenerated again))\n")


e = raises(live_sch.ReloadFailed, body2)
check("session: a failed reload is ReloadFailed (sheets written, say so)",
      e is not None and "File > Revert" in str(e))
behaviour.clear()

e = raises(live_sch.NotLive, lambda: live_sch.session(
    [tmp / "nolocks"]).__enter__())
check("session: nothing held -> NotLive (plain regeneration)",
      e is not None)

r = live_sch.regen([proj], [sys.executable, "-c",
                            "import os, pathlib; pathlib.Path("
                            f"{str(sheet)!r}).write_text(os.environ["
                            f"{regen.SAVED_ENV!r}])"])
check("regen: runs the command with the holder env, then reverts",
      r["ok"] and r["live"] and sheet.read_text() == "sd@book"
      and calls[-1][0] == "revert")

# --- capability words --------------------------------------------------------
check("sch_scope: KiCad 10.0.7 measures list-only",
      live.sch_scope({"documents": ["a.kicad_sch"], "get_items": False,
                      "item_write": False, "SaveDocument": False,
                      "RevertDocument": False}) == "list-only")
check("sch_scope: save+revert",
      live.sch_scope({"documents": ["a"], "SaveDocument": True,
                      "RevertDocument": True}) == "save+revert")
check("sch_scope: items", live.sch_scope(
    {"documents": ["a"], "get_items": True, "item_write": True}) == "items")
check("sch_scope: nothing open", live.sch_scope({"documents": []}) == "none")

print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall OK")
sys.exit(1 if FAILS else 0)
