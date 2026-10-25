#!/usr/bin/env python3
"""Sparse-bits ladder: rungs 1-4.5 on a bits_per_group=16 bank.

The sparse-bits scheme keeps the reference frame (io column, decode band,
overlays, bank outline all at reference positions) and simply omits the
outer bitcell columns; the reference's own implant blanket re-covers the
dead zone.  Wordline indices count from the io column outward, so the
live columns are the LOW column codes: an address is live iff
ADDRESS[6:2] < bits.  Dead addresses still decode (the hardened band is
verbatim) but their one-hot output is a stub that gates no storage.

Runs the full structural ladder on the sparse bank:
  rung 1  invariants          rung 2  connectivity (dead-window aware)
  rung 3  DRC deck            rung 4  device accounting
  rung 4.5 switch-level sweep: live addresses one-hot on the measured
           map; dead addresses touch no storage; write/idle steering.

Usage:  python3 tests/test_sparse_bits.py   (~15 min)
"""
import importlib.util
import pathlib
import sys
import tempfile
from collections import defaultdict

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import BankSpec, generate            # noqa: E402
from sramforge.lvs import extract                   # noqa: E402

BITS = 16


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, REPO / "tests" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def dead_windows(bits):
    skip = 32 - bits
    live_anchor = 864 + skip * 432
    axis = 19236
    if not skip:
        return []
    return [(0, live_anchor - 100),
            (2 * axis - live_anchor + 100, 2 * axis)]


def band_identity_check() -> int:
    """The parametric decode-band path, forced at bits=32, must reproduce
    the vendored ctrl_decode_32 exactly (geometry AND texts)."""
    import klayout.db as kdb
    from sramforge.library import CellLibrary
    from sramforge.band import build_ctrl_band
    ly = kdb.Layout()
    ly.dbu = 0.00025
    lib = CellLibrary(ly)
    ref = lib.get("ctrl_decode_32")
    gen = build_ctrl_band(ly, lib, 32, force_parametric=True)
    bad = sum(1 for li in ly.layer_indexes()
              if not (kdb.Region(ref.begin_shapes_rec(li)).merged()
                      ^ kdb.Region(gen.begin_shapes_rec(li)).merged()
                      ).is_empty())

    def texts(cell):
        out = set()
        for li in ly.layer_indexes():
            info = ly.get_info(li)
            it = cell.begin_shapes_rec(li)
            while not it.at_end():
                sh = it.shape()
                if sh.is_text():
                    p = it.trans() * kdb.Point(sh.text_trans.disp.x,
                                               sh.text_trans.disp.y)
                    out.add((info.layer, info.datatype,
                             sh.text_string, p.x, p.y))
                it.next()
        return out
    ok = bad == 0 and texts(ref) == texts(gen)
    print(f"[{'PASS' if ok else 'FAIL'}] parametric band @32 XOR-identical "
          "to vendored ctrl_decode_32 (geometry + texts)")
    return 0 if ok else 1


def main() -> int:
    spec = BankSpec(bits_per_group=BITS)
    fails_total = band_identity_check()
    with tempfile.TemporaryDirectory() as td:
        gds = str(pathlib.Path(td) / f"{spec.name}.gds")
        generate(spec, gds)
        print(f"generated {spec.name} (sparse bits={BITS})")

        t_inv = load("test_invariants")
        f = t_inv.check(spec, gds)
        print(f"[{'FAIL' if f else 'PASS'}] rung 1", f or "")
        fails_total += len(f)

        t_con = load("test_connectivity")
        f = t_con.check(spec, gds)
        for x in f:
            print("    -", x)
        print(f"[{'FAIL' if f else 'PASS'}] rung 2")
        fails_total += len(f)

        t_drc = load("test_drc")
        violations = t_drc.run_deck(gds, spec.name)
        for v in violations[:8]:
            print("    -", v.rule, v.count)
        print(f"[{'FAIL' if violations else 'PASS'}] rung 3")
        fails_total += len(violations)

        t_lvs = load("test_lvs")
        f = t_lvs.check_bank(spec, gds)
        for x in f:
            print("    -", x)
        print(f"[{'FAIL' if f else 'PASS'}] rung 4")
        fails_total += len(f)

        # ---- rung 4.5, sparse semantics ----
        t_fun = load("test_functional")
        ex = extract(gds, spec.name)
        bench = t_fun.Bench(ex)

        # A wordline label whose probes ALL sit in the dead-column
        # windows labels a decode-band stub, not a storage wordline.
        dw = dead_windows(BITS)

        def in_dead(x):
            return any(a <= x < b for a, b in dw)
        probes_by_label = defaultdict(list)
        for name, x, y, net in ex.label_probes:
            probes_by_label[name].append(x)
        stubs = {lb for lb, xs in probes_by_label.items()
                 if lb.startswith(("wlt<", "wlb<"))
                 and all(in_dead(x) for x in xs)}
        bench.watch = {n: lb for n, lb in bench.watch.items()
                       if lb not in stubs}

        f = []
        bench.cycle(0)
        bench.cycle(0)
        for addr in range(256):
            hi = bench.cycle(addr)
            wls = {h for h in hi if h.startswith("wl") and "<" in h
                   and not h.startswith("wlen")}
            col = (addr >> 2) & 31
            want = {t_fun.expected_wl(addr)} if col < BITS else set()
            if wls != want:
                f.append(f"addr {addr}: {sorted(wls)} vs {sorted(want)}")
            if "sae" not in hi or "wrena" in hi:
                f.append(f"addr {addr}: steering wrong")
            if len(f) > 6:
                f.append("(stopping early)")
                break
        for x in f[:10]:
            print("    -", x)
        print(f"[{'FAIL' if f else 'PASS'}] rung 4.5 sparse read sweep "
              f"(live cols 0..{BITS-1} one-hot, dead addresses touch "
              "no storage)")
        fails_total += len(f)

        hi_w = bench.cycle(20, read=0, write=1)     # col 5: live
        ok_w = ("wrena" in hi_w and "sae" not in hi_w
                and t_fun.expected_wl(20) in hi_w)
        print(f"[{'PASS' if ok_w else 'FAIL'}] rung 4.5 write steering "
              f"({sorted(hi_w)})")
        fails_total += 0 if ok_w else 1

        hi_i = bench.cycle(20, banksel=0)
        print(f"[{'PASS' if not hi_i else 'FAIL'}] rung 4.5 idle "
              f"({sorted(hi_i)})")
        fails_total += 0 if not hi_i else 1

    if fails_total:
        print(f"\nFAIL: {fails_total} sparse-bits ladder failures")
        return 1
    print(f"\nPASS: sparse bits={BITS} clears rungs 1-4.5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
