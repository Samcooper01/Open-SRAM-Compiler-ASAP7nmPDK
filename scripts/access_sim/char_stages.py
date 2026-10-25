"""Characterization drivers on top of the rung 5 access stages.

This module does NOT rebuild the stage A / stage B / stage C flows; it wraps
the same recipe (sramforge.spiceslice.Slice cone, switch-level pre-state as
.ic seeds, gear integration, cshunt/rshunt damping) in reusable classes so
that scripts/characterize.py can emit MANY decks that differ only in their
stimulus (input arrival time, clock slew, output load, stored data) and
measure each one.  Everything numeric that leaves this module is either read
from an ngspice (the open-source SPICE, Simulation Program with Integrated
Circuit Emphasis, simulator) raw file or is a stated estimate flagged as
such by the caller.

Stage A  = the decode/control band alone (cone from the wordline + control
           labels, truncated at the bitline/sense boundary).
Stage B  = the column / sense / output-latch slice (cone from dataout<0>,
           truncated at the control nets), driven by stage A waveforms as
           PWL (piecewise-linear) sources.
Stage C  = stage B's slice with a write cycle followed by a readback cycle
           (control edges timed from the stage A measurements, as in
           stageC_emit.py); used here for the wd (write data) setup search.

Requires ngspice 46 with the OSDI (Open Source Device Interface) build of
BSIM-CMG (Berkeley Short-channel IGFET Model, Common Multi-Gate); the run
directory gets a .spiceinit with the `osdi <file>` load line.
"""
import json
import os
import pathlib
import subprocess
import sys
import time
from collections import namedtuple

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from sramforge import pins as contract        # noqa: E402  (the pin contract)


def sdel_bits_for(spec, corner):
    """sdel<0>..sdel<4> for the bank's measured delay-line code, or all
    ones when its control has no measurement yet."""
    try:
        from sramforge import sense
        code = sense.sdel_code(spec, corner)["code"]
    except Exception:          # the table may be absent on a fresh checkout
        code = None
    return [int(c) for c in (code or "11111")]

VDD = 0.7

Device = namedtuple("Device", "model nfin gate sd bbox")


# ---------------------------------------------------------------------------
# tool locations (the worktree may sit below the project root, so walk up)
def _find_tools():
    """The package's portable finder (sramforge/timing.py find_tools)."""
    from sramforge.timing import find_tools
    return find_tools()


NGSPICE, OSDI = _find_tools()
MODELS = REPO / "spice" / "models"


def models_for(corner):
    return str(MODELS / f"asap7_{corner}.osdi.sp")


# ---------------------------------------------------------------------------
# netlist loading (the rung 4 dump schema of scripts/dump_netlist.py)
class Netlist:
    """Duck-typed stand-in for sramforge.lvs.Extraction: .devices and
    .net_labels, with string net ids."""

    def __init__(self, devices, net_labels):
        self.devices = devices
        self.net_labels = net_labels


def load_netlist(path):
    data = json.load(open(path))
    devices = [Device(d["model"], d["nfin"], d["gate"], frozenset(d["sd"]),
                      None) for d in data["devices"]]
    labels = {n: set(l) for n, l in data["labels"].items()}
    return Netlist(devices, labels)


# ---------------------------------------------------------------------------
# stimulus helpers
def pwl(events, v0, slew_ps, t_end_ns):
    """PWL (piecewise-linear) source text.  events: [(t50_ns, value)], each edge a linear ramp
    whose 10%-to-90% time is slew_ps, centred on t50 (the 50% crossing).
    The ramp is 0-to-100% over slew/0.8."""
    ramp = slew_ps / 0.8 * 1e-3
    pts = [(0.0, v0)]
    cur = v0
    for t, v in events:
        t0, t1 = t - ramp / 2, t + ramp / 2
        if t0 <= pts[-1][0]:
            raise ValueError("PWL (piecewise-linear) events overlap: %r" % (events,))
        pts.append((t0, cur))
        pts.append((t1, v))
        cur = v
    pts.append((t_end_ns, cur))
    return "PWL(" + " ".join(f"{t:.5f}n {v:.4f}" for t, v in pts) + ")"


def cross_time(ts, vs, level, rising, after=0.0, before=None):
    """First time after `after` (and before `before`) at which the trace
    crosses `level` in the given direction; linear interpolation."""
    for i in range(1, len(ts)):
        if ts[i] <= after:
            continue
        if before is not None and ts[i] > before:
            return None
        a, b = vs[i - 1], vs[i]
        if rising and a < level <= b or (not rising) and a > level >= b:
            if b == a:
                return ts[i]
            return ts[i - 1] + (ts[i] - ts[i - 1]) * (level - a) / (b - a)
    return None


def level_in(ts, vs, t0, t1):
    """Mean value of the trace in [t0, t1]."""
    sel = [v for t, v in zip(ts, vs) if t0 <= t <= t1]
    return sum(sel) / len(sel) if sel else None


def slew_10_90(ts, vs, t_start, rising):
    lo, hi = 0.1 * VDD, 0.9 * VDD
    if rising:
        a = cross_time(ts, vs, lo, True, t_start)
        b = cross_time(ts, vs, hi, True, t_start)
    else:
        a = cross_time(ts, vs, hi, False, t_start)
        b = cross_time(ts, vs, lo, False, t_start)
    return (b - a) if (a is not None and b is not None) else None


# ---------------------------------------------------------------------------
# ngspice runner
class Runner:
    def __init__(self, workdir):
        self.workdir = pathlib.Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / ".spiceinit").write_text(f"osdi {OSDI}\n")
        self.log = []

    def run(self, deck_path, timeout=1800):
        """Run one deck (sequentially: the machine is shared).  Returns the
        raw path if ngspice produced one, else None.  Appends a record to
        self.log with wall time and the simulated end time."""
        deck_path = pathlib.Path(deck_path)
        raw = deck_path.with_suffix(".raw")
        if raw.exists():
            raw.unlink()
        t0 = time.time()
        try:
            r = subprocess.run([NGSPICE, "-b", str(deck_path)],
                               cwd=str(self.workdir), capture_output=True,
                               text=True, timeout=timeout)
            (deck_path.with_suffix(".log")).write_text(r.stdout + r.stderr)
            status = "ok" if raw.exists() else "no-raw"
        except subprocess.TimeoutExpired:
            status = "timeout"
        wall = time.time() - t0
        self.log.append({"deck": deck_path.name, "status": status,
                         "wall_s": round(wall, 1)})
        print(f"    [ngspice] {deck_path.name}: {status} in {wall:.0f} s",
              flush=True)
        return raw if raw.exists() else None


def read_raw(path):
    """ngspice ASCII (American Standard Code for Information Interchange)
    rawfile -> (names, points); same as access_analyze."""
    lines = open(path).read().splitlines()
    nvars = 0
    names = []
    for i, ln in enumerate(lines):
        if ln.startswith("No. Variables"):
            nvars = int(ln.split(":")[1])
        elif ln.startswith("Variables:"):
            names = [lines[i + 1 + j].split()[1] for j in range(nvars)]
        elif ln.startswith("Values:"):
            toks = " ".join(lines[i + 1:]).split()
            pts, k = [], 0
            while k < len(toks):
                k += 1
                pts.append([float(toks[k + j]) for j in range(nvars)])
                k += nvars
            return names, pts
    return names, []


class Waves:
    """Column access on a raw file, keyed by SPICE node name."""

    def __init__(self, path):
        self.names, self.pts = read_raw(path)
        self.t = [p[0] for p in self.pts]
        self.t_end = self.t[-1] if self.t else 0.0

    def v(self, node):
        key = f"v({node})"
        j = self.names.index(key)
        return [p[j] for p in self.pts]

    def has(self, node):
        return f"v({node})" in self.names


# ---------------------------------------------------------------------------
# per-fin gate capacitance from the model card (large-signal average)
def measure_gate_cap_per_fin(runner, corner="TT",
                             models=("nmos_rvt", "pmos_rvt", "nmos_sram",
                                     "pmos_sram"), nfin=4):
    """Effective gate capacitance per fin, in farads, for each model: a
    single device with source = drain = its rail (channel forms as the
    gate swings), gate ramped over the full 0..VDD swing in 1 ns; the gate
    current is integrated (Q) and C = Q / VDD.  This is the number the pin
    capacitance estimate multiplies by the fin count of every gate a pin
    net drives."""
    out = {}
    for model in models:
        pm = model.startswith("p")
        rail = "vdd" if pm else "0"
        v0, v1 = (VDD, 0.0) if pm else (0.0, VDD)
        deck = "\n".join([
            f"* gate cap {model} nfin={nfin}",
            f".include {models_for(corner)}",
            ".param vdd=0.7", "Vdd vdd 0 {vdd}",
            f"Vg g 0 PWL(0 {v0} 0.2n {v0} 1.2n {v1} 1.5n {v1})",
            f"N1 {rail} g {rail} {rail} {model} nfin={nfin}",
            ".tran 1p 1.5n", ".control", "run", "set filetype=ascii",
            "write OUT.raw v(g) i(Vg)", ".endc", ".end"])
        sp = runner.workdir / f"gatecap_{model}.sp"
        sp.write_text(deck.replace("OUT.raw", str(sp.with_suffix(".raw"))))
        raw = runner.run(sp, timeout=300)
        if raw is None:
            out[model] = None
            continue
        names, pts = read_raw(raw)
        t = [p[0] for p in pts]
        i = [p[names.index("i(vg)")] for p in pts]
        q = sum(0.5 * (i[k] + i[k - 1]) * (t[k] - t[k - 1])
                for k in range(1, len(t)))
        out[model] = abs(q) / VDD / nfin
    return out


def gate_load_census(ex, nets, exclude_devices=()):
    """{net: {model: total fins}} over devices of the FULL netlist whose
    gate is one of `nets`, excluding the given device objects (the ones
    already inside a slice)."""
    excl = set(id(d) for d in exclude_devices)
    want = set(nets)
    out = {}
    for d in ex.devices:
        if d.gate in want and id(d) not in excl:
            m = out.setdefault(d.gate, {})
            m[d.model] = m.get(d.model, 0) + d.nfin
    return out


def gate_cap_of(census_entry, cfin):
    return sum(fins * cfin[model] for model, fins in census_entry.items())


# ---------------------------------------------------------------------------
# common slice plumbing
class _Stage:
    OPTIONS = (".options reltol=2e-3 abstol=1e-10 vntol=1e-5 rshunt=1e9 "
               "itl4=300 cshunt=2e-16 method=gear")

    def __init__(self, ex, roots, extra, bound):
        from sramforge.spiceslice import Slice
        self.ex = ex
        self.sl = Slice(ex, roots, extra_labels=extra, boundary_labels=bound)
        self.sim = self.sl.sim

    def nets_of(self, label):
        """Nets carrying `label` that are inside the slice circuit (band v2
        puts one label on several net pieces; only live ones count)."""
        return [n for n in sorted(self.sim.label_nets.get(label, set()),
                                  key=str) if n in self.sim.nets]

    def node(self, net):
        return self.sl.node(net)

    def nodes_of(self, label):
        return [self.node(n) for n in self.nets_of(label)]

    def cut_load_lines(self, cfin, wire_len_um=None, c_wire_per_um=0.2e-15,
                       min_f=1e-16):
        """Capacitors standing in for what the cone truncation removed: for
        every net inside the slice, the gate capacitance of all devices of
        the full netlist that hang on it by their GATE but are not in the
        slice (fin count x per-fin cap), plus (optionally) the net's metal
        wire capacitance from a per-net length table.  Without these the
        control drivers see no load and their edges are unphysically fast
        (which is also what breaks the time-step control).  Returns
        (lines, table) where table = {net: farads} for the record."""
        census = gate_load_census(self.ex, self.sim.nets, self.sl.devices)
        lines, table = [], {}
        for k, n in enumerate(sorted(self.sim.nets, key=str)):
            c = gate_cap_of(census.get(n, {}), cfin) if n in census else 0.0
            if wire_len_um and n in wire_len_um:
                c += wire_len_um[n] * c_wire_per_um
            if c >= min_f and n not in self.sim.vdd and n not in self.sim.vss:
                lines.append(f"Ccut{k} {self.node(n)} 0 {c:.4e}")
                table[n] = c
        return lines, table

    def emit(self, path, ic, sources, tran, saves, options=None,
             extra_lines=()):
        """Write the deck; `saves` are nets.  Returns the deck path."""
        opts = [options or self.OPTIONS]
        self.sl.emit(str(path), self._models, ic, list(sources) +
                     list(extra_lines), tran=tran, saves=saves,
                     ic_mode="ic", uic=True, options=opts)
        raw = pathlib.Path(path).with_suffix(".raw")
        text = open(path).read().replace("OUT.raw", str(raw))
        open(path, "w").write(text)
        return pathlib.Path(path)

    def emit_op(self, path, ic, sources, saves, settle_ns=100.0, options=None,
                tstep_ns=0.01, tmax_ns=1.0):
        """Leakage deck: the idle state is reached by a TRANSIENT from the
        digital .ic (uic), not by .op.  A Newton operating point of a few
        thousand BSIM-CMG (Berkeley Short-channel IGFET Model, Common
        Multi-Gate) devices with many high-impedance nodes does not
        converge in half an hour (measured on the 4622-device band).  The
        transient's supply current keeps falling slowly for tens of
        nanoseconds (nodes left charged by the last access leak toward
        their true idle level through off transistors), so the window is
        long (default 100 ns) and the caller records the decay curve, not
        just the end value.  rshunt is huge because
        every node-to-ground shunt carries V/rshunt of fake leakage (1e14
        ohm at 0.7 V is 7 fA per node, negligible against the nA result);
        abstol 1e-12 A (the timing decks' 1e-10 would swamp the pA-scale
        currents; 1e-13 makes the wordline source branches fail the
        time-step test)."""
        lines = [f"* extracted slice: {len(self.sl.devices)} devices (leakage)",
                 f".include {self._models}",
                 ".param vdd=0.7", ".global 0", "Vdd vdd 0 {vdd}",
                 options or (".options reltol=2e-3 abstol=1e-12 vntol=1e-6 "
                             "rshunt=1e14 itl4=300 cshunt=2e-16 method=gear")]
        float_i = 0
        for i, d in enumerate(self.sl.devices):
            sd = list(d.sd)
            if len(sd) == 1:
                sd.append(f"float{float_i}")
                float_i += 1
            a = self.node(sd[0]) if not str(sd[0]).startswith("float") else sd[0]
            b = self.node(sd[1]) if not str(sd[1]).startswith("float") else sd[1]
            g = self.node(d.gate) if d.gate is not None else "0"
            bulk = "0" if d.model.startswith("nmos") else "vdd"
            lines.append(f"N{i} {a} {g} {b} {bulk} {d.model} nfin={d.nfin}")
        lines += list(sources)
        if ic:
            lines.append(".ic " + " ".join(
                f"v({self.node(n)})={v}" for n, v in ic.items()))
        # a small print step keeps the FIRST step small (uic from a preset
        # state blows up on a large first step); the explicit tmax lets the
        # integrator stretch its step once the circuit is quiet
        lines.append(f".tran {tstep_ns}n {settle_ns}n 0 {tmax_ns}n uic")
        lines.append(".control")
        lines.append("run")
        lines.append("set filetype=ascii")
        raw = pathlib.Path(path).with_suffix(".raw")
        lines.append(f"write {raw} i(Vdd) " + " ".join(
            f"v({self.node(n)})" for n in saves))
        lines.append(".endc")
        lines.append(".end")
        open(path, "w").write("\n".join(lines))
        return pathlib.Path(path)


# ---------------------------------------------------------------------------
class StageA(_Stage):
    """The decode/control band.  Roots = the wordline/control labels the
    column slice consumes; boundary = the bitline/sense nets."""

    def __init__(self, ex, spec, corner="TT"):
        self.spec = spec
        self.bits = spec.bits_per_group
        # the address width is the pin contract's (sramforge/pins.py):
        # 8 at the reference 32, 9 for every band-v2 width
        self.n_addr = contract.address_width(spec)
        self._models = models_for(corner)
        self.ctrl = (["wlb<0>", "wlb<1>", "wlt<0>", "sae", "saprechn",
                      "wrena", "wrenan", "blprechtn", "blprechbn", "prechn",
                      "WLENA"]
                     + [f"yselt<{i}>" for i in range(4)]
                     + [f"yselb<{i}>" for i in range(4)]
                     + [f"yseltn<{i}>" for i in range(4)]
                     + [f"yselbn<{i}>" for i in range(4)])
        self.inputs = (["clk", "banksel", "read", "byte_wrenab", "wd<0>"]
                       + [f"ADDRESS<{i}>" for i in range(self.n_addr)]
                       + [f"sdel<{i}>" for i in range(5)])
        bound = (["BL", "BLN", "sa", "san"] + [f"bl<{i}>" for i in range(4)]
                 + [f"bln<{i}>" for i in range(4)] + ["bl", "bln"])
        super().__init__(ex, self.ctrl, self.inputs, bound)
        # the delay-line code this bank needs (sramforge.sense); all ones
        # (the minimum delay, every proof before 2026-09-20) when nothing
        # is measured for its control
        self.sdel = sdel_bits_for(spec, corner)

    # -- digital pre-state -------------------------------------------------
    def input_vector(self, address=0, clk=0, banksel=1, read=1,
                     byte_wrenab=0, wd=0):
        f = {}
        for i in range(self.n_addr):
            f[self.sim.net_of(f"ADDRESS<{i}>")] = (address >> i) & 1
        f[self.sim.net_of("clk")] = clk
        f[self.sim.net_of("banksel")] = banksel
        f[self.sim.net_of("read")] = read
        f[self.sim.net_of("byte_wrenab")] = byte_wrenab
        f[self.sim.net_of("wd<0>")] = wd
        for i in range(5):
            f[self.sim.net_of(f"sdel<{i}>")] = self.sdel[i]
        return f

    def prestate(self, **inputs):
        """Settled switch-level state after two warm-up cycles, ending with
        clk = 0 (the state just before the measured edge)."""
        sim = self.sim
        st = None
        for _ in range(2):
            st, _ = sim.run(self.input_vector(clk=0, **inputs), state=st,
                            max_steps=200)
            st, _ = sim.run(self.input_vector(clk=1, **inputs), state=st,
                            max_steps=200)
        st, _ = sim.run(self.input_vector(clk=0, **inputs), state=st,
                        max_steps=200)
        ic = {n: VDD * st[n] for n in sim.nets if st.get(n) in (0, 1)}
        return ic

    # -- deck ----------------------------------------------------------------
    def sources(self, waves):
        """waves: label -> PWL (piecewise-linear) text or a constant float.
        Every input label gets
        a source; anything not given rests at its digital idle value."""
        out = []
        for lbl in self.inputs:
            w = waves[lbl]
            for k, n in enumerate(self.nets_of(lbl)):
                txt = f"{w}" if isinstance(w, str) else f"{w:.4f}"
                nm = "V" + lbl.replace("<", "_").replace(">", "_") + f"_{k}"
                out.append(f"{nm} {self.node(n)} 0 {txt}")
        return out

    def dc_waves(self, address=0, banksel=1, read=1, byte_wrenab=0, wd=0):
        w = {"banksel": VDD * banksel, "read": VDD * read,
             "byte_wrenab": VDD * byte_wrenab, "wd<0>": VDD * wd}
        for i in range(self.n_addr):
            w[f"ADDRESS<{i}>"] = VDD * ((address >> i) & 1)
        for i in range(5):
            w[f"sdel<{i}>"] = VDD * self.sdel[i]
        return w

    def save_nets(self):
        nets = []
        for lbl in ["clk"] + self.ctrl:
            nets += self.nets_of(lbl)
        seen = set()
        return [n for n in nets if not (n in seen or seen.add(n))]

    def label_trace(self, waves, label):
        """The trace of `label` in a stage A raw: if the label names several
        live nets, the one with the largest swing (the driven piece)."""
        best = None
        for n in self.nets_of(label):
            node = self.node(n)
            if not waves.has(node):
                continue
            v = waves.v(node)
            swing = max(v) - min(v)
            if best is None or swing > best[0]:
                best = (swing, v, node)
        return (best[1], best[2]) if best else (None, None)


# ---------------------------------------------------------------------------
class StageB(_Stage):
    """The column / sense / output-latch slice for dataout<0>."""

    SENSE = {"sa", "san", "sao", "saob", "saon"}

    def __init__(self, ex, spec, corner="TT"):
        self.spec = spec
        bits = self.bits = spec.bits_per_group
        self._models = models_for(corner)
        self.ctrl_bound = (["wlb<0>", "wlt<0>", "sae", "saprechn", "wrena",
                            "wrenan", "blprechtn", "blprechbn", "prechn",
                            "WLENA"]
                           + [f"yselt<{i}>" for i in range(4)]
                           + [f"yselb<{i}>" for i in range(4)]
                           + [f"yseltn<{i}>" for i in range(4)]
                           + [f"yselbn<{i}>" for i in range(4)]
                           + [f"wl<{i}>" for i in range(1, bits)]
                           + [f"wlt<{i}>" for i in range(1, bits)]
                           + [f"wlb<{i}>" for i in range(1, bits)])
        super().__init__(ex, ["dataout<0>"], ["wd<0>"], self.ctrl_bound)
        self.pairs = self.sl.cells()
        self.dataout = self.sim.net_of("dataout<0>")
        self._classify_cells()

    # -- structure -----------------------------------------------------------
    def _classify_cells(self):
        """Addressed cells = pairs whose access device is gated by wlb<0>;
        each is assigned its column-mux index k from the bl<k>/bln<k>
        label of the bitline its access device touches."""
        wl_nets = set(self.nets_of("wlb<0>"))
        pairset = {x for pp in self.pairs for x in pp}
        self.addressed = {}      # pair -> (mux column k or None, bl nets)
        self.orient = {}         # pair -> storage net on the bitline side
        for d in self.sl.devices:
            if d.model != "nmos_sram" or d.gate not in wl_nets \
                    or len(d.sd) != 2:
                continue
            a, b = tuple(d.sd)
            stor = a if a in pairset else (b if b in pairset else None)
            if stor is None:
                continue
            bl = b if stor == a else a
            pp = next(p for p in self.pairs if stor in p)
            labs = self.ex.net_labels.get(bl, set())
            k = None
            for l in labs:
                if l.startswith("bl<") or l.startswith("bln<"):
                    k = int(l[l.index("<") + 1:-1])
            entry = self.addressed.setdefault(pp, [k, set()])
            if k is not None:
                entry[0] = k
            entry[1].add(bl)
            # orientation: the side whose bitline carries the TRUE label
            if any(l.startswith("bl<") for l in labs):
                self.orient[pp] = stor
        self.columns = sorted({k for k, _ in self.addressed.values()
                               if k is not None})

    def bitline_nets(self, k):
        """(bl<k> nets, bln<k> nets) inside the slice."""
        bl = [n for n in self.sim.nets
              if f"bl<{k}>" in self.ex.net_labels.get(n, set())]
        bln = [n for n in self.sim.nets
               if f"bln<{k}>" in self.ex.net_labels.get(n, set())]
        return bl, bln

    # -- presets ---------------------------------------------------------------
    def ic_for(self, stored_by_column, default=1):
        """.ic dict: every cell holds `default`; the addressed cell in mux
        column k holds stored_by_column[k] (1 = the bl-side node high, so a
        read returns 1); sense and bitline nodes precharged."""
        ic = {}
        for pp in self.pairs:
            a, b = pp
            ic[a], ic[b] = (VDD, 0.0) if default else (0.0, VDD)
        for pp, (k, _) in self.addressed.items():
            val = stored_by_column.get(k, default)
            stor = self.orient.get(pp, pp[0])
            other = pp[1] if stor == pp[0] else pp[0]
            ic[stor] = VDD if val else 0.0
            ic[other] = 0.0 if val else VDD
        for n in self.sim.nets:
            labs = self.ex.net_labels.get(n, set())
            if labs & self.SENSE or labs & {"BL", "BLN"} \
                    or any(l.startswith("bl") for l in labs):
                ic[n] = VDD
        return ic

    # -- boundary sources from stage A ------------------------------------------
    def _half_of_sense_net(self, net):
        seen = set()
        frontier = [d for d in self.sl.devices if d.gate == net]
        for _ in range(3):
            nxt = []
            for d in frontier:
                for n in d.sd:
                    if n in seen:
                        continue
                    seen.add(n)
                    for e in self.sl.devices:
                        if n in e.sd:
                            labs = self.ex.net_labels.get(e.gate, set())
                            if any(l.startswith("yselt") for l in labs):
                                return "t"
                            if any(l.startswith("yselb") for l in labs):
                                return "b"
                            nxt.append(e)
            frontier = nxt
        return "?"

    def boundary_sources(self, stageA, wavesA, wd=0.0, decimate=0.02):
        """PWL (piecewise-linear) sources for every boundary net, from the stage A traces of
        the same label.  Labels stage A did not capture rest at the idle
        level implied by their name (ysel*n complements high, all else 0)."""
        out = []
        if wd is not None:          # None: the caller adds its own wd source
            out.append(f"Vwd {self.node(self.sim.net_of('wd<0>'))} 0 {wd:.4f}")
        srcnets = {self.sim.net_of("wd<0>")}
        t = wavesA.t

        def pwl_text(v):
            pts = [(t[0], v[0])]
            for tt, vv in zip(t[1:], v[1:]):
                if abs(vv - pts[-1][1]) > decimate:
                    pts.append((tt, vv))
            if pts[-1][0] < t[-1]:
                pts.append((t[-1], v[-1]))
            return "PWL(" + " ".join(f"{tt:.4e} {vv:.4f}" for tt, vv in pts) + ")"

        for lbl in self.ctrl_bound:
            nets = self.nets_of(lbl)
            if not nets:
                continue
            trace, _ = stageA.label_trace(wavesA, lbl)
            for k, n in enumerate(nets):
                if n in srcnets:
                    continue
                srcnets.add(n)
                nm = "V" + lbl.replace("<", "_").replace(">", "_") + f"_{k}"
                if trace is None:
                    continue          # handled by the rest pass below
                if lbl in ("sae", "saprechn") and len(nets) > 1:
                    if self._half_of_sense_net(n) == "t":
                        out.append(f"{nm} {self.node(n)} 0 0")
                        continue
                out.append(f"{nm} {self.node(n)} 0 {pwl_text(trace)}")
        handled = {ln.split()[1] for ln in out}
        for n in sorted(self.sim.boundary, key=str):
            if n not in self.sim.nets or self.node(n) in handled:
                continue
            labs = self.ex.net_labels.get(n, set())
            if any(l.startswith("yseltn") or l.startswith("yselbn")
                   for l in labs):
                rest = VDD
            elif "wrenan" in labs:
                rest = VDD
            else:
                rest = 0.0
            out.append(f"Vb{self.node(n)[1:]} {self.node(n)} 0 {rest}")
            handled.add(self.node(n))
        return out

    def addressed_pair(self, k):
        """(pair, bl_net, bln_net) of the addressed cell in mux column k:
        the bitline nets are the ones its own access devices touch (the
        bl<k> label is also on every other row's pair k, so the label alone
        does not identify them)."""
        for pp, (col, blnets) in self.addressed.items():
            if col == k:
                bl = [n for n in blnets if any(
                    l.startswith("bl<") for l in self.ex.net_labels.get(n, ()))]
                bln = [n for n in blnets if any(
                    l.startswith("bln<") for l in self.ex.net_labels.get(n, ()))]
                return pp, (bl[0] if bl else None), (bln[0] if bln else None)
        return None, None, None

    def save_nets(self, columns=(0, 1)):
        saves = [self.dataout]
        for k in columns:
            pp, bl, bln = self.addressed_pair(k)
            if pp is None:
                continue
            saves += [x for x in (bl, bln) if x is not None]
            stor = self.orient.get(pp, pp[0])
            other = pp[1] if stor == pp[0] else pp[0]
            saves += [stor, other]
        for lbl in ("sae", "wlb<0>", "wrena"):
            saves += self.nets_of(lbl)[:1]
        for n in self.sim.nets:
            if self.ex.net_labels.get(n, set()) & {"sa", "san"}:
                saves.append(n)
        seen = set()
        return [x for x in saves if not (x in seen or seen.add(x))][:20]

    def idle_sources(self, stageA, idle_state):
        """Constant sources for every boundary net at the band's idle level (the
        stage A switch-level state), for the leakage operating point."""
        out = [f"Vwd {self.node(self.sim.net_of('wd<0>'))} 0 0"]
        done = {self.sim.net_of("wd<0>")}
        for lbl in self.ctrl_bound:
            for n in self.nets_of(lbl):
                if n in done:
                    continue
                done.add(n)
                level = None
                for m in stageA.nets_of(lbl):
                    if m in idle_state:
                        level = idle_state[m]
                        break
                if level is None:
                    labs = self.ex.net_labels.get(n, set())
                    level = VDD if any(l.startswith("yseltn") or
                                       l.startswith("yselbn") or l == "wrenan"
                                       for l in labs) else 0.0
                nm = "V" + lbl.replace("<", "_").replace(">", "_") + f"_{len(done)}"
                out.append(f"{nm} {self.node(n)} 0 {level:.4f}")
        for n in sorted(self.sim.boundary, key=str):
            if n in self.sim.nets and n not in done:
                done.add(n)
                out.append(f"Vb{self.node(n)[1:]} {self.node(n)} 0 0")
        return out


# ---------------------------------------------------------------------------
class BandSlice(_Stage):
    """The whole decode/control band (every wordline + control label as a
    root), used only for the leakage operating point."""

    def __init__(self, ex, spec, corner="TT"):
        self._models = models_for(corner)
        bits = spec.bits_per_group
        n_addr = contract.address_width(spec)
        roots = ([f"wlb<{i}>" for i in range(bits)]
                 + [f"wlt<{i}>" for i in range(bits)]
                 + ["sae", "saprechn", "wrena", "wrenan", "blprechtn",
                    "blprechbn", "prechn", "WLENA", "wlenat", "wlenab"]
                 + [f"yselt<{i}>" for i in range(4)]
                 + [f"yselb<{i}>" for i in range(4)]
                 + [f"yseltn<{i}>" for i in range(4)]
                 + [f"yselbn<{i}>" for i in range(4)])
        self.inputs = (["clk", "banksel", "read", "byte_wrenab", "wd<0>"]
                       + [f"ADDRESS<{i}>" for i in range(n_addr)]
                       + [f"sdel<{i}>" for i in range(5)])
        bound = (["BL", "BLN", "sa", "san"] + [f"bl<{i}>" for i in range(4)]
                 + [f"bln<{i}>" for i in range(4)] + ["bl", "bln"])
        super().__init__(ex, roots, self.inputs, bound)
        self.n_addr = n_addr
        self.sdel = sdel_bits_for(spec, corner)

    def idle_state(self):
        """Switch-level settled state at idle: clk 0, banksel 0."""
        sim = self.sim
        f = {}
        for i in range(self.n_addr):
            f[sim.net_of(f"ADDRESS<{i}>")] = 0
        for lbl, v in (("clk", 0), ("banksel", 0), ("read", 0),
                       ("byte_wrenab", 0), ("wd<0>", 0)):
            f[sim.net_of(lbl)] = v
        for i in range(5):
            f[sim.net_of(f"sdel<{i}>")] = self.sdel[i]
        st = None
        for _ in range(3):
            st, _ = sim.run(f, state=st, max_steps=300)
        return {n: VDD * st[n] for n in sim.nets if st.get(n) in (0, 1)}, f

    def idle_sources(self, forced):
        out = []
        for lbl in self.inputs:
            for k, n in enumerate(self.nets_of(lbl)):
                nm = "V" + lbl.replace("<", "_").replace(">", "_") + f"_{k}"
                out.append(f"{nm} {self.node(n)} 0 {VDD * forced[n]:.4f}")
        return out
