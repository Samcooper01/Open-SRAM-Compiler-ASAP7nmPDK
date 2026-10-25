"""Stage A: write the circuit deck for the decoder and control band.

Takes the extracted netlist of the reference block, keeps only the
decoder and control logic (cut off where it meets the bit lines and the
sense amplifiers), and writes a deck that measures when the word line
and the sense enable rise after the clock.
"""
import pathlib as _pl
_REPO = str(_pl.Path(__file__).resolve().parents[2])
import sys, json
sys.path.insert(0, _REPO + '')
from collections import namedtuple, Counter
from sramforge.spiceslice import Slice

D = namedtuple("Device", "model nfin gate sd bbox")
data = json.load(open(_REPO + '/out/refbank_netlist.json'))
class Ex: pass
ex = Ex()
ex.devices = [D(d["model"], d["nfin"], d["gate"], frozenset(d["sd"]), None)
              for d in data["devices"]]
ex.net_labels = {n: set(l) for n, l in data["labels"].items()}

CTRL = (["wlb<0>", "wlt<0>", "sae", "saprechn", "wrena", "blprechtn", "blprechbn"]
        + [f"yselt<{i}>" for i in range(4)] + [f"yselb<{i}>" for i in range(4)])
INPUTS = (["clk", "banksel", "read", "byte_wrenab", "wd<0>"]
          + [f"ADDRESS<{i}>" for i in range(8)] + [f"sdel<{i}>" for i in range(5)])
BOUND = (["BL", "BLN", "sa", "san"] + [f"bl<{i}>" for i in range(4)]
         + [f"bln<{i}>" for i in range(4)] + ["bl", "bln"])
sl = Slice(ex, CTRL, extra_labels=INPUTS, boundary_labels=BOUND)
sim = sl.sim
print(f"stage A slice: {len(sl.devices)} devices, {len(sim.nets)} nets")
print("models:", dict(Counter(d.model for d in sl.devices)))
print("storage cells in slice (should be 0):", len(sl.cells()))

def base_inputs(clk):
    f = {}
    for i in range(8):
        f[sim.net_of(f"ADDRESS<{i}>")] = 0     # address 0 -> bottom half, wlb<0>
    f[sim.net_of("clk")] = clk
    f[sim.net_of("banksel")] = 1
    f[sim.net_of("read")] = 1
    f[sim.net_of("byte_wrenab")] = 0
    f[sim.net_of("wd<0>")] = 0
    for i in range(5):
        f[sim.net_of(f"sdel<{i}>")] = 1
    return f

st = None
for cyc in range(2):
    st, _ = sim.run(base_inputs(0), state=st, max_steps=80)
    st, _ = sim.run(base_inputs(1), state=st, max_steps=80)
st, _ = sim.run(base_inputs(0), state=st, max_steps=80)
known = sum(1 for n in sim.nets if st.get(n) in (0, 1))
print(f"digital pre-state: {known}/{len(sim.nets)} determinate")

ic = {n: 0.7 * st[n] for n in sim.nets if st.get(n) in (0, 1)}

def V(lbl, val):
    return f"V{lbl.replace('<','_').replace('>','_')} {sl.node(sim.net_of(lbl))} 0 {val}"
sources = [f"Vclk {sl.node(sim.net_of('clk'))} 0 PWL(0 0 0.5n 0 0.55n 0.7 3n 0.7 3.05n 0)",
           V("banksel", 0.7), V("read", 0.7), V("byte_wrenab", 0), V("wd<0>", 0)]
for i in range(8):
    sources.append(V(f"ADDRESS<{i}>", 0))
for i in range(5):
    sources.append(V(f"sdel<{i}>", 0.7))

saves = []
for lbl in ["clk"] + CTRL:
    try:
        saves.append(sim.net_of(lbl))
    except ValueError as e:
        print("skip save", lbl, e)
out = _REPO + "/out/access/stageA"
sl.emit(out + ".sp",
        _REPO + "/spice/models/asap7_TT.osdi.sp",
        ic, sources, tran="5p 4n", saves=saves, ic_mode="ic", uic=True,
        options=[".options reltol=2e-3 abstol=1e-10 vntol=1e-5 rshunt=1e12 itl4=200 cshunt=2e-16 method=gear"])
sp = open(out + ".sp").read().replace("OUT.raw", out + ".raw")
open(out + ".sp", "w").write(sp)
print("emitted", out + ".sp")
