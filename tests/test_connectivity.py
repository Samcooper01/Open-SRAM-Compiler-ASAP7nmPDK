#!/usr/bin/env python3
"""Rung 2 - connectivity: shorts and opens, from first principles.

Builds the conductor graph directly:
  * merge each conductor layer (flattened) - merged polygons are the
    intra-layer islands,
  * two physics-mandated derived layers: active−gate (the channel breaks
    source from drain) and gate−GCut (cuts sever the gate stripes),
  * for every inter-layer connection (via or direct overlap), compute the
    overlap region; each overlap piece links the two islands containing it,
  * union-find the islands into NETS,
  * point-locate every pin text into its island → its net.

Assertions:
  OPENS:  all 'BL' texts of one strip-side land on ONE net (the 32 abutted
          cell slices really concatenate); same per BLN strip and per
          WL word-column half.
  SHORTS: the net sets {BL} {BLN} {vdd!} {vss!} {WL} are pairwise disjoint.
  SELECT LINES: every column-select label (yselt<k> / yseltn<k> for the
          left array, yselb<k> / yselbn<k> for the right) sits on exactly
          one net, none on a supply, and every mux leg's ysel / yseln pin
          in the OUTERMOST row of BOTH stacks, left and right, is on the
          net its label names (the lines run through every row from the
          band; a feed that stops short leaves a whole stack's legs
          floating, which the wordline and bitline probes never see).

Usage:  python3 tests/test_connectivity.py [--config bits,strips,depth]
"""
import sys
import pathlib
import tempfile
from collections import defaultdict

import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import BankSpec, generate  # noqa: E402

from sramforge.nets import ConductorGraph  # noqa: E402


def build_nets(gds_path: str, top_name: str):
    ly = kdb.Layout(); ly.read(gds_path)
    top = ly.cell(top_name)
    graph = ConductorGraph(ly, top)

    probes = []
    for (l, d), cname in [((20, 251), "m2"), ((30, 251), "m3")]:
        for name, x, y in graph.texts(l, d):
            probes.append((name, x, y, graph.net_of(cname, x, y)))
    return probes, graph


# mux leg pin points, leg-local (measured on the vendored precharge /
# y-mux cell): ysel's M2 pin and yseln's M2 pin, per leg flavor (even
# legs are the v1 flavor, odd legs its mirror)
LEG_PIN = {"ysel": ((1420, 870), (1420, 210)), "yseln": ((836, 548), (836, 532))}


def check_selects(spec: BankSpec, graph, supply_nets: set) -> list:
    from sramforge.generator import (UPPER_STACK_ANCHOR_Y, LOWER_STACK_ANCHOR_Y,
                                     IO_COLUMN_X_REF, COLGRP_X_IN_BANK,
                                     colgrp_pitch, uses_band_v2, control_variant)
    from sramforge.band import growth_for
    fails = []
    label_net = {}
    for (l, d), cname in [((30, 251), "m3"), ((50, 251), "m5")]:
        for name, x, y in graph.texts(l, d):
            if name.startswith("ysel") and "<" in name:
                label_net.setdefault(name, set()).add(graph.net_of(cname, x, y))
    for name, nets in sorted(label_net.items()):
        if len(nets) != 1:
            fails.append(f"SELECT: label {name} on {len(nets)} nets")
        elif next(iter(nets)) in supply_nets:
            fails.append(f"SELECT: label {name} on a supply net")
    net_of = {n: next(iter(v)) for n, v in label_net.items() if len(v) == 1}
    growth = growth_for(control_variant(spec)) if uses_band_v2(spec) else 0
    pitch = colgrp_pitch(spec)
    outer = (spec.depth_groups - 1) * pitch
    io_l = COLGRP_X_IN_BANK + IO_COLUMN_X_REF
    io_r = io_l + 7344                          # the mirrored mux's axis
    for stack, yb, sgn in (("upper", UPPER_STACK_ANCHOR_Y + outer, 1),
                           ("lower", LOWER_STACK_ANCHOR_Y - growth - outer, -1)):
        for side, fam in (("left", "t"), ("right", "b")):
            for k in range(spec.strips_per_group):
                for tag, pts in LEG_PIN.items():
                    lx, ly_ = pts[k % 2]
                    x = io_l + lx if side == "left" else io_r - lx
                    y = yb + sgn * (54 + 1080 * k + ly_)
                    want = f"ysel{fam}{'' if tag == 'ysel' else 'n'}<{k}>"
                    net = graph.net_of("m2", x, y)
                    if net is None or net != net_of.get(want):
                        fails.append(f"SELECT: {stack} stack {side} leg {k} "
                                     f"{tag} pin at ({x}, {y}) is on net {net}, "
                                     f"label {want} is on {net_of.get(want)}")
    return fails


def check(spec: BankSpec, gds_path: str) -> list:
    fails = []
    probes, graph = build_nets(gds_path, spec.name)
    if not probes:
        return ["no pin texts found to probe"]

    CLASSMAP = {"BL": "BL", "BLN": "BLN", "vdd!": "VDD",
                "vss!": "VSS", "WL": "WL"}

    # SPARSE-BITS: full-width guard structures (dummy top/bottom rows,
    # fillers, tap rows) carry pin texts at every reference column.  Over
    # LIVE columns those texts merge with the real nets through the
    # abutting bitcells; over the DEAD (absent) columns they land on
    # isolated dummy-metal stubs - labels without circuits.  Exclude the
    # dead-column windows from probing (empty at the reference).
    skip = 32 - spec.bits_per_group
    live_anchor = 864 + skip * 432          # bank x of first live column
    axis = 19236                            # bank x of the io mirror axis
    dead_windows = []
    if skip:
        dead_windows.append((0, live_anchor - 100))
        dead_windows.append((2 * axis - live_anchor + 100, 2 * axis))

    def in_dead(x):
        return any(a <= x < b for a, b in dead_windows)

    unlocated = [(n, x, y) for n, x, y, net in probes
                 if net is None and n in CLASSMAP and not in_dead(x)]
    if unlocated:
        fails.append(f"{len(unlocated)} pin texts on no conductor "
                     f"(e.g. {unlocated[:2]})")

    by_class = defaultdict(list)
    for name, x, y, net in probes:
        cls = CLASSMAP.get(name)
        if cls and net is not None and not in_dead(x):
            by_class[cls].append((x, y, net))

    # ---- SHORTS ----
    net_ids = {c: set(n for _, _, n in lst) for c, lst in by_class.items()}
    classes = sorted(net_ids)
    for i, a in enumerate(classes):
        for b in classes[i + 1:]:
            common = net_ids[a] & net_ids[b]
            if common:
                fails.append(f"SHORT: {a} and {b} share {len(common)} net(s)")

    # ---- OPENS: bitlines, one net per strip-side ----
    # side divider = center of the io column (fixed: sparse-bits keeps
    # every anchor at its reference position)
    mid_x = 18824
    for cls in ("BL", "BLN"):
        groups = defaultdict(set)
        for x, y, net in by_class.get(cls, []):
            # snap y to the 1080-dbu row slot: cell-level and
            # overlay-level labels of one strip differ by a few dbu
            groups[(y // 1080, 0 if x < mid_x else 1)].add(net)
        broken = {k: len(v) for k, v in groups.items() if len(v) > 1}
        if broken:
            k, n = next(iter(broken.items()))
            fails.append(f"OPEN: {cls} strip row-slot {k[0]} side {k[1]} splits "
                         f"into {n} nets ({len(broken)} strips broken)")
        # strips per colgrp-row = strips x 2 arrays; each strip is one side
        expected = spec.strips_per_group * 2 * 2 * spec.depth_groups
        if len(groups) != expected:
            fails.append(f"{cls}: {len(groups)} strip-side groups, "
                         f"expected {expected}")

    # ---- OPENS: wordlines, one net per word column per half ----
    wl_groups = defaultdict(set)
    for x, y, net in by_class.get("WL", []):
        half = 0 if y < 155520 else 1
        wl_groups[half, x].add(net)
    # cells in the same column may report slightly different text x when
    # mirrored - cluster columns on the 432-dbu slot grid instead
    col_groups = defaultdict(set)
    for (half, x), nets in wl_groups.items():
        col_groups[half, x // 432].update(nets)
    broken = {k: len(v) for k, v in col_groups.items() if len(v) > 1}
    if broken:
        k, n = next(iter(broken.items()))
        fails.append(f"OPEN: WL column slot {k[1]} half {k[0]} splits into "
                     f"{n} nets ({len(broken)} columns broken)")

    print("    nets by class: " +
          ", ".join(f"{c}:{len(net_ids.get(c, set()))}"
                    for c in ("BL", "BLN", "VDD", "VSS", "WL")))

    # ---- SELECT LINES: labels one net each, every leg pin on its label ----
    sel_fails = check_selects(spec, graph,
                              net_ids.get("VDD", set()) | net_ids.get("VSS", set()))
    if sel_fails:
        fails.extend(sel_fails[:6] + ([f"({len(sel_fails)} select-line failures)"]
                                      if len(sel_fails) > 6 else []))
    else:
        n_legs = 2 * 2 * spec.strips_per_group
        print(f"    select lines: {2 * 2 * spec.strips_per_group} labels one net "
              f"each, {2 * n_legs} outermost-row leg pins on their labels")
    return fails


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="32,4,32")
    a = ap.parse_args()
    bits, strips, depth = map(int, a.config.split(","))
    spec = BankSpec(bits_per_group=bits, strips_per_group=strips,
                    depth_groups=depth)
    with tempfile.TemporaryDirectory() as td:
        gds = str(pathlib.Path(td) / f"{spec.name}.gds")
        generate(spec, gds)
        print(f"[{spec.name}]")
        fails = check(spec, gds)
    if fails:
        print("[FAIL]")
        for f in fails:
            print("    -", f)
        return 1
    print("[PASS] no shorts, no opens across all probed nets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
