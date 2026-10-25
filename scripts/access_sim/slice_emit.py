"""Write a single circuit deck for a whole read access.

One uncut slice from the clock to the data output.  It is too large
for the circuit simulator to converge reliably, which is why the
access is normally run as stages A and B instead.
"""
import pathlib as _pl
_REPO = str(_pl.Path(__file__).resolve().parents[2])
import sys, json
sys.path.insert(0, _REPO + '')
from collections import namedtuple
from sramforge.spiceslice import Slice

D = namedtuple("Device", "model nfin gate sd bbox")
data = json.load(open(_REPO + '/out/refbank_netlist.json'))
class Ex: pass
ex = Ex()
ex.devices = [D(d["model"], d["nfin"], d["gate"], frozenset(d["sd"]), None)
              for d in data["devices"]]
ex.net_labels = {n: set(l) for n, l in data["labels"].items()}

sl = Slice(ex, ["dataout<0>"],
           extra_labels=["clk", "banksel", "read", "byte_wrenab", "sae"]
           + [f"ADDRESS<{i}>" for i in range(8)]
           + [f"sdel<{i}>" for i in range(5)] + ["wd<0>"])
sim = sl.sim

# which half? look at the labels on the wl column-0 net inside the slice
wl0_nets = [n for n in sim.nets if "wl<0>" in ex.net_labels.get(n, set())]
for n in wl0_nets:
    print("wl<0> net labels:", sorted(ex.net_labels[n])[:8])
wl0 = sorted(wl0_nets)[0]  # deterministic pick
half_label = "wlb<0>" if "wlb<0>" in ex.net_labels[wl0] else (
             "wlt<0>" if "wlt<0>" in ex.net_labels[wl0] else "?")
print("half:", half_label)

# addressed cells: pairs with an ACC gated by wl0
pairs = sl.cells()
pairset = {n for p in pairs for n in p}
addressed = set()
for d in sl.devices:
    if d.model == "nmos_sram" and d.gate == wl0 and len(d.sd) == 2:
        for p in pairs:
            if set(d.sd) & set(p):
                addressed.add(p)
print(f"addressed cells on wl<0>: {len(addressed)}")

# bitline nets of the addressed cells (for saving)
blnets = set()
for d in sl.devices:
    if d.model == "nmos_sram" and d.gate == wl0 and len(d.sd) == 2:
        for n in d.sd:
            if n not in pairset:
                blnets.add(n)
print(f"bitline nets touched: {len(blnets)}")

json.dump({"wl0": wl0, "half": half_label,
           "addressed": [list(p) for p in sorted(addressed, key=str)],
           "blnets": sorted(blnets, key=str)},
          open(f"{_REPO + '/out/access'}/slice_meta.json", "w"))

# ---- digital pre-edge state from the switch-level simulator ----
def digital_state():
    f = {}
    for i in range(8):
        f[sim.net_of(f"ADDRESS<{i}>")] = 0
    f[sim.net_of("clk")] = 0
    f[sim.net_of("banksel")] = 1
    f[sim.net_of("read")] = 1
    f[sim.net_of("byte_wrenab")] = 0
    f[sim.net_of("wd<0>")] = 0
    for i in range(5):
        f[sim.net_of(f"sdel<{i}>")] = 1
    st = None
    for cyc in range(2):                      # warm-up: flush unknowns
        st, _ = sim.run(dict(f), state=st, max_steps=80)
        f1 = dict(f); f1[sim.net_of("clk")] = 1
        st, _ = sim.run(f1, state=st, max_steps=80)
    st, _ = sim.run(dict(f), state=st, max_steps=80)   # end at clk=0
    return st

PRESTATE = digital_state()
known = sum(1 for v in PRESTATE.values() if v in (0, 1))
print(f"digital pre-state: {known} nets determinate of {len(sim.nets)}")

def emit(run_name, addressed_high):
    ic = {}
    for n in sim.nets:
        v = PRESTATE.get(n)
        if v in (0, 1):
            ic[n] = 0.7 * v
    for p in pairs:
        a, b = p
        ic.setdefault(a, 0.7); ic.setdefault(b, 0.0)
    for p in addressed:
        a, b = p
        ic[a], ic[b] = (0.7, 0.0) if addressed_high else (0.0, 0.7)
    # sense-amp analog nodes: the switch sim cannot resolve them, but at
    # clk=0 they sit precharged high - preset them so the sense latch
    # does not start at a metastable contention point
    SENSE = {"sa", "san", "sao", "saob", "saon"}
    for n in sim.nets:
        if ex.net_labels.get(n, set()) & SENSE:
            ic[n] = 0.7
    def V(lbl, val):
        return f"V{lbl.replace('<','_').replace('>','_')} {sl.node(sim.net_of(lbl))} 0 {val}"
    sources = [
        f"Vclk {sl.node(sim.net_of('clk'))} 0 PWL(0 0 0.5n 0 0.55n 0.7 3n 0.7 3.05n 0)",
        V("banksel", 0.7), V("read", 0.7), V("byte_wrenab", 0),
        V("wd<0>", 0),
    ]
    for i in range(8):
        sources.append(V(f"ADDRESS<{i}>", 0.7 if (i == 7 and half_label.startswith('wlt')) else 0))
    for i in range(5):
        sources.append(V(f"sdel<{i}>", 0.7))
    saves = [sim.net_of("dataout<0>"), sim.net_of("clk"), wl0]
    try:
        saves.append(sim.net_of("sae"))
    except ValueError:
        pass
    saves += sorted(blnets, key=str)[:2]
    saves += list(sorted(addressed, key=str)[0])   # one addressed cell's q/qb
    out = _REPO + f"/out/access/access_{run_name}"
    sl.emit(out + ".sp",
            _REPO + "/spice/models/asap7_TT.osdi.sp",
            ic, sources, tran="10p 4n", saves=saves, ic_mode="ic", uic=True,
            options=[".options reltol=2e-3 abstol=1e-10 vntol=1e-5 rshunt=1e12 itl4=300 cshunt=2e-16 method=trap xmu=0.492"])
    sp = open(out + ".sp").read().replace("OUT.raw", out + ".raw")
    open(out + ".sp", "w").write(sp)
    print("emitted", out + ".sp", len(sp.splitlines()), "lines")

emit("hi", True)
emit("lo", False)
