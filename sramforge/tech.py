"""ASAP7 technology constants for sramforge.

All placement constants are in database units (dbu).  One dbu is 0.25 nm
(dbu-to-micron factor 0.00025), matching the asap7_sram_0p0 reference GDS.

The tiling constants below were measured directly from the BSD-3-licensed
reference bank (srambank_32b.gds) - see scripts/extract_cells.py and the
project notes.  They encode how the drawn cells abut:

* Bitcells tile horizontally at a 432 dbu (0.108 um) column pitch as
  interleaved mirrored pairs: the drawn cell (652 dbu / 0.163 um wide)
  overlaps its mirrored neighbour, sharing boundary geometry.
* Bitcell rows tile vertically at 1080 dbu (0.27 um) as mirrored pairs
  (row pitch 2160 dbu per r0/m0 pair), again with drawn overlap.
"""

DBU = 0.00025  # microns per database unit

# --- bitcell strip (sramcol) ---------------------------------------------
BIT_STEP = 864          # x step between successive same-orientation bitcells
BIT_X0 = 108            # x of first r0 bitcell in a strip
STRIP_PAIR_SPAN = 864   # one r0 + one m90 interleaved pair advances this much

# --- strip stacking (array) ----------------------------------------------
ROW_PAIR_PITCH = 2160   # y pitch of one mirrored r0/m0 strip pair
DUMMY_V1_Y0 = -54       # dummy_topbot_v1 base y (per pair: +p*ROW_PAIR_PITCH)
DUMMY_V2_Y0 = 1026      # dummy_topbot_v2 base y

# --- column group ----------------------------------------------------------
COLGRP_ROW_PITCH = 4320     # y pitch between colgrp rows in the bank (1.08 um)

# GDS layer map (re-derived from the ASAP7 technology library layermap):
LAYERS = {
    "well": (1, 0), "fin": (2, 0), "gate": (7, 0), "gcut": (10, 0),
    "active": (11, 0), "nselect": (12, 0), "pselect": (13, 0),
    "lig": (16, 0), "lisd": (17, 0), "sdt": (88, 0),
    "v0": (18, 0), "m1": (19, 0), "v1": (21, 0), "m2": (20, 0),
    "v2": (25, 0), "m3": (30, 0), "v3": (35, 0), "m4": (40, 0),
    "v4": (45, 0), "m5": (50, 0),
    "sramdrc": (99, 0), "sramvt": (110, 0),
    "boundary": (100, 0), "text": (101, 0),
}
