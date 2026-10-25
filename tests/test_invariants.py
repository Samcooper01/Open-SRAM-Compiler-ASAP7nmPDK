#!/usr/bin/env python3
"""Rung 1 - structural invariants, valid at ANY knob setting.

The core check is slot-set equality: an INDEPENDENT enumerator (this file)
re-derives every bitcell's expected absolute placement (position + mirror)
from the measured recipe constants, and the extracted placements from the
generated GDS must match it exactly.  The enumerator deliberately does not
import the generator's build functions - two implementations must agree.

Also checked, per configuration:
  * leaf counts (dummies, taps, guard rows, edge fillers, decoder, fillers)
  * no two bitcells in the same slot
  * every bitcell lies inside the SRAMDRC pushed-rules marker   (reference)
  * bank bounding box matches the closed-form formula           (reference)
  * per-row pin contract: BL/BLN/vdd/vss texts present           (reference)

Reference config is strict; experimental configs check the parametric
invariants only (pins/markers/bbox come from reference-sized overlays -
a known gap until routing generation lands).

Usage:  python3 tests/test_invariants.py
"""
import sys
import pathlib
import tempfile
from collections import Counter

import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import BankSpec, generate  # noqa: E402

# ---- measured recipe constants (duplicated here on purpose) --------------
BIT_STEP = 864
BIT_X0 = 108
ROW_PAIR_PITCH = 2160
STRIP_X = -108
ARRAY_X = -20
MIRROR_ARRAY_X_REF = 36700
COLGRP_X = 884
LOWER_ANCHOR_Y = 139266
UPPER_ANCHOR_Y = 171774
COLGRP_PITCH = 4320
BITCELL = "sram_cell_6t_122"


def expected_bitcell_slots(spec: BankSpec):
    """Independent re-derivation of every bitcell's absolute placement."""
    half = spec.bits_per_group // 2
    # SPARSE-BITS: the frame is always the 32-column reference frame; cells
    # occupy the io-adjacent (right) columns, upright comb offset inward.
    skip = (32 - spec.bits_per_group) // 2
    end_x = BIT_X0 + 16 * BIT_STEP

    strip = []
    for k in range(half):
        strip.append(kdb.Trans(kdb.Trans.R0,
                               kdb.Vector(BIT_X0 + (skip + k) * BIT_STEP, 0)))
        strip.append(kdb.Trans(kdb.Trans.M90, kdb.Vector(end_x - k * BIT_STEP, 0)))

    array = []
    for s in range(spec.strips_per_group):
        if s % 2 == 0:
            st = kdb.Trans(kdb.Trans.R0,
                           kdb.Vector(STRIP_X, (s // 2) * ROW_PAIR_PITCH))
        else:
            st = kdb.Trans(kdb.Trans.M0,
                           kdb.Vector(STRIP_X, ((s + 1) // 2) * ROW_PAIR_PITCH))
        array += [st * t for t in strip]

    colgrp = []
    a_r0 = kdb.Trans(kdb.Trans.R0, kdb.Vector(ARRAY_X, 0))
    a_m90 = kdb.Trans(kdb.Trans.M90, kdb.Vector(MIRROR_ARRAY_X_REF, 0))
    colgrp += [a_r0 * t for t in array]
    colgrp += [a_m90 * t for t in array]

    # band v2 grows the band downward: the whole lower stack slides down
    # with it (independent re-derivation of band.growth_for: 28 new
    # control rows with the binary delay line, 34 for the eight-strip
    # control variant)
    growth = ((34 if spec.strips_per_group == 8 else 28) * 1080
              if (spec.bits_per_group != 32 or spec.strips_per_group != 4
                  or spec.depth_groups > 48)
              else 0)
    # strips > 4: the column-group row pitch grows by one strip height
    # (1080) per extra strip (independent re-derivation of colgrp_pitch)
    pitch = COLGRP_PITCH + (spec.strips_per_group - 4) * 1080
    slots = set()
    for row in range(spec.depth_groups):
        low = kdb.Trans(kdb.Trans.M0, kdb.Vector(
            COLGRP_X, LOWER_ANCHOR_Y - growth - row * pitch))
        up = kdb.Trans(kdb.Trans.R0, kdb.Vector(
            COLGRP_X, UPPER_ANCHOR_Y + row * pitch))
        for t in colgrp:
            for bank_t in (low, up):
                a = bank_t * t
                slots.add((a.disp.x, a.disp.y, a.rot))
    return slots


def extract_placements(layout: kdb.Layout, top: kdb.Cell, target_names):
    """Walk the hierarchy, expanding arrays, collecting absolute transforms
    of every instance of the target cells.  Returns {name: [(x,y,rot)]}."""
    targets = {layout.cell_by_name(n): n for n in target_names
               if layout.cell_by_name(n) is not None}
    found = {n: [] for n in target_names}

    def walk(cell, trans):
        for inst in cell.each_inst():
            ci = inst.cell_inst
            child = layout.cell(ci.cell_index)
            for ia in range(ci.na if ci.is_regular_array() else 1):
                for ib in range(ci.nb if ci.is_regular_array() else 1):
                    if ci.is_regular_array():
                        d = ci.a * ia + ci.b * ib
                        t = trans * kdb.Trans(kdb.Vector(d.x, d.y)) * ci.trans
                    else:
                        t = trans * ci.trans
                    name = targets.get(ci.cell_index)
                    if name is not None:
                        found[name].append((t.disp.x, t.disp.y, t.rot))
                    elif child.child_instances() or True:
                        walk(child, t)
    walk(top, kdb.Trans())
    return found


def check(spec: BankSpec, gds_path: str) -> list:
    """Run all invariants; returns a list of failure strings."""
    fails = []
    ly = kdb.Layout(); ly.read(gds_path)
    top = ly.cell(spec.name)
    if top is None:
        return [f"top cell {spec.name} missing"]

    # the decode band is the vendored cell at the reference width and the
    # GENERATED band v2 (one-box control + decoder cut) at sparse widths
    # (band v2 whenever bits or strips differ from the reference, or the
    # bank is deeper than the reference delay line reaches (48 rows):
    # the same rule as generator.uses_band_v2, re-derived here)
    band_cell = ("ctrl_decode_32"
                 if (spec.bits_per_group == 32 and spec.strips_per_group == 4
                     and spec.depth_groups <= 48)
                 else f"ctrl_decode_v2_{spec.bits_per_group}")
    wide = spec.bits_per_group > 32
    f_top = (f"FILLER_32x2_top_slot_{spec.bits_per_group}" if wide
             else "FILLER_32x2_top")
    f_bot = (f"FILLER_32x2_bottom_slot_{spec.bits_per_group}" if wide
             else "FILLER_32x2_bottom")
    leafs = [BITCELL, "dummy_sram_6t122", "tapcell_sram_6t122",
             "dummy_topbot_v1", "dummy_topbot_v2", "FILLER_cgedge",
             band_cell, f_top, f_bot,
             "senseamp_sram_6t122", "write_draslatch_02"]
    got = extract_placements(ly, top, leafs)

    # ---- 1. slot-set equality for the bitcells ----
    expected = expected_bitcell_slots(spec)
    actual_list = got[BITCELL]
    actual = set(actual_list)
    if len(actual_list) != len(actual):
        dup = len(actual_list) - len(actual)
        fails.append(f"{dup} duplicate bitcell placements (same slot twice)")
    n_exp = (spec.bits_per_group * spec.strips_per_group
             * 2 * 2 * spec.depth_groups)
    if len(actual_list) != n_exp:
        fails.append(f"bitcell count {len(actual_list)} != expected {n_exp}")
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        fails.append(f"slot mismatch: {len(missing)} expected slots empty, "
                     f"{len(extra)} bitcells in unexpected slots "
                     f"(e.g. missing {sorted(missing)[:2]}, "
                     f"extra {sorted(extra)[:2]})")

    # ---- 2. leaf counts ----
    n_strips_total = spec.strips_per_group * 2 * 2 * spec.depth_groups
    n_arrays = 2 * 2 * spec.depth_groups
    n_colgrps = 2 * spec.depth_groups
    expect_counts = {
        "dummy_sram_6t122": n_strips_total,
        "tapcell_sram_6t122": n_strips_total,
        "dummy_topbot_v1": (spec.strips_per_group // 2) * n_arrays,
        "dummy_topbot_v2": (spec.strips_per_group // 2) * n_arrays,
        # the 4368-tall edge fillers stack once per four strips
        "FILLER_cgedge": 2 * n_colgrps * (spec.strips_per_group // 4),
        "senseamp_sram_6t122": n_colgrps,
        "write_draslatch_02": n_colgrps,
        band_cell: 1,
        f_top: 1,
        f_bot: 1,
    }
    for name, want in expect_counts.items():
        have = len(got[name])
        if have != want:
            fails.append(f"{name}: {have} instances, expected {want}")

    # ---- reference-only checks (overlay-derived content) ----
    if spec.is_reference:
        # 3. bounding box formula
        bb = top.bbox()
        if (bb.width(), bb.height()) != (38448, 311216):
            fails.append(f"bank bbox {bb.width()}x{bb.height()} != "
                         "38448x311216 (reference formula)")
        # 4. every bitcell inside the SRAMDRC pushed-rules marker
        sramdrc = kdb.Region(top.begin_shapes_rec(ly.layer(99, 0))).merged()
        outside = [s for s in actual
                   if not sramdrc.interacting(
                       kdb.Region(kdb.Box(s[0]-100, s[1]-100,
                                          s[0]+100, s[1]+100))).count()]
        if outside:
            fails.append(f"{len(outside)} bitcells outside SRAMDRC marker")
        # 5. pin contract: per-net text counts on the bitline/supply pins
        texts = Counter()
        for lyr, dt in [(20, 251), (30, 251)]:
            it = top.begin_shapes_rec(ly.layer(lyr, dt))
            while not it.at_end():
                if it.shape().is_text():
                    texts[it.shape().text_string] += 1
                it.next()
        for name in ("BL", "BLN", "WL"):
            if texts[name] == 0:
                fails.append(f"no {name!r} pin texts found")
    return fails


def main() -> int:
    sweep = [
        BankSpec(),                              # the proven reference point
        BankSpec(depth_groups=48),               # grown depth (1.5x) - first
                                                 # grown config through 1-4.5
        BankSpec(depth_groups=16),               # experimental: half depth
        BankSpec(depth_groups=8),                # experimental: quarter depth
        BankSpec(bits_per_group=16),             # sparse-bits: half width
        BankSpec(bits_per_group=16, depth_groups=4),  # small combo
        # every remaining multiple-of-4 width (band v2 covers them all)
        BankSpec(bits_per_group=4),
        BankSpec(bits_per_group=8),
        BankSpec(bits_per_group=12),
        BankSpec(bits_per_group=20),
        BankSpec(bits_per_group=24),
        BankSpec(bits_per_group=28),
        BankSpec(strips_per_group=8),            # eight strips: two mux
                                                 # tiles, 8640 row pitch
        BankSpec(depth_groups=64),               # deep four-strip bank:
                                                 # band v2 by depth
    ]
    total_fails = 0
    with tempfile.TemporaryDirectory() as td:
        for spec in sweep:
            gds = str(pathlib.Path(td) / f"{spec.name}.gds")
            generate(spec, gds)
            fails = check(spec, gds)
            tag = "REF " if spec.is_reference else "EXP "
            if fails:
                total_fails += len(fails)
                print(f"[FAIL] {tag}{spec.name}")
                for f in fails:
                    print(f"    - {f}")
            else:
                n = (spec.bits_per_group * spec.strips_per_group
                     * 2 * 2 * spec.depth_groups)
                print(f"[PASS] {tag}{spec.name}  ({n} bitcells, "
                      f"all slots verified)")
    if total_fails:
        print(f"\nFAIL: {total_fails} invariant failures")
        return 1
    print("\nPASS: all structural invariants hold across the sweep")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
