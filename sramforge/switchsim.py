"""Rung 4.5 - a charge-retaining, unit-delay switch-level simulator.

Runs the FUNCTION of the extracted transistor netlist (rung 4's
Extraction), with no SPICE (Simulation Program with Integrated Circuit
Emphasis) and no timing model:

  * a transistor is a switch: NMOS (N-type Metal-Oxide-Semiconductor)
    conducts when its gate is 1, PMOS (P-type) when its gate is 0,
  * each time step, nets connected through ON switches form components;
    a component containing a rail or forced input takes that value
    (both rails -> X, the conflict value),
  * a component with no driver KEEPS its previous value - charge
    retention, which is what makes dynamic nodes and latches work,
  * unknown-gate transistors do not conduct (start-up pessimism decays
    as values propagate from the rails).

Unit-delay stepping (values recomputed from the previous step, Jacobi
style) makes self-timed PULSES visible: the SRAM (Static Random-Access
Memory) control stretches the clock edge into a wordline pulse via its
delay line, and at DC (direct current, i.e. a settled steady state) that
pulse has already gone - so the simulator records the HISTORY of each
observed net, not just its final value.

The evaluation is restricted to the backward cone of the observed nets:
a device joins the cone if either source/drain net is in it (its gate
net then joins as a dependency).  Gate-only connections do not extend
the cone, which is what keeps the 65,536 bitcell access transistors out
of a wordline-cone evaluation.
"""
from collections import defaultdict

X = 2  # conflict / unknown-driven value


class SwitchSim:
    def __init__(self, extraction, observe_labels, boundary_labels=()):
        """extraction: sramforge.lvs.Extraction of a full bank.
        observe_labels: net labels whose values/history we care about;
        the device cone is computed backward from these.
        boundary_labels: nets carrying any of these labels are included
        but never expanded through - they truncate the cone (used to cut
        the decode band free of the column it serves)."""
        self.ex = extraction

        # label -> nets (a label can appear on many nets; top-level pins
        # and wordlines resolve to exactly one, which callers assert)
        self.label_nets = defaultdict(set)
        for net, labels in extraction.net_labels.items():
            for lb in labels:
                self.label_nets[lb].add(net)

        # supply nets: a net carrying BOTH polarities has picked up a
        # stray furniture label (boundary-row vdd!/vss! texts at bits>32)
        # - forcing it both ways makes the whole supply web fight and
        # radiates address-dependent X.  Majority vote by label-probe
        # count decides the real polarity; a tie keeps the net out of
        # both sets (weaker than mislabeling either way).
        vdd = set(self.label_nets.get("VDD", set()))
        vss = set(self.label_nets.get("VSS", set()))
        both = vdd & vss
        if both:
            counts = defaultdict(lambda: defaultdict(int))
            for name, x, y, net in getattr(extraction, "label_probes", ()):
                if net in both and name in ("VDD", "VSS", "vdd!", "vss!"):
                    pol = "VDD" if "dd" in name.lower() else "VSS"
                    counts[net][pol] += 1
            for net in both:
                c = counts[net]
                if c["VDD"] > c["VSS"]:
                    vss.discard(net)
                elif c["VSS"] > c["VDD"]:
                    vdd.discard(net)
                else:
                    vdd.discard(net); vss.discard(net)
                import sys as _sys
                print(f"[switchsim] supply label conflict on {net}: "
                      f"VDD x{c['VDD']} vs VSS x{c['VSS']} -> "
                      f"{'VDD' if net in vdd else 'VSS' if net in vss else 'dropped'}",
                      file=_sys.stderr)
        self.vdd = frozenset(vdd)
        self.vss = frozenset(vss)

        observed = set()
        for lb in observe_labels:
            observed |= self.label_nets.get(lb, set())
        boundary = set()
        for lb in boundary_labels:
            boundary |= self.label_nets.get(lb, set())
        self.boundary = frozenset(boundary)

        # ---- backward cone ----
        by_sd = defaultdict(list)
        for d in extraction.devices:
            for n in d.sd:
                by_sd[n].append(d)
        cone_nets = set(observed)
        frontier = list(observed)
        cone_devs = set()
        while frontier:
            n = frontier.pop()
            for d in by_sd[n]:
                if id(d) in cone_devs:
                    continue
                cone_devs.add(id(d))
                for m in list(d.sd) + [d.gate]:
                    if m is not None and m not in cone_nets \
                            and m not in self.vdd and m not in self.vss:
                        cone_nets.add(m)
                        # gates extend the cone too (their drivers matter),
                        # but only through source/drain adjacency; boundary
                        # nets are included but never expanded through
                        if m not in boundary:
                            frontier.append(m)
        self.devices = [d for d in extraction.devices
                        if id(d) in cone_devs and len(d.sd) == 2]
        self.nets = cone_nets

        # ---- tie-cell resolution ----
        # TIEHI/TIELO cells are a cross-coupled pmos/nmos pair: e.g.
        # pmos(VDD -> H, gate=x) + nmos(x -> VSS, gate=H).  Silicon
        # settles them at power-up; a 3-valued simulator cannot bootstrap
        # the feedback from unknowns, so detect the pattern and pre-force
        # the solved values.
        self.ties = {}
        pull_up = {}    # net -> gate of a pmos pulling it from VDD
        pull_dn = {}    # net -> gate of an nmos pulling it to VSS
        for d in self.devices:
            a, b = tuple(d.sd)
            if d.model.startswith("pmos"):
                if a in self.vdd:
                    pull_up[b] = d.gate
                elif b in self.vdd:
                    pull_up[a] = d.gate
            else:
                if a in self.vss:
                    pull_dn[b] = d.gate
                elif b in self.vss:
                    pull_dn[a] = d.gate
        for h, x in pull_up.items():
            # tie-high: H pulled up by pmos gated on x; x pulled down by
            # nmos gated on H  =>  H=1, x=0
            if pull_dn.get(x) == h:
                self.ties[h] = 1
                self.ties[x] = 0
        for l, x in pull_dn.items():
            # tie-low: L pulled down by nmos gated on x; x pulled up by
            # pmos gated on L  =>  L=0, x=1
            if pull_up.get(x) == l:
                self.ties[l] = 0
                self.ties[x] = 1

    def net_of(self, label):
        nets = self.label_nets.get(label, set())
        if len(nets) != 1:
            raise ValueError(f"label {label!r} names {len(nets)} nets")
        return next(iter(nets))

    # ---- one evaluation phase ----
    def run(self, forced, state=None, max_steps=100, watch=()):
        """forced: {net: 0|1} held constant (inputs).  state: previous
        net values to start from (charge).  Returns (final_state,
        pulsed) where pulsed maps each watched net to True if it was 1
        at ANY step of this phase."""
        val = dict(state) if state else dict(self.ties)
        for n in self.vdd:
            val[n] = 1
        for n in self.vss:
            val[n] = 0
        val.update(forced)
        pulsed = {n: (val.get(n) == 1) for n in watch}

        for _ in range(max_steps):
            # components over ON switches, from current values
            parent = {}

            def find(a):
                parent.setdefault(a, a)
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            def union(a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

            for d in self.devices:
                g = val.get(d.gate)
                on = (g == 1) if d.model.startswith("nmos") else \
                     (g == 0) if d.model.startswith("pmos") else False
                if on:
                    a, b = tuple(d.sd)
                    union(a, b)

            # resolve each component: forced/rail beats charge
            comp_drive = {}
            for n, v in val.items():
                r = find(n)
                cur = comp_drive.get(r)
                strong = (n in self.vdd or n in self.vss or n in forced)
                if strong:
                    if cur is None or cur == v:
                        comp_drive[r] = v
                    else:
                        comp_drive[r] = X
            # charge resolution for undriven components: keep values;
            # members that disagree decay to X
            comp_charge = {}
            for n, v in val.items():
                r = find(n)
                if r in comp_drive:
                    continue
                cur = comp_charge.get(r)
                comp_charge[r] = v if (cur is None or cur == v) else X

            new_val = {}
            for n in self.nets | set(val):
                r = find(n)
                if r in comp_drive:
                    new_val[n] = comp_drive[r]
                elif r in comp_charge:
                    new_val[n] = comp_charge[r]
                elif n in val:
                    new_val[n] = val[n]
            for n in self.vdd:
                new_val[n] = 1
            for n in self.vss:
                new_val[n] = 0
            new_val.update(forced)

            for n in pulsed:
                if new_val.get(n) == 1:
                    pulsed[n] = True
            if new_val == val:
                break
            val = new_val
        else:
            if not getattr(self, "_warned_unconverged", False):
                self._warned_unconverged = True
                import sys as _sys
                print(f"[switchsim] WARNING: no fixpoint within "
                      f"{max_steps} steps - results unreliable, raise "
                      f"max_steps", file=_sys.stderr)
        return val, pulsed
