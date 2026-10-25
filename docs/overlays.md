# Overlays: the reference's direct shapes, as an explicit table

## What an overlay is

The reference bank (`srambank_32b`) draws shapes DIRECTLY on five of its
hierarchy levels, beside the placed cells: routing straps, seam
connectors, pin texts, well-tap clusters and the SRAM (Static
Random-Access Memory) pushed-rules marker. A generator that only places
cells reproduces none of that, and without it the golden XOR (rung 0)
fails on 13 layers and every bitline is open. `scripts/extract_cells.py`
harvested those direct shapes per level into "overlay" cells, and the
builders paste them back at the matching level.

Until 2026-09-19 the overlays were five opaque GDS (Graphic Design System
II) files under `cells/`. They are now one explicit table,
`sramforge/overlays.json`, produced by `scripts/harvest_overlays.py` from
those files (which in turn came from the golden bank). The generator
reads only the table (`sramforge/library.py`, `CellLibrary._overlay_source`).

## Why a table

Every parametric rule the generator applies to an overlay is a geometric
classification of its shapes:

| Rule (in `CellLibrary.paste_overlay` / `generator.paste_bank_overlay`) | Applies to |
|---|---|
| `widen` / `tile_lo` / `tile_hi` (bits > 32) | shapes wholly left/right of the array window slide outward; one column-pair window of metal is replicated per extra pair, vias skipped |
| `text_filter` (bits < 32) | per-column wordline labels of dead columns are dropped |
| `heighten` (strips > 4) | polygons spanning the reference group height (the io-column M3 (Metal 3) verticals, the SRAM marker) follow the taller group |
| `repeat_texts` (strips > 4) | per-strip bitline labels are copied for the extra strips |
| edge-cluster shift (depth != 32) | bank-level shapes at or beyond the outermost reference row move with the stack edge |
| row pins (depth != 32 or strips != 4) | `wd<i>` / `dataout<i>` texts are regenerated per row |

With the shapes as data, each entry carries a `role` tag so the reader
can see which rule a shape is subject to without opening a layout viewer.
The tags are documentation: the rules themselves stay geometric, so no
behavior depends on a tag. Roles: `text`, `marker` (layers 99 and 100),
`via` (V0..V4), `full-width` (horizontal, wider than 20 um: the row
supply straps), `through-vertical` (at least 3000 dbu tall: crosses a
whole cell), `stub` (everything else: seam connectors, pads, jogs).

## Contents (2026-09-19 inventory)

| Overlay | Level it is pasted on | Shapes | Of which |
|---|---|---|---|
| `array_x32x4_sram_6t122` | bitcell array | 43 | 1 marker, 42 texts |
| `colgrp_x64x4b_sram_6t122` | column group | 114 | 3 through-verticals, 2 full-width M4 (Metal 4) supply straps, 3 stubs, 13 vias, 93 texts |
| `iocolgrp_sram_6t122_v2` | io column | 65 | 9 stubs, 5 vias, 51 texts |
| `srambank_32b` | bank | 197 | 9 through-vertical M5 (Metal 5) pin columns, 8 markers, 32 stubs (seam connectors), 148 texts |
| `sramcol_x32_sram_6t122` | bitcell strip | 38 | 38 texts |

About 50 polygons and 370 texts in total. The builders' calls and their
options are in `sramforge/generator.py` (search for `paste_overlay`).

## Proof of equivalence

When the table replaced the files, six configurations were generated
both ways and compared: 32,4,32 (the reference), 16,4,32, 4,4,8,
32,8,32, 32,4,8 and 64,4,64. Every drawn layer XORs to empty and every
pin-text layer carries the same (string, x, y) multiset. Rung 0 keeps
proving the reference case on every run.

## Regenerating the table

The table is derived data. To rebuild it from the golden bank:

    python3 scripts/extract_cells.py ../asap7_sram_0p0   # overlay GDS files
    python3 scripts/harvest_overlays.py                   # -> sramforge/overlays.json

The intermediate overlay GDS files are not needed by the generator and are
not kept in the repository.
