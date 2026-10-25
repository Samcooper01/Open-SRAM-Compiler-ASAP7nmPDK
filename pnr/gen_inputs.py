#!/usr/bin/env python3
"""Generate OpenROAD inputs v2: interior pins + band-overhead obstructions.

Pins: dbBPin boxes at the ORIGINAL pad positions/layers (interior pins),
so the band's wires drop onto the new macro exactly as they did onto the
old one - no jumpers.
Obstructions: every band-level m2/m3/m4/m5 shape crossing the macro
region (minus the pad boxes) becomes a routing obstruction, so the new
routing cannot collide with the band's overhead wiring.

Frame: P&R local (0,0) = control-frame (33500,-4720) dbu (0.25nm);
odb coordinates are nm (DEF units 1000/um): nm = (dbu - origin) * 0.25.
"""
import json
import pathlib

import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
# CONTROL VARIANT: SRAMFORGE_CTRL_VARIANT="" builds the standard one-box
# control; "s8" builds the eight-strip variant (scripts/patch_control_s8.py:
# a 3:8 column-select decode with eight more select ports).  A variant
# writes its P&R inputs and outputs beside the standard ones.
import os
VARIANT = os.environ.get("SRAMFORGE_CTRL_VARIANT", "")
SFX = f"_{VARIANT}" if VARIANT else ""
OUT = REPO / "pnr" / f"out{SFX}"
OUT.mkdir(exist_ok=True)

# ONE-BOX CONTROL DIE: full control width; the 4 old bar rows
# (control-y -4320..0) plus N_NEW_ROWS grown DOWNWARD into virgin space
# (band v2 slides the lower stack + guard row down by GROWTH).
# (the eight-strip variant grows six rows more: sramforge/band.py
# N_NEW_ROWS_VARIANT, the single source of the rule)
import sys as _sys0
_sys0.path.insert(0, str(REPO))
from sramforge.band import growth_for, onebox_origin_ctl   # noqa: E402
ROW = 1080
GROWTH = growth_for(VARIANT)                 # 21600 dbu standard
# die origin: five rows above the growth (the four old bar rows plus a
# guard row), so the CORE bottom (local 0.27 um = one site height) sits
# at ctl -25920 (standard) - ON the reference 1080-dbu row grid.
# Without this the floorplanner snaps the core up and every macro rail
# lands 680 dbu off the band's rail grid (VDD/VSS welds at the shifted
# guard tips).
ORIGIN = onebox_origin_ctl(VARIANT)          # control-frame dbu of P&R (0,0)
DIE_TOP_CTL = 400                            # control-y of the die top
# landing strip: one row-height band between core top and the bar
LAND_Y0, LAND_Y1 = -6200, -5400              # control-y of landings
BAND_OFF = (1296, ORIGIN[1] + 8076)          # band-frame coords of P&R (0,0)
# the floorplan the variant's P&R sources (pnr/control_pnr.tcl): the die
# spans ORIGIN..DIE_TOP_CTL in y, 33500..71084 in x (9.396 um); the core
# keeps the standard 1.99 um of top margin (landing strip + bar) and one
# site row at the bottom
DIE_H_UM = (DIE_TOP_CTL - ORIGIN[1]) / 4000
(OUT / "floorplan.tcl").write_text(
    f"initialize_floorplan -die_area {{0 0 9.396 {DIE_H_UM:.3f}}} \\\n"
    f"                     -core_area {{0.378 0.27 7.722 {DIE_H_UM - 1.99:.3f}}} \\\n"
    f"                     -site asap7sc7p5t\n")
LAYERS = {"m2": (20, 0), "m3": (30, 0), "m4": (40, 0), "m5": (50, 0)}
LNAME = {"m2": "M2", "m3": "M3", "m4": "M4", "m5": "M5"}
# M3 x-grid offset (nm): pnr/make_tracks_ctrl.tcl aligns the die grid
# with the band wordline-column grid (exits at 18+36k)
TRACK_OFF = 18
# Minimum separation (nm, so 4 tracks) between two landing plate centers
# in ONE landing row.  A plate is 320 dbu of M4 wide, so 3 tracks left a
# 112 dbu gap against the 157 dbu M4.S.2 horizontal floor and the router
# inherited the violation (one marker between sdel_4 and yselt_1 in the
# eight-strip route of 2026-09-20); 4 tracks give 256 dbu.
ROW_TRACK_SEP = 144


def to_nm_ctl(v, axis):
    return int(round((v - ORIGIN[axis]) * 0.25))


def to_nm_band(v, axis):
    return int(round((v - BAND_OFF[axis]) * 0.25))


d = json.load(open(REPO / "out" / "control_netlist.json"))
# synthetic ports (dual-decoder scheme): pads at the WLENA-rail depth so
# the band's feeds are straight m2 runs; address_8 also carries the
# bank-level ADDRESS<8> label (drawn by band.py)
d["io_geometry"]["address_8"] = [["m3", 33864, -736, 33936, -664]]
d["io_geometry"]["wlenat_hi"] = [["m3", 33864, -3036, 33936, -2964]]
d["io_geometry"]["wlenab_hi"] = [["m3", 70164, -3036, 70236, -2964]]
d["io_geometry"]["wlenat_hi_n"] = [["m3", 34264, -3216, 34336, -3144]]
d["io_geometry"]["wlenab_hi_n"] = [["m3", 69764, -3216, 69836, -3144]]
# RIGHT-ARRAY SELECTS (every variant): the yselb_k / yselbn_k ports
# (scripts/patch_control_yselb.py) drive the band's eight yselb risers,
# M5 columns at bank x 20520 + 192 i that cross the die (band y -21802..
# 37680).  Pad = an M5 box on the riser at the synthetic pad depth,
# landing at the riser's x: the strap is a direct M5 merge (band._strap,
# pad layer m5 at the landing column).  Riser order measured from the
# overlay labels: yselb<0..3> then yselbn<0..3>.
# Their eight risers are 192 dbu apart, so the eight landing plates (240
# wide) are spread along the risers instead of across the three landing
# rows: one 300-tall lane per port at 500 pitch from the top landing
# row down into the core (a plate can sit anywhere on its riser).  The
# lane of each port is chosen below (yselb_lanes), once the M4 obstacle
# world exists: clear of every foreign M4 obstruction (the reference
# band's connector jogs are obstructed at their unshifted positions in
# the die's top region) and at least two lanes away from the x-adjacent
# riser's plate.  Packed into the three rows they left the router 30 M4
# spacing / end-of-line violations; in consecutive lanes still 12.
YSELB_PORTS = [(f"ysel{'b' if i < 4 else 'bn'}_{i % 4}", 53636 + 192 * i, None)
               for i in range(8)]
for port, px, _row in YSELB_PORTS:
    d["io_geometry"][port] = [["m5", px - 48, -3036, px + 48, -2964]]
# eight-strip variant: the sixteen new column-select ports.  Each port's
# pad AND landing sit at its select line's column (generator.SELECT_LINE_X
# in band x + 32204 for the left array's yselt family, the mirror
# 40128 - L + 32204 for the right array's yselb family (a left column
# L..L+96 mirrors to 40080-L..40176-L about the io axis): the M5 riser
# from the landing then IS the line, and band.py extends it to both
# stack seams), so the strap has no lateral jog.  The pads are M3 at
# the standard synthetic pad depth (ctl y -3036), each 164 dbu right of
# a band wordline M3 column on the left (104 clear on M3; the mirror
# 104 clear of its right neighbor column); landing rows chosen where
# the band's under-bar M4 leaves the
# plate clear (scan of 2026-09-19).  Line j of a mux carries
# ysel{t,b}_(4+j//2) for even j and ysel{t,b}n_(4+j//2) for odd j.
S8_LEFT = [3800, 4232, 5096, 5528, 5960, 6392, 6824, 7688]   # band x
S8_ROWS_L = [0, 1, 0, 1, 2, 0, 1, 2]
S8_ROWS_R = [0, 1, 2, 0, 1, 2, 0, 1]
S8_PORTS = (  # (port, pad center = landing ctl x, landing row)
    [(f"yselt{'' if j % 2 == 0 else 'n'}_{4 + j // 2}", L + 48 + 32204,
      S8_ROWS_L[j]) for j, L in enumerate(S8_LEFT)]
    + [(f"yselb{'' if j % 2 == 0 else 'n'}_{4 + j // 2}",
        40128 - L + 32204, S8_ROWS_R[j]) for j, L in enumerate(S8_LEFT)])
if VARIANT == "s8":
    for port, px, _row in S8_PORTS:
        d["io_geometry"][port] = [["m3", px - 36, -3036, px + 36, -2964]]


def san(name):
    return name.replace("<", "_").replace(">", "").replace("!", "")


# ---- interior pins at original pad boxes ----
pin_lines = ["set block [ord::get_db_block]",
             "set tech [ord::get_db_tech]"]
pad_regions = {k: kdb.Region() for k in LAYERS}
pin_map = {}
# three staggered landing rows split the per-row track demand and give
# three different y-lanes past the surviving under-bar metal
used_tracks_rows = [set(), set(), set()]
used_tracks = set()   # global forbids (crossings, forest m5)
riser_tracks = set()  # columns claimed by chosen strap risers
port_index = 0
# forbid landing tracks at crossing-column x and under forest m5
# verticals: the strap's M5 section runs at the landing track, and M5
# columns must never coincide
def forbid(cx_ctl):
    c_nm = to_nm_ctl(cx_ctl, 0)
    base = TRACK_OFF + 36 * round((c_nm - TRACK_OFF) / 36)
    for t in (base - 36, base, base + 36):
        used_tracks.add(t)
for cname, x0, y0, x1, y1 in d["obstructions"]:
    if cname == "m5" and (y1 - y0) > 2000:
        forbid((x0 + x1) // 2)
# the macro PDN M5 stripes are full-die-height verticals: keep the strap
# risers (also M5) well clear of their columns
# No M5 stripes: the macro is powered through its M1 followpin rails
# (fed by the band's via rails).  pdngen's M5 stripes welded crossing
# wires through their M1->M5 stack-via towers (ft_24, wlt<9>) and their
# positions drifted between runs; control_pnr.tcl no longer creates
# them and vendor_and_check.sh asserts the macro has no M5.  The empty
# list keeps the guard/keepout machinery as no-ops.
PDN_STRIPES = []
for sx, _pol in PDN_STRIPES:
    for off in (-720, -360, 0, 360, 720):
        forbid(sx + off)
# landing plates never sit ON a feedthrough column (exact track only -
# M4 plates coexist with M3 exit columns on neighboring tracks)
for e in d["band_exits"]:
    ex_nm = to_nm_ctl((e[1] + e[3]) // 2, 0)
    used_tracks.add(TRACK_OFF + 36 * round((ex_nm - TRACK_OFF) / 36))
# the bank OVERLAY's control-distribution M5 risers also cross the die -
# they are not band-cell shapes, so the obstruction-derived forbids miss
# them; scan the overlay directly (wlenab's strap landed exactly on one)
import sys as _sys
_sys.path.insert(0, str(REPO))
from sramforge.library import CellLibrary as _CL
_ly = kdb.Layout(); _ly.dbu = 0.00025
for (_l, _d), _sh in _CL(_ly).overlay_shapes("srambank_32b"):
    if (_l, _d) == (50, 0) and not _sh.is_text():
        _b = _sh.bbox()
        if _b.height() > 2000 and _b.right > 432 and _b.left < 38016:
            forbid((_b.left + _b.right) // 2 + 33068)   # bank -> ctl x
# landing boxes must not sit over surviving under-bar metal (the m4 jogs
# and m3 segments that stay verbatim cross the landing rows): build
# per-row blocked x-intervals and reject candidate tracks inside them
row_blocked = [[], [], []]             # ctl-x intervals per landing row
ROW_Y = [(-6200, -5900), (-5700, -5400), (-6700, -6400)]
for cname, x0, y0, x1, y1 in d.get("underbar_obs", []):
    if cname not in ("m3", "m4"):
        continue
    for ri, (ry0, ry1) in enumerate(ROW_Y):
        if y0 < ry1 + 150 and y1 > ry0 - 150:
            row_blocked[ri].append((x0 - 250, x1 + 250))


def track_blocked(row, cand_nm):
    cx = ORIGIN[0] + cand_nm * 4
    return any(a < cx < b for a, b in row_blocked[row])

# PAD-DEPTH JOG COLLISION AVOIDANCE: each strap ends with an m4 jog from
# the landing track to the pad at the pad's own y.  A jog crossing a
# NEIGHBOR's pad or glue stack welds two control nets (found as
# wrenan+yselt<0> etc. by the analog write sweep).  Obstacles: every
# pad box, every glue box, and every already-placed jog.
try:
    glue_all = json.load(open(REPO / "cells" / "control_onebox_glue.json"))
except FileNotFoundError:
    glue_all = {}
# PER-LAYER obstacle world: the strap's m4 pieces (jog, plate, nub) must
# clear all foreign m4; its M5 riser must clear ALL foreign m5 at any
# height (short glue tails and pads too, not just tall forest verticals)
jog_obstacles = {"m4": kdb.Region(), "m5": kdb.Region()}
own_clear = {}
for net2, boxes2 in d["io_geometry"].items():
    r = {"m4": kdb.Region(), "m5": kdb.Region()}
    for bx2 in boxes2:
        if bx2[0] in r:
            r[bx2[0]].insert(kdb.Box(bx2[1], bx2[2], bx2[3], bx2[4]))
    for g2 in glue_all.get(san(net2), []):
        if g2[0] in r:
            r[g2[0]].insert(kdb.Box(g2[1], g2[2], g2[3], g2[4]))
    own_clear[san(net2)] = r
    for k in r:
        jog_obstacles[k] += r[k]
for cname2, ox0, oy0, ox1, oy1 in d["obstructions"]:
    if cname2 in jog_obstacles:
        jog_obstacles[cname2].insert(kdb.Box(ox0, oy0, ox1, oy1))
# io-net forest wiring (per net, so a port's own wires are not obstacles)
for net2, fboxes in d.get("io_forest", {}).items():
    for fb in fboxes:
        if fb[0] in jog_obstacles:
            jog_obstacles[fb[0]].insert(kdb.Box(fb[1], fb[2], fb[3], fb[4]))
            if net2 in own_clear and fb[0] in own_clear[net2]:
                own_clear[net2][fb[0]].insert(
                    kdb.Box(fb[1], fb[2], fb[3], fb[4]))

# PAD-COLUMN PROTECTION: a port's m5/m3 pad columns are its landing
# anchors of last resort - no OTHER port's riser may claim them
PREF0 = {"m5": 3, "m4": 2, "m3": 1, "m2": 0}
pad_columns = {}                       # port -> set of column nm
for net0, boxes0 in d["io_geometry"].items():
    cols = set()
    for b0 in boxes0:
        if b0[0] in ("m5", "m3") and (b0[4] - b0[2]) > (b0[3] - b0[1]):
            cols.add(int(round(((b0[1] + b0[3]) // 2 - ORIGIN[0]) * 0.25)))
    pad_columns[san(net0)] = cols


def _hardness(kv):
    """Most-constrained ports place first: the fixed (synthetic) landings
    before everything, so the searched ports keep the one-row spacing
    rule (ROW_TRACK_SEP) away from them (a searched landing placed before a
    fixed one could sit one track from it: address_7 landed 336 dbu
    from yselb_0 in the first eight-strip run, a 96-dbu plate gap);
    then m3-pad ports (wlena family), which have the fewest riser
    options."""
    PREF0 = {"m5": 3, "m4": 2, "m3": 1, "m2": 0}
    bb = max(kv[1], key=lambda b: (PREF0[b[0]],
                                   (b[3]-b[1]) * (b[4]-b[2])))
    if san(kv[0]) in SYNTH_LANDING:
        return (-1, san(kv[0]))
    return (0 if bb[0] == "m3" else 1, san(kv[0]))


# synthetic ports get fixed landings in the die margins (the strap
# search machinery has no room at the extreme edges)
# inboard of the die edge by >= 250 nm (1x): landings at the extreme
# west/east edges jam the detailed router (Rect Only + EolKeepOut + M2
# shorts at 46 violations); the band-side riser corridors at these x
# are verified clear of forest m5, and the pad jogs thread between the
# m4 furniture bars (y -3300..-2436 gap at pad depth -3000)
SYNTH_LANDING = {"wlenat_hi": (34600, 0), "address_8": (34852, 1),
                 "wlenab_hi": (70000, 0),
                 "wlenat_hi_n": (35104, 2), "wlenab_hi_n": (69700, 1)}
# the select ports: landing at the pad x, no jog lane offset
SYNTH_JOG = {}


def yselb_lanes():
    """One landing lane per right-array select port along its riser:
    the first lane (from the top landing row downward, 500 pitch) whose
    plate, padded by 88 (M4 spacing), meets no foreign M4 obstacle, at
    least two lanes from the x-adjacent riser's plate and one from the
    next one."""
    m4_all = jog_obstacles["m4"].dup()
    # the shifted guard row and the surviving under-bar metal are
    # obstructed later (they are not in the extraction's obstruction
    # list): the guard row's M4 slid 6480 dbu deeper for the eight-strip
    # variant, right across the third lane (yselbn_3, 5 violations)
    for cname_g, gx0, gy0, gx1, gy1 in d.get("guard_shapes", []):
        if cname_g == "m4":
            m4_all.insert(kdb.Box(gx0, gy0 - GROWTH, gx1, gy1 - GROWTH))
    for cname_u, ux0, uy0, ux1, uy1 in d.get("underbar_obs", []):
        if cname_u == "m4":
            m4_all.insert(kdb.Box(ux0, uy0, ux1, uy1))
    m4_all.merge()
    lanes = []
    for i, (port, px, _none) in enumerate(YSELB_PORTS):
        for k in range(16):
            y1 = -5400 - 500 * k
            plate = kdb.Box(px - 120 - 88, y1 - 300 - 48 - 88,
                            px + 120 + 88, y1 + 48 + 88)
            if not (kdb.Region(plate) & m4_all).is_empty():
                continue
            if lanes and abs(k - lanes[-1]) < 2:
                continue
            if len(lanes) > 1 and abs(k - lanes[-2]) < 1:
                continue
            lanes.append(k)
            break
        else:
            raise SystemExit(f"no landing lane for {port}")
    return [(-5700 - 500 * k, -5400 - 500 * k) for k in lanes]


for (port, px, _none), lane in zip(YSELB_PORTS, yselb_lanes()):
    SYNTH_LANDING[port] = (px, lane)
for port, px, row in (S8_PORTS if VARIANT == "s8" else []):
    SYNTH_LANDING[port] = (px, row)

for net, boxes in sorted(d["io_geometry"].items(), key=_hardness):
    port = san(net)
    pin_lines.append(f"set bt [$block findBTerm {{{port}}}]")
    pin_lines.append(f"if {{$bt == \"NULL\"}} {{ puts \"MISSING PORT {port}\" }} else {{")
    pin_lines.append("  set bp [odb::dbBPin_create $bt]")
    # ONE box per pin (each dbBPin box demands its own connection).
    # Pads INSIDE the die land on the original pad; pads ABOVE the die
    # (old center-column outputs) get a grid-snapped M3 landing box in
    # the die's top strip at the pad's x - band v2 generates the
    # vertical extension strap from the old pad down to that landing.
    # ALL-STRAPPED LANDINGS: every port gets a clean grid-snapped M3
    # landing box in a dedicated empty strip between the core rows and
    # the old-bar pin forest.  Band v2 generates the deterministic
    # vertical strap (via stack at the pad's own x) from the original
    # pad down to the landing.  The router never enters the forest.
    PREF = {"m5": 3, "m4": 2, "m3": 1, "m2": 0}
    for bx in boxes:
        pad_regions[bx[0]].insert(kdb.Box(bx[1], bx[2], bx[3], bx[4]))
    best = max(boxes, key=lambda b: (PREF[b[0]],
                                     (b[3]-b[1]) * (b[4]-b[2])))
    pcx = (best[1] + best[3]) // 2
    # M4 landing: the M3 feedthrough columns cross the landing rows, an
    # M4 plate never conflicts with them (and needs no exit forbids)
    cname = "m4"
    pcx_nm = to_nm_ctl(pcx, 0)
    base = TRACK_OFF + 36 * round((pcx_nm - TRACK_OFF) / 36)
    row_pref = port_index % 3
    row_order = [row_pref, (row_pref + 1) % 3, (row_pref + 2) % 3]
    port_index += 1
    bpy = (best[2] + best[4]) // 2
    # jog-lane candidates: anywhere along the pad's own y-extent (tall
    # pads give many lanes), padded 300 dbu off the pad ends
    def lane_options(pad):
        pby = (pad[2] + pad[4]) // 2
        lo = pad[2] + 300 - pby
        hi = pad[4] - 300 - pby
        out = [0]
        step = 240
        k2 = 1
        while True:
            added = False
            for cand2 in (k2 * step, -k2 * step):
                if lo <= cand2 <= hi:
                    out.append(cand2); added = True
            if not added and k2 * step > max(abs(lo), abs(hi), 1920):
                break
            k2 += 1
        return out
    dy_options = lane_options(best)
    # own-net exclusion by CONTACT: any obstacle piece touching the
    # port's own pads/glue is the same electrical net (alias keying in
    # io_forest is unreliable - several control nets share a band net)
    foreign = {}
    for k in jog_obstacles:
        obs = jog_obstacles[k].dup()
        obs.merge()
        own = own_clear[port][k]
        foreign[k] = obs - obs.interacting(own) if not own.is_empty() else obs

    def jog_rects(cand, dy):
        cx = pcx if cand is None else ORIGIN[0] + cand * 4
        if cand is None and best[0] == "m5" and dy == 0:
            return []          # M5 riser merges an M5 pad directly: no jog
        if best[1] + 140 <= cx <= best[3] - 140 and dy == 0:
            return []          # landing under the pad's own span: no jog
        jy = bpy + dy
        rects = [kdb.Box(min(cx, pcx) - 140, jy - 68,
                         max(cx, pcx) + 140, jy + 68)]
        if dy:
            rects.append(kdb.Box(pcx - 140, min(bpy, jy) - 68,
                                 pcx + 140, max(bpy, jy) + 68))
        return rects

    def jog_clear(cand, dy):
        cx = pcx if cand is None else ORIGIN[0] + cand * 4
        # M5 riser column: landing row up to the jog lane
        riser = kdb.Box(cx - 68, ROW_Y[row][0] - 100,
                        cx + 68, bpy + dy + 212)
        if not (kdb.Region(riser) & foreign["m5"]).is_empty():
            return False
        # M4 landing plate footprint
        plate = kdb.Box(cx - 160, ROW_Y[row][0] - 68,
                        cx + 160, ROW_Y[row][1] + 68)
        if not (kdb.Region(plate) & foreign["m4"]).is_empty():
            return False
        rects = jog_rects(cand, dy)
        if not rects:
            return True
        r = kdb.Region()
        for rect in rects:
            r.insert(rect)
        return (r & foreign["m4"]).is_empty()

    snap_nm, jog_dy = None, 0
    land_y = None                      # explicit (y0, y1) landing lane
    if port in SYNTH_LANDING:
        lx_s, row_s = SYNTH_LANDING[port]
        snap_nm, jog_dy = to_nm_ctl(lx_s, 0), SYNTH_JOG.get(port, 0)
        if isinstance(row_s, tuple):
            land_y = row_s
            sel_row = 0
            for r_all in range(3):     # its column is foreign to every row
                used_tracks_rows[r_all].add(snap_nm)
        else:
            sel_row = row_s
            used_tracks_rows[row_s].add(snap_nm)
    reject = {"used": 0, "row": 0, "range": 0, "blocked": 0, "jog": 0}
    if snap_nm is None:
        sel_row = row_order[0]
    for row in (row_order if snap_nm is None else []):
      row_used = used_tracks_rows[row]
      if snap_nm is not None:
          break
      for k in range(0, 260):
        for cand in (base + 36 * k, base - 36 * k):
            if cand in used_tracks:
                reject["used"] += 1; continue
            if any(abs(cand - pc) < 72
                   for pp, pcs in pad_columns.items() if pp != port
                   for pc in pcs):
                reject["used"] += 1; continue
            if any(abs(cand - t) < ROW_TRACK_SEP for t in row_used):
                reject["row"] += 1; continue
            if not 100 <= cand <= 9296:
                reject["range"] += 1; continue
            if track_blocked(row, cand):
                reject["blocked"] += 1; continue
            for dy in dy_options:
                if jog_clear(cand, dy):
                    snap_nm, jog_dy = cand, dy
                    sel_row = row          # capture at SUCCESS time
                    break
            else:
                reject["jog"] += 1
            if snap_nm is not None:
                break
        if snap_nm is not None:
            break
    if snap_nm is None:
        print(f"REJECTS for {port}: {reject}")
    if snap_nm is None:
        # FALLBACK: land at a pad's own x (off-grid is fine - the M4
        # plate and M5 riser have no track requirement), zero-length jog.
        # Try EVERY pad of the port, best-preferred first.
        for fb_pad in sorted(boxes, key=lambda b: (PREF[b[0]],
                             (b[3]-b[1]) * (b[4]-b[2])), reverse=True):
            if snap_nm is not None:
                break
            best = fb_pad
            pcx = (best[1] + best[3]) // 2
            bpy = (best[2] + best[4]) // 2
            pcx_nm = to_nm_ctl(pcx, 0)
            cand_nm = pcx_nm
            stripe_bad = any(abs(pcx - sx) < 900 for sx, _pol in PDN_STRIPES)
            if stripe_bad:
                continue
            for row in row_order:
                row_used = used_tracks_rows[row]
                if [t for t in row_used if abs(t - cand_nm) < 200]:
                    continue
                done2 = False
                for dy in lane_options(best):
                    if jog_clear(None, dy):
                        snap_nm, jog_dy = cand_nm, dy
                        done2 = True
                        break
                if done2:
                    sel_row = row
                    break
        if snap_nm is None:
            print(f"FALLBACK-REJECT {port}: stripe={stripe_bad}")
            for row in row_order:
                riser = kdb.Region(kdb.Box(pcx - 68, ROW_Y[row][0] - 100,
                                           pcx + 68, bpy + 212)) & foreign["m5"]
                plate = kdb.Region(kdb.Box(pcx - 160, ROW_Y[row][0] - 68,
                                           pcx + 160, ROW_Y[row][1] + 68)) & foreign["m4"]
                def _bb(r):
                    return [(q.bbox().left, q.bbox().bottom,
                             q.bbox().right, q.bbox().top) for q in r.each()][:2]
                print(f"   row {row}: riser {_bb(riser)} plate {_bb(plate)}")
    assert snap_nm is not None, f"no free track for {port}"
    # placed strap geometry becomes obstacles (span-aware, below);
    # no blanket column exclusion - two risers may share a column at
    # disjoint y-spans
    jx2 = ORIGIN[0] + snap_nm * 4
    for rect in jog_rects(snap_nm, jog_dy):
        jog_obstacles["m4"].insert(rect)
    ly0, ly1 = land_y if land_y else ROW_Y[sel_row]
    jog_obstacles["m5"].insert(kdb.Box(jx2 - 68, ly0 - 100,
                                       jx2 + 68, bpy + jog_dy + 212))
    jog_obstacles["m4"].insert(kdb.Box(jx2 - 160, ly0 - 68,
                                       jx2 + 160, ly1 + 68))
    # plate centers must stay >= ROW_TRACK_SEP apart (distance test - fallback
    # landings are off-grid, exact membership misses them); reserve in
    # the row that actually VALIDATED the landing
    used_tracks_rows[sel_row].add(snap_nm)
    pcx = ORIGIN[0] + snap_nm * 4
    x0, x1 = pcx - 100, pcx + 100
    y0, y1 = ly0, ly1
    strap = {"from_pad": best, "to_box": [cname, x0, y0, x1, y1],
             "jog_dy": jog_dy}
    # FOREST STITCH data: the port's own band forest wires (per-net from
    # the reference conductor graph).  Band v2 drops a V4 wherever the
    # drawn strap crosses one of these - the strap-to-band-wire contact
    # is guaranteed by construction, not by placement luck.  Only
    # wire-like boxes (one dimension <= 200 nm): a merged L-shape's bbox
    # overapproximates and could claim a foreign wire's area.
    strap["forest"] = [fb for fb in d.get("io_forest", {}).get(port, [])
                       if min(fb[3] - fb[1], fb[4] - fb[2]) <= 200]
    nx0, ny0 = to_nm_ctl(x0, 0), to_nm_ctl(y0, 1)
    nx1, ny1 = to_nm_ctl(x1, 0), to_nm_ctl(y1, 1)
    pin_lines.append(f"  odb::dbBox_create $bp [$tech findLayer "
                     f"{LNAME[cname]}] {nx0} {ny0} {nx1} {ny1}")
    pin_lines.append("  $bp setPlacementStatus PLACED }")
    pin_map[port] = {"pads": boxes, "net": net, "strap": strap}
# ---- feedthrough pins: two boxes per exit wire (top at the forest
# edge, bottom above the die floor), on the wire's own layer, at the
# wire's own x - the router carries the wire through its fabric
# tall pin boxes = the full access corridors: every point is backed by
# real band metal (top: the generated stub + the exit wire; bottom: the
# shifted guard vertical + the generated stub), so the router may
# connect anywhere on the box
FT_TOP = (-5650, -4700)
FT_BOT = (ORIGIN[1] + 690, ORIGIN[1] + 1200)
ft_boxes = []                          # ctl-frame pin boxes, for holes
for i, (cname, x0, y0, x1, y1, pol, cx0, cx1) in enumerate(d["band_exits"]):
    lname = LNAME[cname]
    pin_lines.append(f"set bt [$block findBTerm {{ft_{i}}}]")
    pin_lines.append(f"if {{$bt == \"NULL\"}} {{ puts \"MISSING PORT ft_{i}\" }} else {{")
    # TWO bterms on ONE net (a classic feedthrough net): a single bterm
    # is trivially "routed" with no wiring, and DRT rejects multi-bpin
    # bterms - so the bottom box lives on a second bterm created on the
    # same net, giving the router a real two-terminal connection to make
    pin_lines.append("  set bp [odb::dbBPin_create $bt]")
    # wide horizontal exits: the top pin IS the wire itself (its own
    # metal, in-die, below the blanket) - no stub plate, no wide corridor
    if x1 - x0 > 200:
        ty0, ty1 = y0, y1
    else:
        ty0, ty1 = FT_TOP
    pin_lines.append(
        f"  odb::dbBox_create $bp [$tech findLayer {lname}] "
        f"{to_nm_ctl(x0, 0)} {to_nm_ctl(ty0, 1)} "
        f"{to_nm_ctl(x1, 0)} {to_nm_ctl(ty1, 1)}")
    pin_lines.append("  $bp setPlacementStatus PLACED")
    pin_lines.append(f"  set bt2 [odb::dbBTerm_create [$bt getNet] {{ft_{i}_B}}]")
    pin_lines.append("  set bp2 [odb::dbBPin_create $bt2]")
    pin_lines.append(
        f"  odb::dbBox_create $bp2 [$tech findLayer {lname}] "
        f"{to_nm_ctl(cx0, 0)} {to_nm_ctl(FT_BOT[0], 1)} "
        f"{to_nm_ctl(cx1, 0)} {to_nm_ctl(FT_BOT[1], 1)}")
    pin_lines.append("  $bp2 setPlacementStatus PLACED")
    pin_lines.append("}")
    # ACCESS CORRIDORS, not just pin holes: the router approaches the top
    # pin vertically from below (through the exit wire's own obstructed
    # metal) and the bottom pin from above (through the shifted guard's
    # obstructed metal) - open the full column at the pin's x.  Only the
    # ft net's own metal is exposed there.
    if x1 - x0 > 200:
        ft_boxes.append(kdb.Box(x0, y0, x1, y1))
    else:
        ft_boxes.append(kdb.Box(x0, -5650, x1, -4650))
    ft_boxes.append(kdb.Box(cx0, ORIGIN[1], cx1, FT_BOT[1]))
(OUT / "pins.tcl").write_text("\n".join(pin_lines) + "\n")
(OUT / "pin_map.json").write_text(json.dumps(pin_map, indent=1))

# ---- obstructions: foreign-net metal over the macro (from extraction) ----
obs_lines = ["set block [ord::get_db_block]",
             "set tech [ord::get_db_tech]"]
n_obs = 0
all_pads = kdb.Region()
for r in pad_regions.values():
    all_pads += r
# pin holes: strap landing boxes and feedthrough pin boxes are pins too -
# no obstruction may cover them
for rec in pin_map.values():
    _, lx0, ly0, lx1, ly1 = rec["strap"]["to_box"]
    all_pads.insert(kdb.Box(lx0, ly0, lx1, ly1))
for b in ft_boxes:
    all_pads.insert(b)


def emit_obs(cname, box):
    global n_obs
    reg = kdb.Region(box) - all_pads.sized(120)
    for poly in reg.each():
        b = poly.bbox()
        obs_lines.append(f"odb::dbObstruction_create $block "
                         f"[$tech findLayer {LNAME[cname]}] "
                         f"{to_nm_ctl(b.left,0)} {to_nm_ctl(b.bottom,1)} "
                         f"{to_nm_ctl(b.right,0)} {to_nm_ctl(b.top,1)}")
        n_obs += 1


for cname, x0, y0, x1, y1 in d["obstructions"]:
    if y0 > DIE_TOP_CTL:
        continue                      # outside the one-box die
    emit_obs(cname, kdb.Box(x0, y0, x1, y1))

# SINGLE-OWNER FOREST: the macro router has no business above the
# landing strip - that zone belongs to the band's original wires plus
# band v2's generated stacks/straps.  Blanket-obstruct M2-M4 there.
for cname in ("m2", "m3", "m4"):
    obs_lines.append(f"odb::dbObstruction_create $block "
                     f"[$tech findLayer {LNAME[cname]}] "
                     f"{to_nm_ctl(33500, 0)} {to_nm_ctl(-4650, 1)} "
                     f"{to_nm_ctl(71084, 0)} {to_nm_ctl(DIE_TOP_CTL, 1)}")
    n_obs += 1

# SINGLE-OWNER UNDER-BAR ZONE: every band wire that stays verbatim in
# the old under-bar routing zone (jogs, dead-end verticals, non-exit
# segments) is foreign metal - obstruct it (pin holes are subtracted by
# emit_obs).
for cname, x0, y0, x1, y1 in d.get("underbar_obs", []):
    emit_obs(cname, kdb.Box(x0, y0, x1, y1))

# SHIFTED GUARD ROW: the guard row lands inside the die bottom margin
# after sliding down with the lower stack - its metal there is foreign
# to the router (the ft bottom pins sit ON it; emit_obs keeps holes).
for cname, x0, y0, x1, y1 in d.get("guard_shapes", []):
    if cname == "m1":
        continue
    b = kdb.Box(x0, y0 - GROWTH, x1, y1 - GROWTH)
    b = b & kdb.Box(33500, ORIGIN[1], 71084, DIE_TOP_CTL)
    if not b.empty():
        emit_obs(cname, b)

# FEEDTHROUGH TOP STUBS: the band bridges each top pin box to the old
# wire end with a same-layer stub - obstruct the stub extent outside the
# pin box itself (raw emit; clip into the blanket at -4650).
extensions = []
n_sup = 0
for cname, ex0, cy0, ex1, cy1, pol, ccx0, ccx1 in d["band_exits"]:
    extensions.append([cname, ex0, cy0 - GROWTH, ex1, cy0, pol, ccx0, ccx1])
    if pol:
        n_sup += 1
    # stubs below the top pin / above the bottom pin are same-net metal on
    # the access corridor - deliberately NOT obstructed; only the top
    # stub's tail above the pin box is sealed (up to the blanket)
    shi = max(FT_TOP[0], cy0 + 160)
    if min(shi, -4650) > FT_TOP[1]:
        obs_lines.append(f"odb::dbObstruction_create $block "
                         f"[$tech findLayer {LNAME[cname]}] "
                         f"{to_nm_ctl(ex0, 0)} {to_nm_ctl(FT_TOP[1], 1)} "
                         f"{to_nm_ctl(ex1, 0)} {to_nm_ctl(min(shi, -4650), 1)}")
        n_obs += 1
# PDN STACK-POINT GUARDS: pdngen drops M1->M5 stacked vias wherever a
# stripe crosses a rail; a signal routed through that point gets
# punched through (found as the ft_24/VSS weld - latent for every
# dead-at-16 column).  Obstruct M2-M4 in a small box at every stack
# point so no signal ever sits under one.
for sx, _pol in PDN_STRIPES:
    for k in range(0, 22):
        ry = ORIGIN[1] + 1080 + k * 1080
        for lname2 in ("M2", "M3", "M4"):
            obs_lines.append(
                f"odb::dbObstruction_create $block "
                f"[$tech findLayer {lname2}] "
                f"{to_nm_ctl(sx - 260, 0)} {to_nm_ctl(ry - 260, 1)} "
                f"{to_nm_ctl(sx + 260, 0)} {to_nm_ctl(ry + 260, 1)}")
            n_obs += 1
# EXTENSION-VERTICAL COLUMNS (band.py build_ctrl_band_v2, i==0 pair):
# at bits>32 the innermost extension wordline verticals are drawn as
# raw band M3 straight THROUGH the die (ctl x 33680-33752 west,
# 70832-70904 east, full height) - invisible to the macro router.  Any
# macro M3 route crossing them is a weld in the assembled bank (found
# as the bits=64 VDD-VSS supply short after a re-place).  Obstruct M3
# in both columns; emit_obs keeps holes at the ft pin boxes so the
# feedthrough router can still enter/exit there.
for vx0, vx1 in ((33680, 33752), (70832, 70904)):
    emit_obs("m3", kdb.Box(vx0 - 156, ORIGIN[1], vx1 + 156, DIE_TOP_CTL))

# PLACEMENT KEEPOUT under the M5 stripes: a cell placed beneath the
# stripe demands M2 pin access exactly where the stack-point guards
# forbid routing - the router then shorts straight through a guard box
# (nm3_58/nm3_62, two M2 shorts persistent across placements).  With no
# cells under the stripes there is no pin demand there at all.
for sx, _pol in PDN_STRIPES:
    obs_lines.append(
        f"odb::dbBlockage_create $block "
        f"{to_nm_ctl(sx - 420, 0)} 270 {to_nm_ctl(sx + 420, 0)} 4860")
(OUT / "obstructions.tcl").write_text("\n".join(obs_lines) + "\n")
(OUT / "extensions.json").write_text(json.dumps(extensions, indent=1))
print(f"{len(d['io_geometry'])} interior pins, {n_obs} obstructions "
      f"({n_sup} supply exits), {len(extensions)} feedthroughs")
