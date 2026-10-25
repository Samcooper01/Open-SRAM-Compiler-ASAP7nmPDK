"""Parametric decode band - the first GENERATED decode hardware.

The reference band `ctrl_decode_32` is [edge | decoder | control+fillers |
mirrored decoder | edge] plus full-width dummy rows.  Each decoder is a
compact 5-bit predecode block below a bank of eight 4-column wordline
driver slices (pitch 1728 dbu) closed by two end taps.  Wordline indices
count from the io column outward, and the driver bank is mirrored (m90)
inside the decoder, so the io-adjacent columns are driven by the
LOW-numbered slices in slice-local coordinates.

build_postdecode cuts the driver bank the sparse way: keep the
io-adjacent slices and BOTH end taps at their original positions, drop
the outer slices' devices and via cells, and replicate the through-rails
(predecode lines, WLENA - the wordline enable, power) across the vacated
span.  The rails are measured from the slice cell itself (every shape
spanning the full slice pitch is a rail the slices passed along by
abutment), so the replication is self-calibrating.

At bits=32 build_ctrl_band returns the reference cell verbatim; the
parametric path at 32 is exercised by tests and must XOR-match it.
"""
import klayout.db as kdb

from .library import CellLibrary

BAND = "ctrl_decode_32"
DECODER = "decoder_5to32_sram_6t122_and_halved_P1N1_A_original"
POSTDECODE = "post_Decode_and_size_reduced_x32_sram_6t122_P1N1"
SLICE = "post_Decode_and_size_reduced_x1_sram_6t122_P1N1_32bit"

SLICE_X0 = 432          # slice-local x of slice 0 (io-adjacent columns)
SLICE_PITCH = 1728      # one slice = 4 wordline columns
N_SLICES = 8


def _copy_shapes(dst_cell, src_cell, layout, keep=None):
    """Copy direct shapes; keep(layer, datatype, shape) filters."""
    for li in layout.layer_indexes():
        info = layout.get_info(li)
        for sh in src_cell.shapes(li).each():
            if keep is None or keep(info.layer, info.datatype, sh):
                dst_cell.shapes(li).insert(sh)


def build_postdecode(layout: kdb.Layout, bits: int) -> kdb.Cell:
    src = layout.cell(POSTDECODE)
    sl = layout.cell(SLICE)
    new = layout.create_cell(f"post_Decode_gen_x{bits}")
    n_keep = bits // 4
    cut_x0 = SLICE_X0 + n_keep * SLICE_PITCH
    cut_x1 = SLICE_X0 + N_SLICES * SLICE_PITCH      # 14256

    # the through-going metal + implant pieces of one slice (flattened):
    # everything spanning the full pitch is fabric the slices abut through.
    # WELL (1,0) is in the list too: the kept edge slice draws only 36 dbu
    # of well past its active on the side the dropped neighbor used to
    # cover (external deck finding, 16 markers at bits 16: projected
    # ACTIVE.WELL.EN.1 short of 27 nm); tiling the slice's full-pitch well
    # band across the vacated span restores the reference's continuous
    # well.  At bits 32 nothing is vacated, so nothing changes.
    THRU = ((19, 0), (20, 0), (30, 0), (12, 0), (13, 0), (1, 0))
    thru_pieces = {}        # (layer, datatype) -> [Polygon]
    for li in layout.layer_indexes():
        info = layout.get_info(li)
        if (info.layer, info.datatype) not in THRU:
            continue
        reg = kdb.Region(sl.begin_shapes_rec(li)).merged()
        thru_pieces[(info.layer, info.datatype)] = [
            p for p in reg.each()
            if p.bbox().width() >= SLICE_PITCH - 200]
    m1_bands = [p.bbox() for p in thru_pieces.get((19, 0), [])]

    # instances: drop the outer slices; drop V1 via cells in the vacated
    # span whose m1 partner was slice-local (not a replicated rail) -
    # they would dangle.  Everything else (both end taps, the WLENA entry
    # plumbing at local x~14080, rail-to-rail vias) stays put.
    for inst in src.each_inst():
        if (inst.cell.name.startswith(SLICE)
                and inst.trans.disp.x >= cut_x0):
            continue                                # dropped outer slice
        b = inst.bbox()
        if (inst.cell.name.startswith("M2_M1")
                and cut_x0 < (b.left + b.right) // 2 < cut_x1
                and not any(band.bottom <= b.bottom and b.top <= band.top
                            for band in m1_bands)):
            continue                                # would-be dangling V1
        new.insert(inst.cell_inst)

    # direct shapes stay, except: per-column texts over the vacated span,
    # and direct V1 vias there whose m1 partner was slice-local (same
    # dangling-via rule as the via cells)
    def keep(l, d, sh):
        if sh.is_text():
            return not (cut_x0 < sh.text_trans.disp.x < cut_x1)
        if (l, d) == (21, 0):
            b = sh.bbox()
            if (cut_x0 < (b.left + b.right) // 2 < cut_x1
                    and not any(band.bottom <= b.bottom and b.top <= band.top
                                for band in m1_bands)):
                return False
        return True
    _copy_shapes(new, src, layout, keep)

    # replicate the through-going pieces across the vacated span: metal
    # rails (predecode group and PC lines, WLENA, power) plus the implant
    # bands that cover the kept edge slice's boundary whiskers.  The
    # actual polygons are tiled at slice pitch so notches and jogs
    # reproduce exactly and merge by abutment.  Conductive fabric
    # (active, fin) is deliberately NOT replicated - active without its
    # gates would merge into one giant source/drain conductor.
    for (l, d), pieces in thru_pieces.items():
        li = layout.layer(l, d)
        for piece in pieces:
            for k in range(n_keep, N_SLICES):
                shift = kdb.Trans(kdb.Vector(SLICE_X0 + k * SLICE_PITCH, 0))
                new.shapes(li).insert(piece.transformed(shift))
    return new


def build_decoder(layout: kdb.Layout, bits: int) -> kdb.Cell:
    src = layout.cell(DECODER)
    new = layout.create_cell(f"decoder_gen_5to{bits}")
    postdecode = build_postdecode(layout, bits)
    for inst in src.each_inst():
        ci = inst.cell_inst
        if inst.cell.name == POSTDECODE:
            ci = kdb.CellInstArray(postdecode.cell_index(), inst.trans)
        new.insert(ci)

    # texts: wl<i> outputs exist only for live columns
    def keep(l, d, sh):
        if sh.is_text() and sh.text_string.startswith("wl<"):
            return int(sh.text_string[3:-1]) < bits
        return True
    _copy_shapes(new, src, layout, keep)
    return new


def build_ctrl_band(layout: kdb.Layout, cell_library: CellLibrary,
                    bits: int, force_parametric: bool = False) -> kdb.Cell:
    """The decode band for a bits configuration.

    bits=32 returns the vendored reference cell byte-for-byte (rung 0);
    force_parametric runs the generated path anyway - the identity test
    XORs that against the reference.
    """
    band_src = cell_library.get(BAND)
    if bits == 32 and not force_parametric:
        return band_src
    new = layout.create_cell(f"ctrl_decode_gen_{bits}")
    decoder = build_decoder(layout, bits)
    for inst in band_src.each_inst():
        ci = inst.cell_inst
        if inst.cell.name == DECODER:
            ci = kdb.CellInstArray(decoder.cell_index(), inst.trans)
        new.insert(ci)
    _copy_shapes(new, band_src, layout)
    return new


# =========================================================================
# BAND V2 - one-box control: the re-place-and-routed control rectangle
# replaces the hand-packed T; the band grows DOWNWARD by GROWTH_V2 and
# the lower stack slides down with it.  All interface wiring between the
# new macro and the band is GENERATED here with fully known coordinates:
# vertical straps (pad -> landing via stacks), exit-wire extensions
# (wordline verticals, io-column controls, supply spines), and supply
# rail rows for the new cell rows.
# =========================================================================
import json
import pathlib

N_NEW_ROWS_V2 = 20
GROWTH_V2 = N_NEW_ROWS_V2 * 1080          # dbu the band grows downward
CTL_DISP = (-32204, 8076)                 # control placement in the band
ONEBOX_ORIGIN_CTL = (33500, -27000)   # die origin (row-grid aligned)
# CONTROL VARIANTS: every band-v2 control now carries the binary-
# weighted sense-enable delay line (scripts/patch_control_delayline.py,
# 124 buffers and 5 selectors in place of the reference's 15 cells),
# which does not fit the original 20-row die at a routable utilization
# (the yselb-patched standard control already sat at 87 %), so the
# standard die is eight rows taller than the reference one-box die; the
# eight-strip control ("s8", 24 ports and 64 driver cells more) needs
# six rows beyond that.  Sized for about 75 % utilization at place
# time: 80 % routed with one spacing marker, 87 % did not fit.  The band grows further down, the lower stack
# with it.  Every growth-dependent coordinate derives from these two
# functions (pnr/gen_inputs.py writes the matching floorplan;
# tests/test_invariants.py re-derives the rule).
N_NEW_ROWS_VARIANT = {"": N_NEW_ROWS_V2 + 8, "s8": N_NEW_ROWS_V2 + 14}


def growth_for(variant: str = "") -> int:
    """dbu the band grows downward for a control variant."""
    return N_NEW_ROWS_VARIANT[variant] * 1080


def onebox_origin_ctl(variant: str = "") -> tuple:
    """Die origin of the variant's macro in the control frame: five rows
    (the four old bar rows plus the guard row) above the growth, so the
    core bottom sits on the reference 1080-dbu row grid."""
    return (ONEBOX_ORIGIN_CTL[0], -(growth_for(variant) + 5400))
V2_ASSETS = pathlib.Path(__file__).resolve().parent.parent / "cells"

_L = {"m1": (19, 0), "m2": (20, 0), "m3": (30, 0), "m4": (40, 0),
      "m5": (50, 0), "v1": (21, 0), "v2": (25, 0), "v3": (35, 0),
      "v4": (45, 0)}


def _ctl2band(x, y):
    return x + CTL_DISP[0], y + CTL_DISP[1]


def _box(band_cell, layout, lname, x0, y0, x1, y1):
    band_cell.shapes(layout.layer(*_L[lname])).insert(
        kdb.Box(int(x0), int(y0), int(x1), int(y1)))


def _strap(band_cell, layout, strap, probe_cell=None):
    """Strap with the M4-landing architecture: an M4 landing PLATE at the
    landing row (M4 never conflicts with the M3 feedthrough columns that
    cross the landing rows), V4 up to the M5 riser at the landing track
    through the forest, down-stack at the pad's depth with a short m4
    jog to the pad x, then the pad-layer contact.  All coordinates in
    the band frame.  probe_cell (default band_cell) is where the forest
    stitch looks for real metal - pass the TOP cell when band_cell is a
    display-group subcell that holds only the straps themselves."""
    probe = probe_cell if probe_cell is not None else band_cell
    pl, px0, py0, px1, py1 = strap["from_pad"]
    _, lx0, ly0, lx1, ly1 = strap["to_box"]
    lx = (lx0 + lx1) // 2
    pcx, pcy = (px0 + px1) // 2, (py0 + py1) // 2
    jog_dy = strap.get("jog_dy", 0)
    drawn = {"m4": [], "m5": []}

    def _b(lname, x0, y0, x1, y1):
        bx_ = kdb.Box(int(x0), int(y0), int(x1), int(y1))
        if lname in drawn:
            drawn[lname].append(bx_)
        band_cell.shapes(layout.layer(*_L[lname])).insert(bx_)

    def _stitch():
        """FOREST STITCH: V4 wherever the drawn strap crosses one of the
        net's own band forest wires (reference-graph attribution) - the
        strap-to-band-wire contact is guaranteed by construction.  The
        via drops only where real forest metal fully covers it."""
        import os as _os
        if "stitch" in _os.environ.get("SRAMFORGE_ABLATE", "").split(","):
            return
        for fb in strap.get("forest") or []:
            fl = fb[0]
            if fl not in ("m4", "m5"):
                continue
            fx0, fy0 = _ctl2band(fb[1], fb[2])
            fx1, fy1 = _ctl2band(fb[3], fb[4])
            fbox = kdb.Box(fx0, fy0, fx1, fy1)
            for mb in drawn["m5" if fl == "m4" else "m4"]:
                ib = mb & fbox
                if ib.empty() or ib.width() < 72 or ib.height() < 72:
                    continue
                c = ib.center()
                w, h = min(ib.width(), 96), min(ib.height(), 96)
                vb = kdb.Box(c.x - w // 2, c.y - h // 2,
                             c.x + w // 2, c.y + h // 2)
                li5 = layout.layer(*_L[fl])
                real = kdb.Region(
                    probe.begin_shapes_rec_overlapping(li5, vb))
                if (kdb.Region(vb) - real).is_empty():
                    band_cell.shapes(layout.layer(*_L["v4"])).insert(vb)
    bpx0, _ = _ctl2band(px0, 0)
    bpx1, _ = _ctl2band(px1, 0)
    bx, _ = _ctl2band(lx, 0)
    bpx, bpy = _ctl2band(pcx, pcy)
    _, bly0 = _ctl2band(0, ly0)
    _, bly1 = _ctl2band(0, ly1)
    land_cy = (bly0 + bly1) // 2
    # M4 landing plate covering the whole pin box + V4 up to the riser
    _b("m4", bx - 120, bly0 - 48, bx + 120, bly1 + 48)
    _b("v4", bx - 48, land_cy - 48, bx + 48, land_cy + 48)
    # M5 vertical through the forest to the jog lane
    _b("m5", bx - 48, land_cy - 192,
       bx + 48, bpy + strap.get("jog_dy", 0) + 192)
    # down-stack at the pad depth + jog to the pad x on m4; the jog may
    # ride a dodged y-lane (jog_dy) with a vertical m4 nub at the pad x
    jy = bpy + jog_dy
    # landing under a wide pad: contact straight up, no lateral jog
    jtx = bx if bpx0 + 140 <= bx <= bpx1 - 140 else bpx
    if pl == "m5" and abs(bx - bpx) <= 48 and jog_dy == 0:
        # riser sits ON the m5 pad column: direct M5 merge, no jog stack
        _b("m5", bx - 48, land_cy - 192, bx + 48, bpy + 192)
        _stitch()
        return
    _b("v4", bx - 48, jy - 48, bx + 48, jy + 48)
    _b("m4", min(bx, jtx) - 120, jy - 48, max(bx, jtx) + 120, jy + 48)
    if jog_dy:
        _b("m4", bpx - 120, min(bpy, jy) - 48,
           bpx + 120, max(bpy, jy) + 48)
    if pl == "m4":
        pass                                  # jog merges the pad directly
    elif pl == "m5":
        _b("v4", jtx - 48, jy - 48, jtx + 48, jy + 48)
        _b("m5", jtx - 48, jy - 192, jtx + 48, jy + 192)
    else:                                     # m3 pad (wlena family)
        _b("v3", jtx - 36, jy - 36, jtx + 36, jy + 36)
        _b("m3", jtx - 36, jy - 192, jtx + 36, jy + 192)
    _stitch()


# dual-decoder geometry (bits > 32): copy-2 one decoder-width outward
DEC_R0_ANCHOR = (1728, 8076)              # left decoder in the band
DEC_M90_ANCHOR = (38448, 8076)            # right (mirrored) decoder
DEC_SPAN = 32 * 432                       # 13824: one decode bank of columns
# band-level A<0..4> distribution m2 rails (measured): (y0, y1) each;
# left rail ends at x=1736, right rail starts at x=38440
A_RAIL_Y = [(9408, 9480), (10632, 10704), (12072, 12144),
            (12504, 12576), (13812, 13884)]


# EIGHT-STRIP SELECT FEEDS (variant "s8"): the sixteen new select ports
# land at their stack select-line columns, so each strap's M5 riser is
# extended along that column through the whole band, from the top of
# the lower stack's first line (band 2622 - growth - 34: the lower
# stack anchor slides down with the variant's growth, so -25458 - 34
# for the eight-strip die) to the bottom of the upper stack's first
# line (band 35096), with 100 dbu of overlap at each end.  M5 is not a
# macro routing layer and the columns were chosen clear of every band
# and bank-overlay M5 and V4 over this whole span
# (generator.SELECT_LINE_X).  The first feed stopped at the STANDARD
# growth's seam (-19044) and left every lower-stack leg floating; the
# connectivity rung now probes the select lines at the outermost rows
# of both stacks.
S8_FEED_TOP = 35230
LOWER_SEAM_BAND = 2622                    # lower stack anchor, band frame


def s8_feed_y(variant: str = "s8") -> tuple:
    return (LOWER_SEAM_BAND - growth_for(variant) - 34 - 100, S8_FEED_TOP)


def build_ctrl_band_v2(layout: kdb.Layout, cell_library: CellLibrary,
                       bits: int, variant: str = "",
                       select_columns: tuple = ()) -> kdb.Cell:
    """Band v2: cut decoders + one-box control + generated interface.

    variant selects the vendored control artifacts (cells/control_onebox
    <_variant>.*): "" is the standard one-box control, "s8" the
    eight-strip variant with the 3:8 column-select decode, whose sixteen
    select lines are fed along select_columns (band x of each line's
    left edge: the left mux's eight, line j = yselt_(4+j//2) for even
    j and yseltn for odd j, then the mirrored mux's eight, the yselb
    family likewise)."""
    _sfx = f"_{variant}" if variant else ""
    growth = growth_for(variant)
    band_src = cell_library.get(BAND)
    new = layout.create_cell(f"ctrl_decode_v2_{bits}")
    import os as _os_dec
    _abl_dec = set(_os_dec.environ.get("SRAMFORGE_ABLATE", "").split(","))

    # DISPLAY GROUPS: every family of generated wiring lives in its own
    # subcell (instanced at the origin) so the layout is browsable -
    # hide or descend per group in KLayout.  Electrically identical to
    # flat drawing (wrappers sit at Trans()); the verification rungs
    # operate on flattened geometry.
    _groups = {}

    def G(name):
        if name not in _groups:
            c = layout.create_cell(f"v2_{name}_{bits}")
            _groups[name] = c
            new.insert(kdb.CellInstArray(c.cell_index(), kdb.Trans()))
        return _groups[name]
    decoder = build_decoder(layout, min(bits, 32))
    if bits > 32:
        # copy-1's OUTER margin (local x < -80) holds edge furniture
        # (a floating vss-labeled rail among it) that sits exactly on
        # the innermost extension column's track - trim it; the corner
        # slot owns that region now
        dec1t = layout.create_cell(f"decoder_main_{bits}")
        dec1t.copy_tree(decoder)
        dec1t.flatten(-1)
        MARG = kdb.Box(-10**7, -10**7, -80, 10**7)
        # the strip local x -80..0 keeps its METAL (edge furniture), but
        # its VIAS must go: the surviving v1/v2 stub at local (-80..-36,
        # 11844..11916) welds the supply rail one row above the WLENA
        # rail into the tall edge m3 - at bits=64 this merged the copy
        # WLENA rails (hi enables) with the decoder supply web
        VIA_STRIP = kdb.Box(-90, -10**7, 4, 10**7)
        for li9 in layout.layer_indexes():
            info9 = layout.get_info(li9)
            reg9 = kdb.Region()
            texts9 = [sh.text.dup() for sh in dec1t.shapes(li9).each()
                      if sh.is_text() and sh.text_trans.disp.x >= -80]
            for sh in dec1t.shapes(li9).each():
                if not sh.is_text():
                    reg9.insert(sh.polygon)
            reg9 -= kdb.Region(MARG)
            if (info9.layer, info9.datatype) in ((21, 0), (25, 0), (35, 0),
                                                 (45, 0)):
                reg9 -= kdb.Region(VIA_STRIP)
            if ("legacy64" not in _abl_dec
                    and (info9.layer, info9.datatype) == (20, 0)):
                # the strip's M2 furniture is five 64x72 slivers lying
                # entirely at x < 0: with their vias gone they are floating
                # (and under M2 min area), and at TRIM 13824 they face the
                # copy's revived tail diagonally at 94..108 dbu (M2.S.2 /
                # M2.S.5, Log 32 round 2).  Drop the polygons that lie
                # wholly in the strip; anything reaching x >= 0 stays.
                reg9 -= reg9.inside(kdb.Region(kdb.Box(-10**7, -10**7, 0,
                                                       10**7)))
            # (No reference-side rail-tip trim.  A 130-dbu trim of the middle
            # rows was tried for the seam gap and killed reference column 30:
            # the "stub" v1 at reference-local 48..120 on PC<2> is wl<30>'s
            # per-wordline tap - a GATE consumer, invisible to a source/drain
            # driver check.  The seam gap is made on the copy side only; see
            # the SEAM_RAIL_ROWS note in the dec2t block.  Log 32.)
            dec1t.shapes(li9).clear()
            for poly9 in reg9.each():
                dec1t.shapes(li9).insert(poly9)
            for t9 in texts9:
                dec1t.shapes(li9).insert(t9)
        decoder = dec1t

    for inst in band_src.each_inst():
        name = inst.cell.name
        ci = inst.cell_inst
        if name == "control_sram_2kB_6t122_v3_orig10242022_lvspass_32":
            continue                        # replaced by the one-box macro
        if name == DECODER:
            ci = kdb.CellInstArray(decoder.cell_index(), inst.trans)
        elif name == "dummy_vertical_array_X64":
            t = inst.trans
            dy = -growth if t.disp.y < 20000 else 0
            if bits > 32:
                # EXPLODE the guard; corner caps stay but get a SLOT cut
                # through their metal at the innermost extension column
                # (col-32) - the cap physically spans that column and
                # welds anything crossing it to wl<31>
                for ch in inst.cell.each_inst():
                    if ch.cell.name == "dummy_corner_v2":
                        ct = t * ch.cell_inst.trans
                        colx = 1512 if ct.disp.x < 20000 else 38664
                        tc = layout.create_cell(
                            f"corner_slot_{bits}_{int(ct.disp.x)}_{int(ct.disp.y)}")
                        tc.copy_tree(ch.cell)
                        tc.flatten(-1)
                        inv = kdb.Trans(ct.rot % 4, ct.is_mirror(),
                                        ct.disp).inverted()
                        s0 = inv * kdb.Point(colx - 80, -10**6)
                        s1 = inv * kdb.Point(colx + 80, 10**6)
                        slot = kdb.Box(min(s0.x, s1.x), -10**6,
                                       max(s0.x, s1.x), 10**6)
                        for li3 in layout.layer_indexes():
                            info3 = layout.get_info(li3)
                            if (info3.layer, info3.datatype) in (
                                    (19, 251), (20, 251), (30, 251)):
                                keep6 = []
                                for sh6 in tc.shapes(li3).each():
                                    if sh6.is_text():
                                        pt6 = sh6.text_trans.disp
                                        if (sh6.text_string in
                                                ("vss!", "vdd!")
                                                and slot.left <= pt6.x
                                                <= slot.right):
                                            continue
                                        keep6.append(sh6.text.dup())
                                tc.shapes(li3).clear()
                                for t6 in keep6:
                                    tc.shapes(li3).insert(t6)
                                continue
                            if (info3.layer, info3.datatype) not in (
                                    (30, 0), (25, 0), (35, 0), (17, 0)):
                                continue
                            reg3 = kdb.Region()
                            for sp3 in tc.shapes(li3).each():
                                if not sp3.is_text():
                                    reg3.insert(sp3.polygon)
                            if (info3.layer, info3.datatype) == (17, 0):
                                # TIE-OFF DUMMY (Log 32 sign-off): the cap's
                                # dummy transistor at the slot column is a
                                # tie-off - its gate-contact V0 lands on LIG
                                # AND on a LISD tail that runs down into the
                                # via row, so gate and source/drain are one
                                # node.  Harmless on a dead guard rail; on
                                # the live column-32 wordline it is a diode
                                # pull-down (gate = drain = WL, source = VSS)
                                # that fights the driver (4 per side, 12
                                # drivers instead of 8, WL never settles).
                                # Cut the LISD across the slot's full width
                                # at every GATE-contact V0 row in the slot
                                # (V0 touching LIG; the slot's plain S/D
                                # contact vias - V0 on LISD only - must stay,
                                # cutting them floats 8 vias and leaves LISD
                                # slivers: chain 5).  A partial clip would
                                # leave a sub-width sliver; the SDT-covered
                                # contact above/below stays.
                                v0_3 = kdb.Region()
                                for sp3 in tc.shapes(layout.layer(18, 0)).each():
                                    if not sp3.is_text():
                                        v0_3.insert(sp3.polygon)
                                lig_3 = kdb.Region()
                                for sp3 in tc.shapes(layout.layer(16, 0)).each():
                                    if not sp3.is_text():
                                        lig_3.insert(sp3.polygon)
                                gate_v0 = (v0_3 & kdb.Region(slot)).interacting(lig_3)
                                for pv in gate_v0.each():
                                    vb = pv.bbox()
                                    reg3 -= kdb.Region(kdb.Box(
                                        slot.left, vb.bottom - 20,
                                        slot.right, vb.top + 20))
                            else:
                                reg3 -= kdb.Region(slot)
                            tc.shapes(li3).clear()
                            for poly3 in reg3.each():
                                tc.shapes(li3).insert(poly3)
                        new.insert(kdb.CellInstArray(
                            tc.cell_index(),
                            kdb.Trans(ct.rot % 4, ct.is_mirror(),
                                      kdb.Vector(ct.disp.x, ct.disp.y + dy))))
                        continue
                    ct = t * ch.cell_inst.trans
                    new.insert(kdb.CellInstArray(
                        ch.cell.cell_index(),
                        kdb.Trans(ct.rot % 4, ct.is_mirror(),
                                  kdb.Vector(ct.disp.x, ct.disp.y + dy))))
                gly2 = inst.cell.layout()
                for li2 in gly2.layer_indexes():
                    info2 = gly2.get_info(li2)
                    dli2 = layout.layer(info2.layer, info2.datatype)
                    for shp2 in inst.cell.shapes(li2).each():
                        if shp2.is_text():
                            continue
                        G("guard").shapes(dli2).insert(
                            (t * shp2.polygon).transformed(
                                kdb.Trans(kdb.Vector(0, dy))))
                continue
            if dy:
                ci = kdb.CellInstArray(
                    inst.cell.cell_index(),
                    kdb.Trans(t.rot, t.is_mirror(),
                              kdb.Vector(t.disp.x, t.disp.y + dy)))
        new.insert(ci)

    # SEAM STRIP: band-direct content below the guard row in the control
    # x-range (staggered v3/m4/v4 connector jogs, riser feet, m3 stubs,
    # and the texts labeling the lower stack) belongs electrically to the
    # LOWER stack - it all slides down with it.  Band m5 vertical(s)
    # crossing the strip upward stay put and get a same-x M5 extension
    # across the growth (M5 is not a macro routing layer).
    SEAM_LO, SEAM_HI = -300, 3200           # band frame
    # shapes that CONTACT guard metal on their own layer are exits /
    # guard-rail structures - the feedthrough architecture owns them and
    # they must NOT ride down with the seam strip
    guard_reg = {}
    for inst in band_src.each_inst():
        if (inst.cell.name == "dummy_vertical_array_X64"
                and inst.cell_inst.trans.disp.y < 20000):
            t3 = inst.cell_inst.trans
            for gname, (gl, gd) in [("m1", (19, 0)), ("m2", (20, 0)),
                                    ("m3", (30, 0))]:
                gli = layout.layer(gl, gd)
                guard_reg.setdefault(gname, kdb.Region())
                guard_reg[gname] += kdb.Region(
                    inst.cell.begin_shapes_rec(gli)).transformed(t3)
    GNAME = {(19, 0): "m1", (20, 0): "m2", (30, 0): "m3"}

    shifted_boxes = {}                    # metal (l,0) -> [Box] that moved

    def _stays(l, d, sh):
        if sh.is_text():
            # a label moves iff the metal under it moved: below the old
            # guard always moves; in the widened strip only if a shifted
            # shape of the matching metal layer contains the point
            tx, ty = sh.text_trans.disp.x, sh.text_trans.disp.y
            if not (1296 < tx < 38880):
                return True
            if ty < 2462:
                return False
            if ty <= SEAM_HI:
                for bb in shifted_boxes.get((l, 0), []):
                    if bb.left <= tx <= bb.right and bb.bottom <= ty <= bb.top:
                        return False
            return True
        b = sh.bbox()
        if not (SEAM_LO <= b.bottom and b.top <= SEAM_HI
                and b.right > 1296 and b.left < 38880):
            return True
        greg = guard_reg.get(GNAME.get((l, d)))
        if greg is not None and not (kdb.Region(kdb.Box(b)) & greg).is_empty():
            return True                       # guard-contact: stays
        shifted_boxes.setdefault((l, 0), []).append(b)
        return False
    for li in layout.layer_indexes():
        info = layout.get_info(li)
        for sh in band_src.shapes(li).each():
            if not sh.is_text():
                _stays(info.layer, info.datatype, sh)   # prime shifted_boxes
    _copy_shapes(G("band_furniture"), band_src, layout, _stays)
    down = kdb.Trans(kdb.Vector(0, -growth))
    for li in layout.layer_indexes():
        info = layout.get_info(li)
        for sh in band_src.shapes(li).each():
            if not _stays(0, 0, sh):
                if sh.is_text():
                    G("band_furniture").shapes(li).insert(
                        sh.text.transformed(down))
                else:
                    G("band_furniture").shapes(li).insert(
                        sh.polygon.transformed(down))
            elif (not sh.is_text()
                    and (info.layer, info.datatype) == (50, 0)):
                b = sh.bbox()
                if (SEAM_LO <= b.bottom <= SEAM_HI and b.top > SEAM_HI
                        and b.right > 1296 and b.left < 38880):
                    G("band_furniture").shapes(li).insert(kdb.Box(
                        b.left, b.bottom - growth,
                        b.right, b.bottom + 100))

    if bits > 32:
        # DUAL DECODER BANKS: copy-2 (columns 32..bits-1) one bank-width
        # outward on each side, sparse-cut to bits-32 columns; WLENA
        # rails overlap on placement (both banks fire until the control
        # gains the WLENA split); A<0..4> rails get drawn extensions.
        dec2 = build_decoder(layout, bits - 32)
        # TRIM the copy's io-side tail at local x >= TRIM_X.  Historically
        # 13176, which amputated the io-adjacent slice (columns 32-35) -
        # every boundary nearer the true edge "revived floating overlap
        # circuitry" (logs 14-21).  Log 32 found that circuitry was five
        # specific WELDS at the seam, not floats (east M3 riser, the WLENA
        # rail bridge, eleven PA/PB/PC rail bridges, PC<0>'s per-wordline
        # tap, slice-0's PA connector on the reference's vdd! edge M3),
        # all fixed by the sever/reconnect geometry below, so the default
        # is now the true boundary 13824 and all 64 columns decode.
        # the cell is 1296 wider than its 32-column span, and everything
        # past the column-pitch boundary (13824) lands ON the reference
        # decoder.  The old boundary 13176 amputated the copy's innermost
        # 1.5 slices - wl<32>/<33> lost their DRIVERS (found as dead hi
        # wordlines at exactly addr 128+).  Select m2/m1 rails still pass
        # through for the A<i> distribution and supply continuity.
        dec2t = layout.create_cell(f"decoder_ext_{bits}")
        dec2t.copy_tree(dec2)
        dec2t.flatten(-1)
        # io-slice trim boundary (Log 32).  DEFAULT 13824 = the true
        # column-pitch boundary: the io-adjacent slice (columns 32-35) is
        # KEPT, and its per-decoder rails are severed at the seam below.
        # 13176 (the frozen 53/64 trim) amputated that slice; legacy64
        # pins it so the frozen state still reproduces exactly.
        # SRAMFORGE_TRIM overrides either.
        TRIM_X = int(_os_dec.environ.get(
            "SRAMFORGE_TRIM", "13176" if "legacy64" in _abl_dec else "13824"))
        STRIP = kdb.Box(TRIM_X, -10**7, 10**7, 10**7)
        # pass-through rails (cell-local y): the five A<i> rails and the
        # WLENA rail on m2 - the ONLY strip content that survives
        RAILS = [(ry0 - 8076, ry1 - 8076) for (ry0, ry1) in A_RAIL_Y]
        # WLENA deliberately NOT passed through: the copies' rails are
        # severed from the reference rails and fed by wlenat_hi/wlenab_hi
        for li in layout.layer_indexes():
            info = layout.get_info(li)
            texts = [sh.text.dup() for sh in dec2t.shapes(li).each()
                     if sh.is_text()
                     and sh.text_trans.disp.x < TRIM_X]
            reg = kdb.Region()
            for sh in dec2t.shapes(li).each():
                if not sh.is_text():
                    reg.insert(sh.polygon)
            keep_in_strip = kdb.Region()
            if (info.layer, info.datatype) == (20, 0):
                for (ry0, ry1) in RAILS:
                    keep_in_strip += (reg & kdb.Region(kdb.Box(
                        TRIM_X, ry0, 10**7, ry1)))
            elif (info.layer, info.datatype) == (19, 0):
                # power rails pass through too (same row grid as the
                # reference tap they overlap -> same-net merge); severing
                # them orphaned the copy's supply mesh (vss islands)
                instrip = reg & kdb.Region(kdb.Box(TRIM_X, -10**7,
                                                   10**7, 10**7))
                for poly4 in instrip.each():
                    if poly4.bbox().width() > 800:
                        keep_in_strip.insert(poly4)
            reg -= kdb.Region(STRIP)
            reg += keep_in_strip
            # PER-DECODER RAIL SEAM SEVER (Log 32): when the io-tail is kept
            # (TRIM_X > 13176) the copy's twelve per-decoder m2 rails (PA,
            # PB, PC, WLENA) run to the cell edge (local 13824) and land on
            # the reference decoder's identical-y rails (reference edge =
            # local 13744): WLENA merges lo/hi enables; the PA rails bridge
            # PA<k> to PA<k+4> and every upper-16 column double-fires its
            # A<4> twin.  The old 13176 trim gap WAS the sever for all of
            # them.  Only A<0..4> and power are meant to pass through, and
            # the A rails stop at x=7128 inside the cell anyway (the band
            # draws their extensions).  Cut every rail row east of slice-0's
            # own taps (all at local x <= 13496) and short of the reference
            # edge: the slice stays fed, no bridge survives.
            # Rows: (cut_x, y0, y1).  ALL thirteen per-decoder rails (PA/PB/PC/
            # WLENA + slice-0's PA<0> strap) are cut on the COPY - the m2 plus
            # any via left orphaned on the removed segment.  Nine rows cut at
            # local 13560: every slice-level tap (PA/PB/WLENA) sits at
            # x <= 13496 and the per-wordline PC<1>/PC<3> taps are further
            # west.  The middle group PB<2>/PC<0>/PC<2>/PB<3> is cut at 13660
            # because of wl<0>'s per-wordline PC<0> tap: its gate M1 is a
            # C-shaped comb whose only material on the PC<0> row is x 13576..
            # 13704 (plus the leg 13704..13776 the original via sat on, right
            # at the cell edge), interleaved with a NEIGHBORING comb (another
            # net) that owns x 13432..13504 on the same row - a via there
            # welds PC<0> to it (tried: 48/128).  So the tap via moves to
            # 13580..13652, the rail runs to 13660 to cover it, and the other
            # three middle rows are cut at the same x so no tip protrudes past
            # a 72-dbu neighbor (M2.S.2).  Gaps to the reference rails at
            # 13824: 264 and 164, both >= 124 (M2.S.5).  The next rows out
            # (PC<3> at 9216, PA<0> at 10944) are 720/432 away: no cascade.
            # Nothing on the reference side is touched: the outermost
            # wordline at EACH end of the cell taps its PC rail right at the
            # edge (wl<30>'s PC<2> tap is the v1 at reference-local 48..120),
            # so reference tips must never be trimmed.
            SEAM_RAIL_ROWS = ((13560, 8784, 8856), (13560, 8928, 9000),
                              (13560, 9072, 9144), (13560, 9216, 9288),
                              (13660, 10008, 10080), (13660, 10152, 10224),
                              (13660, 10296, 10368), (13660, 10440, 10512),
                              (13560, 10944, 11016), (13560, 11232, 11304),
                              (13560, 11376, 11448), (13560, 11520, 11592),
                              (13560, 11088, 11160))   # slice-0's PA<0> strap
            if (TRIM_X > 13176 and (info.layer, info.datatype)
                    in ((20, 0), (21, 0), (25, 0))):
                for (cx, sy0, sy1) in SEAM_RAIL_ROWS:
                    reg -= kdb.Region(kdb.Box(cx, sy0 - 30, 13900, sy1 + 30))
            if TRIM_X > 13176 and (info.layer, info.datatype) == (21, 0):
                # wl<0>'s relocated PC<0> tap (see the note above)
                reg += kdb.Region(kdb.Box(13580, 10152, 13652, 10224))
            if TRIM_X > 13176 and (info.layer, info.datatype) == (20, 0):
                # SLICE-0 PA RECONNECT (Log 32): slice-0's PA<0> reached its
                # rail through an M3 connector at local x 13716..13788 (v2 at
                # the rail row, v2 at the strap row).  The rail cut at 13560
                # leaves that connector dead, and its M3 overlaps the
                # reference decoder's floating, vdd!-labeled edge M3 (band
                # 1648..1692, its local -80..-36 margin) -> the copy's slice-0
                # select inherited a stray VDD label and was forced high: the
                # slice-0/slice-4 twin-fire.  Bridge strap (y 11088) to the
                # PA<0> rail row (y 11016) with M2 west of everything, where
                # both are present and the gap is empty.  The fill spans the
                # strap's whole surviving length (13400..13560): a narrower
                # jog left 30- and 50-wide notches, 72 deep, between itself
                # and the rail/strap edges (two M2.S.2 tip-to-side markers).
                reg += kdb.Region(kdb.Box(13400, 11010, 13560, 11095))
            if TRIM_X > 13176 and (info.layer, info.datatype) in ((30, 0), (25, 0)):
                # remove the dead M3 connector (local y 10884..11216) and its
                # two v2s - the weld hop - with no leftover slivers
                reg -= kdb.Region(kdb.Box(13700, 10850, 13900, 11250))
            # PC RECEIVER: earlier builds CUT the copy's PC<1> generator
            # transistors here (a workaround for the position-1 deaths).
            # Log 27/28 proved that wrong: the generator is intact and
            # A<0>/A<1>-fed; the real defect was the PC<1> weld (the _hi_n
            # feed + seam bridges below).  Default KEEPS the generator so
            # the copy decodes all four column positions.  Restore the old
            # cut with SRAMFORGE_ABLATE=legacy64 (all three workarounds
            # toggle together; they only make sense as a set).
            if ((info.layer, info.datatype) in ((11, 0), (7, 0))
                    and "legacy64" in _abl_dec):
                reg -= kdb.Region(kdb.Box(5400, 2240, 6470, 3160))
            dec2t.shapes(li).clear()
            for poly in reg.each():
                dec2t.shapes(li).insert(poly)
            for t2 in texts:
                dec2t.shapes(li).insert(t2)
        new.insert(kdb.CellInstArray(
            dec2t.cell_index(),
            kdb.Trans(kdb.Vector(DEC_R0_ANCHOR[0] - DEC_SPAN,
                                 DEC_R0_ANCHOR[1]))))
        new.insert(kdb.CellInstArray(
            dec2t.cell_index(),
            kdb.Trans(2, True, kdb.Vector(DEC_M90_ANCHOR[0] + DEC_SPAN,
                                          DEC_M90_ANCHOR[1]))))
        # (The Option 1 "seam slice" - a fresh driver slice placed in the
        # clear space outboard of each copy, with M4 wordline routes and
        # short input wires - was built through Phase 3 here and then
        # SUPERSEDED: Log 32 fixed columns 32-35 IN PLACE by severing the
        # seam welds above.  Its code lives in git history; see Logs 31-32.)
        for (ry0, ry1) in A_RAIL_Y:
            _box(G("ext64"), layout, "m2", 1736 - DEC_SPAN - 100, ry0,
                 1736 + 100, ry1)
            _box(G("ext64"), layout, "m2", 38440 - 100, ry0,
                 38440 + DEC_SPAN + 100, ry1)
        # EXTENSION GUARD: replicate the guard rows' children (and their
        # direct shapes) from a one-bank-wide window at each array edge,
        # shifted outward; the bottom guard's copies ride down with the
        # stack like the original
        for inst in band_src.each_inst():
            if inst.cell.name != "dummy_vertical_array_X64":
                continue
            t = inst.cell_inst.trans
            sh_y = -growth if t.disp.y < 20000 else 0
            for ch in inst.cell.each_inst():
                ct = t * ch.cell_inst.trans
                if ch.cell.name == "dummy_corner_v2":
                    # outer caps move to the NEW outer ends
                    sgn = -1 if ct.disp.x < 20000 else 1
                    new.insert(kdb.CellInstArray(
                        ch.cell.cell_index(),
                        kdb.Trans(ct.rot % 4, ct.is_mirror(),
                                  kdb.Vector(ct.disp.x + sgn * DEC_SPAN,
                                             ct.disp.y + sh_y))))
                    continue
                for sgn, (w0, w1) in ((-1, (1296, 1296 + DEC_SPAN)),
                                      (1, (38880 - DEC_SPAN, 38880))):
                    if w0 <= ct.disp.x < w1:
                        new.insert(kdb.CellInstArray(
                            ch.cell.cell_index(),
                            kdb.Trans(ct.rot % 4, ct.is_mirror(),
                                      kdb.Vector(ct.disp.x + sgn * DEC_SPAN,
                                                 ct.disp.y + sh_y))))
            for li in layout.layer_indexes():
                info = layout.get_info(li)
                for shp in inst.cell.each_shapes(li) if False else []:
                    pass
            # guard-cell direct shapes (rails): region-copy per window
            gly = inst.cell.layout()
            for li in gly.layer_indexes():
                info = gly.get_info(li)
                dli = layout.layer(info.layer, info.datatype)
                for shp in inst.cell.shapes(li).each():
                    if shp.is_text():
                        continue
                    b = (t * shp.polygon).bbox()
                    # supply-row via stubs (v1/v2 at the rail row just
                    # above the WLENA rail, band y 19920-19992) must not
                    # replicate: in the copy zone the stub's stack ties
                    # the copy's severed WLENA rail into the VDD rail
                    # one row up (found as the bits=64 copy-rail-to-VDD
                    # weld).  Metal layers still replicate - only the
                    # layer-hop at this exact row is dropped.
                    if (info.layer in (21, 25) and info.datatype == 0
                            and b.bottom < 20010 and b.top > 19900):
                        continue
                    for sgn, (w0, w1) in ((-1, (1296, 1296 + DEC_SPAN)),
                                          (1, (38880 - DEC_SPAN, 38880))):
                        if b.left >= w0 and b.right <= w1:
                            G("ext64").shapes(dli).insert(
                                (t * shp.polygon).transformed(kdb.Trans(
                                    kdb.Vector(sgn * DEC_SPAN, sh_y))))
        # EXTENSION WORDLINE VERTICALS: clone the reference per-column m3
        # (guard-top 2462 .. band-top 35290) at every extension column;
        # bottoms extended down to the SHIFTED guard
        n_ext = bits - 32
        for i in range(n_ext):
            for colx in ((DEC_R0_ANCHOR[0] - DEC_SPAN) + 13608 - 432 * i,
                         (DEC_M90_ANCHOR[0] + DEC_SPAN) - 13608 + 432 * i):
                # innermost pair (i=0) passes through the corner slot and
                # reaches the lower colgroup overlap directly.  Only the
                # GAP segments are drawn - the decoder copies carry their
                # own internal wl columns for band y 7988..35190, and a
                # full-height wire would collide with copy-1's edge
                # furniture at the innermost column
                ybot = (-19000 if i == 0
                        else 2462 - growth + 700)
                _box(G("ext64"), layout, "m3", colx - 36, ybot,
                     colx + 36, 35290)

    # the one-box macro
    src = kdb.Layout()
    src.read(str(V2_ASSETS / f"control_onebox{_sfx}.gds"))
    macro = layout.create_cell("control_onebox")
    macro.copy_tree(src.cell("control_sram_2kB"))
    mb = _ctl2band(*onebox_origin_ctl(variant))
    import os as _os
    if "macro" not in set(_os.environ.get("SRAMFORGE_ABLATE", "").split(",")):
        new.insert(kdb.CellInstArray(macro.cell_index(),
                                     kdb.Trans(kdb.Vector(*mb))))

    # ablation switches (debug): comma-separated SRAMFORGE_ABLATE
    import os
    _abl = set(os.environ.get("SRAMFORGE_ABLATE", "").split(","))

    # io-net GLUE: the original vertical interface stacks (v3/m4/v4/m5
    # tails above the pin layer) reconnect the band's untouched control
    # distribution wires to the port pads - without them every ysel/sae/
    # wrena band wire is an OPEN (found by the analog write sweep; the
    # structural rungs don't probe those classes)
    glue = json.loads((V2_ASSETS / f"control_onebox_glue{_sfx}.json").read_text())
    for _net, boxes in glue.items():
        for cname, gx0, gy0, gx1, gy1 in boxes:
            bx0, by0 = _ctl2band(gx0, gy0)
            bx1, by1 = _ctl2band(gx1, gy1)
            _box(G("glue"), layout, cname, bx0, by0, bx1, by1)

    # synthetic-port pad metal (all bits): m3 pads + v2 down to the m2
    # plane + the bank-level ADDRESS<8> label
    for (px0s, py0s, px1s, py1s, lbl) in (
            (33864, -736, 33936, -664, "ADDRESS<8>"),
            (33864, -3036, 33936, -2964, "wlenat_hi"),
            (70164, -3036, 70236, -2964, "wlenab_hi"),
            (34264, -3216, 34336, -3144, "wlenat_hi_n"),
            (69764, -3216, 69836, -3144, "wlenab_hi_n")):
        bx0s, by0s = _ctl2band(px0s, py0s)
        bx1s, by1s = _ctl2band(px1s, py1s)
        _box(G("entry_pads"), layout, "m3", bx0s, by0s, bx1s, by1s)
        if lbl:
            G("entry_pads").shapes(layout.layer(30, 251)).insert(kdb.Text(
                lbl, kdb.Trans(kdb.Vector((bx0s + bx1s) // 2,
                                          (by0s + by1s) // 2))))
    # WLENA ENTRY (all bits): the lo-enable pads must contact the
    # decoders' m2 WLENA rails where the rails cross the pad columns -
    # the original control's entry vias (and most of the pad metal)
    # died with the bar, leaving the rails floating and every wordline
    # dead.  Redraw the full m3 pad column + the v2 entry via.
    WLENA_RAIL_Y = (11232, 11304)                    # ctl frame
    _abl_early = set(os.environ.get("SRAMFORGE_ABLATE", "").split(","))
    for (wx0, wy0, wx1, wy1, wlbl) in () if "wlenaentry" in _abl_early else (
            (48872, 8404, 48944, 11324, "wlenat"),
            (55208, 5020, 55280, 11324, "wlenab")):
        ex0, ey0 = _ctl2band(wx0, wy0)
        ex1, ey1 = _ctl2band(wx1, wy1)
        _box(G("entry_pads"), layout, "m3", ex0, ey0, ex1, ey1)
        _, ry0 = _ctl2band(0, WLENA_RAIL_Y[0])
        _, ry1 = _ctl2band(0, WLENA_RAIL_Y[1])
        _box(G("entry_pads"), layout, "v2", ex0, ry0, ex1, ry1)
        # bank-level 251 label: the macro's own pin texts live on
        # datatype 2, invisible to the extraction - without this the
        # functional bench can never watch the enables
        G("entry_pads").shapes(layout.layer(30, 251)).insert(kdb.Text(
            wlbl, kdb.Trans(kdb.Vector((ex0 + ex1) // 2,
                                       (ey0 + ey1) // 2))))
    if bits > 32:
        # hi-enable delivery needs BOTH polarities per copy, like the
        # reference: the _hi pads (active-high AND) feed the WLENA
        # rails; the _hi_n pads (INV) feed the PC<1> verticals whose
        # 16-finger stage strobes each column pair.
        # RAIL feeds (active-high): EAST riser at 38916 + m2 feed;
        # WEST M5 riser at the pad x + stack at band 960.
        # Log 32 (2026-09-19): the EAST riser on M3 was the class-A
        # "crowbar".  At TRIM=13824 the revived io-tail's own M3 vertical
        # (band x 38916..38988, y 17616..20435) overlaps the M3 riser
        # (38880..38952) -> M3-on-M3 short -> the tail's enable stage
        # drives wlenab_hi against the control and welds wlenab into it.
        # The WEST is on M5 and never had this.  The east now mirrors the
        # west by default: M5 riser, then descend to the m2 feed at
        # x=39220, clear of the tail's M3 (the descent needs an m3 stub,
        # which must not sit at riser_bx).  legacy64 restores the M3 riser.
        for (padx, riser_bx, feed0, feed1) in (
                (70200, 38916, 38844, 39196),):
            bxp, byp = _ctl2band(padx, -3000)
            _box(G("hi_feeds"), layout, "v3", bxp - 32, byp - 32, bxp + 32, byp + 32)
            # landing pads stay FLUSH with the strap's 96-tall M4 plate
            # (byp +/- 48): a taller pad protrudes 16 above and below it
            # as a 128-wide tab, and M4's horizontal min width is 176
            # (M4.W.5, four markers on the west pad in Log 32 round 2)
            _box(G("hi_feeds"), layout, "m4", min(bxp, riser_bx) - 64,
                 byp - 48, max(bxp, riser_bx) + 64, byp + 48)
            if "legacy64" not in _abl_early:
                # descent x: >= 100 dbu clear of the tall M3 ending at 39132
                # (39220 sat 48 away -> M3.S.2); v4 = 96x96 (V4.W.1 is 24 nm)
                # with M4/M5 enclosure, unlike the original 72-wide vias.
                DX = 39290
                _box(G("hi_feeds"), layout, "v4", riser_bx - 48, byp - 48,
                     riser_bx + 48, byp + 48)
                _box(G("hi_feeds"), layout, "m5", riser_bx - 56, byp - 64,
                     riser_bx + 56, 19408)
                _box(G("hi_feeds"), layout, "v4", riser_bx - 48, 19296,
                     riser_bx + 48, 19392)
                _box(G("hi_feeds"), layout, "m4", riser_bx - 64, 19280,
                     DX + 48, 19408)
                _box(G("hi_feeds"), layout, "v3", DX - 32, 19300, DX + 32, 19388)
                _box(G("hi_feeds"), layout, "m3", DX - 40, 19272, DX + 40, 19416)
                _box(G("hi_feeds"), layout, "v2", DX - 32, 19312, DX + 32, 19376)
                _box(G("hi_feeds"), layout, "m2", feed0, 19308,
                     max(feed1, DX + 60), 19380)
            else:
                _box(G("hi_feeds"), layout, "v3", riser_bx - 32, byp - 32,
                     riser_bx + 32, byp + 32)
                _box(G("hi_feeds"), layout, "m3", riser_bx - 36, byp - 48,
                     riser_bx + 36, 19380)
                _box(G("hi_feeds"), layout, "v2", riser_bx - 32, 19312,
                     riser_bx + 32, 19376)
                _box(G("hi_feeds"), layout, "m2", feed0, 19308, feed1, 19380)
        bxp, byp = _ctl2band(33900, -3000)             # band (1696, 5076)
        # (v4 96x96 with M4/M5 enclosure - the original 72-wide v4s were
        # the baseline's V4.W.1 / V4.AUX.1b flags)
        _box(G("hi_feeds"), layout, "v3", bxp - 32, byp - 32, bxp + 32, byp + 32)
        _box(G("hi_feeds"), layout, "m4", bxp - 64, byp - 48, bxp + 64, byp + 48)
        _box(G("hi_feeds"), layout, "v4", bxp - 48, byp - 48, bxp + 48, byp + 48)
        _box(G("hi_feeds"), layout, "m5", bxp - 56, byp - 64, bxp + 56, 19408)
        _box(G("hi_feeds"), layout, "v4", bxp - 48, 19296, bxp + 48, 19392)
        _box(G("hi_feeds"), layout, "m4", 960 - 48, 19280, bxp + 64, 19408)
        _box(G("hi_feeds"), layout, "v3", 960 - 32, 19300, 960 + 32, 19388)
        _box(G("hi_feeds"), layout, "m3", 960 - 40, 19272, 960 + 40, 19416)
        _box(G("hi_feeds"), layout, "v2", 960 - 32, 19312, 960 + 32, 19376)
        _box(G("hi_feeds"), layout, "m2", -748, 19308, 1422, 19380)
        # PC<1> SEAM BRIDGES: the four PC lines select the four columns
        # of each slice; PC<1>'s copy segment ends 4 dbu short of the
        # reference segment (widen registration), so every position-1
        # column in the copies (37, 41, 45, ... ) never selects.  Two
        # 30-dbu m2 bridges close the gap - the exact analogue of the
        # shared A<0..4> rail extensions.
        # PC<1> SEAM BRIDGES (legacy): these m2 boxes padded the copy's
        # PC<1> up against the reference decoder's PC<1>.  Log 27/28: each
        # decoder generates its own PC<1> locally, so the copy must stay
        # ISOLATED - the bridges are off by default.  legacy64 restores.
        if "legacy64" in _abl_early:
            _box(G("ext64"), layout, "m2", 1710, 17148, 1740, 17220)   # west
            _box(G("ext64"), layout, "m2", 38430, 17148, 38460, 17220)  # east
        # PC feeds (active-low, from the _n pads at ctl (34300/69800,
        # -3180) - DIFFERENT x from the _hi pads: _strap's pad nub is
        # +/-192 tall, so same-x pads closer than 384 in y weld through
        # their nubs (that was take two's failure).  The M5 lateral at
        # band y 4848-4944 stays BELOW the west rail-riser's bottom
        # (5028) - then M5 vertical at the PC-vertical x and a v4/m4/v3
        # stack down onto the copy's PC<1> m3 at band y 15442-15558
        # (mid-vertical, clear of the A/PC rail rows; the m4 pad
        # clears the neighbor columns by 12 nm).
        # Log 27/28: this _hi_n feed WELDS the copy's PC<1> to the hi-enable
        # complement net (wlenat_hi_n/wlenab_hi_n) and is unnecessary - the
        # copy's own local generator drives PC<1>.  Off by default; the
        # _hi_n macro pads it fed are now vestigial (drop them in the 2-port
        # macro cleanup / re-vendor).  legacy64 restores the feed.
        if "legacy64" in _abl_early:
            # WEST: direct lateral at pad depth (band y 4848-4944) - clear
            # of the west rail-riser bottom (5028) and everything else.
            bxp, byp = _ctl2band(34300, -3180)
            bcx = (-5292 + -5220) // 2
            _box(G("hi_feeds"), layout, "v3", bxp - 32, byp - 32,
                 bxp + 32, byp + 32)
            _box(G("hi_feeds"), layout, "m4", bxp - 48, byp - 48,
                 bxp + 48, byp + 48)
            _box(G("hi_feeds"), layout, "v4", bxp - 36, byp - 48,
                 bxp + 36, byp + 48)
            _box(G("hi_feeds"), layout, "m5", bcx - 48, byp - 48,
                 bxp + 48, byp + 48)
            _box(G("hi_feeds"), layout, "m5", bcx - 48, byp - 48,
                 bcx + 48, 15598)
            _box(G("hi_feeds"), layout, "v4", -5296, 15454, -5216, 15546)
            _box(G("hi_feeds"), layout, "m4", -5352, 15442, -5160, 15558)
            _box(G("hi_feeds"), layout, "v3", -5292, 15458, -5220, 15542)
            # EAST: a lateral at pad depth crossed the _hi strap's M5 riser
            # (landing 70000, riser top ctl -2808) - same-layer weld, take
            # three's east failure.  Hop route instead: m4 nub east to ctl
            # 69850, M5 up to ctl y -2000 (above every east-belt riser top),
            # M5 lateral at -2000 to the PC vertical, then down the stack.
            bxp, byp = _ctl2band(69800, -3180)
            hopx, _ = _ctl2band(69850, 0)
            _, hy = _ctl2band(0, -2000)
            bcx = (45396 + 45468) // 2
            _box(G("hi_feeds"), layout, "v3", bxp - 32, byp - 32,
                 bxp + 32, byp + 32)
            _box(G("hi_feeds"), layout, "m4", bxp - 48, byp - 48,
                 hopx + 48, byp + 48)
            _box(G("hi_feeds"), layout, "v4", hopx - 36, byp - 48,
                 hopx + 36, byp + 48)
            _box(G("hi_feeds"), layout, "m5", hopx - 48, byp - 48,
                 hopx + 48, hy + 48)
            _box(G("hi_feeds"), layout, "m5", hopx - 48, hy - 48,
                 bcx + 48, hy + 48)
            _box(G("hi_feeds"), layout, "m5", bcx - 48, hy - 48,
                 bcx + 48, 15598)
            _box(G("hi_feeds"), layout, "v4", 45392, 15454, 45472, 15546)
            _box(G("hi_feeds"), layout, "m4", 45336, 15442, 45528, 15558)
            _box(G("hi_feeds"), layout, "v3", 45396, 15458, 45468, 15542)


    # generated interface: straps, exit extensions
    pins = json.loads((V2_ASSETS / f"control_onebox_pins{_sfx}.json").read_text())
    for port, rec in pins.items():
        if rec.get("strap") and "straps" not in _abl:
            _strap(G("straps"), layout, rec["strap"], probe_cell=new)
    if variant == "s8":
        # the sixteen select feeds (left array's yselt family, then the
        # right array's yselb family): the strap riser of each port is
        # extended along its line column to both stack seams, and the
        # line gets its bank-level label (the macro's own pin texts are
        # invisible to the extraction)
        for j, lx in enumerate(select_columns):
            fam = "yselt" if j < 8 else "yselb"
            port = f"{fam}{'' if j % 2 == 0 else 'n'}_{4 + (j % 8) // 2}"
            strap = pins[port]["strap"]
            bx, _ = _ctl2band((strap["to_box"][1] + strap["to_box"][3]) // 2, 0)
            assert bx == lx + 48, (
                f"{port}: landing at band x {bx}, line column {lx}..{lx + 96}"
                f" (pnr/gen_inputs.py S8_PORTS and generator.SELECT_LINE_X "
                f"disagree)")
            fy0, fy1 = s8_feed_y(variant)
            _box(G("select_feeds"), layout, "m5", lx, fy0, lx + 96, fy1)
            G("select_feeds").shapes(layout.layer(50, 251)).insert(kdb.Text(
                port.replace("_", "<") + ">",
                kdb.Trans(kdb.Vector(lx + 48, fy1 - 500))))
    # FEEDTHROUGHS: each exit wire is routed THROUGH the macro by
    # OpenROAD as a two-pin net (ft_i); the band only draws short
    # same-layer stubs from the original wire ends onto the pin boxes.
    exts = json.loads((V2_ASSETS / f"control_onebox_ext{_sfx}.json").read_text())
    # stubs back the FULL pin boxes (top -5650..-4700, bottom
    # -26310..-25800 ctl): wherever the router lands on a box it touches
    # real metal (stub, exit wire, or shifted guard vertical)
    FT_TOP_LO = -5650 + 8076                              # band frame
    FT_BOT_HI = onebox_origin_ctl(variant)[1] + 1200 + 8076
    for cname, x0, yb_new, x1, yb_old, pol, cx0, cx1 in exts:
        if "ftstubs" in _abl or (pol and "polstubs" in _abl):
            continue
        w0, w1 = x0 + CTL_DISP[0], x1 + CTL_DISP[0]
        _, by_old = _ctl2band(0, yb_old)
        _, by_new = _ctl2band(0, yb_new)
        if x1 - x0 <= 200:
            # narrow vertical exits: stub backs the tall top pin box;
            # wide horizontal exits need none (the pin is the wire)
            _box(G("ft_stubs"), layout, cname, w0, FT_TOP_LO, w1,
                 by_old + 160)
        # bottom stub only at the verified guard-contact column (wide
        # horizontal exits span BOTH polarities of guard verticals)
        _box(G("ft_stubs"), layout, cname, cx0 + CTL_DISP[0], by_new,
             cx1 + CTL_DISP[0], FT_BOT_HI)

    # PDN hookup: the macro's M5 power stripes reach the die top; tie
    # them to the forest supply straps with polarity-matched V4s
    # measured from the routed DEF: pdngen puts VSS at the offset and VDD
    # at offset+spacing+width (stripe CENTERS in control-frame dbu)
    STRIPES = [(37012, "VSS"), (39492, "VDD")]
    sup_straps = json.loads(
        (V2_ASSETS / f"control_onebox_supply{_sfx}.json").read_text())["straps"]
    for rec in sup_straps:
        if "v4hook" in _abl:
            break
        if len(rec) < 6 or rec[0] != "m4" or rec[5] is None:
            continue
        _, sx0, sy0, sx1, sy1, spol = rec
        for stx, stpol in STRIPES:
            if stpol != spol or not (sx0 < stx < sx1):
                continue
            bx, by0 = _ctl2band(stx, sy0)
            _, by1 = _ctl2band(0, sy1)
            cy = (by0 + by1) // 2
            _box(G("supply"), layout, "v4", bx - 48, cy - 48,
                 bx + 48, cy + 48)

    # supply glue: the original control's forest-zone straps + via cells
    sup = json.loads((V2_ASSETS / f"control_onebox_supply{_sfx}.json").read_text())
    for cname, x0, y0, x1, y1, _pol in sup["straps"]:
        if "supglue" in _abl:
            break
        bx0, by0 = _ctl2band(x0, y0)
        bx1, by1 = _ctl2band(x1, y1)
        _box(G("supply"), layout, cname, bx0, by0, bx1, by1)
    ROT = {"r0": (0, False), "r90": (1, False), "r180": (2, False),
           "r270": (3, False), "m0": (0, True), "m45": (1, True),
           "m90": (2, True), "m135": (3, True)}
    for cellname, x, y, tstr in sup["via_cells"]:
        if "supglue" in _abl:
            break
        if bits > 32:
            # the innermost extension columns (one per side) run through
            # the die's forest - a supply via cell there would bridge
            # the strap onto them (VSS-to-wl<32> weld); the strap has
            # plenty of other via cells
            bx0v, _ = _ctl2band(x, 0)
            if any(c0 - 400 < bx0v < c1 + 400
                   for (c0, c1) in ((1476, 1548), (38628, 38700))):
                continue
        c = layout.cell(cellname)
        if c is None:
            continue
        rot, mir = ROT.get(tstr.split()[0], (0, False))
        bx, by = _ctl2band(x, y)
        G("supply").insert(kdb.CellInstArray(
            c.cell_index(), kdb.Trans(rot, mir, kdb.Vector(bx, by))))

    if bits > 32:
        # FINAL PASS: no via may land on the innermost extension columns
        # where they cross the die's forest - vendored glue stacks there
        # bridge the supply straps onto them (VSS-to-wl<32> weld).
        # Sweeps every display group plus the (now nearly empty) top.
        _sweep = [new] + list(_groups.values())
        for cell_s in _sweep:
            for (lw, dw) in ((21, 0), (25, 0), (35, 0), (45, 0)):
                liw = layout.layer(lw, dw)
                regw = kdb.Region()
                texts_w = [sh.text.dup() for sh in cell_s.shapes(liw).each()
                           if sh.is_text()]
                for sh in cell_s.shapes(liw).each():
                    if not sh.is_text():
                        regw.insert(sh.polygon)
                for (c0, c1) in ((1456, 1568), (38608, 38720)):
                    regw -= kdb.Region(kdb.Box(c0, 3400, c1, 8500))
                cell_s.shapes(liw).clear()
                for poly in regw.each():
                    cell_s.shapes(liw).insert(poly)
                for t5 in texts_w:
                    cell_s.shapes(liw).insert(t5)
            # stray SUPPLY LABELS on the innermost columns (furniture
            # labels whose metal now merges with the extension columns)
            for lw in (19, 20, 30, 40, 50):
                liw = layout.layer(lw, 251)
                keep_t = []
                for sh in cell_s.shapes(liw).each():
                    if not sh.is_text():
                        continue
                    tx = sh.text_trans.disp.x
                    if (sh.text_string in ("vss!", "vdd!")
                            and any(c0 - 40 < tx < c1 + 40
                                    for (c0, c1) in ((1456, 1568),
                                                     (38608, 38720)))):
                        continue
                    keep_t.append(sh.text.dup())
                cell_s.shapes(liw).clear()
                for t5 in keep_t:
                    cell_s.shapes(liw).insert(t5)

    if bits < 32:
        _patch_sparse_band(new, layout, bits)
    return new


def _patch_sparse_band(band_cell: kdb.Cell, layout: kdb.Layout,
                       bits: int) -> None:
    """Close the two abutment gaps the sparse-bits scheme opens in band
    v2 (both found by the external KLayout deck at bits 16 and invisible
    to rung 3 before its 2026-09-19 hardening; docs/external_signoff.md):

    * dummy-row gate tails: the guard rows' dummy cells tile over all 64
      reference columns; each dummy gate runs 42 dbu past its GCUT and in
      the reference merges with the abutting bitcell's gate.  Over a
      DEAD column (no bitcell) the tail stands alone, an 80 x 42 dbu gate
      sliver (64 at bits 16).  Fix: a GCUT box over every such tail, so
      gate-minus-gcut ends flush with the cut.
    * the post-decode end tap's gate-contact V0 straddles the tap cell's
      boundary; in the reference the abutting slice's LIG (Local
      Interconnect Gate) strip covers the overhang, and band v2 does not
      place that slice.  Fix: a LIG box over the uncovered part of any V0
      that touches LIG but is not fully covered by LIG or LISD (Local
      Interconnect Source/Drain); M1 (Metal 1) already covers it.

    Both are measured on the assembled band rather than hard-coded, so
    they hold at every sparse width; at bits 32 the function is not
    called and the band stays byte-identical.
    """
    def region(l, d):
        return kdb.Region(band_cell.begin_shapes_rec(layout.layer(l, d)))
    # dead-column windows in band coordinates (band sits at bank x -864):
    # bank (0, live_anchor - 100) and its mirror about the io axis
    skip = 32 - bits
    live_anchor = 864 + skip * 432
    axis = 19236
    dead = kdb.Region()
    dead.insert(kdb.Box(0 + 864, -10**7, live_anchor - 100 + 864, 10**7))
    dead.insert(kdb.Box(2 * axis - live_anchor + 100 + 864, -10**7,
                        2 * axis + 864, 10**7))
    tails = (region(7, 0) - region(10, 0)).merged() & dead
    gcut_li = layout.layer(10, 0)
    n_tail = 0
    for p in tails.each():
        b = p.bbox()
        if b.height() <= 60 and b.width() <= 120:
            band_cell.shapes(gcut_li).insert(
                kdb.Box(b.left - 68, b.bottom - 20, b.right + 68, b.top + 20))
            n_tail += 1
    lig = region(16, 0).merged()
    # every gate contact overhangs its LIG by 1 nm (4 dbu) by construction
    # (the deck allows it too); only a real overhang counts
    cover = lig.sized(4) + region(17, 0).merged()
    lig_li = layout.layer(16, 0)
    n_lig = 0
    for p in region(18, 0).interacting(lig).each():
        rem = kdb.Region(p) - cover
        if rem.is_empty():
            continue
        # cover the via's full x extent at the touched LIG strip's own y
        # range (the strip is 64 tall, the via 72: the reference lets the
        # contact overhang its LIG by 1 nm top and bottom).  A box over
        # the bare part alone is 40 wide, under LIG's 64 minimum width;
        # a box at the via's height leaves 4-dbu steps whose vertical
        # edges sit 32 dbu from the uncontacted gate segment next door
        # (LIG.GATE.S.9B wants 68).  Matching the strip's height merges
        # into one clean strip whose right edge is 104 from that gate.
        strip = next(q for q in lig.each()
                     if not kdb.Region(q).interacting(kdb.Region(p)).is_empty())
        sb = strip.bbox()
        band_cell.shapes(lig_li).insert(
            kdb.Box(p.bbox().left, sb.bottom, p.bbox().right, sb.top))
        n_lig += 1
    # expected: 2 tails per dead column pair per guard row (2 rows x 2
    # sides) and one bare tap via per side
    assert n_tail == 8 * (skip // 2), \
        f"dummy gate tails: found {n_tail} at bits {bits}, expected {8 * (skip // 2)}"
    assert n_lig == 2, f"bare tap vias: found {n_lig} at bits {bits}"
