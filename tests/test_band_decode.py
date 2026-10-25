#!/usr/bin/env python3
"""Rung 4.4: band-level decode. Every address selects exactly one wordline,
in the right decoder, graded at the decoders' own outputs.

Rung 4.5 grades the whole bank (two hours at bits=64) and can only say
WHICH bank wordline is wrong. This rung extracts just the decode band
cell (about 6-9k devices, a few seconds), drives the same address pins,
and watches the raw per-decoder outputs wl<k>: each k is labeled once per
decoder (two decoders at bits <= 32, four at bits > 32), so a wordline net
is identified by (decoder, k), where the decoder is read off the x
position of the net's DRIVER devices, never off the net's bounding box
(a net welded across a seam has a meaningless bounding box; that blind
spot hid four dead columns for a whole campaign, bits64_plan Log 32).

Checks, for every half h and column c:
  * live column (c < bits): the settled wordline set is EXACTLY
    {(decoder(h, c), c mod 32)}: one-hot, right decoder, right index;
  * dead column (bits <= c < 32, sparse widths): nothing labeled settles;
  * no net carries a predecode label (PA/PB/PC/WLENA) together with a
    supply label: the enable-rail weld class that rung 2 cannot see
    (enables are not one of its probed net classes);
  * mutation: a stuck wordline driver in one decoder must break the sweep.

Usage:  python3 tests/test_band_decode.py [--config bits,strips,depth]
"""
import sys
import pathlib
import tempfile
from collections import Counter

import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import BankSpec, generate            # noqa: E402
from sramforge.lvs import Extraction                # noqa: E402
from sramforge.switchsim import SwitchSim           # noqa: E402

# Decoder positions in band coordinates (fixed for every width: the sparse
# scheme keeps the reference frame, and the bits>32 copies sit one decode
# span outside the reference pair).  A wordline net belongs to the decoder
# whose span holds the mean x of its driver devices.
DECODER_EDGES = ((1648, "copyL"), (20000, "refL"), (38528, "refR"))


def decoder_of_x(x):
    for edge, name in DECODER_EDGES:
        if x < edge:
            return name
    return "copyR"


def expected(h, c):
    """(decoder, local index) that address half h / column c must select:
    columns 0-31 live in the reference pair, 32-63 in the copies; half 0
    is the right-hand (m90) decoder of each pair."""
    if c >= 32:
        return ("copyR" if h == 0 else "copyL", c - 32)
    return ("refR" if h == 0 else "refL", c)


class BandBench:
    def __init__(self, extraction, wlmap, nine):
        labels = ([f"wl<{k}>" for k in range(32)]
                  + ["clk", "banksel", "read", "byte_wrenab"]
                  + [f"ADDRESS<{i}>" for i in range(9 if nine else 8)]
                  + [f"sdel<{i}>" for i in range(5)])
        self.sim = SwitchSim(extraction, labels)
        self.wlmap = wlmap
        self.nine = nine
        self.state = None

    def inputs(self, addr, clk):
        s = self.sim
        f = {}
        for i in range(9 if self.nine else 8):
            f[s.net_of(f"ADDRESS<{i}>")] = (addr >> i) & 1
        for name, v in (("clk", clk), ("banksel", 1), ("read", 1),
                        ("byte_wrenab", 0)):
            f[s.net_of(name)] = v
        for i in range(5):
            f[s.net_of(f"sdel<{i}>")] = 1     # delay taps: value irrelevant
        return {n: v for n, v in f.items() if n is not None}

    def cycle(self, addr):
        """One clock cycle; returns the set of (decoder, k) settled high."""
        self.state, _ = self.sim.run(self.inputs(addr, 0), state=self.state,
                                     max_steps=400)
        self.state, _ = self.sim.run(self.inputs(addr, 1), state=self.state,
                                     max_steps=400)
        return {self.wlmap[n] for n in self.wlmap if self.state.get(n) == 1}

    def address(self, h, c):
        return (h << (8 if self.nine else 7)) | (c << 2)


def wordline_map(extraction):
    """net -> (decoder, k) for every labeled decoder output, by driver x."""
    wlmap = {}
    for k in range(32):
        for n in extraction.nets_labeled(f"wl<{k}>"):
            drv = [d for d in extraction.devices if n in d.sd]
            if not drv:
                continue                     # a label on undriven metal
            mx = sum(d.bbox.center().x for d in drv) // len(drv)
            wlmap[n] = (decoder_of_x(mx), k)
    return wlmap


def run_sweep(bench, bits):
    """Returns a list of failure strings (empty = pass)."""
    fails = []
    bench.cycle(0)
    bench.cycle(0)                           # flush start-up unknowns
    if bits > 32:
        # the copies' column-pair keepers settle on their first strobe
        # (real power-up behavior): one unmeasured access per live pair
        for c in range(32, bits, 2):
            for h in (0, 1):
                bench.cycle(bench.address(h, c))
    for h in (0, 1):
        for c in range(max(bits, 32)):
            got = bench.cycle(bench.address(h, c))
            want = {expected(h, c)} if c < bits else set()
            if got != want:
                fails.append(f"half {h} col {c}: settled {sorted(got)}, "
                             f"expected {sorted(want)}")
            if len(fails) > 8:
                fails.append("(stopping early)")
                return fails
    return fails


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="32,4,32")
    a = ap.parse_args()
    b, st, dp = map(int, a.config.split(","))
    nine = (b != 32 or st != 4 or dp > 48)   # band v2 (ninth address bit)
    spec = BankSpec(bits_per_group=b, strips_per_group=st, depth_groups=dp)
    band_name = f"ctrl_decode_v2_{b}" if nine else "ctrl_decode_32"
    fails = 0

    with tempfile.TemporaryDirectory() as td:
        gds = str(pathlib.Path(td) / f"{spec.name}.gds")
        generate(spec, gds)
        ly = kdb.Layout()
        ly.read(gds)
        band = ly.cell(band_name)
        if band is None:
            print(f"FAIL: no band cell {band_name} in {spec.name}")
            return 1
        ex = Extraction(ly, band)
    print(f"extracted band {band_name}: {len(ex.devices)} devices")

    # ---- decoder census: every k labeled once per decoder ----
    wlmap = wordline_map(ex)
    n_dec = 4 if b > 32 else 2
    census = Counter(dec for dec, k in wlmap.values())
    per_k = Counter(k for dec, k in wlmap.values())
    live_k = range(min(b, 32))
    census_ok = (len(census) == n_dec
                 and all(per_k[k] == n_dec for k in live_k))
    print(f"[{'PASS' if census_ok else 'FAIL'}] decoder census: "
          f"{dict(census)} (want {n_dec} decoders, each k once per decoder)")
    fails += 0 if census_ok else 1

    # ---- enable-rail welds (invisible to rung 2) ----
    welded = [n for n, labs in ex.net_labels.items()
              if labs & {"VDD", "VSS"}
              and any(x.startswith(("PA", "PB", "PC", "WLENA"))
                      for x in labs)]
    print(f"[{'PASS' if not welded else 'FAIL'}] predecode rails: "
          f"{len(welded)} net(s) carry a PA/PB/PC/WLENA label with a supply")
    fails += 0 if not welded else 1

    # ---- the sweep ----
    bench = BandBench(ex, wlmap, nine)
    sweep = run_sweep(bench, b)
    n_addr = 2 * max(b, 32)
    if sweep:
        fails += len(sweep)
        print(f"[FAIL] band decode sweep ({n_addr} (half, column) addresses):")
        for f in sweep[:10]:
            print("    -", f)
    else:
        print(f"[PASS] band decode sweep: {n_addr} (half, column) addresses "
              f"each settle exactly one wordline in the right decoder"
              + (f"; {2 * (32 - b)} dead-column addresses settle none"
                 if b < 32 else ""))

    # ---- mutation: a stuck driver on one decoder's wl<1> ----
    target = next((n for n, v in wlmap.items() if v == ("refL", 1)), None)
    vss = next(iter(bench.sim.vss))
    stuck = [d._replace(gate=vss) if target in d.sd else d
             for d in ex.devices]

    class Mut:
        pass
    mut = Mut()
    mut.devices = stuck
    mut.net_labels = ex.net_labels
    mfails = run_sweep(BandBench(mut, wlmap, nine), b)
    print(f"[{'PASS' if mfails else 'FAIL'}] mutation caught: stuck refL "
          f"wl<1> driver -> {len(mfails)} sweep failures")
    fails += 0 if mfails else 1

    if fails:
        print(f"\nFAIL: {fails} rung-4.4 failures")
        return 1
    print("\nPASS: rung 4.4 - every address decodes to exactly one wordline "
          "in the right decoder, at the decoders' own outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
