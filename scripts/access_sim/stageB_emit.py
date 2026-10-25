"""Stage B: write the circuit decks for one read through a column.

Takes the column, sense amplifier and output slice of the extracted
reference block (256 real memory cells) and drives it with the
waveforms stage A measured.  Writes two decks that differ only in the
stored bit, so the data output must land on 1 in one and 0 in the other.
"""
import pathlib as _pl
_REPO = str(_pl.Path(__file__).resolve().parents[2])
import sys, json
sys.path.insert(0, _REPO + '')
sys.path.insert(0, _REPO + '/out/access')
from collections import namedtuple, Counter
from sramforge.spiceslice import Slice
from access_analyze import read_raw

SCRATCH = _REPO + '/out/access'
D = namedtuple("Device", "model nfin gate sd bbox")
data = json.load(open(_REPO + '/out/refbank_netlist.json'))
class Ex: pass
ex = Ex()
ex.devices = [D(d["model"], d["nfin"], d["gate"], frozenset(d["sd"]), None)
              for d in data["devices"]]
ex.net_labels = {n: set(l) for n, l in data["labels"].items()}

# Stage B: the column + sense + output latch, bounded at the CONTROL nets
# (which stage A measured).  Cone from dataout<0>, stopped at wordline,
# ysel, prech, sense-enable and write-control nets.
CTRL_BOUND = (["wlb<0>", "wlt<0>", "sae", "saprechn", "wrena", "wrenan",
               "blprechtn", "blprechbn", "prechn", "WLENA"]
              + [f"yselt<{i}>" for i in range(4)]
              + [f"yselb<{i}>" for i in range(4)]
              + [f"yseltn<{i}>" for i in range(4)]
              + [f"yselbn<{i}>" for i in range(4)]
              + [f"wl<{i}>" for i in range(1, 32)]    # other columns' wordlines
              + [f"wlt<{i}>" for i in range(1, 32)]
              + [f"wlb<{i}>" for i in range(1, 32)])
sl = Slice(ex, ["dataout<0>"], extra_labels=["wd<0>"], boundary_labels=CTRL_BOUND)
sim = sl.sim
print(f"stage B slice: {len(sl.devices)} devices, {len(sim.nets)} nets")
print("models:", dict(Counter(d.model for d in sl.devices)))
pairs = sl.cells()
print("cells:", len(pairs))

# stage A measured waveforms -> PWL sources for the boundary nets
names, pts = read_raw(f"{SCRATCH}/stageA.raw")
tcol = names.index("time")
def pwl_of(varname, decimate_mv=20):
    j = names.index(varname)
    out = [(pts[0][tcol], pts[0][j])]
    for p in pts[1:]:
        if abs(p[j] - out[-1][1]) > decimate_mv/1000 or p is pts[-1]:
            out.append((p[tcol], p[j]))
    return "PWL(" + " ".join(f"{t:.4e} {v:.4f}" for t, v in out) + ")"

# ---- boundary sources from stage A (PWL up to its end, then modeled tail)
tcol = names.index("time")
t_end = pts[-1][tcol]

def node_of(lbl):
    return "v(" + ("n" + str(sorted(sim.label_nets[lbl], key=str)[0]).replace(":", "_")) + ")"

def pwl(lbl, tail):
    """PWL from stage A samples; `tail` = list of (t, v) appended after
    the measured span (the modeled continuation, documented)."""
    var = node_of(lbl)
    j = names.index(var)
    out = [(pts[0][tcol], pts[0][j])]
    for p1 in pts[1:]:
        if abs(p1[j] - out[-1][1]) > 0.02:
            out.append((p1[tcol], p1[j]))
    out.append((pts[-1][tcol], pts[-1][j]))
    out += [(t, v) for t, v in tail if t > out[-1][0]]
    return "PWL(" + " ".join(f"{t:.4e} {v:.4f}" for t, v in out) + ")"

# measured up to sae rise; continuations: hold, then shut off at clk fall
TAILS = {
    "wlb<0>":    [(2.5e-9, 0.7), (2.55e-9, 0.0), (4e-9, 0.0)],
    "wlt<0>":    [(4e-9, 0.0)],
    "sae":       [(0.75e-9, 0.7), (2.5e-9, 0.7), (2.55e-9, 0.0), (4e-9, 0.0)],
    "saprechn":  [(0.75e-9, 0.7), (2.5e-9, 0.7), (2.55e-9, 0.0), (4e-9, 0.0)],
    "wrena":     [(4e-9, 0.0)],
    "blprechtn": [(2.5e-9, 0.7), (2.55e-9, 0.0), (4e-9, 0.0)],
    "blprechbn": [(2.5e-9, 0.7), (2.55e-9, 0.0), (4e-9, 0.0)],
}
for i in range(4):
    TAILS[f"yselt<{i}>"] = [(4e-9, 0.0)]                 # top half deselected
    TAILS[f"yselb<{i}>"] = [(4e-9, 0.7 if i == 0 else 0.0)]

# ---- classify per-half analog control nets (sae, saprechn) ----------
# each half has its own sense amp; only the ADDRESSED half's sense may
# fire.  Classify: an sae/saprechn net belongs to the half whose sense
# amp's column mux is gated by that half's ysel family.
def half_of_sense_net(net):
    """Walk: net gates sense devices -> their sd nets -> tgates on those
    nets -> ysel family of the tgate gates."""
    seen = set()
    frontier = [d for d in sl.devices if d.gate == net]
    for _ in range(3):
        nxt = []
        for d in frontier:
            for n in d.sd:
                if n in seen:
                    continue
                seen.add(n)
                for e in sl.devices:
                    if n in e.sd:
                        labs = ex.net_labels.get(e.gate, set())
                        if any(l.startswith("yselt") for l in labs):
                            return "t"
                        if any(l.startswith("yselb") for l in labs):
                            return "b"
                        nxt.append(e)
        frontier = nxt
    return "?"

sources = [f"Vwd {sl.node(sim.net_of('wd<0>'))} 0 0"]
srcnets = {sim.net_of('wd<0>')}
for lbl, tail in TAILS.items():
    nets = sorted(sim.label_nets.get(lbl, set()), key=str)
    nets = [n for n in nets if n in sim.nets]
    for k, n in enumerate(nets):
        if n in srcnets:
            continue
        srcnets.add(n)
        if lbl in ("sae", "saprechn") and len(nets) > 1:
            half = half_of_sense_net(n)
            print(f"  {lbl} net {n}: half={half}")
            if half == "t":     # deselected half: sense stays idle
                sources.append(f"V{lbl.replace('<','_').replace('>','_')}_{k} "
                               f"{sl.node(n)} 0 0")
                continue
        sources.append(f"V{lbl.replace('<','_').replace('>','_')}_{k} "
                       f"{sl.node(n)} 0 {pwl(lbl, tail)}")
# remaining boundary nets: inverted selects are the complement of the
# ysel drive above (ONLY yselb<0> is selected -> only yselbn<0> low);
# everything else (other wordlines etc.) rests at 0
handled = set(srcnets)
for n in sorted(sim.boundary if hasattr(sim, 'boundary') else [], key=str):
    if n in sim.nets and n not in handled:
        labs = ex.net_labels.get(n, set())
        if any(l.startswith("yselbn<0>") for l in labs):
            rest = 0.0
        elif any(l.startswith("yseltn") or l.startswith("yselbn")
                 for l in labs):
            rest = 0.7
        else:
            rest = 0.0
        sources.append(f"Vb{sl.node(n)[1:]} {sl.node(n)} 0 {rest}")
        handled.add(n)
print(f"{len(sources)} sources")

def emit(run_name, addressed_high):
    # addressed cells: on wlb<0> (bottom half fires at address 0)
    wlb0 = sorted(sim.label_nets["wlb<0>"], key=str)[0]
    pairset = {x for pp in pairs for x in pp}
    addressed = set()
    for d in sl.devices:
        if d.model == "nmos_sram" and d.gate == wlb0 and len(d.sd) == 2:
            for pp in pairs:
                if set(d.sd) & set(pp):
                    addressed.add(pp)
    ic = {}
    for pp in pairs:
        a, b = pp
        ic[a], ic[b] = 0.7, 0.0
    for pp in addressed:
        a, b = pp
        ic[a], ic[b] = (0.7, 0.0) if addressed_high else (0.0, 0.7)
    # precharged analog nodes
    for n in sim.nets:
        labs = ex.net_labels.get(n, set())
        if labs & {"sa", "san", "sao", "saob", "saon"} or \
           labs & {"BL", "BLN"} or any(l.startswith("bl") for l in labs):
            ic[n] = 0.7
    do = sim.net_of("dataout<0>")
    saves = [do]
    # the addressed (bottom-half) bitline pair, for the waveform pages
    for n in sorted(sim.nets, key=str):
        if ex.net_labels.get(n, set()) & {"bl<0>", "bln<0>"}:
            saves.append(n)
    for lbl in ["sae", "wlb<0>"]:
        saves.append(sorted(sim.label_nets[lbl], key=str)[0])
    # one addressed cell + its bitlines + sa/san
    ap = sorted(addressed, key=str)[0]
    saves += list(ap)
    for n in sim.nets:
        labs = ex.net_labels.get(n, set())
        if labs & {"sa", "san"}:
            saves.append(n)
    seen = set(); saves = [x for x in saves if not (x in seen or seen.add(x))][:16]
    print(f"{run_name}: {len(addressed)} addressed cells")
    out = f"{SCRATCH}/stageB_{run_name}"
    sl.emit(out + ".sp",
            _REPO + "/spice/models/asap7_TT.osdi.sp",
            ic, sources, tran="5p 4n", saves=saves, ic_mode="ic", uic=True,
            options=[".options reltol=2e-3 abstol=1e-10 vntol=1e-5 rshunt=1e9 itl4=300 cshunt=2e-16 method=gear"])
    sp = open(out + ".sp").read().replace("OUT.raw", out + ".raw")
    open(out + ".sp", "w").write(sp)
    print("emitted", out + ".sp")

emit("hi", True)
emit("lo", False)
