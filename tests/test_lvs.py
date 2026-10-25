#!/usr/bin/env python3
"""Rung 4 - LVS (Layout Versus Schematic): device extraction + comparison.

Three parts:
  1. Bitcell LVS: full exact graph isomorphism between the extracted 6T
     (six-transistor) bitcell and the golden measured schematic (models,
     fin counts, gate nets, source/drain nets, cross-couple).
  2. Bitcell mutations: three classes of injected damage must each break
     the match - swapped implants (device types flip), deleted GCut
     (gates merge), deleted SDT (source/drain contacts float).
  3. Bank-level device accounting on the reference bank: exact closed-form
     counts for every bitcell transistor, and per-net degree uniformity
     (every wordline gates the same number of access transistors, every
     bitline touches exactly --bits access drains).

Usage:  python3 tests/test_lvs.py
"""
import sys
import pathlib
import tempfile
from collections import Counter

import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import BankSpec, generate                     # noqa: E402
from sramforge.lvs import (Extraction, extract, match_netlist,  # noqa: E402
                           GOLDEN_6T, GOLDEN_6T_INTERNAL)
from sramforge.tech import LAYERS                            # noqa: E402

BITCELL_GDS = str(REPO / "cells" / "sram_cell_6t_122.gds")
BITCELL = "sram_cell_6t_122"


def mutated_extraction(mutate):
    """Load the bitcell, apply `mutate(layout, top)`, extract."""
    ly = kdb.Layout()
    ly.read(BITCELL_GDS)
    top = ly.cell(BITCELL)
    mutate(ly, top)
    return Extraction(ly, top)


def swap_selects(ly, top):
    ln = ly.layer(*LAYERS["nselect"])
    lp = ly.layer(*LAYERS["pselect"])
    n_shapes = [s.polygon for s in top.shapes(ln).each() if s.is_polygon()
                or s.is_box() or s.is_path()]
    p_shapes = [s.polygon for s in top.shapes(lp).each() if s.is_polygon()
                or s.is_box() or s.is_path()]
    top.shapes(ln).clear(); top.shapes(lp).clear()
    for s in n_shapes:
        top.shapes(lp).insert(s)
    for s in p_shapes:
        top.shapes(ln).insert(s)


def delete_gcut(ly, top):
    top.shapes(ly.layer(*LAYERS["gcut"])).clear()


def delete_sdt(ly, top):
    top.shapes(ly.layer(*LAYERS["sdt"])).clear()


MUTATIONS = [
    ("swapped NSELECT/PSELECT (every device changes type)", swap_selects),
    ("deleted GCUT (gate stripes merge, gates short together)", delete_gcut),
    ("deleted SDT (source/drain contacts float)", delete_sdt),
]


def check_bank(spec: BankSpec, gds: str) -> list:
    fails = []
    ex = extract(gds, spec.name)
    n_cells = (spec.bits_per_group * spec.strips_per_group
               * 2 * 2 * spec.depth_groups)
    census = ex.census()

    if ex.unclassified:
        fails.append(f"{len(ex.unclassified)} channel pieces with no "
                     "implant classification")
    # exact closed-form bitcell device counts
    checks = [
        ("nmos_sram 2-fin (2 access + 2 pulldown per cell)",
         census.get(("nmos_sram", 2), 0), 4 * n_cells),
        ("pmos_sram 1-fin (2 pullups per cell)",
         census.get(("pmos_sram", 1), 0), 2 * n_cells),
    ]
    stray_sram = {k: v for k, v in census.items()
                  if k[0].endswith("_sram")
                  and k not in (("nmos_sram", 2), ("pmos_sram", 1))}
    if stray_sram:
        fails.append(f"unexpected sram-flavor devices: {stray_sram}")

    # SPARSE-BITS: guard structures label disconnected stubs over the
    # dead (absent) columns - same artifact rung 2 excludes.  Count only
    # nets labeled OUTSIDE the dead windows (empty at the reference).
    skip = 32 - spec.bits_per_group
    live_anchor = 864 + skip * 432
    axis = 19236
    dead = []
    if skip:
        dead = [(0, live_anchor - 100),
                (2 * axis - live_anchor + 100, 2 * axis)]

    def live_nets(label):
        return {net for (name, x, y, net) in ex.label_probes
                if name == label
                and not any(a <= x < b for a, b in dead)}

    # wordline gating: every WL net gates the same number of access
    # transistors, and the total is exactly 2 per bitcell
    wl_nets = live_nets("WL")
    # one wordline per bitcell column per half - a BITS quantity, constant
    # in depth (at the reference, bits == depth == 32 masked this)
    n_wl = 2 * spec.bits_per_group
    acc_per_wl = Counter()
    bl_deg = Counter()
    bln_deg = Counter()
    bl_nets = live_nets("BL")
    bln_nets = live_nets("BLN")
    for d in ex.devices:
        if d.model == "nmos_sram" and d.gate in wl_nets:
            acc_per_wl[d.gate] += 1
            for n in d.sd:
                if n in bl_nets:
                    bl_deg[n] += 1
                if n in bln_nets:
                    bln_deg[n] += 1
    checks += [
        ("WL nets", len(wl_nets), n_wl),
        ("access transistors gated by WL", sum(acc_per_wl.values()),
         2 * n_cells),
        ("BL nets", len(bl_nets), 2 * 2 * spec.strips_per_group
         * spec.depth_groups),
        ("BLN nets", len(bln_nets), 2 * 2 * spec.strips_per_group
         * spec.depth_groups),
    ]
    for name, have, want in checks:
        if have != want:
            fails.append(f"{name}: {have}, expected {want}")

    def uniform(cnt, want, what):
        bad = {k: v for k, v in cnt.items() if v != want}
        if bad:
            k, v = next(iter(bad.items()))
            fails.append(f"{what}: {len(bad)} nets off (e.g. {v}, "
                         f"expected {want})")

    uniform(acc_per_wl, 2 * n_cells // n_wl,
            "access transistors per WL net")
    uniform(bl_deg, spec.bits_per_group, "access drains per BL net")
    uniform(bln_deg, spec.bits_per_group, "access drains per BLN net")

    print(f"    {len(ex.devices)} devices extracted; "
          f"census heads: nmos_sram2={census.get(('nmos_sram', 2), 0)}, "
          f"pmos_sram1={census.get(('pmos_sram', 1), 0)}; "
          f"WL={len(wl_nets)}, BL={len(bl_nets)}, BLN={len(bln_nets)}")
    return fails


def main() -> int:
    fails = 0

    # ---- part 1: bitcell LVS ----
    ex = extract(BITCELL_GDS, BITCELL)
    ok, detail = match_netlist(GOLDEN_6T, GOLDEN_6T_INTERNAL, ex)
    if ok:
        print("[PASS] bitcell LVS: layout == measured 6T schematic "
              "(6 devices, fin counts 1/2/2, cross-couple verified)")
    else:
        fails += 1
        print(f"[FAIL] bitcell LVS: {detail}")

    # ---- part 2: mutations must break the match ----
    for desc, mutate in MUTATIONS:
        mok, _ = match_netlist(GOLDEN_6T, GOLDEN_6T_INTERNAL,
                               mutated_extraction(mutate))
        if not mok:
            print(f"[PASS] mutation caught: {desc}")
        else:
            fails += 1
            print(f"[FAIL] mutation MISSED: {desc}")

    # ---- part 3: bank-level device accounting ----
    spec = BankSpec()
    with tempfile.TemporaryDirectory() as td:
        gds = str(pathlib.Path(td) / f"{spec.name}.gds")
        generate(spec, gds)
        bank_fails = check_bank(spec, gds)
    if bank_fails:
        fails += len(bank_fails)
        print("[FAIL] bank-level device accounting:")
        for f in bank_fails:
            print(f"    - {f}")
    else:
        print("[PASS] bank-level device accounting: every closed-form "
              "count and per-net degree exact")

    if fails:
        print(f"\nFAIL: {fails} rung-4 failures")
        return 1
    print("\nPASS: rung 4 - bitcell LVS exact, mutations caught, "
          "bank accounting exact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
