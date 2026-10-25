# LEF (Library Exchange Format) export

`sramforge/lef.py` writes one LEF (Library Exchange Format) 5.8 macro per generated bank, derived
from the signed-off GDS (Graphic Design System II) file and nothing else.
It is the first exporter on the pin contract (`docs/pin_interface.md`);
`sramforge/pins.py` holds that contract in code so the LEF (Library
Exchange Format) view, the Verilog view and, later, the Liberty view
share one pin list.

```
python3 -m sramforge --bits 32 --strips 4 --depth 32 --lef --out out/
# -> out/sramforge_32bx4x64.gds, .v, .json and .lef (exact, 4000 units)
python3 -m sramforge --lef --lef-units 1000 --out out/
# -> the same .lef rounded outward onto the 1 nm grid, for OpenROAD
```

or from Python: `emit_lef(gds_path, top_name, spec, out_path,
units=4000)` returns a small manifest (units, pin count, obstruction
counts per layer, the origin shift, the macro size, and at 1000 units
the number of rectangle coordinates the rounding moved) and raises
`LefExportError` when the layout breaks the contract. `units` is 4000
(exact, the contract, default) or 1000 (the outward-rounded consumer
variant; see "Units and the rounding policy" below).

LEF (Library Exchange Format) syntax follows the LEF (Library Exchange Format)/DEF (Design Exchange Format) 5.8 Language
Reference published by Si2 (https://si2.org/oa-tools-utils-libs/); the
independent reader used to check the files is KLayout's LEF (Library Exchange Format)/DEF (Design Exchange Format) import
(https://www.klayout.de/doc-qt5/manual/lefdef_import.html); the flow the
files are meant for is OpenROAD (https://theopenroadproject.org/).

## How it works

1. **Read the bank.** The GDS (Graphic Design System II) file is opened
   with the pip `klayout` module; the top cell is the bank cell named by
   the spec (`sramforge_<bits>bx<strips>x<2*depth>`). The database unit
   must be 0.25 nm (4000 units per micron), otherwise the exporter
   refuses: the `DATABASE MICRONS 4000` it writes would lie.

2. **Collect pin texts** (contract section 1). Every text on a pin-text
   layer (datatype 251 of GDS (Graphic Design System II) layers 19, 20, 30, 40, 50 = M1..M5) that
   the bank cell owns directly is a pin candidate. The one decode band
   instance (`ctrl_decode_32` at 32 bits, `ctrl_decode_v2_<bits>`
   otherwise) is scanned recursively too, but only for the contract's
   control and address families (`ADDRESS<n>`, `clk`, `banksel`,
   `read`, `byte_wrenab`, `sdel<n>`): the band hierarchy carries
   thousands of standard-cell pin texts (`A`, `Y`, `B`, `CLK`, `VDD`,
   `VSS`, `wl<n>`, ...) that are internal to the macro. The reason the
   band is read at all is `ADDRESS<8>`, which band v2 places in a band
   subcell (`v2_entry_pads_<bits>`), not at bank level.

3. **Merge the metal.** For each of M1..M5 the drawn metal (datatype 0)
   of the whole bank is flattened and merged; the merged polygons are
   the islands (`sramforge.nets.IslandIndex`, the same engine rung 2
   uses). The island containing a text point is that pin's shape.

4. **Resolve pins and refuse defects** (contract section 6). Each text
   is located in its layer's islands. The exporter raises, and writes
   nothing, on: a text with no metal beneath it; a signal name on two
   different islands (or two layers); two different names on one
   island; a name outside the contract's vocabulary; a census that does
   not match the spec (see the correction below). Supplies may own
   several islands: the two `vdd!` texts become one pin `VDD` with two
   rectangles in one PORT, as the contract prescribes. Duplicate labels
   on one island (at 32 bits the band carries its own copy of the
   bank's `ADDRESS`/`clk`/`sdel` texts) are the same pin.

5. **Shift and write.** The bank bounding box lower-left is moved to
   0 0; every coordinate is the GDS (Graphic Design System II) coordinate plus that shift, stated
   in the header comment and carried by `FOREIGN <macro> dx dy` (the
   GDS (Graphic Design System II) cell origin expressed in macro coordinates). Coordinates are
   written in microns with five decimals, which is exact at 0.25 nm.
   At `units=1000` a rounding step sits between the shift and the
   writing (next section) and coordinates are written with three
   decimals, exact on the 1 nm grid.

## What is exported

```
VERSION 5.8 ;  BUSBITCHARS "[]" ;  DIVIDERCHAR "/" ;
UNITS  DATABASE MICRONS 4000 ;  END UNITS
MACRO sramforge_32bx4x64
  CLASS BLOCK ;  FOREIGN sramforge_32bx4x64 0 0.022 ;  ORIGIN 0 0 ;
  SIZE 9.612 BY 77.804 ;  SYMMETRY X Y ;
  PIN ...  (one per exported name, contract-table order)
  OBS  ... (merged non-pin metal, M1..M5)
END sramforge_32bx4x64
END LIBRARY
```

| Item | Rule |
|---|---|
| Pin names | `NAME<n>` in the layout becomes `NAME[n]`; `vdd!`/`vss!` become `VDD`/`VSS`; every other name keeps its case. |
| DIRECTION / USE | `ADDRESS`, `banksel`, `read`, `byte_wrenab`, `sdel`, `wd`: INPUT SIGNAL; `clk`: INPUT CLOCK; `dataout`: OUTPUT SIGNAL; `VDD`: INOUT POWER; `VSS`: INOUT GROUND. |
| PORT shapes | The merged island under the text, as RECTs. A rectangular island is one RECT. The band-v2 M5 risers (rail plus offset foot, 8 vertices) are cut into rectangles (`Polygon.decompose_trapezoids`) inside the same PORT, which every reader treats as one connected pin. A polygon with holes or a non-rectilinear one (none exist in these banks) would fall back to `POLYGON`. |
| OBS | Every merged island of M1..M5 not claimed by a pin: RECT for a box, `POLYGON` (LEF (Library Exchange Format) 5.6 and later) otherwise. Vias are not exported. |
| SIZE | The bank cell's bounding box over all layers, as `generate()` reports in the manifest. |
| SYMMETRY | `X Y`: a whole-macro mirror keeps the fin direction; R90 does not and is not claimed. |

Pin order in the file: `ADDRESS[0..a-1]`, `clk`, `banksel`, `read`,
`byte_wrenab`, `sdel[0..4]`, `wd[0..w-1]`, `dataout[0..w-1]`, `VDD`,
`VSS`.

## Units and the rounding policy

| | `units=4000` (default) | `units=1000` |
|---|---|---|
| Role | The contract: exact | Consumer variant for OpenROAD, opt-in |
| `DATABASE MICRONS` | 4000 (1 unit = the layout's 0.25 nm dbu) | 1000 (1 unit = 1 nm = 4 dbu) |
| Coordinates | The shifted layout coordinate, no rounding, 5 decimals | Rounded onto the 1 nm grid after the origin shift, 3 decimals |
| Pin shapes | The island, as RECTs | Each island cut into rectangles, each rectangle rounded OUTWARD (left/bottom down, right/top up): a pin never shrinks below its metal |
| Obstructions | Every unclaimed island exactly as merged | Each island cut into rectangles, each rounded OUTWARD (never under-covers metal), the rectangles merged per layer AFTER rounding (touching ones become one polygon, no slivers), then the rounded pin shapes subtracted so OBS (obstruction) and PIN stay disjoint |
| `SIZE` | The bank bounding box | Rounded up to the grid |
| `FOREIGN` | The exact shift | Rounded to nearest; the header says so if it moved (it does not at 32, 16 or 64: all three shifts are on the grid) |
| Header | Origin shift statement | Origin shift statement plus a `# ROUNDING:` paragraph stating the policy and the count of rectangle coordinates moved |

Why outward and not nearest: an abstract that under-covers metal is a
routing short waiting to happen (the router believes there is room where
there is metal), and a pin smaller than its metal hides legal access;
over-cover only costs the router up to 0.75 nm per edge, which is below
any ASAP7 (Arizona State University 7 nm predictive Process Design Kit)
spacing rule. Why merge after rounding: two obstruction rectangles that
were 0.5 nm apart can touch or overlap once each has grown, and a merged
polygon is what a reader wants instead of two overlapping slivers. Why
subtract the pins: outward rounding could otherwise let an obstruction
edge cross into a pin's halo; keeping the two disjoint preserves the
exact export's property that a pin's rectangles are reachable metal and
nothing else. At 32, 16 and 64 bits no island pair is closer than 1 nm,
so the merge changes no island count and the subtraction removes
nothing; both are there for layouts that are not the reference.

Measured at the three verified configurations (rung 6, `units=1000`):

| Bank | Rectangle coordinates moved | Over-coverage M1 / M2 / M3 / M4 / M5 (um2) | As % of the layer's drawn metal | As % of the 1 nm halo bound |
|---|---|---|---|---|
| `sramforge_32bx4x64` | 181134 | 1.384 / 7.020 / 0.008 / 0.000 / 0.000 | 0.93 / 3.16 / 0.00 / 0.00 / 0.00 | 7.7 / 27.4 / 0.0 / 0.0 / 0.0 |
| `sramforge_16bx4x64` | 103447 | 0.762 / 3.942 / 0.008 / 0.000 / 0.001 | 0.72 / 2.88 / 0.01 / 0.00 / 0.01 | 6.0 / 24.7 / 0.1 / 0.0 / 0.1 |
| `sramforge_64bx4x128` | 689656 | 5.288 / 26.574 / 0.016 / 0.000 / 0.001 | 1.14 / 3.38 / 0.00 / 0.00 / 0.00 | 9.3 / 29.4 / 0.0 / 0.0 / 0.1 |

M2 shows by far the largest over-coverage (most of its edges are off
the 1 nm grid), M1 next; M3 and above are almost entirely on the grid.
Nothing comes near the bound, which is what a 0.75 nm maximum move
against a 1 nm halo predicts.

## Measured at the three verified configurations

| Bank | Pins (texts) | Address bits | SIZE (um) | Shift (dbu) | OBS islands M1 / M2 / M3 / M4 / M5 | File | Time |
|---|---|---|---|---|---|---|---|
| `sramforge_32bx4x64` (32,4,32) | 147 (174) | 8 | 9.612 x 77.804 | (0, 88) | 53457 / 26033 / 1362 / 372 / 38 | 4.9 MB | 2.9 s |
| `sramforge_16bx4x64` (16,4,32) | 148 (166) | 9 | 9.612 x 85.364 | (0, 30328) | 34551 / 20716 / 1631 / 436 / 66 | 3.5 MB | 1.8 s |
| `sramforge_64bx4x128` (64,4,64) | 276 (294) | 9 | 16.524 x 154.484 | (13824, 168568) | 188571 / 84836 / 2701 / 694 / 68 | 17.0 MB | 8.9 s |
| `sramforge_32bx8x64` (32,8,32) | 148 (166) | 9 | 9.612 x 156.104 | (0, 175048) | 103463 / 49804 / 2735 / 1487 / 80 | 9.6 MB | 2.1 s (whole CLI run) |

Heights, shifts and obstruction counts of the three band-v2 rows were
refreshed on 2026-09-20, after the control block gained the binary-
weighted sense delay line and its die grew eight rows: every band-v2
bank is 2.16 um taller than before, and the reference row is unchanged
because it places the vendored band.  The export times are from the
original measurement run.

Pin geometry facts (from the layout, all three banks): the M5 control
and address pins are full-height rails of 0.024 um width with no metal
above them; the `wd`/`dataout` pins are short M3 stubs (0.018 um wide,
about 0.24 to 0.41 um long) 0.94 um or more inside the macro edge, and
every one of them has M4 or M5 metal (hence OBS) directly above it; the
`VDD` pin is two M3 rails of 0.018 um width in the lower half only, the
`VSS` pin one full-height M3 rail; the band-v2 `ADDRESS[8]` pad is an
M3 rectangle of 0.018 x 0.096 um, 0.199 um from the left edge at 16
bits and 3.655 um at 64 bits.

## Verification

`tests/test_lef.py` (rung 6, in `tests/run_all.py` after rung 4.5)
generates the three banks above, emits their LEF (Library Exchange
Format) and proves each file against the GDS (Graphic Design System II):

* KLayout's own LEF (Library Exchange Format)/DEF (Design Exchange Format) reader parses the file (an independent parser,
  not the writer's tokenizer): the macro exists, its OUTLINE equals the
  bank bounding box, every PIN shape carries its pin name (reader
  option `pin_property_name`);
* every PIN rectangle shifted back to GDS (Graphic Design System II) coordinates is covered by the
  merged drawn metal of its layer, and every layout pin text lies
  inside its pin's rectangles;
* OBS never overlaps a pin shape, and OBS + pin shapes XOR (exclusive
  or) the merged drawn metal to empty on all five layers, so the file
  is bit-exact with the layout, no cover, no bloat;
* the pin census is exactly the contract's list for the spec;
* three mutations of a GDS (Graphic Design System II) copy are each refused with nothing written:
  a deleted `wd<3>` text (census), the text moved 1 um outside the bank
  (no metal beneath), and a second `wd<3>` text on `wd<5>`'s island
  (two disconnected polygons).

For the 1000-unit variant (check (v) of the test's docstring) the same
round trip, census, text and disjointness checks run, every shape
vertex is checked to lie on the 1 nm grid, and the exact XOR (exclusive
or) is replaced by three containment checks: (a) every pin's
rectangles, mapped back to the layout's 0.25 nm dbu, contain the pin's
original metal island (the island is recomputed in the test from the
merged metal under the text, independently of the exporter), (b) OBS
(obstruction) plus pins cover all drawn metal, (c) the over-coverage
area per layer is printed and must not exceed a 1 nm halo times the
perimeter of the layer's drawn metal. The bound: outward rounding
moves an edge by at most 0.75 nm, so the over-cover lies inside a
0.75 nm halo whose area is 0.75 nm times the perimeter plus corner
squares of at most 0.75 x 0.75 nm; a 1 nm halo times the perimeter
absorbs the corners whenever every metal edge is longer than 2.25 nm,
which every ASAP7 (Arizona State University 7 nm predictive Process
Design Kit) width and spacing rule guarantees. The variant runs at 32
and 16 by default; at 64 it adds about 75 s to the rung and runs only
with `--full` (measured once, PASS, table above).

OpenROAD (image `openroad/orfs:latest`, OpenROAD 26Q3-173-gf5df0085c2)
with the ORFS (OpenROAD-flow-scripts) ASAP7 (Arizona State University
7 nm predictive Process Design Kit) platform, four `read_lef`
experiments on the 32-bit bank (2026-09-19, verbatim tool output):

1. Technology LEF (Library Exchange Format) `asap7_tech_1x_201209.lef`
   (1000 units), then the exact 4000-unit bank file: `[WARNING
   ODB-0205] The LEF UNITS DATABASE MICRON convert factor (4000) is
   greater than the database units per micron (1000) of the current
   technology.` then `[INFO ODB-0227] LEF file: ...
   sramforge_32bx4x64_4000.lef, created 1 library cells` then `[ERROR
   ODB-0292] LEF data from ... sramforge_32bx4x64_4000.lef is discarded
   due to errors`. The bank is **discarded**.
2. The same technology LEF (Library Exchange Format), then the
   1000-unit outward-rounded bank file: `[INFO ODB-0227] LEF file:
   ... sramforge_32bx4x64_1000.lef, created 1 library cells`, no
   warning. The database then holds master `sramforge_32bx4x64`, type
   BLOCK, 9.612 x 77.804 um, 147 pins (81 input, 64 output, 2 inout),
   116885 obstruction boxes (OpenROAD cuts the POLYGON obstructions
   into boxes). The 16 and 64 files load the same way in the same
   session: `sramforge_16bx4x64` BLOCK 9.612 x 83.204 um, 148 pins (82
   in, 64 out, 2 inout), 83685 obstruction boxes; `sramforge_64bx4x128`
   BLOCK 16.524 x 152.324 um, 276 pins (146 in, 128 out, 2 inout),
   378063 obstruction boxes.
3. A scratch copy of the technology LEF (Library Exchange Format) with
   its one line changed to `DATABASE MICRONS 4000` (`MANUFACTURINGGRID
   0.001` untouched), then the exact 4000-unit bank file: `[INFO
   ODB-0227] LEF file: /work/asap7_tech_4000.lef, created 30 layers, 9
   vias` then `[INFO ODB-0227] LEF file: ...
   sramforge_32bx4x64_4000.lef, created 1 library cells`, no warning;
   the database is at 4000 units per micron and the master is BLOCK,
   9.612 x 77.804 um, 147 pins, 116885 obstruction boxes.
4. As 3, but with the platform's own standard-cell LEF (Library
   Exchange Format) `asap7sc7p5t_28_R_1x_220121a.lef` (1000 units) read
   between the two: `created 212 library cells`, no warning;
   `INVx1_ASAP7_75t_R` is 0.162 x 0.27 um in the 4000-unit database,
   and the exact bank loads after it.

So OpenROAD scales a smaller unit up but refuses a larger one. An exact
flow needs the platform's technology LEF (Library Exchange Format) at
4000 (one line; the standard cells need no change), which moves the
platform. The exporter's 1000-unit variant moves the macro instead, by
at most 0.75 nm per edge and only outward.

## Contract corrections and interpretations

These are places where `docs/pin_interface.md` and the signed-off
layout disagree, or where the contract's words needed an interpretation.
The exporter follows the layout; the contract should be amended.

1. **Address width.** The contract says `a` = 8 at bits <= 32 and 9
   above. The layout says 8 only at the reference band (bits = 32):
   every other width places the band-v2 control, which decodes nine
   address bits (half = `ADDRESS[8]`, column = `ADDRESS[7:2]`, mux =
   `ADDRESS[1:0]`), carries nine `ADDRESS` texts, and is swept on that
   9-bit map by rung 4.5 at 16 bits as well as 64. `pins.address_width`
   therefore returns 9 for every bits != 32. Column codes >= bits are
   dead (sparse-bits: no cells, no driver).
2. **Band texts.** "Texts of the decode band cell" cannot be taken
   literally (thousands of internal standard-cell labels, and
   `ADDRESS<8>` sits in a band subcell); the exporter accepts from the
   band hierarchy only the contract's control and address families.
3. **Origin.** The bank bounding box does not start at 0,0 at the
   reference either: its lower-left is (0, -88) dbu at 32 bits and
   (0, -21688) at 16 (band-v2 growth), not only at bits > 32. The shift
   is applied unconditionally and always stated.
4. **"One text = one pin rectangle."** At band v2 the M5 address and
   sdel islands are 8-vertex polygons (rail plus an offset riser foot),
   so those pins are several RECTs in one PORT. The island is still one
   polygon; only its LEF (Library Exchange Format) spelling is plural.
5. **Supplies.** The pin shapes are exactly the labeled rails (two
   `VDD`, one `VSS`), all in the lower half or full height; the other
   supply rails are OBS. Rung 2 measures VDD and VSS as one net each in
   the bank, so this is electrically sufficient, but a power planner
   sees only 1.25 um2 of VDD pin area (2.49 um2 at 64 bits).
6. **Census wording.** "2 * depth data pins" is read as 2 * depth `wd`
   pins AND 2 * depth `dataout` pins, with bus indices exactly 0..w-1.

## Known limitations and open decisions

* **Units versus OpenROAD: decided.** The contract's `DATABASE MICRONS
  4000` keeps every coordinate exact but is refused by OpenROAD
  alongside the ORFS (OpenROAD-flow-scripts) ASAP7 (Arizona State
  University 7 nm predictive Process Design Kit) technology LEF
  (Library Exchange Format) (1000 units per micron, `MANUFACTURINGGRID
  0.001`), and the reference layout has metal edges on the 0.25 and
  0.5 nm positions, off the technology's own manufacturing grid. Exact
  stays the default; `units=1000` is the opt-in outward-rounded
  consumer variant ("Units and the rounding policy" above), proven by
  containment in rung 6 and loaded by OpenROAD. The alternative, a
  4000-unit technology LEF (Library Exchange Format), works in OpenROAD
  too but changes the platform and is not done here.
* **Exact OBS, not a cover.** The obstruction is every merged island
  (188146 on M1 alone at 64 bits; 16.9 MB). A conventional abstract
  would instead cover each layer with a few bloated shapes. Exactness
  was the contract's choice; a cover option is a future flag.
* **Pin access.** All `wd`/`dataout` pins are interior M3 stubs with
  M4/M5 metal (OBS) directly above them and OBS all around on M3. A
  router can reach them only from the sides on M3 within the macro, if
  at all. This is a property of the layout, not of the exporter, and it
  is the most likely first finding when the macro is placed in a flow.
* **Layer semantics.** `SYMMETRY X Y` is asserted from the fin
  direction argument, not derived from the layout. No ANTENNA
  properties, no SITE, no DENSITY, no per-pin `SHAPE`/`ANTENNA`
  statements are written. Layer names `M1`..`M5` match the ASAP7 LEF (Library Exchange Format).
* **No Liberty yet.** `pins.py` already carries what `liberty.py` will
  need (names, directions, widths, bus naming style); timing is not
  part of this change.
