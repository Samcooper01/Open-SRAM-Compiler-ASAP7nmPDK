"""Rung 4 - LVS (Layout Versus Schematic): FinFET device extraction.

Built from first principles on the rung 2 conductor graph (sramforge.nets):

  * a DEVICE is a piece of channel - (ACTIVE ∩ (GATE − GCUT)) - since the
    surviving gate over active is exactly what switches current,
  * its TYPE comes from the implant covering the channel (NSELECT → NMOS,
    PSELECT → PMOS) and its FLAVOR from the VT (threshold-voltage) marker
    (SRAMVT → *_sram, else *_rvt - the regular-VT default),
  * its WIDTH is the FIN COUNT: the number of fin stripes crossing the
    channel piece (FinFET width is quantized in fins),
  * its GATE net is the gate_c island the channel belongs to; its
    SOURCE/DRAIN nets are the sd (source/drain = ACTIVE − GATE) islands
    immediately left and right of the channel.  Source and drain are
    geometrically symmetric, so they are reported as an unordered pair.
  * bulk/well terminals are not extracted (4-terminal accuracy belongs to
    the electrical rung); documented limitation.

Net NAMES come from the pin texts (M1/M2/M3 251-datatype labels), with
vdd!/vss! normalized to VDD/VSS.  Unlabeled nets keep their canonical
union-find ids.

The module also carries the golden schematic of the 6T (six-transistor)
bitcell - the measured topology documented on the bitcell pages - and a
small exact-isomorphism matcher used by tests/test_lvs.py.
"""
from collections import Counter, defaultdict, namedtuple

import klayout.db as kdb

from .nets import ConductorGraph, IslandIndex, interior_point
from .tech import LAYERS

# One extracted transistor.  sd is a frozenset of 0-2 net ids (a device at
# a cell edge may have a dangling side); nets are ConductorGraph net ids.
Device = namedtuple("Device", "model nfin gate sd bbox")

PIN_TEXT_LAYERS = [((19, 251), "m1"), ((20, 251), "m2"), ((30, 251), "m3"),
                   ((40, 251), "m4"), ((50, 251), "m5")]


class _StripeIndex:
    """y-binned fin stripes for fast fin counting under a channel."""
    GRID = 5000

    def __init__(self, region):
        self.stripes = [p.bbox() for p in region.merged().each()]
        self.bins = defaultdict(list)
        for i, b in enumerate(self.stripes):
            for gy in range(b.bottom // self.GRID, b.top // self.GRID + 1):
                self.bins[gy].append(i)

    def count_crossing(self, box):
        seen = set()
        for gy in range(box.bottom // self.GRID, box.top // self.GRID + 1):
            for i in self.bins.get(gy, ()):
                if i in seen:
                    continue
                s = self.stripes[i]
                if (s.bottom < box.top and s.top > box.bottom
                        and s.left < box.right and s.right > box.left):
                    seen.add(i)
        return len(seen)


class Extraction:
    def __init__(self, layout: kdb.Layout, top: kdb.Cell):
        self.graph = ConductorGraph(layout, top)

        def flat(name):
            l, d = LAYERS[name]
            return kdb.Region(top.begin_shapes_rec(layout.layer(l, d)))

        nselect = IslandIndex(flat("nselect"))
        pselect = IslandIndex(flat("pselect"))
        sramvt = IslandIndex(flat("sramvt"))
        fins = _StripeIndex(flat("fin"))

        channel = (self.graph.raw["active"]
                   & self.graph.raw["gate_c"]).merged()

        self.devices = []
        self.unclassified = []
        for piece in channel.each():
            ip = interior_point(piece)
            if ip is None:
                continue
            if pselect.locate(ip.x, ip.y) is not None:
                base = "pmos"
            elif nselect.locate(ip.x, ip.y) is not None:
                base = "nmos"
            else:
                self.unclassified.append(piece.bbox())
                continue
            flavor = ("sram" if sramvt.locate(ip.x, ip.y) is not None
                      else "rvt")
            bb = piece.bbox()
            cy = ip.y
            sd = frozenset(n for n in (
                self.graph.net_of("sd", bb.left - 2, cy),
                self.graph.net_of("sd", bb.right + 2, cy)) if n is not None)
            self.devices.append(Device(
                model=f"{base}_{flavor}",
                nfin=fins.count_crossing(bb),
                gate=self.graph.net_of("gate_c", ip.x, ip.y),
                sd=sd, bbox=bb))

        # ---- net names from pin texts ----
        # net_labels: every label a net carries (a flat bank net may be
        # labeled at several hierarchy levels); net_names: the unique
        # label where there is exactly one (used for cell-level LVS).
        self.net_labels = defaultdict(set)
        self.label_probes = []      # (label, x, y, net) - position-aware
        for (l, d), cname in PIN_TEXT_LAYERS:
            for name, x, y in self.graph.texts(l, d):
                net = self.graph.net_of(cname, x, y)
                if net is None:
                    continue
                name = {"vdd!": "VDD", "vss!": "VSS"}.get(name, name)
                self.net_labels[net].add(name)
                self.label_probes.append((name, x, y, net))
        self.net_names = {net: next(iter(labels))
                          for net, labels in self.net_labels.items()
                          if len(labels) == 1}

    def nets_labeled(self, label):
        """All nets that carry the given label (possibly among others)."""
        return {net for net, labels in self.net_labels.items()
                if label in labels}

    def named(self, net):
        return self.net_names.get(net, net)

    def census(self):
        """Counter of (model, nfin) over all devices."""
        return Counter((d.model, d.nfin) for d in self.devices)


def extract(gds_path, top_name):
    ly = kdb.Layout()
    ly.read(gds_path)
    top = ly.cell(top_name)
    if top is None:
        raise ValueError(f"top cell {top_name} not found in {gds_path}")
    return Extraction(ly, top)


# ---------------------------------------------------------------------------
# The golden schematic of the 6T bitcell - the measured topology (see the
# bitcell how-it-works page): two cross-coupled inverters (PU/PD pairs
# gated by the opposite storage node) and two access transistors gated by
# WL, ACC-L bridging BLN<->QB and ACC-R bridging BL<->Q.
# Each entry: (model, nfin, gate_net, {sd_net, sd_net}).
# Q and QB are internal nets; the rest must match pin-labeled nets.
GOLDEN_6T = [
    # fin counts are the cell's own name: 6t_122 = PU 1 / ACC 2 / PD 2
    ("nmos_sram", 2, "WL", frozenset({"BLN", "QB"})),   # ACC-L
    ("nmos_sram", 2, "WL", frozenset({"BL", "Q"})),     # ACC-R
    ("pmos_sram", 1, "QB", frozenset({"VDD", "Q"})),    # PU-A
    ("nmos_sram", 2, "QB", frozenset({"VSS", "Q"})),    # PD-A
    ("pmos_sram", 1, "Q", frozenset({"VDD", "QB"})),    # PU-B
    ("nmos_sram", 2, "Q", frozenset({"VSS", "QB"})),    # PD-B
]
GOLDEN_6T_INTERNAL = {"Q", "QB"}


def match_netlist(golden, internal_names, extraction):
    """Exact isomorphism between a small golden device list and an
    extraction.  Named nets must match by name; internal golden nets are
    matched by backtracking over the extraction's unnamed nets.

    Returns (ok, detail) - detail explains the first failure found."""
    devices = [(d.model, d.nfin, extraction.named(d.gate),
                frozenset(extraction.named(n) for n in d.sd))
               for d in extraction.devices]
    if len(devices) != len(golden):
        return False, (f"device count {len(devices)} != "
                       f"golden {len(golden)}")

    unnamed = sorted({n for d in devices
                      for n in ([d[2]] + list(d[3]))
                      if not isinstance(n, str)}, key=str)
    internal = sorted(internal_names)
    if len(unnamed) != len(internal):
        return False, (f"{len(unnamed)} unnamed extracted nets vs "
                       f"{len(internal)} internal golden nets")

    def try_mapping(mapping):
        subst = []
        for model, nfin, gate, sd in devices:
            g = mapping.get(gate, gate)
            s = frozenset(mapping.get(n, n) for n in sd)
            subst.append((model, nfin, g, s))
        return Counter(subst) == Counter(
            (m, f, g, s) for m, f, g, s in golden)

    import itertools
    for perm in itertools.permutations(internal):
        if try_mapping(dict(zip(unnamed, perm))):
            return True, "matched"
    return False, ("no internal-net assignment makes the device "
                   f"multisets equal; extracted = {sorted(devices, key=str)}")
