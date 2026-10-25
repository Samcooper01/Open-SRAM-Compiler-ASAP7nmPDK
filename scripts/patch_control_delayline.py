#!/usr/bin/env python3
"""Patch the control netlist's sense-enable delay line to a binary-
weighted one, so that deep banks can be timed.

The reference times sae with five AO22 stages behind the latched taps
sdel<4:0>: tap i high selects the short path around stage i+1, low
inserts it, and each inserted stage adds only 12 to 24 ps, so the
whole line spans 204 to 277 ps (measured, scripts/sae_margin.py).  The
far row of a 64-row stack (or a 32-row stack at eight strips: the same
wordline wire) is not up before about 350 ps, so those banks cannot be
timed by any tap setting.

This patch keeps the tap latches, their complements, the chain input
(nm3_116 / nm2_221: the read-or-write-and-enable term) and the chain
output (nm3_134, into the nine-buffer sae driver), and rebuilds the
five stages so that stage k inserts 2**(k+1) buffer PAIRS (BUFx2) on its
long path:

    in_0 = nm2_221
    stage k:  out_k = AO22(A1 = in_k, A2 = q_k, B1 = qn_k,
                           B2 = in_k delayed by 2**(k+1) pairs)
    in_(k+1) = out_k;  out_4 = nm3_134

so the five pins carry a 5-bit code: bit k = tap k = sdel<k>, 1 =
short, 0 = long; 11111 is the minimum delay as before (every existing
tie stays valid) and 00000 the maximum, 62 pairs.  The unit is two
pairs, not one: at one pair the line reached 894 ps at the typical
corner, short of the 1030 ps that 64 rows at eight strips need
(measured).  Two pairs per unit buys about
1.5 ns of reach for 62 more buffers, at twice the step (about 42 ps),
which the 20 ps timing margin absorbs.  The code a bank needs is
computed by the generator from the timing benches (section 2 of the
plan) and stated in its views.

Re-appliable: if the target already carries the UDL cells it removes
them and rebuilds at the current weights, so a weight change needs no
re-extraction.  Run
after scripts/patch_control_yselb.py, before scripts/patch_control_s8
.py and pnr/gen_inputs.py.
"""
import pathlib
import re

V = pathlib.Path(__file__).resolve().parent.parent / "out" / "control_netlist.v"

# the reference chain: five AO22 stages, their five INVxp33 tap
# complements (kept: they are the qn_k inputs), and the five BUFx2
# between stages plus the input buffer of stage 0's short path
OLD_STAGES = ["U564", "U605", "U643", "U402", "U401"]
OLD_BUFS = ["U561", "U560", "U701", "U693", "U633"]
# per stage k: (latched tap q_k, its complement qn_k), measured
TAPS = [("nm3_123", "nm2_237"), ("nm3_99", "nm3_96"), ("nm3_139", "nm3_89"),
        ("nm3_87", "nm3_86"), ("nm3_114", "nm3_105")]
CHAIN_IN, CHAIN_OUT = "nm2_221", "nm3_134"


def main() -> int:
    s = V.read_text()
    assert "yselb_0" in s, "run scripts/patch_control_yselb.py first"
    removed = 0
    if "UDL0_" in s:
        # RE-WEIGHT: an earlier run of this script already replaced the
        # reference chain, so remove ITS cells and wires and rebuild at
        # the current weights (the script converges to the weighting it
        # currently defines instead of refusing, which would otherwise
        # need a full re-extraction to change a weight).
        s, removed = re.subn(r"^\s*\S+\s+UDLS?\d+_?\d*\s*\(.*?\);\n", "",
                             s, flags=re.M | re.S)
        s = re.sub(r"^\s*wire udl\d+_\w+;\n", "", s, flags=re.M)
        print(f"re-weighting: {removed} cells of the previous delay line "
              f"removed")
    else:
        for name in OLD_STAGES + OLD_BUFS:
            s, n = re.subn(r"^\s*\S+\s+" + name + r"\s*\(.*?\);\n", "", s,
                           flags=re.M | re.S)
            assert n == 1, f"{name}: expected one instance, found {n}"
            removed += n
    for q, qn in TAPS:
        assert (f".Q({q})" in s or f".Y({q})" in s) and f".Y({qn})" in s, (q, qn)
    cells = []
    wires = []
    cur = CHAIN_IN
    for k, (q, qn) in enumerate(TAPS):
        pairs = 2 ** (k + 1)
        prev = cur
        for j in range(2 * pairs):
            net = f"udl{k}_{j}"
            wires.append(net)
            cells.append(f"  BUFx2_ASAP7_75t_R UDL{k}_{j} (.A({prev}), .Y({net}));")
            prev = net
        out = CHAIN_OUT if k == len(TAPS) - 1 else f"udl{k}_out"
        if out != CHAIN_OUT:
            wires.append(out)
        cells.append(f"  AO22x2_ASAP7_75t_R UDLS{k} (.A1({cur}), .A2({q}), "
                     f".B1({qn}), .B2({prev}), .Y({out}));")
        cur = out
    s = s.replace("  wire aq8;",
                  "  wire aq8;\n" + "".join(f"  wire {w};\n" for w in wires).rstrip("\n"))
    s = s.replace("endmodule", "\n".join(cells) + "\nendmodule")
    V.write_text(s)
    n_pairs = sum(2 ** (k + 1) for k in range(len(TAPS)))
    print(f"patched {V}: {removed} reference delay-line cells removed, "
          f"{len(cells)} added ({len(cells) - 5} BUFx2 in {n_pairs} pairs, "
          f"5 AO22)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
