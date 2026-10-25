#!/usr/bin/env python3
"""Energy per access of a generated bank, in picojoules.

The timing library ships leakage power today; this is the other half of
the power story, and the number anyone integrating the macro has to
budget.  It is built from the same benches the timing comes from, so it
is composed rather than guessed, and every term is reported separately:

    access energy = columns x column energy + one wordline

where the column energy is the supply charge a column draws over a full
precharge, access and restore cycle (measured on the column bench: one
addressed cell, the rest of the column as real loads, the bitline wire
and the precharge devices) and the wordline term is the supply charge to
drive one row at this depth and strip count.

Not included, and stated rather than hidden: the control block's own
switching.  It is a fixed overhead per access, independent of the array,
and measuring it needs the decode slice from the characterization flow.

    python3 scripts/access_energy.py [--config 32,4,32] [--corners TT,SS,FF]

Writes out/access_energy.json and prints the table.
"""
import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import BankSpec, timing                        # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="32,4,32")
    ap.add_argument("--corners", default="TT,SS,FF")
    a = ap.parse_args()
    timing.require_tools()          # say what is missing before printing anything
    b, st, dp = map(int, a.config.split(","))
    spec = BankSpec(bits_per_group=b, strips_per_group=st, depth_groups=dp)
    work = REPO / "out" / "timing"
    # every column of the addressed row develops a bitline differential,
    # both arrays of the column group, one column per bit position
    columns = 2 * b
    rec = {"config": spec.name, "bits": b, "strips": st, "depth": dp,
           "columns_per_access": columns, "vdd_v": timing.VDD,
           "method": ("column energy from the column bench with a supply "
                      "probe, integrated over the whole cycle; wordline "
                      "energy from the wordline bench at this depth and "
                      "strip count; control-block switching NOT included"),
           "corners": {}}
    print(f"{'corner':6s} {'column read':>12s} {'column write':>13s} "
          f"{'wordline':>10s} {'read access':>12s} {'write access':>13s}")
    for corner in a.corners.split(","):
        row = {}
        for kind in ("read", "write"):
            raw = timing.run_deck(timing.energy_bench(kind, b, corner), work,
                                  f"energy_{kind}_{b}_{corner}")
            m = timing.measure_energy(raw)
            row[f"column_{kind}_fj"] = m["energy_fj"] if m else None
        wl_deck = timing.wordline_bench(dp, st, corner).replace(
            "write OUT.raw", "write OUT.raw i(Vdd)")
        raw = timing.run_deck(wl_deck, work, f"energy_wl_{dp}_{st}_{corner}")
        m = timing.measure_energy(raw)
        row["wordline_fj"] = m["energy_fj"] if m else None
        for kind in ("read", "write"):
            c, w = row[f"column_{kind}_fj"], row["wordline_fj"]
            row[f"{kind}_access_pj"] = (round((columns * c + w) / 1000, 4)
                                        if c is not None and w is not None
                                        else None)
        rec["corners"][corner] = row
        print(f"{corner:6s} {row['column_read_fj']:>10.2f} fJ "
              f"{row['column_write_fj']:>11.2f} fJ "
              f"{row['wordline_fj']:>8.2f} fJ "
              f"{row['read_access_pj']:>10.3f} pJ "
              f"{row['write_access_pj']:>11.3f} pJ")
    (REPO / "out" / "access_energy.json").write_text(json.dumps(rec, indent=1))
    print("\nwrote out/access_energy.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
