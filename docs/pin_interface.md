# Pin interface contract (LEF, Liberty, Verilog, and the layout agree on this)

This is the single source of truth for how a generated bank presents its
pins to the outside world. `sramforge/lef.py`, `sramforge/liberty.py`,
`sramforge/verilog.py` and any external sign-off script must all derive
their pin lists from the same rules below, never from hand-kept tables.

## 1. Where pins come from

The layout is the authority. A pin is a text on a pin-text layer
(GDS (Graphic Design System II) datatype 251: layer 19 = M1, 20 = M2,
30 = M3, 40 = M4, 50 = M5) that belongs to the bank cell itself or to
the decode band cell placed in it (`ctrl_decode_32` at bits = 32,
`ctrl_decode_v2_<bits>` otherwise). Texts inside column groups, arrays,
guard rows and fillers are internal (`wlt`, `wlb`, `wl`, `BL`, `BLN`,
`WL`, `Y`, `A`, `B`, `PA` ...) and are never exported.

Rationale for including the band cell: the ninth address bit of band v2
(`ADDRESS<8>`, every width except the reference 32) is labeled in the
band hierarchy (a grandchild cell), not at the top.

"Texts of the band cell" is not literal: the band hierarchy holds about
2000 internal standard-cell texts (`A`, `Y`, `B`, `CLK`, `VDD`, `VSS`,
`wl<n>` ...). From the band the exporter accepts only the control and
address families named in section 3. Supplies come from the bank
cell's own texts only (the band's own supply texts would add internal
rails as pins).

The physical pin shape is the metal polygon on the same layer number
(datatype 0) that contains the text point. One text = one pin island;
an island that is not a rectangle (band v2's M5 address and `sdel`
islands are 8-vertex rail-plus-riser-foot shapes) is exported as one
PORT with several RECTs. If a name appears on more than one
disconnected polygon the exporter must fail loudly, not pick one.

## 2. Names and bus notation

Layout texts use angle brackets: `ADDRESS<7>`, `wd<12>`, `dataout<12>`,
`sdel<0>`. The exported name is the same identifier with square-bracket
bus notation: `ADDRESS[7]`, `wd[12]`, `dataout[12]`, `sdel[0]`. The
conversion is purely syntactic (`<n>` becomes `[n]`) and is generated
at export time; nothing is renamed by hand. LEF (Library Exchange
Format) declares `BUSBITCHARS "[]"` and `DIVIDERCHAR "/"`. Liberty
declares the same via `bus_naming_style : "%s[%d]"`.

Supply texts `vdd!` and `vss!` export as `VDD` and `VSS` (the `!` is a
layout-only convention). Every other name keeps its case exactly.

## 3. The pin list of a bank `sramforge_<bits>bx<strips>x<2*depth>`

| Exported name | Direction | Use | Layer in layout | Count |
|---|---|---|---|---|
| `ADDRESS[a-1:0]` | input | address; a = 8 at the reference (bits = 32, four strips, depth up to 48: the vendored band), 9 at every other configuration (band v2 carries `ADDRESS<8>` at 16 as well as at 64 and at deep four-strip banks, which place band v2 for its sense-enable delay line; the 9-bit map is what rungs 4.4 and 4.5 drive there) | M5 (bit 8: M3, in the band) | a |
| `clk` | input | clock | M5 | 1 |
| `banksel` | input | access enable (active high); nothing happens when low | M5 | 1 |
| `read` | input | read enable | M5 | 1 |
| `byte_wrenab` | input | write enable | M5 | 1 |
| `sdel[4:0]` | input | sense-timing delay-line code; tie to the constant the manifest's `sense_timing` names (measured rule, `sramforge/sense.py`, `sramforge/sae_codes.json`); all 1 = the minimum delay | M5 | 5 |
| `wd[w-1:0]` | input | write data | M3 | w |
| `dataout[w-1:0]` | output | read data, latched; holds between reads | M3 | w |
| `VDD` | inout, power | supply | M3 | 2 shapes, 1 pin |
| `VSS` | inout, ground | supply | M3 | 1 |

with `w` = data width = `2 * depth` (one bit per column-group row, both
halves): 64 at depth 32, 128 at depth 64. Words = `2 * bits * strips`
(256 at bits 32, 512 at bits 64 or at eight strips). The address field
map is measured: `ADDRESS[1:0]` = column-mux select, `ADDRESS[6:2]` =
wordline column (`[7:2]` at bits > 32), the top bit = half select. At
eight strips (bits 32 only) the bank places the eight-strip control
variant (`generator.control_variant`, band v2 "s8"): `ADDRESS[7]` is
the third column-mux bit (mux leg = `ADDRESS[7], ADDRESS[1:0]`), the
column field stays `ADDRESS[6:2]`, `ADDRESS[8]` = half select, and all
512 words are live (`sramforge/pins.py column_bits`, `mux_bits`).

The half select picks the ARRAY of each column group: half 1 opens the
left array's column mux (select family `yselt<k>` / `yseltn<k>`, k =
the mux leg) and half 0 the right array's (`yselb<k>` / `yselbn<k>`);
the selects are levels of the latched address, one-hot within the
addressed family, all off in the other (measured with the switch-level
bench on the reference; rung 4.5 grades this per half and leg).  Every
band-v2 control exports both families (`scripts/patch_control_yselb.py`;
the extraction alone had no `yselb` ports, so the right array of every
band-v2 bank was unreachable until 2026-09-19).

Supplies: the two `vdd!` texts are one electrical net and export as one
pin `VDD` with two rectangles (LEF allows several RECTs per PORT; use
one PORT with both). Liberty lists `VDD`/`VSS` as `pg_pin`s with
`voltage_name` VDD/VSS.

## 4. Units and geometry conventions

* Layout database unit is 0.25 nm (4000 dbu per um). LEF (Library
  Exchange Format) `DATABASE MICRONS 4000`; every coordinate is exported
  exactly (no rounding). That is the default; the 1000-unit consumer
  variant below is the one exception and it is opt-in.
* LEF `MACRO` name = the bank cell name. `CLASS BLOCK`, `ORIGIN 0 0`,
  `SIZE` = the bank bounding box. The bank bbox never starts at 0,0
  (its lower-left is (0, -88) dbu at 32, (0, -21688) at 16, (-13824,
  -159928) at 64), so the exporter always shifts every shape so the
  macro sits at 0,0, states the shift in a header comment, and records
  it in `FOREIGN <macro> dx dy`.
* Census: exactly `2 * depth` `wd` pins AND `2 * depth` `dataout` pins,
  indices 0..w-1 with no gaps, and `a` address pins 0..a-1.
* Units decision (2026-09-19, implemented): exact export at 4000 is the
  contract and the default (`emit_lef(..., units=4000)`, CLI (Command-
  Line Interface) `--lef-units 4000`). OpenROAD's ASAP7 (Arizona State
  University 7 nm predictive Process Design Kit) technology LEF (Library
  Exchange Format) is at `DATABASE MICRONS 1000` and OpenROAD discards a
  macro LEF (Library Exchange Format) whose unit is larger, and the
  layout has edges on 0.25 and 0.5 nm positions, so a 1000-unit file
  cannot be exact. The 1000-unit export (`units=1000`, `--lef-units
  1000`) is therefore a *consumer variant*, never the contract: after
  the origin shift every pin and obstruction rectangle is rounded
  OUTWARD onto the 1 nm grid (left and bottom down, right and top up,
  at most 0.75 nm per edge), so a pin never shrinks below its metal and
  an obstruction never under-covers metal; obstructions are merged
  after rounding and the rounded pins are subtracted from them so
  obstruction and pin stay disjoint; `SIZE` rounds up, `FOREIGN` to
  nearest, and the file header states the policy. Rung 6 proves the
  variant by containment (every rounded pin contains its island, the
  cover holds all drawn metal) with the over-coverage reported and
  bounded by a 1 nm halo times the drawn-metal perimeter; see
  `docs/lef_export.md`. An exact flow is possible the other way round:
  OpenROAD loads the 4000-unit file, and the platform's own 1000-unit
  standard cells, once the technology LEF (Library Exchange Format)
  says `DATABASE MICRONS 4000` (one line of a scratch copy); that moves
  the platform, not the exporter, and is not done here.
* Obstructions: every drawn metal polygon on M1..M5 that is not a pin
  rectangle, merged per layer, as `OBS`. Vias are not exported.
* Layer names follow the ASAP7 (Arizona State University 7 nm predictive
  Process Design Kit) LEF: `M1`..`M5`.
* Liberty time unit ns, capacitance pF, voltage V, current mA, power
  unit nW; nominal 0.7 V, 25 C, corner named `TT_0p7V_25C` (FF/SS later).

## 5. Verilog

`sramforge/verilog.py` currently emits `address`, `write` and omits
`sdel`. It must be brought onto this contract (same names, same widths,
`sdel` present) in the same change that lands the LEF exporter, so that
the three views are interchangeable. Until then the Verilog view is
the odd one out and the exporters must not copy from it.

## 6. What the exporters must refuse

* A pin text with no metal under it (label without a shape).
* A name on two disconnected polygons.
* A width or address count that does not match the spec's `bits` and
  `depth` (the exporter checks the text census against `2 * depth`
  data pins and `a` address pins and fails on any difference).

The point of the refusals: the layout may have a defect, and the export
must expose it rather than paper over it.
