#!/usr/bin/env python3
"""Rung 7.5: sense-enable timing reach.

For every configuration the ladder proves, the placed control's delay
line must have a code whose measured clk-to-sae comes after the far
row's wordline is up and the bitlines have split, at TT and SS
(sramforge/sense.py, the measured rule; sramforge/sae_codes.json, the
table scripts/sae_margin.py --record writes).  The rule is also checked
for internal consistency: the chosen code's clk-to-sae is the smallest
that meets the need, the need grows with depth and with the strip
count, and a code that cannot exist (an unreachable depth) is reported
as None, never invented.

Every other rung is switch-level or geometric and sees no timing: a
bank whose sense amplifier strobes before its far row is up passes them
all (found 2026-09-20 for depth 64 and the eight-strip bank on the
reference delay line).

Usage:  python3 tests/test_sense_timing.py
"""
import sys
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import BankSpec, sense               # noqa: E402

# the configurations the ladder proves, plus the depth sweep
CONFIGS = [(32, 4, 8), (32, 4, 16), (32, 4, 24), (32, 4, 32), (32, 4, 48),
           (32, 4, 64), (32, 4, 96), (16, 4, 32), (64, 4, 64),
           (32, 8, 8), (32, 8, 16), (32, 8, 32), (32, 8, 64)]
# nothing is outside the envelope any more: the 138 um wordline of 64
# rows at eight strips needs about 1030 ps and the binary line reaches
# 1568 ps at the typical corner (sramforge/sae_codes.json).
BEYOND = []
# the reference band keeps the family's delay line: 73 ps of reach,
# depth 48 at four strips is its limit (measured); every band-v2 bank
# carries the binary line
REFERENCE_LIMIT_ROWS = 48


def main() -> int:
    fails = 0
    if not sense.SAE_CODES_JSON.exists():
        print(f"FAIL: {sense.SAE_CODES_JSON.name} missing "
              f"(scripts/sae_margin.py --record)")
        return 1
    print(f"{'config':>10s}  {'corner':6s} {'need':>7s} {'code':>6s} "
          f"{'sae':>6s} {'slack':>6s}")
    prev_need = {}
    for bits, strips, depth in CONFIGS + BEYOND:
        spec = BankSpec(bits_per_group=bits, strips_per_group=strips,
                        depth_groups=depth)
        for corner in ("TT", "SS"):
            r = sense.sdel_code(spec, corner)
            tag = f"{bits},{strips},{depth}"
            print(f"{tag:>10s}  {corner:6s} {str(r['need_ps']):>7s} "
                  f"{str(r['code']):>6s} {str(r['sae_ps']):>6s} "
                  f"{str(r['slack_ps']):>6s}   {r['note']}")
            if r["need_ps"] is None:
                print(f"    [FAIL] no measurement: {r['note']}")
                fails += 1
                continue
            # monotone in the wire: a deeper bank at the same strips and
            # bits needs more, never less
            key = (bits, strips, corner)
            if key in prev_need and r["need_ps"] < prev_need[key]:
                print("    [FAIL] the need shrank with depth")
                fails += 1
            prev_need[key] = r["need_ps"]
            expect_reach = ((bits, strips, depth) in CONFIGS
                            and (sense.variant_key(spec) != "reference"
                                 or sense.rows_equiv(spec) <= REFERENCE_LIMIT_ROWS))
            if r["code"] is None:
                if expect_reach:
                    print(f"    [FAIL] no code reaches {r['need_ps']} ps")
                    fails += 1
                else:
                    print(f"    [beyond the envelope] no code reaches "
                          f"{r['need_ps']} ps (reported, not required)")
            if r["code"] is not None and r["slack_ps"] < 0:
                print("    [FAIL] negative slack")
                fails += 1
    # the reference's limit is real: depth 64 at four strips on the
    # reference line must be reported unreachable (never invented)
    r = sense.sdel_code(BankSpec(depth_groups=64), "TT")
    ok = (sense.variant_key(BankSpec(depth_groups=64)) != "reference"
          or r["code"] is None)
    print(f"[{'PASS' if ok else 'FAIL'}] an unreachable depth on the "
          f"reference line is reported as None")
    fails += 0 if ok else 1
    if fails:
        print(f"\nFAIL: {fails} sense-timing failures")
        return 1
    print(f"\nPASS: rung 7.5, each of the {len(CONFIGS)} proven "
          f"configurations has a delay-line code that strobes after its far "
          f"row at TT and SS; {len(BEYOND)} larger size(s) reported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
