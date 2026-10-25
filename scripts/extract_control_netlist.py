#!/usr/bin/env python3
"""Reconstruct the control block's gate-level netlist from geometry.

Rung-4 machinery pointed at standard cells: build the conductor graph
over the control macro, locate every instance's signal-pin text through
its transform, and point-probe the net under each pin.  Ports come from
the macro's own pin labels.  Output:

  out/control_netlist.v      structural Verilog (ASAP7 cell names)
  out/control_netlist.json   instances (cell, x, y, orientation),
                             pin->net map, ports, nets

This is the input for the OpenROAD re-place-and-route of the block (the
step that unlocks true bits-narrowing).

Usage:  python3 scripts/extract_control_netlist.py
"""
import json
import pathlib
import sys
from collections import defaultdict

import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge.nets import ConductorGraph          # noqa: E402

CTRL = "control_sram_2kB_6t122_v3_orig10242022_lvspass_32"
PIN_LAYER = (19, 251)          # std-cell pin texts live on m1's text layer
SUPPLIES = {"VDD", "VSS", "VDD!", "VSS!", "vdd!", "vss!"}
# physical-only cells: no signal pins, excluded from the netlist
PHYS = ("TAPCELL", "FILLER", "DECAP", "VIA", "control_sram_2kB")


def cell_pins(layout, cell):
    """Signal-pin texts of a cell definition: [(name, x, y)]."""
    out = []
    for li in layout.layer_indexes():
        info = layout.get_info(li)
        if (info.layer, info.datatype) != PIN_LAYER:
            continue
        for sh in cell.shapes(li).each():
            if sh.is_text() and sh.text_string not in SUPPLIES:
                d = sh.text_trans.disp
                out.append((sh.text_string, d.x, d.y))
    return out


def short_name(cell_name):
    return cell_name.split("_asap7sc7p5t")[0]


def main():
    ly = kdb.Layout()
    ly.read(str(REPO / "cells" / "ctrl_decode_32.gds"))
    ctl = ly.cell(ly.cell_by_name(CTRL))
    graph = ConductorGraph(ly, ctl)

    pin_cache = {}
    instances = []
    unresolved = []
    net_pins = defaultdict(list)       # net -> [(inst, pin)]
    for i, inst in enumerate(ctl.each_inst()):
        cname = inst.cell.name
        if cname.startswith(PHYS):
            continue
        if cname not in pin_cache:
            pin_cache[cname] = cell_pins(ly, inst.cell)
        t = inst.cell_inst.trans
        iname = f"U{i}"
        pins = {}
        for pname, px, py in pin_cache[cname]:
            p = t * kdb.Point(px, py)
            net = graph.net_of("m1", p.x, p.y)
            if net is None:
                unresolved.append((iname, cname, pname, p.x, p.y))
            pins[pname] = net
        instances.append({
            "name": iname, "cell": short_name(cname),
            "x": t.disp.x, "y": t.disp.y,
            "orient": str(t),
            "pins": {k: (f"{v[0]}:{v[1]}" if v else None)
                     for k, v in pins.items()},
        })
        for pname, net in pins.items():
            if net is not None:
                net_pins[net].append((iname, pname))

    # ---- I/O nets: single-pin nets + anything reaching m4/m5 pads ----
    from sramforge.nets import interior_point
    io_nets = {n for n, ps in net_pins.items() if len(ps) == 1}
    io_points = defaultdict(list)      # net -> [(cname, x, y)] probe points
    io_boxes = defaultdict(list)
    m3_boxes = defaultdict(list)
    for cname in ("m3", "m4", "m5"):
        idx = graph.idx[cname]
        for i, poly in enumerate(idx.polys):
            net = graph.uf.find((cname, i))
            if net in io_nets or net in net_pins:
                ip = interior_point(poly)
                if ip is None:
                    continue
                if cname == "m5":
                    io_nets.add(net)
                io_points[net].append((cname, ip.x, ip.y))
                b = poly.bbox()
                if net in io_nets and cname != "m3":
                    io_boxes[net].append(
                        [cname, b.left, b.bottom, b.right, b.top])
                elif cname == "m3":
                    m3_boxes[net].append(
                        [cname, b.left, b.bottom, b.right, b.top])

    # ---- port names: probe a BAND-level conductor graph.  The interface
    # labels (ADDRESS/clk/sdel on m5, WLENA on the decoder...) sit on
    # band-level wires that CONNECT to the macro's pads; only the whole-
    # band graph sees the connection. ----
    band = ly.cell(ly.cell_by_name("ctrl_decode_32"))
    ctl_disp = None
    for inst in band.each_inst():
        if inst.cell.name == CTRL:
            ctl_disp = inst.cell_inst.trans.disp
    band_graph = ConductorGraph(ly, band)
    band_names = defaultdict(set)      # band net -> label names
    for (l, d), cname in [((19, 251), "m1"), ((20, 251), "m2"),
                          ((30, 251), "m3"), ((40, 251), "m4"),
                          ((50, 251), "m5")]:
        for name, x, y in band_graph.texts(l, d):
            if name in SUPPLIES:
                continue
            net = band_graph.net_of(cname, x, y)
            if net is not None:
                band_names[net].add(name)
    ports = {}
    band_net_of = {}                   # control net -> band net
    for net, pts in io_points.items():
        for cname, x, y in pts:
            bnet = band_graph.net_of(cname, x + ctl_disp.x, y + ctl_disp.y)
            if bnet is None:
                continue
            band_net_of.setdefault(net, bnet)
            for label in band_names.get(bnet, ()):
                ports.setdefault(label, set()).add(net)

    # The REAL landing pads are the BAND-side wires overlapping the macro
    # region (the macro-side islands can be tiny stubs too small to land
    # a via on).  Replace each io net's boxes with its band net's
    # m3/m4/m5 geometry clipped to the macro extent, in control frame.
    ctl_box_band = kdb.Box(ctl_disp.x + 33500, ctl_disp.y - 4720,
                           ctl_disp.x + 71084, ctl_disp.y + 26012)
    # TRUE io test: a control net is I/O only if its band-side net ESCAPES
    # the macro region (has geometry outside it) - the band graph includes
    # the macro's own shapes, so without this test every net that touches
    # m3 looks like a port.
    escapes = set()
    for cname in ("m1", "m2", "m3", "m4", "m5"):
        idx = band_graph.idx[cname]
        for i, poly in enumerate(idx.polys):
            b = poly.bbox()
            if not (b.left >= ctl_box_band.left
                    and b.right <= ctl_box_band.right
                    and b.bottom >= ctl_box_band.bottom
                    and b.top <= ctl_box_band.top):
                escapes.add(band_graph.uf.find((cname, i)))
    # generic cell-pin-name labels (A/B/D/Q/Y/CLK...) that leak onto
    # band nets - never evidence of a real top-level port
    GENERIC_LABELS = {"A", "B", "C", "D", "Q", "Y", "H", "CLK",
                      "A1", "A2", "B1", "B2"}
    # a band net carrying a REAL top-level port label (e.g. clk, whose
    # whole band wire sits INSIDE the macro-extent box) is external by
    # definition - the geometric escape test alone drops it, which loses
    # its glue and forest attribution and leaves the port's band wire
    # floating after any strap re-placement.  Generic cell-pin labels
    # (A/B/CLK...) leak onto internal band nets and must not mint ports.
    band_net_of = {net: bnet for net, bnet in band_net_of.items()
                   if bnet in escapes
                   or band_names.get(bnet, set()) - GENERIC_LABELS}
    io_nets = {n for n in io_nets if n in band_net_of or True} & (
        set(band_net_of) | {n for n, ps in net_pins.items() if len(ps) == 1})
    io_boxes = defaultdict(
        list, {n: v for n, v in io_boxes.items() if n in io_nets})
    io_band_nets = set(band_net_of.values())

    def slabs(region):
        """Rectilinear region -> horizontal rectangle slabs (exact)."""
        out = []
        for poly in region.each():
            ys = sorted({pt.y for pt in poly.each_point_hull()})
            for y0, y1 in zip(ys, ys[1:]):
                row = kdb.Region(poly) & kdb.Region(
                    kdb.Box(poly.bbox().left, y0, poly.bbox().right, y1))
                for rp in row.each():
                    out.append(rp.bbox())
        return out

    band_geo = defaultdict(list)       # band net -> [(cname, Box)]
    obstruction_shapes = []            # foreign nets' metal over the macro
    for cname in ("m2", "m3", "m4", "m5"):
        idx = band_graph.idx[cname]
        for i, poly in enumerate(idx.polys):
            if not poly.bbox().overlaps(ctl_box_band):
                continue
            bnet = band_graph.uf.find((cname, i))
            clipped = kdb.Region(poly) & kdb.Region(ctl_box_band)
            for rect in slabs(clipped):
                rec = [cname, rect.left - ctl_disp.x,
                       rect.bottom - ctl_disp.y,
                       rect.right - ctl_disp.x, rect.top - ctl_disp.y]
                if bnet in io_band_nets:
                    band_geo[bnet].append((cname, rect))
                else:
                    obstruction_shapes.append(rec)
    # PIN MODEL: the router lands on the MACRO-side m4 (or m3) islands the
    # original design engineered as landing zones; the original vertical
    # interface stack ABOVE the pin layer (v3/v4 cuts, m4/m5 tails) is
    # reinstated verbatim at merge time as "glue", reconnecting the
    # untouched band wires exactly as before.  Band-side geometry is used
    # only when a net has no macro-side m4/m3 landing.
    glue = defaultdict(list)           # net_name -> [[layer, x0,y0,x1,y1]]
    GLUE_LAYERS = ("v3", "m4", "v4", "m5")
    macro_land = defaultdict(list)     # net -> [(cname, Box)] m4/m3 islands
    for cname in ("m3", "m4"):
        idx = graph.idx[cname]
        for i, poly in enumerate(idx.polys):
            net = graph.uf.find((cname, i))
            if net in io_nets:
                macro_land[net].append((cname, poly.bbox()))
    for cname in GLUE_LAYERS:
        if cname not in graph.idx:
            continue
        idx = graph.idx[cname]
        for i, poly in enumerate(idx.polys):
            net = graph.uf.find((cname, i))
            if net in io_nets:
                b = poly.bbox()
                glue[net].append([cname, b.left, b.bottom, b.right, b.top])
    for net, bnet in band_net_of.items():
        land = macro_land.get(net)
        if land:
            # prefer m4 landings, largest first
            land.sort(key=lambda cb: (cb[0] == "m4",
                                      cb[1].area()), reverse=True)
            io_boxes[net] = [[c, b.left, b.bottom, b.right, b.top]
                             for c, b in land]
        else:
            pieces = band_geo.get(bnet)
            if pieces:
                io_boxes[net] = [
                    [cname, b.left - ctl_disp.x, b.bottom - ctl_disp.y,
                     b.right - ctl_disp.x, b.top - ctl_disp.y]
                    for cname, b in pieces]

    # generic cell-pin-name labels leak onto band nets as aliases -
    # drop them, and collapse case-duplicate aliases by preferring the
    # longest, most specific name per net
    ports = {k: v for k, v in ports.items() if k not in GENERIC_LABELS}
    port_conflicts = {k: v for k, v in ports.items() if len(v) > 1}
    by_net = defaultdict(set)
    for name, nets in ports.items():
        for n in nets:
            by_net[n].add(name)
    port_net = {}
    for net, names in by_net.items():
        # deterministic total order: length, then lexicographic
        best = max(names, key=lambda s: (len(s), s))
        port_net[best] = net

    # net naming: port name where labeled, else n<layer>_<island>
    net_name = {}
    for pname, net in port_net.items():
        net_name.setdefault(net, _sanitize(pname))
    for net in net_pins:
        net_name.setdefault(net, f"n{net[0]}_{net[1]}")

    # ---- emit Verilog ----
    # module ports = every net with an io pad (named or not): the band
    # connects at those pads, so the router must treat them as ports
    used_nets = sorted({net_name[n] for n in net_pins}
                       | set(net_name[n] for n in port_net.values()))
    port_names = sorted({net_name[n] for n in io_boxes}
                        | {_sanitize(p) for p in port_net})
    # ---- band exits: wires leaving the band bottom across the control
    # x-range.  Supply exits are flagged (they must cross the one-box die
    # on their ORIGINAL layer to feed the row rails; signal exits cross
    # on M5).
    vdd_nets, vss_nets = set(), set()
    for (l, dd), cname in [((19, 251), "m1"), ((20, 251), "m2"),
                           ((30, 251), "m3"), ((40, 251), "m4"),
                           ((50, 251), "m5")]:
        for name2, x2, y2 in band_graph.texts(l, dd):
            if name2 in SUPPLIES:
                nn = band_graph.net_of(cname, x2, y2)
                if nn is not None:
                    (vdd_nets if "dd" in name2.lower()
                     else vss_nets).add(nn)
    band_supply_nets = vdd_nets | vss_nets
    # The electrical seam to the lower stack is the bottom GUARD ROW
    # (dummy_vertical_array_X64): its vertical wires carry every signal
    # and rail from the band into the mirrored column groups, and band v2
    # slides it down with the stack.  A band wire is an EXIT (needs a
    # macro feedthrough) iff it overlaps guard metal on its own layer -
    # jogs and intermediate segments of the same nets stay verbatim.
    guard_reg = {}
    guard_shapes = []
    for inst in band.each_inst():
        if (inst.cell.name == "dummy_vertical_array_X64"
                and inst.cell_inst.trans.disp.y < 20000):
            t3 = inst.cell_inst.trans
            for gname, (gl, gd) in [("m1", (19, 0)), ("m2", (20, 0)),
                                    ("m3", (30, 0))]:
                gli = ly.layer(gl, gd)
                guard_reg.setdefault(gname, kdb.Region())
                guard_reg[gname] += kdb.Region(
                    inst.cell.begin_shapes_rec(gli)).transformed(t3)
    for gname, greg in guard_reg.items():
        greg.merge()
        for poly in greg.each():
            gb = poly.bbox()
            guard_shapes.append([gname, gb.left - ctl_disp.x,
                                 gb.bottom - ctl_disp.y,
                                 gb.right - ctl_disp.x, gb.top - ctl_disp.y])
    band_exits = []
    for cname in ("m2", "m3", "m4", "m5"):
        li3 = ly.layer(*{"m2": (20, 0), "m3": (30, 0),
                         "m4": (40, 0), "m5": (50, 0)}[cname])
        greg = guard_reg.get(cname)
        if greg is None:
            continue
        for sh in band.shapes(li3).each():
            if sh.is_text():
                continue
            b3 = sh.bbox()
            if not (b3.right > ctl_disp.x + 33500
                    and b3.left < ctl_disp.x + 71084):
                continue
            if b3.width() > b3.height():
                # horizontal wires LIE ON a guard rail (they don't cross
                # the seam) - their mesh is fed by the vertical exits, and
                # a vertical feedthrough stub here would cross BOTH guard
                # rails on the same layer (VDD-VSS weld)
                continue
            # sized(2): edge-ABUTTING wires (bottom exactly at the guard
            # top - wl<30>/31-right) are contacts too
            gover = kdb.Region(kdb.Box(b3)).sized(2) & greg
            if gover.is_empty():
                continue
            ip3 = interior_point(sh.polygon)
            bn3 = band_graph.net_of(cname, ip3.x, ip3.y) if ip3 else None
            pol = ("VDD" if bn3 in vdd_nets
                   else "VSS" if bn3 in vss_nets else None)
            # contact column: x-range of the LARGEST wire-guard overlap.
            # Wide horizontal exits span guard verticals of BOTH
            # polarities - the feedthrough may only reconnect at a column
            # the original wire actually contacted.
            big = max(gover.each(), key=lambda p: p.area())
            gb = big.bbox()
            band_exits.append(
                [cname, b3.left - ctl_disp.x, b3.bottom - ctl_disp.y,
                 b3.right - ctl_disp.x, b3.top - ctl_disp.y, pol,
                 gb.left - ctl_disp.x, gb.right - ctl_disp.x])
    # single-owner die: every band wire in the under-bar zone that STAYS
    # (jogs, dead-end verticals, non-exit segments) becomes a routing
    # obstruction for the macro router; guard metal is excluded (it moves
    # down with the stack, so its old position is free space).
    # per-net FOREST geometry of io nets (m4/m5 above the under-bar zone):
    # strap risers/jogs must treat every OTHER port's band wiring as an
    # obstacle - io nets are excluded from `obstructions`, and a riser
    # dropped between two distribution columns welds them (found by the
    # analog write sweep as ysel/ysel welds)
    io_forest = {}
    for cname in ("m4", "m5"):
        idx4 = band_graph.idx[cname]
        for i4, poly4 in enumerate(idx4.polys):
            b4 = poly4.bbox()
            if (b4.top < ctl_disp.y - 4650
                    or b4.right < ctl_disp.x + 33500
                    or b4.left > ctl_disp.x + 71084):
                continue
            bn4 = band_graph.uf.find((cname, i4))
            for net4, bnet4 in band_net_of.items():
                if bnet4 == bn4:
                    nm4 = net_name.get(net4)
                    if nm4:
                        io_forest.setdefault(_sanitize(nm4), []).append(
                            [cname, b4.left - ctl_disp.x,
                             b4.bottom - ctl_disp.y,
                             b4.right - ctl_disp.x, b4.top - ctl_disp.y])
                    break

    underbar = []
    UB_Y0, UB_Y1 = -8100, -4650                      # control frame
    ub_clip = kdb.Region(kdb.Box(ctl_disp.x + 33500, ctl_disp.y + UB_Y0,
                                 ctl_disp.x + 71084, ctl_disp.y + UB_Y1))
    for cname in ("m2", "m3", "m4", "m5"):
        li3 = ly.layer(*{"m2": (20, 0), "m3": (30, 0),
                         "m4": (40, 0), "m5": (50, 0)}[cname])
        reg = kdb.Region(band.begin_shapes_rec(li3)) & ub_clip
        reg -= guard_reg.get(cname, kdb.Region())
        for poly in reg.merged().each():
            b3 = poly.bbox()
            underbar.append(
                [cname, b3.left - ctl_disp.x, b3.bottom - ctl_disp.y,
                 b3.right - ctl_disp.x, b3.top - ctl_disp.y])

    # rail polarity: probe the original control's m1 rails per row anchor
    rail_pol = {}
    for k in range(6):
        ry = -4356 + k * 1080 + 36           # rail center, control frame
        nn = band_graph.net_of("m1", ctl_disp.x + 50000, ctl_disp.y + ry)
        rail_pol[str(ry % 2160)] = ("VDD" if nn in vdd_nets
                                    else "VSS" if nn in vss_nets else None)


    # feedthrough ports: one per band exit wire - the router carries
    # each exit through the macro fabric (two pin boxes, top and bottom)
    ft_names = [f"ft_{i}" for i in range(len(band_exits))]
    lines = [f"// reconstructed from {CTRL} geometry by "
             "scripts/extract_control_netlist.py",
             f"module control_sram_2kB "
             f"({', '.join(port_names + ft_names)});"]
    for p in port_names + ft_names:
        lines.append(f"  inout {p};")   # direction unknown from geometry
    for w in used_nets:
        if w not in port_names:
            lines.append(f"  wire {w};")
    for inst in instances:
        conns = ", ".join(f".{p}({net_name[_unkey(n)]})"
                          for p, n in sorted(inst["pins"].items())
                          if n is not None)
        lines.append(f"  {inst['cell']} {inst['name']} ({conns});")
    lines.append("endmodule")
    (REPO / "out" / "control_netlist.v").write_text("\n".join(lines) + "\n")

    for pname, net in port_net.items():
        if net not in io_boxes and net in m3_boxes:
            io_boxes[net] = sorted(m3_boxes[net],
                key=lambda b: (b[3]-b[1])*(b[4]-b[2]))[-2:]
    io_geo = {net_name.get(n, f"n{n[0]}_{n[1]}"): boxes
              for n, boxes in io_boxes.items()}

    # ---- supply web: the macro's own VDD/VSS straps + via cells become
    # merge-time glue, and their strap regions become routing obstructions
    supply_nets = set()
    for (l, dd), cname in [((19, 251), "m1"), ((20, 251), "m2"),
                           ((30, 251), "m3"), ((40, 251), "m4"),
                           ((50, 251), "m5")]:
        li2 = ly.layer(l, dd)
        for sh in ctl.shapes(li2).each():
            if sh.is_text() and sh.text_string in SUPPLIES:
                t2 = sh.text_trans.disp
                nn = graph.net_of(cname, t2.x, t2.y)
                if nn is not None:
                    supply_nets.add(nn)
    # rails at rows are re-made by the PDN; glue only m2 and above
    supply_glue = []
    for cname in ("m2", "m3", "m4", "m5"):
        idx = graph.idx[cname]
        for i, poly in enumerate(idx.polys):
            nn2 = graph.uf.find((cname, i))
            if nn2 in supply_nets:
                b = poly.bbox()
                # polarity: probe the BAND graph at the strap itself -
                # supply nets are not io nets, so band_net_of skips them
                ip2 = interior_point(poly)
                bn2 = (band_graph.net_of(cname, ip2.x + ctl_disp.x,
                                         ip2.y + ctl_disp.y)
                       if ip2 else None)
                pol2 = ("VDD" if bn2 in vdd_nets
                        else "VSS" if bn2 in vss_nets else None)
                supply_glue.append([cname, b.left, b.bottom,
                                    b.right, b.top, pol2])
                obstruction_shapes.append([cname, b.left, b.bottom,
                                           b.right, b.top])
    supply_boxes = [kdb.Box(x0, y0, x1, y1)
                    for _, x0, y0, x1, y1, _pol in supply_glue]
    supply_via_cells = []
    for inst in ctl.each_inst():
        if inst.cell.name.startswith(("VIA", "control_sram_2kB_6t122_v3_VIA")):
            b = inst.bbox()
            # only via arrays that land on the supply web - signal-net via
            # cells belong to the OLD routing and must not be pasted
            if not any(b.touches(sb) or b.overlaps(sb)
                       for sb in supply_boxes):
                continue
            t3 = inst.cell_inst.trans
            supply_via_cells.append([inst.cell.name, t3.disp.x, t3.disp.y,
                                     str(t3)])

    data = {
        "instances": instances,
        "rail_polarity": rail_pol,
        "band_exits": band_exits,
        "io_forest": io_forest,
        "underbar_obs": underbar,
        "guard_shapes": guard_shapes,
        "supply_glue": supply_glue,
        "supply_via_cells": supply_via_cells,
        "ports": {p: f"{n[0]}:{n[1]}" for p, n in port_net.items()},
        "port_conflicts": {p: [f"{n[0]}:{n[1]}" for n in v]
                           for p, v in port_conflicts.items()},
        "io_geometry": io_geo,
        "obstructions": obstruction_shapes,
        "glue": {net_name.get(n, f"n{n[0]}_{n[1]}"): g
                 for n, g in glue.items()},
        "unresolved_pins": unresolved,
        "n_nets": len(net_pins),
    }
    (REPO / "out" / "control_netlist.json").write_text(json.dumps(data, indent=1))

    n_gates = len(instances)
    print(f"instances (logic): {n_gates}")
    print(f"nets: {len(net_pins)}  ports: {len(port_net)}")
    print(f"unresolved pins: {len(unresolved)}"
          + (f"  e.g. {unresolved[:4]}" if unresolved else ""))
    print(f"port label conflicts: {len(port_conflicts)}"
          + (f"  {list(port_conflicts)[:6]}" if port_conflicts else ""))
    # sanity: single-pin nets (dangling)
    dangling = [net_name[n] for n, ps in net_pins.items()
                if len(ps) == 1 and n not in set(port_net.values())]
    print(f"single-pin internal nets: {len(dangling)}"
          + (f"  e.g. {dangling[:6]}" if dangling else ""))
    print("wrote out/control_netlist.v and .json")


def _sanitize(name):
    return (name.replace("<", "_").replace(">", "").replace("!", "")
            .replace("[", "_").replace("]", ""))


_KEY = {}


def _unkey(s):
    if s not in _KEY:
        l, i = s.split(":")
        _KEY[s] = (l, int(i))
    return _KEY[s]


if __name__ == "__main__":
    main()
