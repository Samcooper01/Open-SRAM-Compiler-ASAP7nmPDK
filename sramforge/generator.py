"""sramforge generator: builds a single-port SRAM (Static Random-Access
Memory) bank for ASAP7 from the vendored BSD-3 leaf cells.

The construction replays - parametrically - the tiling rules measured from
the asap7_sram_0p0 reference bank, one build function per hierarchy level:

    build_bitcell_strip       a horizontal strip of N bitcells (interleaved
                              mirrored pairs at half-pitch) closed by a
                              dummy cell and a well-tap cell
    build_bitcell_array       strips stacked as mirrored pairs, with dummy
                              guard rows protecting the lithographic
                              environment top and bottom
    build_readwrite_io_column the read/write column periphery: precharge +
                              column mux (with write assist), sense
                              amplifier, write-driver latch, output latch
    build_column_group        bitcell array | io column | mirrored array,
                              with edge fillers
    build_bank                two mirrored vertical stacks of column
                              groups, the central decode/control band, and
                              top/bottom fillers

At the reference parameters (bits_per_group=32, strips_per_group=4,
depth_groups=32) the output is geometrically identical to srambank_32b.gds
- verified by XOR in tests/test_golden.py.

depth_groups is parametric end-to-end: the bank-level overlay re-anchors
its edge clusters and regenerates the per-row pin texts
(paste_bank_overlay); grown (48) and shrunk (16) depths pass rungs 1-4.5.

bits_per_group <= 32 uses the SPARSE-BITS scheme (tests/test_sparse_bits
.py): the reference frame never moves - the io column, decode band (a
fixed-area placed-and-routed control macro that cannot narrow without
re-place-and-route) and overlays stay put; the outer bitcell columns are
simply omitted, the reference's own implant blanket re-covers the dead
zone, and dead columns' pin labels drop.  Wordline indices count from the
io column outward, so live columns are the LOW column codes (address live
iff ADDRESS[6:2] < bits).  Cost: bank area does not shrink with bits.
bits > 32 is the dual-decoder scheme (two 32-column decode banks, band
v2 with a ninth address bit); strips_per_group = 8 tiles the vendored
four-leg column mux to eight legs (colgrp_pitch/colgrp_height scale the
stacks) with legs 4..7 on their own select lines, fed by the eight-strip
control variant (band v2 "s8": a 3:8 column-select decode whose eight
new ports rise along the select-line columns; control_variant).
"""
from dataclasses import dataclass
import pathlib
import re

import klayout.db as kdb

from .tech import (DBU, BIT_STEP, BIT_X0, ROW_PAIR_PITCH,
                   DUMMY_V1_Y0, DUMMY_V2_Y0, COLGRP_ROW_PITCH)
from .library import CellLibrary
from .builder import place, place_array
from .band import build_ctrl_band, build_ctrl_band_v2, growth_for

# ---------------------------------------------------------------------------
# Reference-bank anchor offsets (database units), measured from
# srambank_32b.gds.  "REF" values are valid for 32-bit strips and are
# shifted linearly for other widths.
IO_COLUMN_X_REF = 14668        # io column x for 32-bit strips
MIRRORED_ARRAY_X_REF = 36700   # right (mirrored) array x for 32-bit strips
EDGE_FILLER_LEFT_XY = (-884, -24)
EDGE_FILLER_RIGHT_X_REF = 37564
STRIP_X_IN_ARRAY = -108        # strip x offset inside the array
ARRAY_X_IN_COLGRP = -20        # array x offset inside the column group
COLGRP_X_IN_BANK = 884
LOWER_STACK_ANCHOR_Y = 139266  # mirrored stack grows DOWN from here
# seam-connector strip (bank y): the staggered overlay jogs joining the
# band's control-distribution risers to the lower stack's verticals
CONNECTOR_ZONE = (136350, 140200)
UPPER_STACK_ANCHOR_Y = 171774  # upright stack grows UP from here
DECODER_XY = (-864, 136644)
COLGRP_DRAWN_HEIGHT = 4640     # drawn height of one column group
STRIP_HEIGHT = 1080            # one bitcell strip (half a mirrored pair)
# SELECT LINES for the second column-mux tile (strips = 8): eight M5
# columns per mux over its own array, in mux-local x (bank x =
# mux-local + 15552 for the left mux, band x = bank + 864; the mirrored
# mux puts its eight over the right array at band x 39984 - L for a
# left column L).  The two sets carry DIFFERENT signals, like the
# reference's yselt<k> (left array, half = 1) and yselb<k> (right array,
# half = 0) families: yselt_4..7 / yseltn_4..7 on the left, yselb_4..7
# / yselbn_4..7 on the right.  The columns are chosen for the control
# feed, which rises through the band at these very x: each left column
# sits 164 dbu right of one of the band's 432-pitch wordline M3 columns
# (the control macro's pad and landing at the same x clear the columns
# by 104 on M3, its mirror by 104 on the other side), inside the
# macro's landing windows (ctl x 35104..40484 west, 64644..68532 east),
# with no band or
# bank-overlay M5 within 92 dbu and no V4 under any column over the
# whole band height (scan of 2026-09-19; the gaps after wordline
# columns 4500 and 7092 are skipped for their V4s).  Band x, left: 3800
# 4232 5096 5528 5960 6392 6824 7688; right (40080 - L): 36280 35848
# 34984 34552 34120 33688 33256 32392.
SELECT_LINE_X = (-12616, -12184, -11320, -10888, -10456, -10024, -9592,
                 -8728)
SELECT_LINE_Y0, SELECT_LINE_Y1 = -34, 8760      # one row pitch + overlap


def colgrp_pitch(spec) -> int:
    """Row pitch of the column-group stacks: the reference 4320 at four
    strips, one STRIP_HEIGHT more per extra strip (strips=8 -> 8640).
    Every bank-level y that scales with the row count uses this, never
    the constant, so a strips change moves the stacks, the edge clusters,
    the fillers and the row pins together."""
    return COLGRP_ROW_PITCH + (spec.strips_per_group - 4) * STRIP_HEIGHT


def uses_band_v2(spec) -> bool:
    """Which control block a configuration gets.  The reference (32 bits,
    4 strips) keeps the vendored band byte-for-byte (rung 0).  Every other
    configuration uses band v2, the re-placed-and-routed one-box control
    with a ninth address bit: the sparse and dual-decoder widths always
    did, and strips = 8 needs the ninth bit as the column-mux tile select
    (the vendored band has no spare address input).  Feasibility at
    32,8,32 measured 2026-09-19: band v2 assembles, every wordline column
    is one net through the die, rung 2 clean.

    Deep four-strip banks use band v2 too: the reference band's hand-
    packed control has a fixed sense-enable delay line that reaches the
    far row of at most REFERENCE_LINE_ROWS rows (measured, sramforge/sae_codes.json), and only the re-placed control carries the
    binary-weighted line that a deeper bank needs (sramforge/sense.py)."""
    return (spec.bits_per_group != 32 or spec.strips_per_group != 4
            or spec.depth_groups > REFERENCE_LINE_ROWS)


# rows (at the four-strip pitch) whose far wordline the reference
# control's delay line can still strobe after, with the rung 7 margin:
# depth 48 needs its slowest setting, depth 64 is out of reach
REFERENCE_LINE_ROWS = 48


def control_variant(spec) -> str:
    """Which vendored one-box control a band-v2 configuration places:
    "" is the standard control (four column-select lines), "s8" the
    eight-strip variant (scripts/patch_control_s8.py: the same control
    with a 3:8 column-select decode on ADDRESS[1:0] and ADDRESS[7], and
    eight more select ports).  Meaningful only when uses_band_v2."""
    return "s8" if spec.strips_per_group == 8 else ""


def select_columns_in_band(spec) -> tuple:
    """Band x of the left edge of each stack select line: the left mux's
    eight (yselt family) then the mirrored mux's eight (yselb family),
    line j of each.  Generator frame: mux-local + io column + column
    group, minus the band's placement; the mirror maps a left column L
    to 39984 - L (io axis at io-local 7344, 96-wide lines)."""
    if spec.strips_per_group != 8:
        return ()
    left = tuple(lx + IO_COLUMN_X_REF + COLGRP_X_IN_BANK - DECODER_XY[0]
                 for lx in SELECT_LINE_X)
    right = tuple(2 * (IO_COLUMN_X_REF + COLGRP_X_IN_BANK - DECODER_XY[0])
                  + 7344 - 96 - L for L in left)
    return left + right


def colgrp_height(spec) -> int:
    """Drawn height of one column group, same growth rule as the pitch."""
    return COLGRP_DRAWN_HEIGHT + (spec.strips_per_group - 4) * STRIP_HEIGHT
# Filler standoffs, derived from the reference: the bottom filler sits
# 794 dbu below the lower stack edge; the top filler tucks 530 dbu below
# the upper stack edge (overlapping the dummy row).
BOTTOM_FILLER_STANDOFF = 794
TOP_FILLER_TUCK = 530


@dataclass
class BankSpec:
    """User-visible parameters of one generated bank."""
    bits_per_group: int = 32   # bitcells per strip (data-column slice width)
    strips_per_group: int = 4  # bitcell rows per column group
    depth_groups: int = 32     # column-group rows in each mirrored half

    def __post_init__(self):
        if self.bits_per_group % 4:
            raise ValueError("bits_per_group must be a multiple of 4 "
                             "(the decoder tiles 4-column driver slices)")
        if self.bits_per_group > 64:
            raise ValueError(
                "bits_per_group > 64 is not buildable: 64 = two full "
                "32-column decode banks (the dual-decoder scheme); wider "
                "needs a third bank and two more address bits.")
        if self.strips_per_group % 2:
            raise ValueError("strips_per_group must be even (mirrored pairs)")
        if self.strips_per_group not in (4, 8):
            raise ValueError(
                "strips_per_group must be 4 (the reference column mux) or "
                "8 (two mux tiles): the shared read/write periphery fills "
                "a 4-strip group, so fewer strips cannot shrink it, and "
                "wider than 8 needs a longer select decode than the "
                "control block has.")
        if self.strips_per_group != 4 and self.bits_per_group != 32:
            raise ValueError(
                "strips_per_group != 4 is only built at bits_per_group = 32 "
                "for now: the sparse-bits and extension machinery has not "
                "been combined with the taller column group.")

    @property
    def is_reference(self) -> bool:
        return (self.bits_per_group, self.strips_per_group,
                self.depth_groups) == (32, 4, 32)

    @property
    def name(self) -> str:
        return (f"sramforge_{self.bits_per_group}b"
                f"x{self.strips_per_group}x{2 * self.depth_groups}")


def build_bitcell_strip(layout: kdb.Layout, cell_library: CellLibrary,
                        bits_per_strip: int) -> kdb.Cell:
    """One horizontal strip of bitcells + dummy + well tap.

    The strip is electrically one bitline column: all its cells share one
    horizontal bitline pair (hence the reference's name "sramcol").
    """
    strip = layout.create_cell(f"bitcell_strip_x{bits_per_strip}")
    pair_count = bits_per_strip // 2
    # SPARSE-BITS SCHEME: non-reference widths keep the reference FRAME.
    # Cells occupy the io-adjacent (right) columns; the outer-left columns
    # are simply absent.  Nothing else moves - the io column, decode band
    # (a fixed-area placed-and-routed control macro that cannot narrow
    # without re-place-and-route), overlays and bank outline all stay at
    # reference positions, so bitline continuity and every wordline pickup
    # stay aligned by construction.  The cost: bank area does not shrink
    # with bits.  True narrowing = the control re-P&R roadmap item.
    skip_pairs = (32 - bits_per_strip) // 2
    strip_end_x = BIT_X0 + 16 * BIT_STEP          # reference end, FIXED

    bitcell = cell_library.get("sram_cell_6t_122")
    # Two overlapping regular arrays create the half-pitch interleave:
    # upright cells stepping right, mirrored cells stepping left from the
    # far end.  The combs mesh, and mirrored neighbours share boundary
    # geometry (wells, contacts).
    place_array(strip, bitcell, BIT_X0 + skip_pairs * BIT_STEP, 0, "r0",
                1, (0, 1400), pair_count, (BIT_STEP, 0))
    place_array(strip, bitcell, strip_end_x, 0, "m90",
                1, (0, 1400), pair_count, (-BIT_STEP, 0))
    place(strip, cell_library.get("dummy_sram_6t122"), strip_end_x, 0, "r0")
    place(strip, cell_library.get("tapcell_sram_6t122"),
          strip_end_x + BIT_STEP // 2, 0, "r0")
    live_x0 = BIT_X0 + skip_pairs * BIT_STEP
    wd = width_delta(bits_per_strip)
    cell_library.paste_overlay(
        strip, "sramcol_x32_sram_6t122",
        text_filter=lambda l, d, s, x, y: s != "wl" or x >= live_x0,
        widen={"wd": wd, "left_x": BIT_X0,
               "tile_lo": (BIT_X0, BIT_X0 + BIT_STEP),
               "rename": lambda t, k: None if t in ("vss!", "vdd!") else t,
               "tile_skip": ((21, 0), (25, 0), (35, 0), (45, 0))} if wd else None)
    return strip


def build_bitcell_array(layout: kdb.Layout, cell_library: CellLibrary,
                        bits_per_strip: int, strips_per_array: int) -> kdb.Cell:
    """Strips stacked as mirrored pairs, with dummy guard rows."""
    array = layout.create_cell(
        f"bitcell_array_x{bits_per_strip}x{strips_per_array}")
    strip = build_bitcell_strip(layout, cell_library, bits_per_strip)

    for strip_index in range(strips_per_array):
        if strip_index % 2 == 0:
            place(array, strip, STRIP_X_IN_ARRAY,
                  (strip_index // 2) * ROW_PAIR_PITCH, "r0")
        else:
            # Odd strips flip about the x axis and abut the strip below,
            # sharing power straps and well taps at the seam.
            place(array, strip, STRIP_X_IN_ARRAY,
                  ((strip_index + 1) // 2) * ROW_PAIR_PITCH, "m0")

    dummy_row_a = cell_library.get("dummy_topbot_v1")
    dummy_row_b = cell_library.get("dummy_topbot_v2")
    # edge guards live in the array's LEFT margin - they move outward
    # with the extension (their grounding vias otherwise land ON the
    # extension columns: the wl<32>-to-VSS weld)
    wd_g = width_delta(bits_per_strip)
    for pair_index in range(strips_per_array // 2):
        place(array, dummy_row_a, -wd_g,
              DUMMY_V1_Y0 + pair_index * ROW_PAIR_PITCH, "m90")
        place(array, dummy_row_b, -wd_g,
              DUMMY_V2_Y0 + pair_index * ROW_PAIR_PITCH, "m90")
    # array frame = strip frame + STRIP_X_IN_ARRAY
    live_x0 = (BIT_X0 + (32 - bits_per_strip) // 2 * BIT_STEP
               + STRIP_X_IN_ARRAY)
    wd = width_delta(bits_per_strip)
    ax0 = BIT_X0 + STRIP_X_IN_ARRAY
    # strips > 4: the array overlay's SRAM (Static Random-Access Memory)
    # pushed-rules marker spans the reference 4 strips (0..4320); it must
    # cover the extra strip pairs too
    dh = (strips_per_array - 4) * STRIP_HEIGHT
    cell_library.paste_overlay(
        array, "array_x32x4_sram_6t122",
        text_filter=lambda l, d, s, x, y: s != "wl" or x >= live_x0,
        widen={"wd": wd, "left_x": ax0,
               "tile_lo": (ax0, ax0 + BIT_STEP),
               "rename": lambda t, k: None if t in ("vss!", "vdd!") else t,
               "tile_skip": ((21, 0), (25, 0), (35, 0), (45, 0))} if wd else None,
        heighten={"dh": dh, "span": (0, 4 * STRIP_HEIGHT)} if dh else None)
    return array


def build_column_mux(layout: kdb.Layout, cell_library: CellLibrary,
                     strips: int) -> kdb.Cell:
    """The precharge / column-mux stack facing one array: one leg per
    strip.

    At four strips this is the vendored `wrasst_prech_ymux_x4` verbatim.
    That cell is nothing but four leg subcells (`sram_prech_ymux_6t112_v1`
    and `_v2`, alternating) at y = 54 + 1080 k plus eight direct V2 vias
    that assign each leg's ysel/yseln M2 pin to one of the select
    verticals every leg carries; consecutive 2160 slabs are identical.
    For more strips the legs are tiled at the same pitch (leg k faces
    strip k by construction) and the original eight assignment vias are
    kept for legs 0..3; legs 4.. get no assignment (their select pins
    are tied off by the column-group builder until the control exports
    more select lines)."""
    x4 = cell_library.get("wrasst_prech_ymux_x4_sram_6t122_v2")
    if strips == 4:
        return x4
    name = f"wrasst_prech_ymux_x{strips}_gen"
    if layout.has_cell(name):
        return layout.cell(name)
    legs = {}
    for inst in x4.each_inst():
        legs[inst.trans.disp.y] = inst.cell
    ys = sorted(legs)
    assert len(ys) == 4 and ys[1] - ys[0] == STRIP_HEIGHT, \
        "the vendored x4 mux is not the four-leg tile this builder expects"
    mux = layout.create_cell(name)
    m2 = layout.layer(20, 0)
    for k in range(strips):
        leg = legs[ys[k % 4]]          # v1/v2 alternate with the parity of k
        y0 = ys[0] + k * STRIP_HEIGHT
        mux.insert(kdb.CellInstArray(
            leg.cell_index(), kdb.Trans(kdb.Vector(0, y0))))
        if k < 4:
            continue
        # TIE-OFF of the unaddressed legs (no select line exists for them
        # until the control block exports eight): the leg is a
        # transmission gate, nmos on the right (x >= 864, nselect) gated by
        # ysel, pmos on the left (nwell) gated by yseln.  Off = ysel low,
        # yseln high.  Measured leg geometry (cell-local, the v1 flavor;
        # the v2 flavor is its mirror about y = 540 with the supply rails
        # swapped):
        #   ysel's gate island is M1 (Metal 1) x 1200..1476, y 504..924,
        #   the VSS M1 rail is y -36..36 (x up to 1316) and the corridor
        #   x 1340..1600 below the island is empty, so an M1 stub down the
        #   island's right leg plus a short horizontal onto the rail ties
        #   it low (72 dbu clear of the neighboring M1 block at x <= 1332);
        #   yseln's M2 (Metal 2) pin is x 436..980, y 504..576.  Left of
        #   it and above it every M2 corridor is blocked (a 196-dbu
        #   channel between two M2 pieces cannot hold a 72 strap with the
        #   63 spacing floor on both sides), and the leg-seam M2 rails are
        #   NOT supply (probed: their own nets).  To the RIGHT the row
        #   y 504..576 is clear between the shared sense lines sa/san
        #   (y 320..392 and 688..760, 112 dbu away), so the pin is extended
        #   on M2 to the leg edge, a V1 at x 1556..1628 drops onto a new M1
        #   stub that runs to the leg's VDD M1 rail (top rail y 1044..1116
        #   in the v1 flavor, extended right from x 1512 to meet the stub;
        #   80 dbu clear of the ysel island at x <= 1476).
        # Both flavors are verified electrically by the column-group tie
        # probe (strips8_log) and by rung 2 at the bank level.
        # SELECT LINES FOR THE SECOND TILE (increment B, step 1): instead of
        # tying each leg's pins inside the leg, legs 4..7 get real select
        # nets that run through every row like the original eight: per
        # leg two taps (ysel, yseln), each an M2 pin extension to the
        # empty M3 column at x 1660..1732, V2, a short M3 stub, V3, an M4
        # jog west over the array to its own M5 line (96 wide, 192 pitch,
        # SELECT_LINE_X, where M5 and M4 are absent in the stacks), and
        # the lines span one row pitch so consecutive rows abut.  The
        # band feeds them (band.py, variant "s8": the control's eight new
        # select ports land at these columns and their M5 risers run to
        # both stack seams).  Before the feed existed every line was tied
        # at the group's M4 supply straps by a V4 (ysel lines at VSS,
        # yseln lines at VDD; git history of build_column_mux).  The
        # mirrored mux instance gives the right array its own eight (the
        # yselb family, fed at the mirrored columns).
        v2l = layout.layer(25, 0)
        m3l = layout.layer(30, 0)
        v3l = layout.layer(35, 0)
        m4l = layout.layer(40, 0)
        v4l = layout.layer(45, 0)
        m5l = layout.layer(50, 0)
        ysel_y = 832 if k % 2 == 0 else 176   # pin row per leg flavor
        for pin_y, line, ext_x0 in ((ysel_y, 2 * (k - 4), 1588),
                                    (504, 2 * (k - 4) + 1, 980)):
            mux.shapes(m2).insert(kdb.Box(ext_x0, y0 + pin_y,
                                          1740, y0 + pin_y + 72))
            mux.shapes(v2l).insert(kdb.Box(1660, y0 + pin_y,
                                           1732, y0 + pin_y + 72))
            mux.shapes(m3l).insert(kdb.Box(1660, y0 + pin_y - 42,
                                           1732, y0 + pin_y + 114))
            mux.shapes(v3l).insert(kdb.Box(1660, y0 + pin_y,
                                           1732, y0 + pin_y + 72))
            lx = SELECT_LINE_X[line]
            mux.shapes(m4l).insert(kdb.Box(lx, y0 + pin_y - 12,
                                           1732, y0 + pin_y + 84))
            mux.shapes(v4l).insert(kdb.Box(lx, y0 + pin_y - 12,
                                           lx + 96, y0 + pin_y + 84))
        if k == 4:
            # the eight lines, once per mux (fed by the band: the s8
            # control variant's straps rise along these very columns)
            for line in range(8):
                lx = SELECT_LINE_X[line]
                mux.shapes(m5l).insert(kdb.Box(lx, SELECT_LINE_Y0,
                                               lx + 96, SELECT_LINE_Y1))
    for li in layout.layer_indexes():
        for sh in x4.shapes(li).each():
            if not sh.is_text():
                mux.shapes(li).insert(sh.polygon)
    return mux


def build_readwrite_io_column(layout: kdb.Layout,
                              cell_library: CellLibrary,
                              strips: int = 4) -> kdb.Cell:
    """The read/write column periphery, exactly as in the reference bank.

    One of these serves each column group: the two precharge + column-mux
    blocks face the flanking arrays, and one sense amplifier / write
    driver / output latch pair produces one data bit.  At strips > 4 the
    mux blocks are the tiled generated cell and the per-strip bitline
    pin labels (blt/bltn/blb/blbn<i>) are repeated for the extra strips;
    the shared periphery does not move.
    """
    io_column = layout.create_cell("readwrite_io_column"
                                   + ("" if strips == 4 else f"_x{strips}"))
    precharge_column_mux = build_column_mux(layout, cell_library, strips)
    place(io_column, precharge_column_mux, 0, 0, "r0")
    place(io_column, cell_library.get("write_draslatch_02"), 1944, 1674, "m0")
    place(io_column, cell_library.get("senseamp_sram_6t122"), 1944, 1782, "r0")
    place(io_column, cell_library.get("srlatch_sram_6t122"), 4104, 54, "r0")
    place(io_column, precharge_column_mux, 7344, 0, "m90")
    tiles = strips // 4 - 1

    def rename_bl(txt, k):
        m = re.match(r"^(blt|bltn|blb|blbn)<(\d+)>$", txt)
        return f"{m.group(1)}<{int(m.group(2)) + 4 * k}>" if m else None
    # strips > 4: the io overlay's leg-seam supply stacks (the V1/M2/V2
    # ladders at y 1098 and 3258) are NOT repeated for the extra tile: a
    # repeated copy lands on M1 and M3 that belong to the shared
    # periphery cells and are not repeated with it (64 V1.AUX.1a and 64
    # V2.AUX.1b markers when it was tried).  The extra legs get their
    # supplies through the leg-to-leg M1 rail chain instead, and the
    # tie-off uses each leg's own rails.
    cell_library.paste_overlay(
        io_column, "iocolgrp_sram_6t122_v2",
        repeat_texts={"window": (0, 4 * STRIP_HEIGHT),
                      "dy": 4 * STRIP_HEIGHT, "steps": tiles,
                      "rename": rename_bl} if tiles else None)
    # (The left and right mux's select lines are NOT bridged: they are
    # different signals, each array's legs open only for its own half,
    # exactly as the reference's yselt / yselb families for legs 0..3.
    # A bridge was tried and is in git history.)
    return io_column


def width_delta(bits_per_group: int) -> int:
    """Outward growth per side (dbu) for bits beyond the reference 32."""
    return max(0, bits_per_group - 32) * 432


def build_column_group(layout: kdb.Layout, cell_library: CellLibrary,
                       spec: BankSpec) -> kdb.Cell:
    """bitcell array | io column | mirrored array, with edge fillers."""
    column_group = layout.create_cell(f"column_group_{spec.bits_per_group}b")
    bitcell_array = build_bitcell_array(layout, cell_library,
                                        spec.bits_per_group,
                                        spec.strips_per_group)
    io_column = build_readwrite_io_column(layout, cell_library,
                                          spec.strips_per_group)

    # SPARSE-BITS: every anchor stays at its reference position - the
    # arrays are internally sparse (io-adjacent columns live), so the io
    # column, mirrored array and edge fillers never move and bitline /
    # wordline continuity is preserved by construction.
    place(column_group, bitcell_array, ARRAY_X_IN_COLGRP, 0, "r0")
    place(column_group, io_column, IO_COLUMN_X_REF, 0, "r0")
    place(column_group, bitcell_array, MIRRORED_ARRAY_X_REF, 0, "m90")

    edge_filler = cell_library.get("FILLER_cgedge")
    wd = width_delta(spec.bits_per_group)
    # strips > 4: the 4368-tall edge fillers stack once per four strips
    for k in range(spec.strips_per_group // 4):
        dy = k * 4 * STRIP_HEIGHT
        place(column_group, edge_filler,
              EDGE_FILLER_LEFT_XY[0] - wd, EDGE_FILLER_LEFT_XY[1] + dy, "r0")
        place(column_group, edge_filler, EDGE_FILLER_RIGHT_X_REF + wd,
              -24 + dy, "m90")

    # colgrp overlay: wlt<i>/wlb<i> pin texts are per-column - drop the
    # dead (outer) columns' labels.  Wordline indices count from the io
    # column OUTWARD (wlt<0> io-adjacent at x=13612, wlt<31> outermost at
    # x=220), so the live io-adjacent columns are i < bits and the sparse
    # address map keeps the LOW column codes: an address is live iff its
    # column field ADDRESS[6:2] < bits.
    bits = spec.bits_per_group

    def keep_text(l, d, s, x, y):
        if s.startswith(("wlt<", "wlb<")) and s.endswith(">"):
            return int(s[4:-1]) < bits
        return True
    wd = width_delta(bits)

    def rename(txt, k):
        # tiled pair window covers columns 30,31 - each 864-dbu step adds
        # one column PAIR outward: index += 2k; supply labels are NOT
        # per-column (their copies would land on column metal)
        if txt in ("vss!", "vdd!"):
            return None
        if txt.startswith(("wlt<", "wlb<")) and txt.endswith(">"):
            return f"{txt[:4]}{int(txt[4:-1]) + 2 * k}>"
        return txt
    # strips > 4: the colgrp overlay's three io-column M3 verticals span
    # the reference group height (-14..4442); they follow the taller
    # group.  The two full-width M4 supply straps and the M2 sit with the
    # shared periphery and stay.
    dh = (spec.strips_per_group - 4) * STRIP_HEIGHT
    cell_library.paste_overlay(
        column_group, "colgrp_x64x4b_sram_6t122",
        text_filter=keep_text,
        widen={"wd": wd, "left_x": -20, "right_x": 36720,
               "tile_lo": (-20, 844), "tile_hi": (35856, 36720),
               "rename": rename,
               # grounding vias near the window edge would land ON the
               # extension columns (wl-to-VSS welds) - metal only
               "tile_skip": ((21, 0), (25, 0), (35, 0), (45, 0))} if wd else None,
        heighten={"dh": dh, "span": (-14, 4442)} if dh else None)
    if bits < 32:
        _paste_implant_blanket(layout, cell_library, column_group,
                               32 - bits)
    if dh:
        # CONTROL VERTICALS THROUGH THE TALLER GROUP: the periphery cells
        # carry five M3 verticals (wrena, vsswrite, saprechn, sae, wrenan;
        # io-local x about 2550..3200, 144 pitch) that span the reference
        # group height (54..4442) and abut the next row's copies.  They
        # belong to fixed cells, so the taller group leaves a 4320 gap
        # above them and every row's sense/write control is cut off from
        # the band (rung 4.5 at 32,8,32: "sense-amp enable did not fire"
        # at every address while the wordlines decoded).  Extend every
        # full-height io-column M3 vertical that ends at the old top by
        # dh; the space above the periphery is empty at strips > 4.
        m3 = layout.layer(30, 0)
        reg = kdb.Region(column_group.begin_shapes_rec(m3))
        reg.merge()
        io_x0 = IO_COLUMN_X_REF - 500
        io_x1 = IO_COLUMN_X_REF + 7900
        n_ext = 0
        for p in reg.each():
            b = p.bbox()
            if (io_x0 <= b.left and b.right <= io_x1 and b.bottom <= 150
                    and 4150 <= b.top <= 4800 and b.height() > 3000):
                column_group.shapes(m3).insert(
                    kdb.Box(b.left, b.top - 40, b.right, b.top + dh))
                n_ext += 1
        assert n_ext == 5, (
            f"expected the five io-column control verticals to extend, "
            f"found {n_ext}: the periphery cell inventory changed")
    return column_group


def _paste_implant_blanket(layout: kdb.Layout, cell_library: CellLibrary,
                           column_group: kdb.Cell, skip_cols: int) -> None:
    """Sparse-bits: re-cover the dead columns' guard active.

    Removing the outer bitcells removed the implant blankets their guard
    active relied on.  The correct cover for the dead zone is exactly what
    the reference draws there, so paste the reference's own per-row
    implant bands (extracted by scripts/extract_implant_blanket.py)
    clipped to the dead windows: every implant width/space/enclosure rule
    then passes exactly as the reference does.  The 400-dbu overlap into
    live territory merges with identical existing geometry.
    """
    path = cell_library.cells_dir / "implant_blanket_colgrp.gds"
    src = kdb.Layout()
    src.read(str(path))
    blanket = src.cell("implant_blanket_colgrp")
    live_x = -20 + skip_cols * 432       # colgrp x of the first live column
    axis = 18352                         # io-column mirror axis
    windows = kdb.Region()
    windows.insert(kdb.Box(-1000, -600, live_x + 400, 5000))
    windows.insert(kdb.Box(2 * axis - live_x - 400, -600, 39000, 5000))
    for li in src.layer_indexes():
        info = src.get_info(li)
        reg = kdb.Region(blanket.begin_shapes_rec(li)) & windows
        dli = layout.layer(info.layer, info.datatype)
        for p in reg.each():
            column_group.shapes(dli).insert(p)


REF_DEPTH = 32                 # depth_groups of the reference bank
_ROW_PIN = re.compile(r"^(wd|dataout)<(\d+)>$")


def paste_bank_overlay(bank: kdb.Cell, cell_library: CellLibrary,
                       spec: BankSpec, growth: int = 0) -> None:
    """Paste the reference bank-level overlay, re-anchored for depth.

    The reference overlay carries three kinds of content, each with its
    own depth behavior:
      * decode-band shapes and pin texts - depth-independent, pasted fixed;
      * well-tap / marker clusters hugging the bank's top and bottom edges
        - translated outward with their stack edge (left at reference
        positions they land on grown rows' wiring and short BL/VDD/VSS,
        the rung-2 failure that motivated this function);
      * per-row wd<i>/dataout<i> pin texts - regenerated: reference rows
        keep their measured positions, the lower stack renumbers from
        <REF_DEPTH+r> to <depth+r>, and rows beyond the reference
        extrapolate on the row pitch from the outermost reference row.

    At depth_groups=REF_DEPTH every branch is the identity, so the
    reference bank stays byte-for-byte as pasted before (rung 0's XOR).
    """
    layout = bank.layout()
    depth = spec.depth_groups
    pitch = colgrp_pitch(spec)
    # how far the stack's OUTER edge moved from the reference outer edge:
    # (depth-1) rows of the new pitch plus the new group height, against
    # 31 rows of 4320 plus 4640.  At strips=4 this is (depth-32)*4320.
    delta = ((depth - 1) * pitch + colgrp_height(spec)
             - ((REF_DEPTH - 1) * COLGRP_ROW_PITCH + COLGRP_DRAWN_HEIGHT))
    # band v2 growth: everything anchored to the LOWER stack shifts down
    # by `growth` in addition to the depth delta
    # A shape belongs to an edge cluster iff it sits at/beyond the
    # outermost reference row; everything between is decode-band territory.
    top_zone_y = UPPER_STACK_ANCHOR_Y + (REF_DEPTH - 1) * COLGRP_ROW_PITCH
    bottom_zone_y = LOWER_STACK_ANCHOR_Y - (REF_DEPTH - 1) * COLGRP_ROW_PITCH

    row_pins = {}      # (kind, ref_index) -> (layer_index, x, y)
    for (l, d), sh in cell_library.overlay_shapes("srambank_32b"):
        dli = layout.layer(l, d)
        if sh.is_text():
            x, y = sh.text_trans.disp.x, sh.text_trans.disp.y
            m = _ROW_PIN.match(sh.text_string)
            if m:
                row_pins[(m.group(1), int(m.group(2)))] = (dli, x, y)
            else:
                ty = y
                # supply labels anchored to the lower-stack side of the
                # band slide down with the band-v2 growth
                if (sh.text_string in ("vdd!", "vss!")
                        and 136644 < y < CONNECTOR_ZONE[1]):
                    ty = y - growth
                bank.shapes(dli).insert(
                    kdb.Text(sh.text_string, kdb.Trans(kdb.Vector(x, ty))))
            continue
        poly = sh.polygon
        b = poly.bbox()
        dy = (delta if b.bottom >= top_zone_y
              else -(delta + growth) if b.top <= bottom_zone_y else 0)
        # BAND V2 SEAM CONNECTORS: the control-distribution wires (ysel,
        # sae, wrena, wd, ...) reach the lower stack through overlay
        # connector stacks (staggered v3/m4/v4 jogs + m5 riser feet) in a
        # strip just above the lower-stack anchor.  The stack slides down
        # by `growth`; connector pieces wholly inside the strip go WITH
        # it, and each m5 riser (foot in the strip, body up in the band)
        # gets a same-x M5 extension across the growth - M5 is not a
        # macro routing layer, so the extension crosses the die freely.
        if growth and dy == 0:
            in_zone = (CONNECTOR_ZONE[0] <= b.bottom
                       and b.top <= CONNECTOR_ZONE[1])
            if in_zone:
                dy = -growth
            elif ((l, d) == (50, 0)
                  and CONNECTOR_ZONE[0] <= b.bottom <= CONNECTOR_ZONE[1]
                  and b.top > CONNECTOR_ZONE[1]):
                bank.shapes(dli).insert(kdb.Box(
                    b.left, b.bottom - growth, b.right, b.bottom + 100))
        bank.shapes(dli).insert(
            poly.transformed(kdb.Trans(kdb.Vector(0, dy))))

    def emit(kind, new_index, ref_key, dy):
        dli, x, y = row_pins[ref_key]
        bank.shapes(dli).insert(
            kdb.Text(f"{kind}<{new_index}>",
                     kdb.Trans(kdb.Vector(x, y + dy))))

    for kind in ("wd", "dataout"):
        for r in range(depth):
            if spec.strips_per_group == 4:
                # reference pitch: keep every measured text position (the
                # reference rows' texts are hand-placed, not periodic:
                # steps 3988..4976 dbu) and extrapolate beyond row 31
                extra = max(0, r - (REF_DEPTH - 1)) * COLGRP_ROW_PITCH
                # upper stack, row r counted away from the band: index r
                emit(kind, r, (kind, min(r, REF_DEPTH - 1)), extra)
                # lower stack, same row: index depth + r
                emit(kind, depth + r,
                     (kind, REF_DEPTH + min(r, REF_DEPTH - 1)),
                     -extra - growth)
            else:
                # any other pitch: every row is periodic from row 0's
                # measured offset inside its group (the io column's M3
                # stub is at the same place in every group)
                emit(kind, r, (kind, 0), r * pitch)
                emit(kind, depth + r, (kind, REF_DEPTH),
                     -r * pitch - growth)


def build_bank(layout: kdb.Layout, cell_library: CellLibrary,
               spec: BankSpec) -> kdb.Cell:
    """Full bank: two mirrored column-group stacks facing a central
    decode/control band, closed by top and bottom filler rows."""
    bank = layout.create_cell(spec.name)
    column_group = build_column_group(layout, cell_library, spec)
    rows_per_half = spec.depth_groups
    # BAND V2 (one-box control) grows the band downward for bits<32:
    # the lower stack, its guard row and the bottom filler slide down
    growth = growth_for(control_variant(spec)) if uses_band_v2(spec) else 0

    # The two stacks grow AWAY from the central decode band: the mirrored
    # stack grows downward, the upright stack grows upward.
    pitch = colgrp_pitch(spec)
    place_array(bank, column_group, COLGRP_X_IN_BANK,
                LOWER_STACK_ANCHOR_Y - growth,
                "m0", rows_per_half, (0, -pitch), 1, (121600, 0))
    place_array(bank, column_group, COLGRP_X_IN_BANK, UPPER_STACK_ANCHOR_Y,
                "r0", rows_per_half, (0, pitch), 1, (121600, 0))
    band_cell = (build_ctrl_band_v2(layout, cell_library,
                                    spec.bits_per_group,
                                    variant=control_variant(spec),
                                    select_columns=select_columns_in_band(spec))
                 if uses_band_v2(spec)
                 else build_ctrl_band(layout, cell_library,
                                      spec.bits_per_group))
    place(bank, band_cell, *DECODER_XY, "r0")

    # Fillers hug the actual stack extents so non-reference depths stay
    # compact.  At rows_per_half=32 these land on the reference anchors.
    lower_stack_bottom_edge = (LOWER_STACK_ANCHOR_Y - growth
                               - colgrp_height(spec)
                               - (rows_per_half - 1) * pitch)
    upper_stack_top_edge = (UPPER_STACK_ANCHOR_Y + colgrp_height(spec)
                            + (rows_per_half - 1) * pitch)
    filler_top = cell_library.get("FILLER_32x2_top")
    filler_bot = cell_library.get("FILLER_32x2_bottom")
    wd_f = width_delta(spec.bits_per_group)
    if wd_f:
        # the fillers' margin grounding vias sit where the extension's
        # innermost columns now run - slot the via layers there (same
        # cure as the guard corners and edge-guard rows)

        def _slot_filler(cell, tag):
            tc = layout.create_cell(f"{cell.name}_slot_{tag}")
            tc.copy_tree(cell)
            tc.flatten(-1)
            slots4 = kdb.Region()
            for cx4 in (648, 37824):
                slots4.insert(kdb.Box(cx4 - 120, -10**7, cx4 + 120, 10**7))
            for li4 in layout.layer_indexes():
                info4 = layout.get_info(li4)
                if (info4.layer, info4.datatype) not in ((21, 0), (25, 0),
                                                         (35, 0), (45, 0),
                                                         (17, 0)):
                    continue
                reg4 = kdb.Region()
                for sp4 in tc.shapes(li4).each():
                    if not sp4.is_text():
                        reg4.insert(sp4.polygon)
                if (info4.layer, info4.datatype) == (17, 0):
                    # the filler's tie-off dummy at the slot column: its
                    # gate-contact V0 also lands on a LISD tail, tying gate
                    # to source/drain.  On the live column-32 wordline that
                    # is a diode pull-down (band.py corner slot has the full
                    # note).  Cut the LISD across the slot's full width at
                    # every GATE-contact V0 row in the slot (V0 touching
                    # LIG; the slot's plain S/D contact vias must stay).  A
                    # partial clip would leave a sub-width sliver; the
                    # SDT-covered contact stays.
                    v0_4 = kdb.Region()
                    for sp4 in tc.shapes(layout.layer(18, 0)).each():
                        if not sp4.is_text():
                            v0_4.insert(sp4.polygon)
                    lig_4 = kdb.Region()
                    for sp4 in tc.shapes(layout.layer(16, 0)).each():
                        if not sp4.is_text():
                            lig_4.insert(sp4.polygon)
                    for pv in (v0_4 & slots4).interacting(lig_4).each():
                        vb = pv.bbox()
                        cx4 = min((648, 37824), key=lambda c: abs(c - vb.center().x))
                        reg4 -= kdb.Region(kdb.Box(cx4 - 120, vb.bottom - 20,
                                                   cx4 + 120, vb.top + 20))
                else:
                    reg4 -= slots4
                tc.shapes(li4).clear()
                for poly4 in reg4.each():
                    tc.shapes(li4).insert(poly4)
            # drop supply labels at the slot columns (their metal now
            # merges with the extension columns' wordlines)
            for lt in (19, 20, 30):
                lit = layout.layer(lt, 251)
                keep5 = []
                for sh5 in tc.shapes(lit).each():
                    if sh5.is_text():
                        tx5 = sh5.text_trans.disp.x
                        if (sh5.text_string in ("vss!", "vdd!")
                                and any(abs(tx5 - cx4) < 200
                                        for cx4 in (648, 37824))):
                            continue
                        keep5.append(sh5.text.dup())
                tc.shapes(lit).clear()
                for t5 in keep5:
                    tc.shapes(lit).insert(t5)
            return tc
        filler_top = _slot_filler(filler_top, spec.bits_per_group)
        filler_bot = _slot_filler(filler_bot, spec.bits_per_group)
    place(bank, filler_top, 0,
          upper_stack_top_edge - TOP_FILLER_TUCK, "r0")
    place(bank, filler_bot, 0,
          lower_stack_bottom_edge - BOTTOM_FILLER_STANDOFF, "r0")
    paste_bank_overlay(bank, cell_library, spec, growth)
    return bank


def generate(spec: BankSpec, out_gds: str) -> dict:
    """Generate one bank and write it to out_gds.  Returns a manifest."""
    layout = kdb.Layout()
    layout.dbu = DBU
    cell_library = CellLibrary(layout)
    bank = build_bank(layout, cell_library, spec)
    layout.write(out_gds)
    bounding_box = bank.bbox()
    return {
        "top_cell": bank.name,
        "gds": pathlib.Path(out_gds).name,    # beside the manifest
        "bits_per_group": spec.bits_per_group,
        "strips_per_group": spec.strips_per_group,
        "depth_groups": spec.depth_groups,
        "bitcells": (spec.bits_per_group * spec.strips_per_group
                     * 2 * 2 * spec.depth_groups),
        "width_um": round(bounding_box.width() * layout.dbu, 4),
        "height_um": round(bounding_box.height() * layout.dbu, 4),
        "reference_exact": spec.is_reference,
    }
