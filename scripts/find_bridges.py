#!/usr/bin/env python3
"""Bridge finder: locate the FIRST geometric edges that weld two nets of
different signal classes together.

Runs the same island construction as the rung-2 conductor graph, but
instead of blindly union-finding, it carries a CLASS SET per component
(seeded from the pin texts).  When an overlap edge would merge two
components whose class sets conflict (both non-empty and different), the
merge is refused and the edge is reported with its bbox - the exact spot
of the bridge.

Usage: python3 scripts/find_bridges.py [--config 16,4,32] [--max 8]
"""
import argparse
import pathlib
import sys
import tempfile
from collections import defaultdict

import klayout.db as kdb

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from sramforge import BankSpec, generate                    # noqa: E402
from sramforge.nets import (CONDUCTORS, CONNECTIONS, IslandIndex,
                            UnionFind, interior_point)      # noqa: E402
from sramforge.tech import LAYERS                           # noqa: E402

CLASSMAP = {"BL": "BL", "BLN": "BLN", "vdd!": "VDD",
            "vss!": "VSS", "WL": "WL"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="16,4,32")
    ap.add_argument("--max", type=int, default=8)
    ap.add_argument("--gds", default=None,
                    help="check an existing bank GDS instead of generating")
    a = ap.parse_args()
    bits, strips, depth = map(int, a.config.split(","))
    spec = BankSpec(bits_per_group=bits, strips_per_group=strips,
                    depth_groups=depth)

    td = tempfile.TemporaryDirectory()
    gds = a.gds or str(pathlib.Path(td.name) / "bank.gds")
    if not a.gds:
        generate(spec, gds)
    ly = kdb.Layout()
    ly.read(gds)
    top = ly.cell(spec.name)

    def flat(name):
        l, d = LAYERS[name]
        return kdb.Region(top.begin_shapes_rec(ly.layer(l, d)))

    raw = {n: flat(n) for n in
           ("active", "gate", "gcut", "sdt", "lisd", "lig", "v0", "m1",
            "v1", "m2", "v2", "m3", "v3", "m4", "v4", "m5")}
    raw["sd"] = raw["active"] - raw["gate"]
    raw["gate_c"] = raw["gate"] - raw["gcut"]
    idx = {name: IslandIndex(raw[name]) for name in CONDUCTORS}

    # class seeds from pin texts (same dead-window logic as rung 2)
    skip = 32 - spec.bits_per_group
    live_anchor = 864 + skip * 432
    axis = 19236
    dead = ([(0, live_anchor - 100),
             (2 * axis - live_anchor + 100, 2 * axis)] if skip else [])

    def in_dead(x):
        return any(lo <= x < hi for lo, hi in dead)

    seeds = {}                       # (conductor, island) -> class
    for (l, d), cname in [((20, 251), "m2"), ((30, 251), "m3")]:
        li = ly.layer(l, d)
        it = top.begin_shapes_rec(li)
        while not it.at_end():
            sh = it.shape()
            if sh.is_text():
                p = it.trans() * kdb.Point(sh.text_trans.disp.x,
                                           sh.text_trans.disp.y)
                cls = CLASSMAP.get(sh.text_string)
                if cls and not in_dead(p.x):
                    i = idx[cname].locate(p.x, p.y)
                    if i is not None:
                        seeds[(cname, i)] = cls
            it.next()

    # WL/BL classes are per-net: two different WL columns must NOT merge
    # either.  Give every seed a unique id within its class, so ANY merge
    # of two differently-seeded components is a bridge candidate - but
    # only report cross-CLASS bridges first (they are the rung-2 shorts).
    uf = UnionFind()
    comp_class = {}                  # root -> class name
    for k, cls in seeds.items():
        comp_class[uf.find(k)] = cls

    bridges = []
    merged = {name: kdb.Region([p for p in idx[name].polys])
              for name in CONDUCTORS}
    for cn_a, cn_b in CONNECTIONS:
        overlap = (merged[cn_a] & merged[cn_b]).merged()
        for piece in overlap.each():
            ip = interior_point(piece)
            if ip is None:
                continue
            ia = idx[cn_a].locate(ip.x, ip.y)
            ib = idx[cn_b].locate(ip.x, ip.y)
            if ia is None or ib is None:
                continue
            ra, rb = uf.find((cn_a, ia)), uf.find((cn_b, ib))
            if ra == rb:
                continue
            ca, cb = comp_class.get(ra), comp_class.get(rb)
            if ca is not None and cb is not None and ca != cb:
                bridges.append((ca, cb, cn_a, cn_b, piece.bbox()))
                continue             # refuse the merge, keep classes pure
            uf.union(ra, rb)
            r = uf.find(ra)
            comp_class[r] = ca if ca is not None else cb

    print(f"bridges (cross-class welds): {len(bridges)}")
    for ca, cb, cn_a, cn_b, bb in bridges[:a.max]:
        print(f"  {ca}--{cb} via {cn_a}/{cn_b} at "
              f"({bb.left},{bb.bottom})-({bb.right},{bb.top})")
    return 0 if not bridges else 1


if __name__ == "__main__":
    sys.exit(main())
