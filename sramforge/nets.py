"""The conductor graph: merged islands + physics splits + union-find.

Promoted from tests/test_connectivity.py (rung 2) so that rung 4 (LVS -
Layout Versus Schematic) can build device extraction on the same engine.
Behavior is identical to the rung 2 in-test version.

The graph is built from first principles:
  * merge each conductor layer (flattened) - merged polygons are the
    intra-layer islands,
  * two physics-mandated derived layers: active−gate (the channel breaks
    source from drain) and gate−GCut (cuts sever the gate stripes),
  * for every inter-layer connection (via or direct overlap), compute the
    overlap region; each overlap piece links the two islands containing it,
  * union-find the islands into NETS.
"""
from collections import defaultdict

import klayout.db as kdb

from .tech import LAYERS

CONDUCTORS = ["sd", "gate_c", "sdt", "lisd", "lig", "v0", "m1", "v1",
              "m2", "v2", "m3", "v3", "m4", "v4", "m5"]
CONNECTIONS = [("sd", "sdt"), ("sdt", "lisd"), ("lisd", "lig"),
               ("gate_c", "lig"), ("lisd", "v0"), ("lig", "v0"),
               ("v0", "m1"), ("m1", "v1"), ("v1", "m2"), ("m2", "v2"),
               ("v2", "m3"), ("m3", "v3"), ("v3", "m4"), ("m4", "v4"),
               ("v4", "m5")]


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, a):
        p = self.parent.setdefault(a, a)
        while p != self.parent[p]:
            self.parent[p] = self.parent[self.parent[p]]
            p = self.parent[p]
        self.parent[a] = p
        return p

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


class IslandIndex:
    """Merged polygons of one conductor + grid-hashed point location."""
    GRID = 5000  # dbu bin size

    def __init__(self, region: kdb.Region):
        self.polys = list(region.merged().each())
        self.bins = defaultdict(list)
        for i, p in enumerate(self.polys):
            b = p.bbox()
            for gx in range(b.left // self.GRID, b.right // self.GRID + 1):
                for gy in range(b.bottom // self.GRID, b.top // self.GRID + 1):
                    self.bins[(gx, gy)].append(i)

    def locate(self, x, y):
        pt = kdb.Point(x, y)
        for i in self.bins.get((x // self.GRID, y // self.GRID), ()):
            if self.polys[i].inside(pt):
                return i
        return None


def interior_point(poly: kdb.Polygon):
    c = poly.bbox().center()
    if poly.inside(c):
        return c
    for pt in poly.each_point_hull():
        for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            q = kdb.Point(pt.x + dx, pt.y + dy)
            if poly.inside(q):
                return q
    return None


class ConductorGraph:
    """Islands per conductor + union-find nets over one cell of a layout."""

    def __init__(self, layout: kdb.Layout, top: kdb.Cell):
        self.layout = layout
        self.top = top

        def flat(name):
            l, d = LAYERS[name]
            return kdb.Region(top.begin_shapes_rec(layout.layer(l, d)))

        raw = {n: flat(n) for n in
               ("active", "gate", "gcut", "sdt", "lisd", "lig", "v0", "m1",
                "v1", "m2", "v2", "m3", "v3", "m4", "v4", "m5")}
        raw["sd"] = raw["active"] - raw["gate"]
        raw["gate_c"] = raw["gate"] - raw["gcut"]
        self.raw = raw

        self.idx = {name: IslandIndex(raw[name]) for name in CONDUCTORS}
        self.uf = UnionFind()

        merged = {name: kdb.Region([p for p in self.idx[name].polys])
                  for name in CONDUCTORS}
        self.merged = merged
        for a, b in CONNECTIONS:
            overlap = (merged[a] & merged[b]).merged()
            for piece in overlap.each():
                ip = interior_point(piece)
                if ip is None:
                    continue
                ia = self.idx[a].locate(ip.x, ip.y)
                ib = self.idx[b].locate(ip.x, ip.y)
                if ia is not None and ib is not None:
                    self.uf.union((a, ia), (b, ib))

    def net_of(self, conductor, x, y):
        """Canonical net id of the point (x, y) on the given conductor,
        or None if no island contains it."""
        i = self.idx[conductor].locate(x, y)
        return self.uf.find((conductor, i)) if i is not None else None

    def texts(self, l, d):
        """All text labels on GDS layer (l, d): (string, x, y) triples."""
        out = []
        it = self.top.begin_shapes_rec(self.layout.layer(l, d))
        while not it.at_end():
            sh = it.shape()
            if sh.is_text():
                p = it.trans() * kdb.Point(sh.text_trans.disp.x,
                                           sh.text_trans.disp.y)
                out.append((sh.text_string, p.x, p.y))
            it.next()
        return out
