#!/usr/bin/env python3
"""Rung 3 - DRC (Design Rule Check) deck: golden-clean + mutation-tested.

Two halves:
  1. The regenerated reference bank must come out CLEAN under the
     calibrated deck (every calibrated floor documented in sramforge/drc.py
     is the golden-observed value, so clean means "nowhere worse than the
     silicon-validated reference").
  2. Mutation tests: five classes of injected damage, each of which must
     fire the specific DRM (Design Rule Manual) rule that governs it.

Usage:  python3 tests/test_drc.py
"""
import sys
import pathlib
import tempfile

import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import BankSpec, generate            # noqa: E402
from sramforge.drc import DrcDeck, LAYERS           # noqa: E402


def run_deck(gds_path, top_name):
    ly = kdb.Layout()
    ly.read(gds_path)
    deck = DrcDeck(ly, ly.cell(top_name))
    deck.run()
    return deck.violations


def mutate_and_run(gds_path, top_name, shapes):
    """Copy the layout, insert (layer_name, Box) shapes into the top
    cell, run the deck, return the set of fired rule ids."""
    ly = kdb.Layout()
    ly.read(gds_path)
    top = ly.cell(top_name)
    for layer_name, box in shapes:
        l, d = LAYERS[layer_name]
        top.shapes(ly.layer(l, d)).insert(box)
    deck = DrcDeck(ly, top)
    deck.run()
    return {v.rule for v in deck.violations}


# All mutations are placed in empty space right of the bank (x > 40000)
# so they interact with nothing and fire exactly their own rule.
MUTATIONS = [
    ("narrow metal sliver (M2 40 dbu = 10 nm wide)",
     [("m2", kdb.Box(50000, 1000, 50040, 3000))],
     "M2.W.1"),
    ("metal spacing violation (two wide M1 bars 40 dbu = 10 nm apart)",
     [("m1", kdb.Box(50000, 10000, 50200, 10600)),
      ("m1", kdb.Box(50240, 10000, 50440, 10600))],
     "M1.S.1"),
    ("floating via (V1 with no M1/M2 anywhere near)",
     [("v1", kdb.Box(50000, 20000, 50072, 20072))],
     "V1.AUX.1a"),
    ("undersized metal island (M1 60 x 60 dbu = 225 nm^2)",
     [("m1", kdb.Box(50000, 30000, 50060, 30060))],
     "M1.A.1"),
    ("off-grid gate stripe (80 dbu wide, shifted off the 54 nm pitch)",
     [("gate", kdb.Box(50006, 40000, 50086, 41000))],
     "GATE.S.1"),
]


def main() -> int:
    spec = BankSpec()
    fails = 0
    with tempfile.TemporaryDirectory() as td:
        gds = str(pathlib.Path(td) / f"{spec.name}.gds")
        generate(spec, gds)

        # ---- half 1: the golden reference must be clean ----
        violations = run_deck(gds, spec.name)
        if violations:
            fails += 1
            print(f"[FAIL] reference bank not DRC-clean: "
                  f"{len(violations)} categories")
            for v in violations[:10]:
                print(f"    [{v.rule}] {v.detail}: {v.count}")
        else:
            print("[PASS] reference bank DRC-clean under the calibrated deck")

        # ---- half 2: every mutation must fire its rule ----
        for desc, shapes, expected_rule in MUTATIONS:
            fired = mutate_and_run(gds, spec.name, shapes)
            if expected_rule in fired:
                print(f"[PASS] mutation caught: {desc} -> {expected_rule}")
            else:
                fails += 1
                print(f"[FAIL] mutation MISSED: {desc} "
                      f"(expected {expected_rule}, fired: {sorted(fired)})")

    if fails:
        print(f"\nFAIL: {fails} rung-3 failures")
        return 1
    print("\nPASS: rung 3 - golden clean, all mutations caught")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
