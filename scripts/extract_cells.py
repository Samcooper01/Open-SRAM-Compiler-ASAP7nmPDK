#!/usr/bin/env python3
"""Extract the leaf cells sramforge builds from, out of the BSD-3-licensed
asap7_sram_0p0 reference banks, into individual GDS files under cells/.

Run once (or whenever upstream changes):
    python3 scripts/extract_cells.py /path/to/asap7_sram_0p0
"""
import sys
import pathlib
import klayout.db as kdb

# Every cell sramforge instantiates.  All come from the 32-bit reference bank.
# Cells with children (decoder, fillers, io cells) are extracted with their
# full subtree so they stay self-contained black boxes.
CELLS = [
    # bitcell array leaves
    "sram_cell_6t_122",
    "dummy_sram_6t122",
    "tapcell_sram_6t122",
    "dummy_topbot_v1",
    "dummy_topbot_v2",
    # io column group leaves
    "write_draslatch_02",
    "srlatch_sram_6t122",
    "wrasst_prech_ymux_x4_sram_6t122_v2",
    "senseamp_sram_6t122",
    # column-group / bank furniture (subtrees)
    "FILLER_cgedge",
    "ctrl_decode_32",
    "FILLER_32x2_top",
    "FILLER_32x2_bottom",
]

def main() -> None:
    src_dir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                           else "../asap7_sram_0p0")
    out_dir = pathlib.Path(__file__).resolve().parent.parent / "cells"
    out_dir.mkdir(exist_ok=True)

    src = kdb.Layout()
    src.read(str(src_dir / "gds" / "srambank_32b.gds"))

    for name in CELLS:
        ci = src.cell_by_name(name)
        if ci is None:
            raise SystemExit(f"cell not found in reference bank: {name}")
        dst = kdb.Layout()
        dst.dbu = src.dbu
        top = dst.create_cell(name)
        top.copy_tree(src.cell(ci))
        path = out_dir / f"{name}.gds"
        dst.write(str(path))
        n_shapes = sum(top.shapes(li).size() for li in dst.layer_indexes())
        print(f"  {name:38s} -> {path.name:42s} "
              f"({dst.cells()} cells, {n_shapes} top-level shapes)")

    print(f"\nextracted {len(CELLS)} cells to {out_dir}")

if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
# Overlays: the reference draws routing straps, pin shapes and marker layers
# as DIRECT shapes on each hierarchy cell (not in the leaves).  Extract each
# level's direct shapes into an overlay cell the builders can paste back.
OVERLAY_LEVELS = [
    "sramcol_x32_sram_6t122",
    "array_x32x4_sram_6t122",
    "iocolgrp_sram_6t122_v2",
    "colgrp_x64x4b_sram_6t122",
    "srambank_32b",
]

def extract_overlays(src_dir: pathlib.Path) -> None:
    out_dir = pathlib.Path(__file__).resolve().parent.parent / "cells"
    src = kdb.Layout()
    src.read(str(src_dir / "gds" / "srambank_32b.gds"))
    for name in OVERLAY_LEVELS:
        ref = src.cell(src.cell_by_name(name))
        dst = kdb.Layout(); dst.dbu = src.dbu
        top = dst.create_cell(f"overlay_{name}")
        n = 0
        for li in src.layer_indexes():
            info = src.get_info(li)
            dli = dst.layer(info.layer, info.datatype)
            for sh in ref.shapes(li).each():
                top.shapes(dli).insert(sh)
                n += 1
        path = out_dir / f"overlay_{name}.gds"
        dst.write(str(path))
        print(f"  overlay_{name:36s} {n:4d} direct shapes -> {path.name}")

if __name__ == "__main__":
    extract_overlays(pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                                  else "../asap7_sram_0p0"))
