#!/usr/bin/env python3
"""Rung 3b: DRC (Design Rule Check) deck on NON-reference configurations,
under the "no worse than the reference" policy.

Rung 3 (tests/test_drc.py) proves the reference configuration 32,4,32 is
clean under the calibrated deck.  The generated non-reference banks are
NOT clean: the 16,4,32 and 64,4,64 banks fire a known set of rule
categories (documented in docs/external_signoff.md sections 3.3 and 3.4).
This rung turns that known debt into a regression fence:

  * Every rule category that fires on a configuration must be listed in
    the per-configuration baseline `tests/drc_baselines.json`.  A category
    that fires and is not listed FAILS the test.  Because the reference
    bank fires nothing (rung 3), a category not in the baseline is by
    definition one that does not fire on the reference either.
  * A listed category may not fire MORE markers than its baseline count.
    A higher count FAILS the test.
  * A lower count is reported as reduced debt and PASSES; reducing the
    recorded debt requires a deliberate `--rebaseline`, so the record in
    the JSON (JavaScript Object Notation) file always reflects an
    intentional state, never an accidental one.

The baseline is regenerated with

    python3 tests/test_drc_configs.py --rebaseline            # all configs
    python3 tests/test_drc_configs.py --rebaseline 16,4,32    # one config

and every rebaseline must be committed together with the reason for the
change (a generator fix that reduced the debt, or a deck change that
measures differently).  The baseline JSON (JavaScript Object Notation)
also records up to MAX_EXAMPLES example boxes per rule (database units,
1 dbu = 0.25 nm) so the recorded debt can be located in the layout.

Runtime: the deck takes 20 s to 2 minutes on the 16 bank and 2 to several
minutes on the 64 bank depending on machine load, so every configuration
runs the deck exactly once per test invocation.  `--only bits,strips,depth`
restricts a run to the listed configurations (development use; the full
fence is what run_all.py executes).

Usage:  python3 tests/test_drc_configs.py [--rebaseline [cfg ...]] [--only cfg ...]
"""
import argparse
import json
import pathlib
import sys
import tempfile
import time

import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import BankSpec, generate            # noqa: E402
from sramforge.drc import DrcDeck                   # noqa: E402

BASELINE_PATH = pathlib.Path(__file__).resolve().parent / "drc_baselines.json"

# (bits_per_group, strips_per_group, depth_groups) of every fenced config
CONFIGS = [(16, 4, 32), (64, 4, 64), (32, 8, 32)]


def run_deck(gds_path, top_name):
    ly = kdb.Layout()
    ly.read(gds_path)
    deck = DrcDeck(ly, ly.cell(top_name))
    deck.run()
    return deck.violations


def measure(spec):
    """Generate the bank, run the deck, return {rule: (count, examples)}."""
    with tempfile.TemporaryDirectory() as td:
        gds = str(pathlib.Path(td) / f"{spec.name}.gds")
        t0 = time.time()
        generate(spec, gds)
        t1 = time.time()
        violations = run_deck(gds, spec.name)
        t2 = time.time()
    print(f"    {spec.name}: generate {t1 - t0:.0f} s, deck {t2 - t1:.0f} s, "
          f"{sum(v.count for v in violations)} markers in "
          f"{len(violations)} categories")
    return {v.rule: (v.count, [[b.left, b.bottom, b.right, b.top]
                               for b in v.examples])
            for v in violations}


def load_baseline():
    if not BASELINE_PATH.exists():
        return {}
    with open(BASELINE_PATH) as f:
        return json.load(f)


def save_baseline(baseline):
    with open(BASELINE_PATH, "w") as f:
        json.dump(baseline, f, indent=1, sort_keys=True)
        f.write("\n")


def check_config(spec, measured, baseline):
    """Apply the policy for one configuration.  Returns the failure count."""
    fails = 0
    entry = baseline.get(spec.name)
    if entry is None:
        print(f"[FAIL] {spec.name}: no baseline recorded "
              f"(run with --rebaseline {spec.bits_per_group},"
              f"{spec.strips_per_group},{spec.depth_groups})")
        return 1
    known = entry["rules"]
    for rule in sorted(measured):
        count, examples = measured[rule]
        if rule not in known:
            fails += 1
            print(f"[FAIL] {spec.name}: NEW category {rule} fires "
                  f"{count} (not in baseline, not on the reference); "
                  f"e.g. {examples[:2]}")
        elif count > known[rule]["count"]:
            fails += 1
            print(f"[FAIL] {spec.name}: {rule} fires {count} > baseline "
                  f"{known[rule]['count']}; e.g. {examples[:2]}")
        elif count < known[rule]["count"]:
            print(f"[NOTE] {spec.name}: {rule} fires {count} < baseline "
                  f"{known[rule]['count']} (debt reduced; rebaseline "
                  f"deliberately to record it)")
    for rule in sorted(known):
        if rule not in measured:
            print(f"[NOTE] {spec.name}: {rule} no longer fires (baseline "
                  f"{known[rule]['count']}; rebaseline to record it)")
    total = sum(c for c, _ in measured.values())
    if not fails:
        print(f"[PASS] {spec.name}: {total} markers in {len(measured)} "
              f"categories, none new, none above baseline "
              f"(baseline {entry['total']} in {len(known)})")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rebaseline", nargs="*", metavar="bits,strips,depth",
                    help="measure and record the baseline for the listed "
                         "configurations (all fenced configurations when "
                         "none are listed)")
    ap.add_argument("--only", nargs="+", metavar="bits,strips,depth",
                    help="check only the listed configurations")
    a = ap.parse_args()

    def parse(cfgs):
        return [tuple(int(x) for x in s.split(",")) for s in cfgs]

    if a.rebaseline is not None:
        wanted = parse(a.rebaseline) or CONFIGS
        baseline = load_baseline()
        for cfg in wanted:
            spec = BankSpec(*cfg)
            measured = measure(spec)
            baseline[spec.name] = {
                "config": {"bits_per_group": cfg[0],
                           "strips_per_group": cfg[1],
                           "depth_groups": cfg[2]},
                "total": sum(c for c, _ in measured.values()),
                "rules": {rule: {"count": c, "examples": ex}
                          for rule, (c, ex) in sorted(measured.items())},
            }
            print(f"    recorded {spec.name}: {baseline[spec.name]['total']} "
                  f"markers in {len(measured)} categories")
        save_baseline(baseline)
        print(f"baseline written to {BASELINE_PATH}")
        return 0

    baseline = load_baseline()
    fails = 0
    for cfg in (parse(a.only) if a.only else CONFIGS):
        spec = BankSpec(*cfg)
        measured = measure(spec)
        fails += check_config(spec, measured, baseline)
    if fails:
        print(f"\nFAIL: {fails} rung-3b failures (non-reference DRC "
              f"(Design Rule Check) regressions)")
        return 1
    print("\nPASS: rung 3b: non-reference configurations no worse than "
          "their recorded baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
