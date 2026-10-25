#!/usr/bin/env python3
"""Turn the five vendored overlay GDS (Graphic Design System II) files into
one explicit, role-tagged table: sramforge/overlays.json.

Why: the overlays are the last reference geometry the generator pastes as
opaque binary blobs.  Their content is small (about 50 polygons and a few
hundred pin texts, inventoried 2026-09-19), and every parametric rule the
generator applies to them (widen, tile, heighten, repeat, text filters)
is a geometric classification.  Holding the shapes as data with a role
per shape makes those rules reviewable in plain text and removes the
binary inputs.  The generator reads the table through
sramforge.library.CellLibrary, which rebuilds each overlay as a KLayout
cell in the same shape order, so the generated banks are geometrically
identical (proved by per-layer XOR of every configuration when the table
was introduced).

Roles (documentation, derived here from geometry; the paste rules stay
geometric so no behavior depends on the tag):
  text            a pin label (datatype 251)
  marker          the SRAM pushed-rules marker or a boundary (layers 99, 100)
  via             V0..V4
  full-width      a horizontal polygon wider than 20 um (row supply straps)
  through-vertical a polygon at least 3000 dbu tall (crosses a whole cell)
  stub            anything else (short jogs, seam connectors, pads)

Usage:  python3 scripts/harvest_overlays.py [cells_dir]
"""
import json
import pathlib
import sys

import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
OVERLAYS = ["array_x32x4_sram_6t122", "colgrp_x64x4b_sram_6t122",
            "iocolgrp_sram_6t122_v2", "srambank_32b",
            "sramcol_x32_sram_6t122"]
VIA_LAYERS = {18, 21, 25, 35, 45}


def role_of(layer, datatype, box):
    if datatype == 251:
        return "text"
    if layer in (99, 100):
        return "marker"
    if layer in VIA_LAYERS:
        return "via"
    if box.width() > 20000 and box.height() < 1000:
        return "full-width"
    if box.height() >= 3000:
        return "through-vertical"
    return "stub"


def harvest(cells_dir: pathlib.Path) -> dict:
    table = {}
    for name in OVERLAYS:
        path = cells_dir / f"overlay_{name}.gds"
        ly = kdb.Layout()
        ly.read(str(path))
        top = ly.cell(ly.cell_by_name(f"overlay_{name}"))
        shapes = []
        for li in ly.layer_indexes():
            info = ly.get_info(li)
            for sh in top.shapes(li).each():
                entry = {"layer": info.layer, "datatype": info.datatype}
                if sh.is_text():
                    d = sh.text_trans.disp
                    entry.update(text=sh.text_string, x=d.x, y=d.y,
                                 role="text")
                else:
                    poly = sh.polygon
                    b = poly.bbox()
                    if poly.is_box():
                        entry["box"] = [b.left, b.bottom, b.right, b.top]
                    else:
                        entry["points"] = [[p.x, p.y]
                                           for p in poly.each_point_hull()]
                    entry["role"] = role_of(info.layer, info.datatype, b)
                shapes.append(entry)
        bb = top.bbox()
        table[name] = {"source": path.name, "dbu": ly.dbu,
                       "bbox": [bb.left, bb.bottom, bb.right, bb.top],
                       "shapes": shapes}
        roles = {}
        for s in shapes:
            roles[s["role"]] = roles.get(s["role"], 0) + 1
        print(f"  {name:32s} {len(shapes):4d} shapes  {roles}")
    return table


def main() -> int:
    cells = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "cells"
    table = harvest(cells)
    out = REPO / "sramforge" / "overlays.json"
    out.write_text(json.dumps(table, indent=1))
    print(f"wrote {out} ({out.stat().st_size // 1024} kB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
