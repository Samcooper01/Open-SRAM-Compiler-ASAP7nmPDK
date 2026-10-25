#!/usr/bin/env python3
"""Locate the exact overlap piece(s) welding VDD to VSS.

Builds the full conductor graph of a generated bank, takes the welded
supply net, forms its island-adjacency graph (overlap pieces = edges),
computes bridge edges, and reports every bridge whose removal separates
the VDD-labeled islands from the VSS-labeled islands - the physical
short, with its bbox and layer pair.

Usage: python3 scripts/find_weld.py [--config 16,4,32]
"""
import argparse
import pathlib
import sys
import tempfile
from collections import defaultdict

import klayout.db as kdb

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from sramforge import BankSpec, generate                    # noqa: E402
from sramforge.nets import (CONDUCTORS, CONNECTIONS, ConductorGraph,
                            interior_point)                 # noqa: E402

SUP = {"VDD", "VSS", "VDD!", "VSS!", "vdd!", "vss!"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="16,4,32")
    a = ap.parse_args()
    bits, strips, depth = map(int, a.config.split(","))
    spec = BankSpec(bits_per_group=bits, strips_per_group=strips,
                    depth_groups=depth)
    td = tempfile.TemporaryDirectory()
    gds = str(pathlib.Path(td.name) / "bank.gds")
    generate(spec, gds)
    ly = kdb.Layout(); ly.read(gds)
    top = ly.cell(spec.name)
    g = ConductorGraph(ly, top)

    vdd_isl, vss_isl = set(), set()
    for (l, dd), cn in [((19, 251), "m1"), ((20, 251), "m2"),
                        ((30, 251), "m3"), ((40, 251), "m4"),
                        ((50, 251), "m5")]:
        for n, x, y in g.texts(l, dd):
            if n in SUP:
                i = g.idx[cn].locate(x, y)
                if i is not None:
                    (vdd_isl if "dd" in n.lower()
                     else vss_isl).add((cn, i))
    welded = ({g.uf.find(k) for k in vdd_isl}
              & {g.uf.find(k) for k in vss_isl})
    print(f"welded roots: {len(welded)}")
    if not welded:
        print("no VDD-VSS weld - supply nets are separate")
        return 0
    W = welded

    # edges of the welded net(s)
    nodes = set()
    edges = []                          # (u, v, (cn_a, cn_b, bbox))
    for cn_a, cn_b in CONNECTIONS:
        ov = (g.merged[cn_a] & g.merged[cn_b]).merged()
        for piece in ov.each():
            ip = interior_point(piece)
            if ip is None:
                continue
            ia = g.idx[cn_a].locate(ip.x, ip.y)
            ib = g.idx[cn_b].locate(ip.x, ip.y)
            if ia is None or ib is None:
                continue
            u, v = (cn_a, ia), (cn_b, ib)
            if g.uf.find(u) in W:
                nodes.add(u); nodes.add(v)
                edges.append((u, v, (cn_a, cn_b, piece.bbox())))
    print(f"welded net: {len(nodes)} islands, {len(edges)} edges")

    # Tarjan bridges (iterative)
    adj = defaultdict(list)
    for ei, (u, v, _) in enumerate(edges):
        adj[u].append((v, ei)); adj[v].append((u, ei))
    disc, low = {}, {}
    bridges = []
    timer = [0]
    for s0 in nodes:
        if s0 in disc:
            continue
        stack = [(s0, -1, iter(adj[s0]))]
        disc[s0] = low[s0] = timer[0]; timer[0] += 1
        while stack:
            u, pe, it = stack[-1]
            adv = False
            for v, ei in it:
                if ei == pe:
                    continue
                if v not in disc:
                    disc[v] = low[v] = timer[0]; timer[0] += 1
                    stack.append((v, ei, iter(adj[v])))
                    adv = True
                    break
                low[u] = min(low[u], disc[v])
            if not adv:
                stack.pop()
                if stack:
                    pu = stack[-1][0]
                    low[pu] = min(low[pu], low[u])
                    if low[u] > disc[pu]:
                        bridges.append(pe)
    print(f"bridge edges: {len(bridges)}")

    # which bridges separate VDD seeds from VSS seeds?
    vdd_in = {k for k in vdd_isl if g.uf.find(k) in W}
    vss_in = {k for k in vss_isl if g.uf.find(k) in W}
    culprits = []
    for bei in bridges:
        parent = {}
        def find(x):
            r = x
            while parent.get(r, r) != r:
                r = parent[r]
            parent[x] = r
            return r
        for ei, (u, v, _) in enumerate(edges):
            if ei == bei:
                continue
            ru, rv = find(u), find(v)
            if ru != rv:
                parent[ru] = rv
        sides_v = {find(k) for k in vdd_in}
        sides_s = {find(k) for k in vss_in}
        if not (sides_v & sides_s):
            culprits.append(bei)
    print(f"separating welds: {len(culprits)}")
    for ei in culprits:
        u, v, (cn_a, cn_b, bb) = edges[ei]
        print(f"  WELD {cn_a}/{cn_b} at "
              f"({bb.left},{bb.bottom})-({bb.right},{bb.top})")
    return 1 if culprits or welded else 0


if __name__ == "__main__":
    sys.exit(main())
