#!/usr/bin/env python3
"""Rung 4.5 - functional equivalence: the layout COMPUTES the right thing.

Drives the extracted transistor netlist (rung 4) with a charge-retaining
switch-level simulator (sramforge/switchsim.py) - no SPICE (Simulation
Program with Integrated Circuit Emphasis), no timing, pure switch logic -
and checks the bank's FUNCTION against the documented organization:

  * full read sweep (256 addresses; 512 at eight strips): for every
    address exactly ONE wordline pulses, and it is the one the measured
    address map demands:
        ADDRESS[1:0] = column-mux select (never changes the wordline)
        ADDRESS[6:2] = wordline column index
        ADDRESS[7]   = half select (0 = bottom wlb, 1 = top wlt)
    (band v2: half = ADDRESS[8], column = ADDRESS[7:2]; the eight-strip
    variant: half = ADDRESS[8], mux = ADDRESS[7] and ADDRESS[1:0]), and
    per half and mux leg exactly ONE column-select line is high: the
    left array's yselt<k> at half 1, the right array's yselb<k> at half 0
  * the matching half's wordline-enable pulses, the other half stays
    quiet, and sense-amp enable (sae) fires on every read
  * a write pulses the write driver enable (wrena) and NOT sae
  * with banksel low, nothing pulses at all
  * mutations: a stuck wordline driver and a swapped wordline pair must
    each break the sweep.

Usage:  python3 tests/test_functional.py
"""
import sys
import pathlib
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import BankSpec, generate            # noqa: E402
from sramforge.lvs import extract                   # noqa: E402
from sramforge.switchsim import SwitchSim           # noqa: E402

CTL_LABELS = ["wlenat", "wlenab", "wlenat_hi", "wlenab_hi",
              "sae", "wrena"]
SEL_LABELS = [f"ysel{f}<{k}>" for f in ("t", "b") for k in range(8)]
# module-level knobs set by main() from the config: NINE=True for the
# band-v2 (patched-control) map - half = ADDRESS[8], column =
# ADDRESS[7:2]; the 32-bit reference keeps the original 8-bit map.
# S8=True for the eight-strip control variant (band v2 "s8"): ADDRESS[7]
# is the third column-mux bit, the column field is ADDRESS[6:2].
BITS = 32
NINE = False
S8 = False
N_STRIPS = 4
WL_LABELS = ([f"wlb<{i}>" for i in range(32)]
             + [f"wlt<{i}>" for i in range(32)])


def expected_wl(addr):
    """The measured address map (8-bit reference / 9-bit band v2)."""
    if NINE:
        half = "wlt" if addr & 0x100 else "wlb"
        return f"{half}<{(addr >> 2) & (31 if S8 else 63)}>"
    half = "wlt" if addr & 0x80 else "wlb"
    return f"{half}<{(addr >> 2) & 31}>"


class Bench:
    def __init__(self, extraction):
        self.settled = set()
        self.sim = SwitchSim(extraction, WL_LABELS + CTL_LABELS
                             + SEL_LABELS)
        self.watch = {}
        for lb in WL_LABELS + CTL_LABELS + SEL_LABELS:
            nets = self.sim.label_nets.get(lb, set())
            if len(nets) == 1:
                self.watch[next(iter(nets))] = lb
        self.state = None

    def inputs(self, addr, clk, banksel=1, read=1, write=0):
        s = self.sim
        f = {}
        for i in range(9 if NINE else 8):
            f[s.net_of(f"ADDRESS<{i}>")] = (addr >> i) & 1
        f[s.net_of("clk")] = clk
        f[s.net_of("banksel")] = banksel
        f[s.net_of("read")] = read
        f[s.net_of("byte_wrenab")] = write
        for i in range(5):
            f[s.net_of(f"sdel<{i}>")] = 1
        return f

    def cycle(self, addr, **kw):
        """One full clock cycle.  Returns the set of labels that PULSED
        during the clk-high phase (the observable for the self-timed
        control strobes), and leaves the SETTLED set - labels still high
        at the end of the phase - in self.settled.

        Wordlines are graded on `settled`, not on `pulsed`: the decoder
        drives them as levels for the whole access, while `pulsed` marks
        a line high at ANY step, seeds itself from the incoming state,
        and therefore also reports charge left over from the previous
        cycle (which made undriven columns look like they fired forever
        after - bits64_plan log 23)."""
        self.state, _ = self.sim.run(self.inputs(addr, 0, **kw),
                                     state=self.state, max_steps=400)
        self.state, pulsed = self.sim.run(self.inputs(addr, 1, **kw),
                                          state=self.state, max_steps=400,
                                          watch=set(self.watch))
        self.settled = {self.watch[n] for n in self.watch
                        if self.state.get(n) == 1}
        return {self.watch[n] for n, p in pulsed.items() if p}


# DEAD COLUMNS at dual-decoder widths: NONE (bits64_plan Logs 27-32).
# Class B (Log 27/28): the position-1 columns were a PC<1> weld (the _hi_n
#   feed + seam bridges); isolating the copy's PC<1> fixed them.
# Class A (Log 32): columns 32-35 were the copy's io-adjacent slice,
#   amputated by the 13176 trim.  The io-slice is now KEPT (trim at the
#   true boundary 13824) and the five seam welds that made the old trim
#   load-bearing are severed/reconnected in band.py, so all 64 columns
#   decode exclusively.  SRAMFORGE_ABLATE=legacy64 reproduces the frozen
#   53/64 state (dead {32,33,34,35,37,41,45,49,53,57,61}).
SEAM_COLS = set()


def run_sweep(bench):
    """Full read sweep; returns a list of failure strings."""
    fails = []
    waived = 0
    bench.cycle(0)
    bench.cycle(0)          # warm-up: flush start-up unknowns
    if NINE and BITS > 32:
        # the decoder COPIES power up at X: each column PAIR's keeper
        # settles on its first strobe, and BOTH columns of the pair
        # pulse during that settling (real power-up behavior - silicon
        # needs the same flush).  One unmeasured access per live hi
        # pair, both halves, before anything is graded.
        for col in range(32, BITS, 2):     # slice 0 (32-35) is live too
            for h in (0, 1):
                bench.cycle((h << 8) | (col << 2))
    if S8:
        addrs = [h << 8 | (mux >> 2) << 7 | col << 2 | (mux & 3)
                 for h in (0, 1) for col in range(BITS) for mux in range(8)]
    else:
        addrs = [h << (8 if NINE else 7) | col << 2 | mux
                 for h in (0, 1) for col in range(BITS) for mux in range(4)]
    for addr in (addrs if NINE else range(256)):
        if NINE and ((addr >> 2) & 63) in SEAM_COLS and BITS > 32:
            waived += 1
            continue
        hi = bench.cycle(addr)
        wls = {h for h in bench.settled if h.startswith("wl") and "<" in h
               and not h.startswith("wlen")}
        want = expected_wl(addr)
        if wls != {want}:
            fails.append(f"addr {addr}: wordlines {sorted(wls)}, "
                         f"expected {{{want}}}")
        # enable selection: half bit picks top/bottom; in the 9-bit map
        # columns >= 32 (ADDRESS[7]=1) live in the outer decoder copies
        # driven by the _hi enables instead of the lo pair
        half_bit = 0x100 if NINE else 0x80
        suffix = "_hi" if (NINE and not S8 and addr & 0x80) else ""
        want_ena = ("wlenat" if addr & half_bit else "wlenab") + suffix
        others = {"wlenat", "wlenab", "wlenat_hi", "wlenab_hi"} - {want_ena}
        if S8:
            # the variant un-gates the lo enables; its _hi outputs stay
            # driven (they follow ADDRESS[7]) but feed nothing at 32 bits
            others -= {"wlenat_hi", "wlenab_hi"}
        if want_ena not in hi or (hi & others):
            fails.append(f"addr {addr}: enable pulses wrong "
                         f"({sorted(h for h in hi if h.startswith('wlen'))})")
        if "sae" not in hi:
            fails.append(f"addr {addr}: sense-amp enable did not fire")
        if "wrena" in hi:
            fails.append(f"addr {addr}: write enable fired on a read")
        if len(fails) > 6:
            fails.append("(stopping early)")
            break
    if waived:
        print(f"    [WAIVED] {waived} addresses on {len(SEAM_COLS)} DEAD "
              f"columns {sorted(SEAM_COLS)} - bits=64 delivers "
              f"{BITS - len(SEAM_COLS)}/{BITS} columns "
              f"(bits64_plan logs 22-25)")
    return fails


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="32,4,32")
    a = ap.parse_args()
    b, st, dp = map(int, a.config.split(","))
    global BITS, NINE, S8, N_STRIPS, WL_LABELS, SEL_LABELS
    BITS = b
    NINE = (b != 32 or st != 4 or dp > 48)   # band v2 (ninth address bit)
    S8 = (st == 8)                   # eight-strip control variant
    N_STRIPS = st
    SEL_LABELS = [f"ysel{f}<{k}>" for f in ("t", "b") for k in range(st)]
    WL_LABELS = ([f"wlb<{i}>" for i in range(b)]
                 + [f"wlt<{i}>" for i in range(b)])
    fails = 0
    spec = BankSpec(bits_per_group=b, strips_per_group=st,
                    depth_groups=dp)
    with tempfile.TemporaryDirectory() as td:
        gds = str(pathlib.Path(td) / f"{spec.name}.gds")
        generate(spec, gds)
        ex = extract(gds, spec.name)
    print(f"extracted {len(ex.devices)} devices; "
          f"decode cone drives the switch-level simulator")

    # ---- part 1: full read sweep against the measured address map ----
    bench = Bench(ex)
    sweep_fails = run_sweep(bench)
    if sweep_fails:
        fails += len(sweep_fails)
        print("[FAIL] read sweep:")
        for f in sweep_fails[:8]:
            print("    -", f)
    else:
        print(f"[PASS] {2 * BITS * st}-address read sweep: one-hot "
              "wordline pulses, measured map "
              + ("ADDRESS[6:2]->column ADDRESS[7],ADDRESS[1:0]->mux "
                 "ADDRESS[8]->half, " if S8 else
                 "ADDRESS[6:2]->column ADDRESS[7]->half, ")
              + "sae on every read")

    # ---- part 2: write and idle control steering ----
    addr_w = ((1 << 8) | (min(18, BITS - 1) << 2)) if NINE else 200
    bench.cycle(addr_w)
    hi_w = bench.cycle(addr_w, read=0, write=1)
    ok_w = ("wrena" in hi_w and "sae" not in hi_w
            and expected_wl(addr_w) in bench.settled)
    print(f"[{'PASS' if ok_w else 'FAIL'}] write: wordline + wrena pulse, "
          f"sae quiet ({sorted(hi_w)})")
    fails += 0 if ok_w else 1
    # column-mux steering: the select lines are LEVELS of the latched
    # address (measured on the reference and on band v2): for half = 1
    # exactly yselt<mux> is high (the LEFT array's leg, yselt family),
    # for half = 0 exactly yselb<mux> (the RIGHT array's leg, yselb
    # family), on reads and writes alike.  Both families graded, one
    # access per leg and half.  (Band v2 banks had NO yselb driver at
    # all until scripts/patch_control_yselb.py: the right array's mux
    # never opened and this rung, grading wordlines only, passed.)
    mux_fails = []
    half_bit = 0x100 if NINE else 0x80
    for h in (0, 1):
        for mux in range(N_STRIPS):
            addr_m = ((addr_w & ~(0x83 | half_bit)) | (h * half_bit)
                      | (mux & 3) | ((mux >> 2) << 7))
            bench.cycle(addr_m)
            bench.cycle(addr_m, read=0, write=1)
            sel = {x for x in bench.settled if x.startswith(("yselt<",
                                                             "yselb<"))}
            want = f"ysel{'t' if h else 'b'}<{mux}>"
            if sel != {want}:
                mux_fails.append(f"addr {addr_m} (half {h}, mux {mux}): "
                                 f"select lines high {sorted(sel)}, "
                                 f"expected {{{want}}}")
    print(f"[{'PASS' if not mux_fails else 'FAIL'}] column mux: per half "
          f"and leg exactly the addressed array's select line is high "
          f"({N_STRIPS} legs x 2 halves)")
    for f in mux_fails[:8]:
        print("    -", f)
    fails += len(mux_fails)

    # settle one deselected cycle first: `pulsed` counts a net that is
    # STILL high from the preceding (selected) cycle as a pulse.  The
    # wlena enables are LEVEL signals (latched address decode, not
    # banksel-gated) - a high level at idle is correct behavior, so
    # they are excluded; the guarantee is that no WORDLINE fires.
    bench.cycle(addr_w, banksel=0)
    hi_i = {h for h in bench.cycle(addr_w, banksel=0)
            if not h.startswith("wlen")}
    print(f"[{'PASS' if not hi_i else 'FAIL'}] idle (banksel=0): "
          f"nothing pulses ({sorted(hi_i)})")
    fails += 0 if not hi_i else 1

    # ---- part 3: mutations must break the sweep ----
    # (a) stuck wordline: reconnect every device on wlb<1> to gate VSS -
    # its pmos pull-up sticks ON, the wordline jams high
    sim = bench.sim
    wl_net = sim.net_of("wlb<1>")
    vss = next(iter(sim.vss))
    stuck = [d._replace(gate=vss) if wl_net in d.sd else d
             for d in ex.devices]

    class Mut:
        pass
    mut = Mut()
    mut.devices = stuck
    mut.net_labels = ex.net_labels
    mfails = run_sweep(Bench(mut))
    print(f"[{'PASS' if mfails else 'FAIL'}] mutation caught: "
          f"stuck wordline driver -> {len(mfails)} sweep failures")
    fails += 0 if mfails else 1

    # (b) swapped wordlines: exchange the two nets everywhere - one-hot
    # still holds, but the ADDRESS MAP assertion must object
    a, b = sim.net_of("wlb<2>"), sim.net_of("wlb<3>")
    swap = {a: b, b: a}

    def swap_net(n):
        return swap.get(n, n)
    swapped = [d._replace(gate=swap_net(d.gate),
                          sd=frozenset(swap_net(n) for n in d.sd))
               for d in ex.devices]
    mut2 = Mut()
    mut2.devices = swapped
    mut2.net_labels = ex.net_labels
    mfails2 = run_sweep(Bench(mut2))
    print(f"[{'PASS' if mfails2 else 'FAIL'}] mutation caught: "
          f"swapped wordline pair -> {len(mfails2)} sweep failures")
    fails += 0 if mfails2 else 1

    if fails:
        print(f"\nFAIL: {fails} rung-4.5 failures")
        return 1
    print("\nPASS: rung 4.5 - the layout provably decodes, steers "
          "read/write, and gates on select")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
