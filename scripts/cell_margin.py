#!/usr/bin/env python3
"""Static margins of the generated bank's 6T (six-transistor) bitcell:
hold and read static noise margin, and the write trip point, at every
process corner.

These are the numbers a memory reviewer asks for first, and they are
properties of the CELL, so they hold for every configuration the
compiler emits.  The cell is the vendored ASAP7 (Arizona State
University 7 nanometre predictive Process Design Kit) 6T cell exactly
as rung 4 extracted it: access and pull-down two fins each, pull-up one
fin.  The cell ratio (pull-down over access) is therefore 1.0 and the
pull-up ratio 0.5; fin quantization allows nothing between.

What is NOT here, and why: a margin distribution.  Margin at five or
six sigma needs mismatch models, and the kit ships three nominal corner
cards with no statistical parameters at all, so any sigma number would
be invented.  The corner spread below is the honest substitute.

    python3 scripts/cell_margin.py [--corners TT,SS,FF]

Writes out/cell_margin.json and prints the table.
"""
import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import timing                                  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corners", default="TT,SS,FF")
    a = ap.parse_args()
    timing.require_tools()          # say what is missing before printing anything
    work = REPO / "out" / "timing"
    rec = {"vdd_v": timing.VDD, "temp_c": 25, "cell": "sram6t122",
           "sizing": {"access_fins": 2, "pulldown_fins": 2, "pullup_fins": 1,
                      "cell_ratio": 1.0, "pullup_ratio": 0.5},
           "method": ("hold and read static noise margin from the half-cell "
                      "transfer curve (the largest square inscribed in the "
                      "butterfly lobe, computed in rotated coordinates); "
                      "write trip point from a 20 ns quasi-static bitline "
                      "ramp with the wordline high"),
           "corners": {}}
    print(f"{'corner':6s} {'hold SNM':>9s} {'read SNM':>9s} "
          f"{'write trip':>11s} {'write margin':>13s}")
    for corner in a.corners.split(","):
        row = {}
        for kind in ("hold", "read"):
            raw = timing.run_deck(timing.margin_bench(kind, corner), work,
                                  f"margin_{kind}_{corner}")
            row[f"{kind}_snm_mv"] = timing.measure_snm(raw)
        raw = timing.run_deck(timing.margin_bench("write", corner), work,
                              f"margin_write_{corner}")
        row.update(timing.measure_write_trip(raw))
        rec["corners"][corner] = row
        print(f"{corner:6s} {str(row['hold_snm_mv']):>7s} mV "
              f"{str(row['read_snm_mv']):>7s} mV "
              f"{str(row['trip_mv']):>9s} mV "
              f"{str(row['write_margin_mv']):>11s} mV")
    (REPO / "out" / "cell_margin.json").write_text(json.dumps(rec, indent=1))
    print("\nwrote out/cell_margin.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
