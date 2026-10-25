#!/usr/bin/env python3
"""Merge the re-P&R'd control block into a reference bank and run rung 2."""
import sys, pathlib, importlib.util
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import json, os
import klayout.db as kdb
from sramforge import BankSpec
from sramforge.generator import generate
import sramforge.generator as gen
from sramforge.library import CellLibrary

CTRL = "control_sram_2kB_6t122_v3_orig10242022_lvspass_32"
CTL_DISP = (-32204, 8076)
LAYERS = {"m2": (20, 0), "m3": (30, 0), "m4": (40, 0), "m5": (50, 0),
          "v3": (35, 0), "v4": (45, 0)}

spec = BankSpec()
layout = kdb.Layout(); layout.dbu = 0.00025
lib = CellLibrary(layout)
bank = gen.build_bank(layout, lib, spec)

# swap the control macro inside the band cell
band = layout.cell("ctrl_decode_32")
for inst in list(band.each_inst()):
    if inst.cell.name == CTRL:
        band.erase(inst)
src = kdb.Layout(); src.read(str(REPO / "pnr/out/control_pnr.gds"))
newc = layout.create_cell("control_pnr")
newc.copy_tree(src.cell("control_sram_2kB"))
band.insert(kdb.CellInstArray(newc.cell_index(),
    kdb.Trans(kdb.Vector(CTL_DISP[0] + 33500, CTL_DISP[1] - 4720))))

GLUE = os.environ.get("GLUE", "all")
# paste the interface glue (original vertical via stacks)
d = json.load(open(REPO / "out/control_netlist.json"))
n = 0
for net, shapes in (d["glue"].items() if GLUE in ("all", "io") else []):
    for cname, x0, y0, x1, y1 in shapes:
        li = layout.layer(*LAYERS[cname])
        band.shapes(li).insert(kdb.Box(x0 + CTL_DISP[0], y0 + CTL_DISP[1],
                                       x1 + CTL_DISP[0], y1 + CTL_DISP[1]))
        n += 1
print(f"glue shapes pasted: {n}")

# supply web glue: original strap shapes + via cells
for cname, x0, y0, x1, y1 in (d.get("supply_glue", []) if GLUE in ("all", "supply") else []):
    li = layout.layer(*LAYERS[cname])
    band.shapes(li).insert(kdb.Box(x0 + CTL_DISP[0], y0 + CTL_DISP[1],
                                   x1 + CTL_DISP[0], y1 + CTL_DISP[1]))
ROT = {"r0": kdb.Trans.R0, "r90": kdb.Trans.R90, "r180": kdb.Trans.R180,
       "r270": kdb.Trans.R270, "m0": kdb.Trans.M0, "m45": kdb.Trans.M45,
       "m90": kdb.Trans.M90, "m135": kdb.Trans.M135}
nv = 0
for cellname, x, y, tstr in (d.get("supply_via_cells", []) if GLUE in ("all", "supply") else []):
    c = layout.cell(cellname)
    if c is None:
        continue
    rot = ROT.get(tstr.split()[0], kdb.Trans.R0)
    band.insert(kdb.CellInstArray(c.cell_index(),
        kdb.Trans(rot, kdb.Vector(x + CTL_DISP[0], y + CTL_DISP[1]))))
    nv += 1
print(f"supply glue: {len(d.get('supply_glue', []))} straps, {nv} via cells")
gds = str(REPO / "out/merged_ctrl_test.gds")
layout.write(gds)
print("wrote", gds)

def load(name):
    s = importlib.util.spec_from_file_location(name, REPO / "tests" / f"{name}.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    return m
t_con = load("test_connectivity")
fails = t_con.check(spec, gds)
for f in fails[:10]: print("   -", f)
print("[FAIL]" if fails else "[PASS]", "rung 2 on merged bank")
