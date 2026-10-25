#!/usr/bin/env python3
"""Extract the reference bank's implant blanket for ONE colgrp row.

The sparse-bits scheme removes outer bitcell columns, and with them the
implant (NSELECT/PSELECT) cover their guard-row active relied on.  The
correct cover for the dead zone is exactly what the reference draws
there - so clip the golden bank's implant layers to one column-group row
(the pattern tiles with depth) and vendor it as a paste-able asset.

Writes cells/implant_blanket_colgrp.gds with the clipped NSELECT (12/0)
and PSELECT (13/0) regions in COLGRP-frame coordinates.

Usage:  python3 scripts/extract_implant_blanket.py [path/to/asap7_sram_0p0]
"""
import pathlib
import sys

import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
REF = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                   else REPO.parent / "asap7_sram_0p0")

# bank frame: upper-stack row 0 occupies one COLGRP_ROW_PITCH starting at
# (COLGRP_X_IN_BANK, UPPER_STACK_ANCHOR_Y) = (884, 171774)
COLGRP_X, ROW_Y, PITCH = 884, 171774, 4320
WIN = kdb.Box(-116, ROW_Y, 39484, ROW_Y + PITCH)   # full row incl. fillers

src = kdb.Layout()
src.read(str(REF / "gds" / "srambank_32b.gds"))
top = src.cell("srambank_32b")

out = kdb.Layout()
out.dbu = src.dbu
blanket = out.create_cell("implant_blanket_colgrp")
shift = kdb.Trans(kdb.Vector(-COLGRP_X, -ROW_Y))
for l, d in ((12, 0), (13, 0)):
    reg = kdb.Region(top.begin_shapes_rec(src.layer(l, d))).merged()
    clipped = (reg & kdb.Region(WIN)).transformed(shift)
    for p in clipped.each():
        blanket.shapes(out.layer(l, d)).insert(p)
    print(f"layer {l}/{d}: {clipped.count()} polygons")

dst = REPO / "cells" / "implant_blanket_colgrp.gds"
out.write(str(dst))
print("wrote", dst)
