"""Rung 5 - export an extracted-netlist slice to SPICE (Simulation
Program with Integrated Circuit Emphasis) for end-to-end analog access
simulation.

The slice is the same backward cone the switch-level simulator uses
(sramforge.switchsim): start from an observed net (e.g. dataout<0>),
include every device whose source/drain touches the growing net set, and
let gate nets pull in their own drivers.  From dataout<0> that captures
the output latch, sense amplifier, column mux, both bitlines with every
cell hanging on them, the write driver, the wordline drivers, and the
whole decode/control band back to clk/ADDRESS - the complete access path,
a few thousand devices out of 114k.

Device emission: `N<i> d g s b <model> nfin=<n>` - source/drain order is
arbitrary (BSIM-CMG - Berkeley Short-channel IGFET Model - Common
Multi-Gate - is symmetric here), bulk goes to the rail matching the type
(the layout ties nwell to VDD and the substrate to VSS).  Dangling
source/drain sides get private floating nodes.

Storage cells are found structurally (the cross-coupled pmos_sram pairs)
so every cell in the slice can be preset with .ic - including the one
addressed cell, whose preset the caller flips to prove dataout tracks it.
"""
from collections import defaultdict

from .switchsim import SwitchSim


def sanitize(net):
    return str(net).replace("(", "").replace(")", "").replace("'", "") \
        .replace(", ", "_").replace(",", "_").replace(" ", "") \
        .replace("<", "_").replace(">", "_").replace(":", "_")


class Slice:
    def __init__(self, extraction, roots, extra_labels=(), boundary_labels=()):
        """roots: labels whose cone forms the slice (e.g. ['dataout<0>'])."""
        self.ex = extraction
        self.sim = SwitchSim(extraction, list(roots) + list(extra_labels),
                             boundary_labels=boundary_labels)
        self.devices = self.sim.devices
        self.vdd = self.sim.vdd
        self.vss = self.sim.vss

    def node(self, net):
        if net in self.vdd:
            return "vdd"
        if net in self.vss:
            return "0"
        return "n" + sanitize(net)

    def cells(self):
        """Find 6T storage pairs inside the slice: two pmos_sram devices
        cross-coupled (each one's gate is the other's non-VDD drain).
        Returns a list of (q_net, qb_net) pairs (orientation arbitrary)."""
        pus = []
        for d in self.devices:
            if d.model == "pmos_sram" and len(d.sd) == 2:
                sd = [n for n in d.sd if n not in self.vdd]
                if len(sd) == 1:
                    pus.append((sd[0], d.gate))  # (drain, gate)
        by_drain = {dr: g for dr, g in pus}
        pairs = set()
        for dr, g in pus:
            if by_drain.get(g) == dr:  # cross-coupled partner exists
                pairs.add(tuple(sorted((dr, g), key=str)))
        return sorted(pairs, key=str)

    def find_cell(self, wl_label, bl_net_of):
        """The addressed cell: the storage pair whose access transistor is
        gated by wl_label.  bl_net_of: function net -> True if the net is
        one of the slice's bitline nets (used to pick the right column).
        Returns (q_net, qb_net) with q on the ACC side that touches a
        bitline satisfying bl_net_of."""
        wl_nets = self.sim.label_nets.get(wl_label, set())
        pairs = self.cells()
        pairset = {n for p in pairs for n in p}
        for d in self.devices:
            if d.model == "nmos_sram" and d.gate in wl_nets and len(d.sd) == 2:
                a, b = tuple(d.sd)
                stor = a if a in pairset else (b if b in pairset else None)
                bln = b if stor is a else a
                if stor is not None and bl_net_of(bln):
                    for p in pairs:
                        if stor in p:
                            other = p[0] if p[1] == stor else p[1]
                            return stor, other
        return None

    def emit(self, path, models_include, ic, sources, tran="10p 8n",
             saves=(), options=(), ic_mode="ic", uic=True):
        """Write the SPICE deck.  ic: {net: volts}; sources: list of
        source lines (already node-named); saves: net list to record.
        ic_mode "nodeset" + uic=False lets a DC operating point find a
        consistent start (the robust path for big slices with latches)."""
        lines = [f"* extracted slice: {len(self.devices)} devices",
                 f".include {models_include}",
                 ".param vdd=0.7", ".global 0",
                 "Vdd vdd 0 {vdd}"]
        lines += list(options)
        float_i = 0
        for i, d in enumerate(self.devices):
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
            lines.append(f".{ic_mode} " + " ".join(
                f"v({self.node(n)})={v}" for n, v in ic.items()))
        lines.append(f".tran {tran}" + (" uic" if uic else ""))
        lines.append(".control")
        lines.append("run")
        lines.append("set filetype=ascii")
        lines.append("write OUT.raw " + " ".join(
            f"v({self.node(n)})" for n in saves))
        lines.append(".endc")
        lines.append(".end")
        open(path, "w").write("\n".join(lines))
        return path
