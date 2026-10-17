"""Live-PCB logic without a KiCad: socket discovery (flatpak/native paths,
liveness, stale files, api.enable_server), target selection, the GUI-lock
write refusal, the one-commit wrapper, placement planning/readback/files,
the ssh forward command and the headless scratch config. Hermetic: fake
$HOME and $XDG_RUNTIME_DIR in temp dirs, real unix sockets.

Usage: python3 tests/test_live_pcb.py
"""

import json
import os
import pathlib
import socket
import sys
import tempfile
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kicad_lib import ipc_discovery as disc  # noqa: E402
from kicad_lib import live, live_headless, live_pcb, live_remote  # noqa: E402
from kicad_lib import sexp  # noqa: E402
from kicad_lib.live_ops import committed  # noqa: E402

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


def serve(path):
    """A listening unix socket that accepts and hangs up (like any live
    server would answer a bare connect)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.listen(16)

    def loop():
        while True:
            try:
                c, _ = s.accept()
                c.close()
            except OSError:
                return
    threading.Thread(target=loop, daemon=True).start()
    return s


td = tempfile.mkdtemp(prefix="kx_livepcb_")
home = pathlib.Path(td) / "home"
run = pathlib.Path(td) / "run"
run.mkdir()
saved = {k: os.environ.get(k) for k in ("HOME", "XDG_RUNTIME_DIR",
                                        "KICAD_API_SOCKET", "TMPDIR",
                                        "KICAD_CONFIG_HOME",
                                        "XDG_CONFIG_HOME", "KX_IGNORE_LOCK")}
for k in saved:
    os.environ.pop(k, None)
os.environ["HOME"] = str(home)
os.environ["XDG_RUNTIME_DIR"] = str(run)

# --- discovery: where the flatpak socket really is --------------------------
fdir = home / ".var/app/org.kicad.KiCad/cache/tmp/kicad"
fdir.mkdir(parents=True)
check("no sockets in an empty home", disc.candidates(str(home), {}) == [])
main_sock = fdir / "api.sock"
srv = serve(main_sock)
(fdir / "api-4242.sock").touch()   # second instance's name, stale file
(fdir / "api.lock").touch()        # not a socket, must be ignored
c = disc.candidates(str(home), {})
check("flatpak socket under ~/.var/app/<id>/cache/tmp/kicad",
      c[0] == (str(main_sock), "flatpak"))
check("api-<pid>.sock found after api.sock, lock ignored",
      [pathlib.Path(p).name for p, _ in c] == ["api.sock", "api-4242.sock"])
check("listening: bound socket yes", disc.listening(str(main_sock)))
check("listening: leftover file no",
      not disc.listening(str(fdir / "api-4242.sock")))
rep = disc.report(str(home), {})
check("report picks the listening one",
      rep["socket"] == str(main_sock) and rep["source"] == "flatpak")
check("report lists the stale one", rep["stale"] == [str(fdir / "api-4242.sock")])

ndir = pathlib.Path(td) / "tmpdir/kicad"
ndir.mkdir(parents=True)
(ndir / "api.sock").touch()
c = disc.candidates(str(home), {"TMPDIR": str(ndir.parent)})
check("native socket under $TMPDIR/kicad, after flatpak",
      (str(ndir / "api.sock"), "native") in c and c[0][1] == "flatpak")

# --- api.enable_server settings ----------------------------------------------
fcfg = home / ".var/app/org.kicad.KiCad/config/kicad"
for ver, on in (("9.0", True), ("10.0", False)):
    (fcfg / ver).mkdir(parents=True)
    (fcfg / ver / "kicad_common.json").write_text(
        json.dumps({"api": {"enable_server": on}}))
(home / ".config/kicad/10.99").mkdir(parents=True)
(home / ".config/kicad/10.99/kicad_common.json").write_text(
    json.dumps({"api": {"enable_server": True}}))
(home / ".config/kicad/10.98").mkdir(parents=True)
(home / ".config/kicad/10.98/kicad_common.json").write_text("{not json")
st = disc.api_settings(str(home), {})
check("settings: flatpak newest first",
      [(s["install"], s["version"]) for s in st][:2]
      == [("flatpak", "10.0"), ("flatpak", "9.0")])
check("settings: enable_server read",
      st[0]["enable_server"] is False and st[1]["enable_server"] is True)
check("settings: native 10.99 seen", any(
    s["install"] == "native" and s["version"] == "10.99"
    and s["enable_server"] for s in st))
check("settings: broken json -> None, no crash", any(
    s["version"] == "10.98" and s["enable_server"] is None for s in st))
ov = pathlib.Path(td) / "cfgover/10.0"
ov.mkdir(parents=True)
(ov / "kicad_common.json").write_text(json.dumps({"api": {
    "enable_server": True}}))
st2 = disc.api_settings(str(home), {"KICAD_CONFIG_HOME": str(ov.parent)})
check("settings: KICAD_CONFIG_HOME listed first",
      st2[0]["install"] == "override")

why = disc.why_not({"socket": None, "stale": [], "api_settings": st})
check("why_not names the OFF install and the fix",
      "OFF in flatpak 10.0" in why and "Enable KiCad API" in why)
check("why_not names where it is ON", "ON in native 10.99" in why)
check("why_not empty when a socket is live",
      disc.why_not({"socket": "/x"}) == "")

# --- target selection (env > target > discovery) ----------------------------
check("live.ipc_socket = discovered flatpak socket",
      live.ipc_socket() == str(main_sock))
fwd = run / "kx" / "fwd.sock"
fwd.parent.mkdir(parents=True, exist_ok=True)
fsrv = serve(fwd)
live.write_target({"kind": "remote", "host": "book", "socket": str(fwd)})
info = live.ipc_socket_info()
check("listening target beats discovery",
      info["socket"] == str(fwd) and info["source"] == "remote")
fsrv.close()
fwd.unlink()
info = live.ipc_socket_info()
check("dead target ignored, reported, discovery used",
      info["socket"] == str(main_sock) and info["stale_target"]["host"]
      == "book")
os.environ["KICAD_API_SOCKET"] = "ipc:///some/where.sock"
check("KICAD_API_SOCKET wins, ipc:// stripped",
      live.ipc_socket_info()["socket"] == "/some/where.sock"
      and live.ipc_socket_info()["source"] == "env")
del os.environ["KICAD_API_SOCKET"]
live.clear_target()
check("scope follows the SERVER version, not kicad-cli",
      live.ipc_scope("10.0.7", 11) == "pcb"
      and live.ipc_scope("10.99.0", 10) == "pcb+sch"
      and live.ipc_scope(None, 11) == "pcb+sch")

# --- GUI lock refuses .kicad_pcb file writes --------------------------------
proj = pathlib.Path(td) / "proj"
proj.mkdir()
pcbf = proj / "b.kicad_pcb"
tree = sexp.parse("(kicad_pcb (version 20260206))")
sexp.save_file(str(pcbf), tree)
check("write ok without a lock", pcbf.exists())
(proj / "~b.kicad_pcb.lck").write_text(
    json.dumps({"hostname": "book", "username": "sd"}))
e = raises(live.HeldByGui, lambda: sexp.save_file(str(pcbf), tree))
check("save_file refuses a held .kicad_pcb", e is not None)
check("refusal names the holder and live mode",
      e is not None and "sd@book" in str(e) and "kx live pcb" in str(e))
(proj / "~s.kicad_sch.lck").touch()
sexp.save_file(str(proj / "s.kicad_sch"), tree)
check("schematic writes are not refused (pcb-only rule)",
      (proj / "s.kicad_sch").exists())
os.environ["KX_IGNORE_LOCK"] = "1"
check("KX_IGNORE_LOCK=1 overrides a stale lock",
      raises(live.HeldByGui, lambda: sexp.save_file(str(pcbf), tree))
      is None)
del os.environ["KX_IGNORE_LOCK"]
d = live.detect(str(proj))
check("detect: file backend blocked, note points to live mode",
      not d["backends"]["file"]["writable_now"]
      and "kx live pcb" in d["backends"]["file"]["note"])
check("detect: reports api settings + stale sockets",
      d["api_server_settings"] and str(fdir / "api-4242.sock")
      in d["ipc_stale_sockets"])


# --- one commit per op ---------------------------------------------------------
class FakeDoc:
    def __init__(self, fail_push=False):
        self.log, self.fail_push = [], fail_push

    def begin_commit(self):
        self.log.append("begin")
        return "C"

    def push_commit(self, c, msg):
        if self.fail_push:
            raise RuntimeError("push failed")
        self.log.append(("push", c, msg))

    def drop_commit(self, c):
        self.log.append(("drop", c))


doc = FakeDoc()
with committed(doc, "kx move R1"):
    doc.log.append("edit")
check("committed: begin, edit, push(msg)",
      doc.log == ["begin", "edit", ("push", "C", "kx move R1")])
doc = FakeDoc()


def _boom(d):
    with committed(d, "x"):
        d.log.append("edit")
        raise ValueError("bad")


check("committed: exception drops + re-raises",
      raises(ValueError, lambda: _boom(doc)) is not None
      and doc.log == ["begin", "edit", ("drop", "C")])
doc = FakeDoc(fail_push=True)


def _ok(d):
    with committed(d, "x"):
        d.log.append("edit")


check("committed: failed push drops the commit",
      raises(RuntimeError, lambda: _ok(doc)) is not None
      and doc.log[-1] == ("drop", "C"))

# --- placement plan / readback / files ----------------------------------------
cur = {"R1": {"ref": "R1", "x": 10.0, "y": 20.0, "rot": 0.0, "side": "top",
              "locked": False},
       "J1": {"ref": "J1", "x": 0.0, "y": 0.0, "rot": 90.0, "side": "top",
              "locked": True}}
ch, err = live_pcb.plan_placement(cur, {"R1": {"x": 1, "y": 2, "rot": 450}})
check("plan: valid move, rot normalised",
      not err and ch == [{"ref": "R1", "flip": False, "x": 1.0, "y": 2.0,
                          "rot": 90.0}])
ch, err = live_pcb.plan_placement(cur, {"R1": {"side": "bottom"}})
check("plan: side change -> flip", not err and ch[0]["flip"])
_, err = live_pcb.plan_placement(cur, {"R1": {"x": 1}, "Q9": {"x": 1,
                                                              "y": 1}})
check("plan: unknown ref + half coordinate refused",
      any("Q9" in e for e in err) and any("both x and y" in e for e in err))
_, err = live_pcb.plan_placement(cur, {"J1": {"rot": 0}})
check("plan: locked footprint refused", any("locked" in e for e in err))
_, err = live_pcb.plan_placement(cur, {"J1": {"rot": 0}}, force=True)
check("plan: --force allows locked", not err)
_, err = live_pcb.plan_placement(cur, {"R1": {"side": "left", "z": 1}})
check("plan: bad side + unknown key refused",
      any("side must be" in e for e in err) and any("unknown keys" in e
                                                    for e in err))
after = {"R1": dict(cur["R1"], x=1.0000004, y=2.0, rot=-180.0)}
check("verify: within tolerance, 180 == -180",
      live_pcb.verify_placement(after, {"R1": {"x": 1, "y": 2,
                                               "rot": 180}}) == [])
bad = live_pcb.verify_placement(after, {"R1": {"x": 1.1, "y": 2,
                                               "side": "bottom"},
                                        "C9": {"x": 0, "y": 0}})
check("verify: wrong x, wrong side, vanished ref all reported",
      len(bad) == 3)


class RefuseBoard:  # refusal must send nothing — no commit at all
    def __init__(self):
        self.calls = []

    def get_footprints(self):
        return []

    def begin_commit(self):
        self.calls.append("begin")


rb = RefuseBoard()
out = live_pcb.apply_placement(rb, {"R1": {"x": 0, "y": 0}}, "kx t")
check("apply: refusal opens no commit", out.get("refused")
      and rb.calls == [])

pj = proj / "p.json"
pj.write_text(json.dumps({"R1": {"x": 1, "y": 2}}))
check("load: json dict", live_pcb.load_placement(str(pj))
      == {"R1": {"x": 1, "y": 2}})
pj.write_text(json.dumps([{"ref": "R2", "x": 3, "y": 4, "side": "bottom"}]))
check("load: json list", live_pcb.load_placement(str(pj))
      == {"R2": {"x": 3, "y": 4, "side": "bottom"}})
pc = proj / "p.csv"
pc.write_text("﻿Designator,Mid X,Mid Y,Layer,Rotation\n"
              "R1,10.5mm,20mm,Top,90\nC1,1,2,Bottom,\n\n")
check("load: csv header aliases, mm suffix, Top/Bottom",
      live_pcb.load_placement(str(pc))
      == {"R1": {"x": 10.5, "y": 20.0, "rot": 90.0, "side": "top"},
          "C1": {"x": 1.0, "y": 2.0, "side": "bottom"}})
pc.write_text("foo,bar\n1,2\n")
check("load: csv without a ref column refused",
      raises(ValueError, lambda: live_pcb.load_placement(str(pc)))
      is not None)

# --- ssh forward + headless config ---------------------------------------------
loc, ctl = live_remote.paths_for("book.home.avatarsd.com")
check("forward paths in the private runtime dir",
      loc.parent == run / "kx" and loc.name == "book.home.avatarsd.com.sock")
argv = live_remote.forward_argv("book", "/r/api.sock", str(loc), str(ctl))
check("forward argv: unix -L local:remote, master ctl, unlink, batch",
      argv[0] == "ssh" and f"{loc}:/r/api.sock" in argv
      and "StreamLocalBindUnlink=yes" in argv and "-M" in argv
      and "BatchMode=yes" in argv and argv[-1] == "book")
check("disconnect without a remote target is a no-op",
      live_remote.disconnect()["disconnected"] is False)

cfg = live_headless.scratch_config(pathlib.Path(td) / "hcfg",
                                   "10.0.7-rc2")
common = json.loads((cfg / "10.0/kicad_common.json").read_text())
check("headless config: API on, first-run prompts answered",
      common["api"]["enable_server"] is True
      and common["do_not_show_again"]["update_check_prompt"] is True
      and common["do_not_show_again"]["data_collection_prompt"] is True)
check("headless config: design-block table present",
      (cfg / "10.0/design-block-lib-table").exists())
check("headless config: user's real config untouched",
      json.loads((fcfg / "10.0/kicad_common.json").read_text())
      == {"api": {"enable_server": False}})
check("headless missing() returns a list", isinstance(
    live_headless.missing(), list))

srv.close()
for k, v in saved.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v
print("PASS" if not FAILS else f"FAIL ({len(FAILS)})")
raise SystemExit(0 if not FAILS else 1)
