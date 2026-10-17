"""Regenerate tests/fixtures/livepcb/livepcb.kicad_pcb (the live-PCB test
board: R1 R2 C1 J1, nets VIN/OUT/GND, 30x20 mm outline, B.Cu GND zone).
Runs under the flatpak KiCad pcbnew python:

  flatpak run --branch=beta --command=python3 org.kicad.KiCad \
      tools/gen_livepcb.py $PWD/tests/fixtures/livepcb/livepcb.kicad_pcb
"""
import pcbnew, sys
out = sys.argv[1]
b = pcbnew.BOARD()
fplib = "/app/extensions/Library/footprints"
def mm(v): return pcbnew.FromMM(v)
nets = {}
for n in ("GND", "VIN", "OUT"):
    ni = pcbnew.NETINFO_ITEM(b, n); b.Add(ni); nets[n] = ni
def place(lib, name, ref, val, x, y, padnets):
    fp = pcbnew.FootprintLoad(f"{fplib}/{lib}.pretty", name)
    fp.SetReference(ref); fp.SetValue(val)
    fp.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
    fp.SetFPIDAsString(f"{lib}:{name}")
    for p in fp.Pads():
        if p.GetNumber() in padnets: p.SetNet(nets[padnets[p.GetNumber()]])
    b.Add(fp)
place("Resistor_SMD", "R_0603_1608Metric", "R1", "10k", 110, 100, {"1": "VIN", "2": "OUT"})
place("Resistor_SMD", "R_0603_1608Metric", "R2", "10k", 115, 100, {"1": "OUT", "2": "GND"})
place("Capacitor_SMD", "C_0603_1608Metric", "C1", "100n", 120, 100, {"1": "OUT", "2": "GND"})
place("Connector_PinHeader_2.54mm", "PinHeader_1x03_P2.54mm_Vertical", "J1", "IN", 105, 105, {"1": "VIN", "2": "OUT", "3": "GND"})
pts = [(100, 95), (130, 95), (130, 115), (100, 115)]
for a, c in zip(pts, pts[1:] + pts[:1]):
    s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1]))); s.SetEnd(pcbnew.VECTOR2I(mm(c[0]), mm(c[1])))
    s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(0.1)); b.Add(s)
z = pcbnew.ZONE(b); z.SetLayer(pcbnew.B_Cu); z.SetNet(nets["GND"])
ol = z.Outline(); ol.NewOutline()
for x, y in pts: ol.Append(mm(x), mm(y))
b.Add(z)
pcbnew.SaveBoard(out, b)
print("ok", out)
