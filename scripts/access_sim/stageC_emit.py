"""Stage C: write the circuit deck for a write followed by a read.

Two clock cycles on the column slice of an extracted block: the first
writes a bit into the addressed cell, the second reads it back.  Used by
writeread_sweep.py for every width; the control edges are placed at the
times stage A measured.
"""
import pathlib as _pl
_REPO = str(_pl.Path(__file__).resolve().parents[2])
import os, sys, json
sys.path.insert(0, _REPO + '')
from collections import namedtuple
from sramforge.spiceslice import Slice

SCRATCH = os.environ.get(
    "SRAMFORGE_SCRATCH",
    _REPO + '/out/access')
os.makedirs(SCRATCH, exist_ok=True)
data = json.load(open(os.environ.get(
    "SRAMFORGE_NETLIST",
    _REPO + '/out/refbank_netlist.json')))
D = namedtuple("Device", "model nfin gate sd bbox")
class Ex: pass
ex = Ex()
ex.devices = [D(d["model"], d["nfin"], d["gate"], frozenset(d["sd"]), None)
              for d in data["devices"]]
ex.net_labels = {n: set(l) for n, l in data["labels"].items()}

CTRL_BOUND = (["wlb<0>", "wlt<0>", "sae", "saprechn", "wrena", "wrenan",
               "blprechtn", "blprechbn", "prechn", "WLENA"]
              + [f"yselt<{i}>" for i in range(4)] + [f"yselb<{i}>" for i in range(4)]
              + [f"yseltn<{i}>" for i in range(4)] + [f"yselbn<{i}>" for i in range(4)]
              + [f"wl<{i}>" for i in range(1, 32)]
              + [f"wlt<{i}>" for i in range(1, 32)] + [f"wlb<{i}>" for i in range(1, 32)])
sl = Slice(ex, ["dataout<0>"], extra_labels=["wd<0>"], boundary_labels=CTRL_BOUND)
sim = sl.sim
pairs = sl.cells()
print(f"slice: {len(sl.devices)} devices, {len(pairs)} cells")

# Two access cycles.  Edge TIMES are the measured stage-A numbers
# (clk 50% = 0.5275; WL +55 ps; wrena +40 ps; blprech release +85 ps;
# sae +170 ps); shapes are analytic 25 ps ramps; pulse widths modeled.
E = 0.025
def pulse(rise, fall, lo=0.0, hi=0.7):
    return (f"PWL(0 {lo} {rise}n {lo} {rise+E}n {hi} {fall}n {hi} "
            f"{fall+E}n {lo} 6n {lo})")

T1, T2 = 0.5275, 2.5275   # write-cycle and read-cycle clk edges
CTRL = {
    "wlb<0>":    f"PWL(0 0 {T1+0.055}n 0 {T1+0.08}n 0.7 {T1+1.0}n 0.7 {T1+1.025}n 0 "
                 f"{T2+0.055}n 0 {T2+0.08}n 0.7 {T2+1.0}n 0.7 {T2+1.025}n 0 6n 0)",
    "wrena":     pulse(T1+0.040, T1+1.0),
    "blprechbn": f"PWL(0 0 {T1+0.085}n 0 {T1+0.11}n 0.7 {T1+1.05}n 0.7 {T1+1.075}n 0 "
                 f"{T2+0.085}n 0 {T2+0.11}n 0.7 {T2+1.05}n 0.7 {T2+1.075}n 0 6n 0)",
    "sae":       pulse(T2+0.170, T2+1.0),
    "saprechn":  pulse(T2+0.120, T2+1.0),
    "wlt<0>":    "0", "blprechtn": "0", "wrenan": None,  # wrenan = complement
    "yselb<0>":  "0.7",
    "yselb<1>":  "0", "yselb<2>": "0", "yselb<3>": "0",
    "yselt<0>":  "0", "yselt<1>": "0", "yselt<2>": "0", "yselt<3>": "0",
}
def emit(run_name, wd_value):
    wlb0 = sorted(sim.label_nets["wlb<0>"], key=str)[0]
    pairset = {x for pp in pairs for x in pp}
    addressed = set()
    acc_side = {}
    for d in sl.devices:
        if d.model == "nmos_sram" and d.gate == wlb0 and len(d.sd) == 2:
            for pp in pairs:
                if set(d.sd) & set(pp):
                    addressed.add(pp)
    ic = {}
    for pp in pairs:
        a, b = pp
        ic[a], ic[b] = 0.7, 0.0
    # addressed cells preset OPPOSITE of what we will write, ORIENTED:
    # the BL-side storage node (access-device sd that is in the pair)
    # must start at NOT(wd) so the write genuinely has to flip the cell
    orient = {}
    for d in sl.devices:
        if d.model == "nmos_sram" and d.gate == wlb0 and len(d.sd) == 2:
            for pp in addressed:
                inpair = [x for x in d.sd if x in pp]
                outpair = [x for x in d.sd if x not in pp]
                if inpair and outpair:
                    orient[pp] = inpair[0]
    for pp in addressed:
        a, b = pp
        stor = orient.get(pp, a)
        other = b if stor == a else a
        ic[stor] = 0.0 if wd_value else 0.7
        ic[other] = 0.7 if wd_value else 0.0
    for n in sim.nets:
        labs = ex.net_labels.get(n, set())
        if labs & {"sa", "san", "sao", "saon"} or labs & {"BL", "BLN"} \
           or any(l.startswith("bl") for l in labs):
            ic[n] = 0.7
    sources = [f"Vwd {sl.node(sim.net_of('wd<0>'))} 0 {0.7 if wd_value else 0}"]
    srcnets = {sim.net_of('wd<0>')}
    for lbl, w in CTRL.items():
        if w is None:
            continue
        for k, n in enumerate(sorted(sim.label_nets.get(lbl, set()), key=str)):
            if n in sim.nets and n not in srcnets:
                srcnets.add(n)
                sources.append(f"V{lbl.replace('<','_').replace('>','_')}_{k} "
                               f"{sl.node(n)} 0 {w}")
    # wrenan = inverted wrena
    for k, n in enumerate(sorted(sim.label_nets.get("wrenan", set()), key=str)):
        if n in sim.nets and n not in srcnets:
            srcnets.add(n)
            inv = (f"PWL(0 0.7 {T1+0.040}n 0.7 {T1+0.065}n 0 {T1+1.0}n 0 "
                   f"{T1+1.025}n 0.7 6n 0.7)")
            sources.append(f"Vwrenan_{k} {sl.node(n)} 0 {inv}")
    handled = set(srcnets)
    for n in sorted(sim.boundary, key=str):
        if n in sim.nets and n not in handled:
            labs = ex.net_labels.get(n, set())
            if any(l.startswith("yselbn<0>") for l in labs):
                rest = 0.0
            elif any(l.startswith("yseltn") or l.startswith("yselbn") for l in labs):
                rest = 0.7
            else:
                rest = 0.0
            sources.append(f"Vb{sl.node(n)[1:]} {sl.node(n)} 0 {rest}")
            handled.add(n)
    do = sim.net_of("dataout<0>")
    saves = [do]
    for lbl in ("wlb<0>", "wrena", "sae"):
        # band v2 puts a label on several net pieces; only nets that are
        # actually IN the slice circuit are saveable
        live = [n for n in sorted(sim.label_nets[lbl], key=str)
                if n in sim.nets]
        if live:
            saves.append(live[0])
    # the SELECTED cell: its access transistor touches a bl<0>/bln<0> net
    bl0 = {n for n in sim.nets
           if ex.net_labels.get(n, set()) & {"bl<0>", "bln<0>"}}
    sel = None
    for d in sl.devices:
        if d.model == "nmos_sram" and d.gate == wlb0 and len(d.sd) == 2:
            if set(d.sd) & bl0:
                stor = next(x for x in d.sd if x not in bl0)
                for pp in addressed:
                    if stor in pp:
                        other = next(x for x in pp if x != stor)
                        sel = (stor, other)      # ORIENTED: [0]=BL side
    print("   selected cell:", sel)
    if sel:
        saves += list(sel)
    saves += [n for n in sorted(bl0, key=str) if n in sim.nets]
    for n in sim.nets:
        if ex.net_labels.get(n, set()) & {"sa", "san"}:
            saves.append(n)
    saves.append(sim.net_of("wd<0>"))
    seen = set(); saves = [x for x in saves if not (x in seen or seen.add(x))][:14]
    out = f"{SCRATCH}/stageC_{run_name}"
    sl.emit(out + ".sp",
            _REPO + "/spice/models/asap7_TT.osdi.sp",
            ic, sources, tran="5p 6n", saves=saves, ic_mode="ic", uic=True,
            options=[os.environ.get(
                "SRAMFORGE_SPICE_OPTS",
                ".options reltol=2e-3 abstol=1e-10 vntol=1e-5 rshunt=1e9 itl4=300 cshunt=2e-16 method=gear")])
    sp = open(out + ".sp").read().replace("OUT.raw", out + ".raw")
    open(out + ".sp", "w").write(sp)
    json.dump({"dataout": sl.node(do),
               "sel": [sl.node(x) for x in sel] if sel else None,
               "bl": [sl.node(x) for x in sorted(bl0, key=str)]},
              open(out + "_map.json", "w"))
    print("emitted", out + ".sp", f"({len(addressed)} addressed cells, writing {int(wd_value)})")

emit("w1", True)
emit("w0", False)
