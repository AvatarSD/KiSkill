"""Save / revert an OPEN KiCad schematic editor through its own X11 window.

Why this exists: KiCad 10.0.7 serves almost nothing for schematics over
IPC (measured, `kx live sch caps`): GetOpenDocuments, plus Create/Update
handlers that reject every schematic item type ("Could not decode a valid
type"); no GetItems, no SaveDocument, no RevertDocument, no RunAction —
those are PCB-only in 10. So the editor is driven the way the operator
drives it: Ctrl+S, then File > Revert, with xdotool on the editor's X11
display (Xvfb, a desktop X server, or XWayland — KiCad's flatpak is an
X11 client even on a Wayland desktop).

Every step is checked, and the driver fails SAFE — it raises instead of
guessing, and never types into a window it did not identify:
  - the editor window is found by title, "<sheet> [<project>/<path>] —
    Schematic Editor" or "<project> — Schematic Editor"; KiCad prefixes
    "*" while the document has unsaved changes (that is how "modified"
    is read);
  - any other visible KiCad window (a dialog the operator has open)
    refuses the whole operation — nothing is typed; known non-modal
    frames (3D Viewer, library browsers, simulator...) are harmless;
  - save = Ctrl+S, then the "*" must disappear (else NotSaved);
  - revert = File menu > Revert (the item index depends on how the editor
    runs: 5 in standalone eeschema, 2 under the project manager, told
    apart by WM_CLASS), then the "Confirmation" dialog must appear and is
    answered Yes; any other window that appears is cancelled (Escape)
    and the call raises.

Self-contained (stdlib only, no package imports): live_sch pipes this file
to `ssh HOST python3 - ACTION ...` to drive an editor on another host,
exactly as live_remote does with ipc_discovery.

  python3 x11_editor.py find PROJECT_STEM [DISPLAY]
  python3 x11_editor.py save|revert PROJECT_STEM [DISPLAY]
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time

EDITOR_SUFFIX = " — Schematic Editor"
FRAME_RE = re.compile(r" — (Schematic Editor|PCB Editor|Symbol Editor|"
                      r"Footprint Editor|KiCad \d+(\.\d+)*)$")
# Non-modal KiCad frames that may stay open beside the editor: they block
# no keystroke, so they are not "a dialog the operator has open". Matched
# as the whole title or a "NAME — ..." / "... — NAME" part.
NONMODAL_FRAMES = ("3D Viewer", "Footprint Library Browser",
                   "Symbol Library Browser", "Footprint Viewer",
                   "Symbol Viewer", "Footprint Wizard", "Simulator",
                   "SPICE Simulator", "Gerber Viewer", "Calculator Tools",
                   "Image Converter", "Drawing Sheet Editor",
                   "Bitmap to Component Converter")
NONMODAL_RE = re.compile(
    r"(^|\s—\s)(" + "|".join(map(re.escape, NONMODAL_FRAMES)) +
    r")(\s*(\[[^\]]*\]|\([^)]*\)))?(\s—\s|$)")


def is_frame(title: str) -> bool:
    """A main frame or a known non-modal frame (not an open dialog)."""
    return bool(FRAME_RE.search(title) or NONMODAL_RE.search(title))


# File menu position of Revert (first item highlighted when the menu
# opens): standalone eeschema has New, Open, Open Recent, Save, Save As,
# Revert; under the project manager Save, Save Current Sheet Copy As,
# Revert (KiCad 10 eeschema/menubar.cpp, measured on 10.0.7)
REVERT_DOWNS = {"eeschema": 5, "kicad": 2}
CONFIRM_TITLE = "Confirmation"
# a dialog that stays this long is a question for the operator; shorter
# ones are KiCad's progress windows ("Load Schematic" during a revert)
DIALOG_GRACE_S = 5.0


class EditorError(RuntimeError):
    pass


class NotSaved(EditorError):
    """The editor still shows unsaved changes after Ctrl+S."""


def parse_title(title: str) -> dict | None:
    """Schematic-editor title -> {project, sheet, modified}; None for any
    other window."""
    if not title.endswith(EDITOR_SUFFIX):
        return None
    t = title[:-len(EDITOR_SUFFIX)]
    modified = t.startswith("*")
    t = t.lstrip("*")
    m = re.match(r"^(.*) \[([^/\]]+)(/[^\]]*)?\]$", t)
    if m:
        return {"project": m.group(2), "sheet": m.group(1),
                "modified": modified}
    return {"project": t, "sheet": t, "modified": modified}


def pick_editor(windows: list[dict], project: str) -> dict:
    """The one schematic-editor window of project among windows
    [{id, title, cls}]."""
    hits = [dict(w, **p) for w in windows
            if (p := parse_title(w["title"])) and p["project"] == project]
    if not hits:
        raise EditorError(f"no schematic editor for project '{project}' "
                          f"(windows: {[w['title'] for w in windows][:8]})")
    if len(hits) > 1:
        raise EditorError(f"{len(hits)} schematic editors for '{project}'")
    return hits[0]


def dialogs(windows: list[dict]) -> list[str]:
    """Visible KiCad windows that are not main frames = open dialogs."""
    return [w["title"] for w in windows
            if w["title"] and not is_frame(w["title"])]


_XAUTH: dict[str, str] = {}   # DISPLAY -> the KiCad process's XAUTHORITY


def find_display(project: str) -> str | None:
    """DISPLAY of the running KiCad that has project open (its process
    environment; a flatpak sandbox shares the host /proc). Falls back to
    any KiCad's DISPLAY, then to $DISPLAY. The same process's XAUTHORITY
    is remembered for X11() — an XWayland server wants its cookie, which
    an ssh session does not have."""
    best, any_ = None, None
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
            exe = os.path.basename(argv[0].decode(errors="replace"))
            if exe not in ("kicad", "eeschema"):
                continue
            with open(f"/proc/{p}/environ", "rb") as f:
                env = dict(x.split(b"=", 1) for x in f.read().split(b"\0")
                           if b"=" in x)
        except OSError:
            continue
        d = env.get(b"DISPLAY", b"").decode()
        if not d:
            continue
        xa = env.get(b"XAUTHORITY", b"").decode()
        if xa and os.path.exists(xa):
            _XAUTH.setdefault(d, xa)
        any_ = any_ or d
        if any(re.search(rf"(^|/){re.escape(project)}\.kicad_(pro|sch)$",
                         a.decode(errors="replace")) for a in argv[1:]):
            best = best or d
    return best or any_ or os.environ.get("DISPLAY")


def _resolve(project: str, display: str | None) -> str:
    found = find_display(project)          # also learns XAUTHORITY
    display = display or found
    if not display:
        raise EditorError("no X display found for a running KiCad")
    return display


class X11:
    def __init__(self, display: str):
        if not shutil.which("xdotool"):
            raise EditorError("xdotool is not installed on this host")
        self.env = dict(os.environ, DISPLAY=display)
        if display in _XAUTH:
            self.env["XAUTHORITY"] = _XAUTH[display]
        self.display = display

    def run(self, *args, check=True) -> str:
        r = subprocess.run(["xdotool", *args], capture_output=True,
                           text=True, env=self.env, timeout=30)
        if check and r.returncode != 0:
            raise EditorError(f"xdotool {' '.join(args)}: "
                              f"{r.stderr.strip()[-200:]}")
        return r.stdout

    def wm_class(self, wid: str) -> str:
        if not shutil.which("xprop"):
            return ""
        r = subprocess.run(["xprop", "-id", wid, "WM_CLASS"],
                           capture_output=True, text=True, env=self.env)
        m = re.search(r'= "([^"]*)"', r.stdout)
        return m.group(1).lower() if m else ""

    def windows(self) -> list[dict]:
        """Visible KiCad windows (class kicad or eeschema)."""
        ids = []
        for cls in ("kicad", "eeschema"):
            ids += self.run("search", "--onlyvisible", "--class", cls,
                            check=False).split()
        out = []
        for wid in dict.fromkeys(ids):
            title = self.run("getwindowname", wid, check=False).strip()
            if title in ("", "kicad", "eeschema"):
                continue  # GTK's unmapped leader windows
            out.append({"id": wid, "title": title, "cls": self.wm_class(wid)})
        return out

    def focus(self, wid: str) -> None:
        # a window manager wants activation; bare Xvfb has none
        if subprocess.run(["xdotool", "windowactivate", "--sync", wid],
                          capture_output=True, env=self.env,
                          timeout=10).returncode != 0:
            self.run("windowfocus", "--sync", wid)

    def title(self, wid: str) -> str:
        return self.run("getwindowname", wid, check=False).strip()


def _editor(x: X11, project: str) -> dict:
    wins = x.windows()
    ed = pick_editor(wins, project)
    dl = dialogs(wins)
    if dl:
        raise EditorError(f"KiCad has a dialog open ({dl}) — close it, "
                          "nothing was typed")
    return ed


def find(project: str, display: str | None = None) -> dict:
    display = _resolve(project, display)
    if not display:
        raise EditorError("no X display found for a running KiCad")
    x = X11(display)
    ed = pick_editor(x.windows(), project)
    return dict(ed, display=display)


def save(project: str, display: str | None = None,
         timeout_s: float = 20.0) -> dict:
    """Ctrl+S when the editor shows unsaved changes; verified."""
    display = _resolve(project, display)
    x = X11(display)
    ed = _editor(x, project)
    if not ed["modified"]:
        return {"saved": False, "needed": False, "title": ed["title"],
                "display": display}
    x.focus(ed["id"])
    x.run("key", "--clearmodifiers", "Escape", "Escape", "ctrl+s")
    deadline = time.monotonic() + timeout_s
    since = None
    while time.monotonic() < deadline:
        t = x.title(ed["id"])
        dl = dialogs(x.windows())
        if t and not t.startswith("*") and not dl:
            return {"saved": True, "needed": True, "title": t,
                    "display": display}
        since = (since or time.monotonic()) if dl else None
        if since and time.monotonic() - since > DIALOG_GRACE_S:
            raise NotSaved(f"Ctrl+S opened a dialog ({dl}) — answer it in "
                           "KiCad; nothing else was typed")
        time.sleep(0.3)
    raise NotSaved(f"'{ed['title']}' still shows unsaved changes after "
                   "Ctrl+S")


def revert(project: str, display: str | None = None,
           timeout_s: float = 60.0) -> dict:
    """File > Revert, answer Yes; verified by the dialog and the title."""
    display = _resolve(project, display)
    x = X11(display)
    ed = _editor(x, project)
    if ed["modified"]:
        raise EditorError(f"'{ed['title']}' has unsaved changes — revert "
                          "would discard them; save first")
    downs = REVERT_DOWNS.get(ed["cls"])
    if downs is None:
        raise EditorError(f"unknown editor class '{ed['cls']}' — cannot "
                          "place File > Revert")
    x.focus(ed["id"])
    x.run("key", "--clearmodifiers", "Escape", "alt+f")
    time.sleep(0.6)
    x.run("key", *(["Down"] * downs), "Return")
    deadline = time.monotonic() + 10
    confirm = None
    while time.monotonic() < deadline and confirm is None:
        time.sleep(0.3)
        for w in x.windows():
            if w["title"] == CONFIRM_TITLE:
                confirm = w
            elif w["title"] and not is_frame(w["title"]):
                x.focus(w["id"])
                x.run("key", "Escape")
                raise EditorError(f"File menu item {downs} opened "
                                  f"'{w['title']}', not Revert — cancelled")
    if confirm is None:
        x.run("key", "Escape", check=False)
        raise EditorError("no revert confirmation appeared")
    x.focus(confirm["id"])
    x.run("key", "Return")
    deadline = time.monotonic() + timeout_s
    since = None
    while time.monotonic() < deadline:
        time.sleep(0.5)
        wins = x.windows()
        dl = dialogs(wins)
        try:
            ed2 = pick_editor(wins, project)
        except EditorError:
            ed2 = None
        if ed2 and not dl:
            return {"reverted": True, "title": ed2["title"],
                    "display": display}
        since = (since or time.monotonic()) if dl else None
        if since and time.monotonic() - since > max(DIALOG_GRACE_S, 30):
            raise EditorError(f"reverted, but KiCad shows a dialog: {dl}")
    raise EditorError("revert did not finish")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("find", "save", "revert"):
        print(__doc__)
        return 2
    fn = {"find": find, "save": save, "revert": revert}[argv[0]]
    try:
        out = fn(argv[1], argv[2] if len(argv) > 2 else None)
        ok = True
    except EditorError as e:
        out = {"error": str(e), "kind": type(e).__name__}
        ok = False
    print(json.dumps(out))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
