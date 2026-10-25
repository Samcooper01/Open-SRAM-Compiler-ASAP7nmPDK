#!/usr/bin/env python3
"""Sense-enable timing budget: does the control's delay line fire the
sense amplifier late enough for a given bank, and which delay-line
setting (the sdel<4:0> taps) does each configuration need?

The control's sae strobe is clk delayed by a chain of AO22 stages, one
per tap: tap i high selects the short path around stage i+1, tap i low
inserts it, so the useful settings are 11111 (minimum delay), 01111,
00111, 00011, 00001, 00000 (measured in out/control_netlist.v).  Every
proof and characterization so far tied sdel to all ones.

Two measurements, all with ngspice on the bank's own extracted netlist:

  sweep   stage A (the decode / control band, rung 7 engine) at each
          useful setting: clk to sae and clk to the near wordline, at
          20 ps clock slew.
  need    the far end of the addressed wordline must be up and the
          bitline pair split by 100 mV before sae: clk-to-WL (stage A)
          + the wordline flight to the far row (sramforge.timing
          wordline bench at the configuration's row pitch) + the
          bitline split (rung 5 grid, by bits) + a stated margin.

Writes out/sae_margin_<corner>.json and prints the table: for every
(bits, strips, depth) of interest the required sae time, the smallest
setting that meets it, and its slack.

    python3 scripts/sae_margin.py [--corner TT] [--margin-ps 20]
                                  [--config 32,4,32]
"""
import argparse
import json
import pathlib
import sys
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
import characterize as ch                                    # noqa: E402
from access_sim import char_stages as cs                     # noqa: E402
from access_sim.char_stages import VDD, StageA, Runner, pwl  # noqa: E402
from sramforge import BankSpec, timing                       # noqa: E402

# the reference delay line's useful settings (thermometer taps) and
# the binary-weighted line's 32 codes (scripts/patch_control_delayline
# .py; sdel<0> is the leftmost character, 1 = short path)
SETTINGS_TAPS = ["11111", "01111", "00111", "00011", "00001", "00000"]
SETTINGS_BINARY = ["".join("0" if (n >> k) & 1 else "1" for k in range(5))
                   for n in range(32)]
# (bits, strips, depth) rows of the table: every configuration the
# ladder proves plus the depth sweep
# (depth 48 at four strips is left out: the wordline bench at exactly
# that length hangs at SS and diverges at FF, a numerical quirk of that
# deck; the quadratic flight fit through the other lengths covers it
# and sramforge.sense evaluates any depth from the fit)
CONFIGS = [(32, 4, 8), (32, 4, 16), (32, 4, 24), (32, 4, 32),
           (32, 4, 64), (32, 4, 96), (16, 4, 32), (64, 4, 64),
           (32, 8, 8), (32, 8, 16), (32, 8, 32), (32, 8, 64)]


def stage_a_sweep(spec, corner, work, cfin, settings):
    """clk -> sae and clk -> WL (ps, second cycle) per tap setting."""
    res = {"config": {}, "provenance": {}, "estimates": {}, "notes": [],
           "setup_ps": {}, "hold_ps": {}}      # the record shape step_netlist expects
    ch.step_netlist(spec, work, res)
    ex = cs.load_netlist(work / "netlist.json")
    A = StageA(ex, spec, corner)
    loads, _ = A.cut_load_lines(cfin)
    runner = Runner(work)
    out = {}
    for setting in settings:
        bits = [int(c) for c in setting]          # sdel<0> first

        def input_vector(self, address=0, clk=0, banksel=1, read=1,
                         byte_wrenab=0, wd=0, _bits=bits):
            f = StageA.input_vector(self, address, clk, banksel, read,
                                    byte_wrenab, wd)
            for i in range(5):
                f[self.sim.net_of(f"sdel<{i}>")] = _bits[i]
            return f
        A.input_vector = types.MethodType(input_vector, A)
        ic = A.prestate(address=0)
        # MEASUREMENT WINDOW: the sense strobe is read between the second
        # clock rise and the following fall, so the high phase must be
        # longer than the longest delay the line can produce.  The
        # characterization's own 1 ns phase truncated the binary line at
        # 974 ps and recorded everything slower as unmeasured, which read
        # as a circuit limit rather than a bench limit (2026-09-20).
        T_END = 6.4
        waves = A.dc_waves(address=0)
        for i in range(5):
            waves[f"sdel<{i}>"] = VDD * bits[i]
        waves["clk"] = pwl(ch.clk_events([0.5, 3.0], [2.0, 6.0]), 0.0, 20,
                           T_END)
        wv = ch.run_stage_a(A, runner, f"sae_sweep_{setting}", ic, waves,
                            T_END, loads)
        if wv is None:
            out[setting] = None
            print(f"  setting {setting}: did not converge")
            continue
        rises, falls = ch.clk_edges(A, wv)
        m = {}
        for lbl, key in (("wlb<0>", "clk_to_wl_ps"), ("sae", "clk_to_sae_ps")):
            tr, _ = A.label_trace(wv, lbl)
            m[key] = ch.edge_after(wv, tr, rises[-1], True, falls[-1])
        out[setting] = m
        print(f"  setting {setting}: clk->WL {m['clk_to_wl_ps']} ps, "
              f"clk->sae {m['clk_to_sae_ps']} ps")
    return out


def wordline_flight(depth, strips, corner, work):
    """Far-row wordline t50 (ps) from the rung 5 bench, None when the
    bench does not produce a crossing (a hung or aborted ngspice run is
    a missing number, never a zero: a zero would corrupt the fit)."""
    try:
        raw = timing.run_deck(timing.wordline_bench(depth, strips, corner),
                              work, f"wl_{depth}_{strips}_{corner}")
        t50 = timing.measure_wordline(raw)["wl_t50_ps"]
    except Exception as e:                       # timeout, no raw, no crossing
        print(f"  wordline bench depth {depth} strips {strips} {corner}: "
              f"failed ({type(e).__name__})")
        return None
    return t50 if t50 else None


def flight_fit(points):
    """Least-squares c + a n + b n^2 through (rows_equiv, t50_ps)."""
    import numpy as np
    n = np.array([p[0] for p in points], dtype=float)
    t = np.array([p[1] for p in points], dtype=float)
    M = np.vstack([np.ones_like(n), n, n * n]).T
    c, a_, b = np.linalg.lstsq(M, t, rcond=None)[0]
    resid = max(abs(M @ np.array([c, a_, b]) - t))
    return {"c": round(float(c), 3), "a": round(float(a_), 5),
            "b": round(float(b), 6), "max_residual_ps": round(float(resid), 1),
            "points": [[round(x, 2), y] for x, y in points]}


def record(spec, corner, sweep, rows, grid):
    """Merge this run into sramforge/sae_codes.json (sramforge.sense)."""
    from sramforge import sense
    from sramforge.tech import COLGRP_ROW_PITCH
    from sramforge.generator import colgrp_pitch
    path = sense.SAE_CODES_JSON
    t = json.loads(path.read_text()) if path.exists() else {}
    key = sense.variant_key(spec)
    base = sweep.get("11111") or {}
    codes = {c: (m["clk_to_sae_ps"] if m else None) for c, m in sweep.items()}
    t.setdefault("controls", {}).setdefault(key, {})[corner] = {
        "clk_to_wl_ps": base.get("clk_to_wl_ps"), "codes": codes,
        "measured_on": spec.name}
    pts = []
    for r in rows:
        if not r["wl_flight_ps"]:
            continue
        sp = BankSpec(bits_per_group=r["bits"], strips_per_group=r["strips"],
                      depth_groups=r["depth"])
        pts.append((r["depth"] * colgrp_pitch(sp) / COLGRP_ROW_PITCH,
                    r["wl_flight_ps"]))
    pts = sorted(set(pts))
    t.setdefault("flight_fit", {})[corner] = flight_fit(pts)
    t.setdefault("split_ps", {})[corner] = {
        k.split("_")[0]: v["read"]["d100_ps"]
        for k, v in grid["columns"].items() if k.endswith("_" + corner)}
    t["method"] = ("controls: stage A (rung 7 engine) clk-to-WL and clk-to-"
                   "sae per delay-line code, 20 ps clk slew, second cycle; "
                   "measured in a 3 ns clock-high window so the whole "
                   "delay line is observable; "
                   "flight_fit: rung 5 wordline bench t50 vs wire length in "
                   "four-strip row pitches, quadratic least squares; split_ps:"
                   " rung 5 column bench bitline 100 mV split by bits")
    path.write_text(json.dumps(t, indent=1))
    print(f"recorded control '{key}' {corner} into {path.name}: "
          f"{len(codes)} codes, flight fit {t['flight_fit'][corner]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corner", default="TT", choices=["TT", "SS", "FF"])
    ap.add_argument("--margin-ps", type=float, default=20.0)
    ap.add_argument("--config", default="32,4,32",
                    help="bank whose control is measured (bits,strips,depth)")
    ap.add_argument("--codes", default="taps", choices=["taps", "binary"],
                    help="the reference thermometer taps or the binary line")
    ap.add_argument("--record", action="store_true",
                    help="merge the measurements into sramforge/sae_codes.json, "
                         "the table sramforge.sense reads")
    a = ap.parse_args()
    timing.require_tools()          # say what is missing before printing anything
    SETTINGS = SETTINGS_TAPS if a.codes == "taps" else SETTINGS_BINARY
    b, st, dp = map(int, a.config.split(","))
    spec = BankSpec(bits_per_group=b, strips_per_group=st, depth_groups=dp)
    work = REPO / "out" / "char" / "work" / f"{spec.name}_{a.corner}"
    work.mkdir(parents=True, exist_ok=True)
    rec = REPO / "out" / "char" / f"{spec.name}_{a.corner}.json"
    cfin = None
    if rec.exists():
        cfin = json.loads(rec.read_text()).get("estimates", {}).get(
            "gate_cap_per_fin_f")
    if not cfin:
        cfin = cs.measure_gate_cap_per_fin(Runner(work), a.corner)
    print(f"stage A tap sweep on {spec.name} {a.corner}:")
    sweep = stage_a_sweep(spec, a.corner, work, cfin, SETTINGS)
    grid = json.loads((REPO / "out" / "timing_grid.json").read_text())
    base = sweep.get("11111") or {}
    clk_to_wl = base.get("clk_to_wl_ps")
    wl_work = REPO / "out" / "timing"
    rows = []
    print(f"\nrequirement (clk->WL {clk_to_wl} ps + wordline flight + "
          f"bitline 100 mV split + {a.margin_ps:.0f} ps margin) vs the settings:")
    for bits, strips, depth in CONFIGS:
        flight = wordline_flight(depth, strips, a.corner, wl_work)
        split = grid["columns"][f"{bits}_{a.corner}"]["read"]["d100_ps"]
        if flight is None or clk_to_wl is None:
            rows.append({"bits": bits, "strips": strips, "depth": depth,
                         "wl_flight_ps": flight, "bl_split_ps": split,
                         "sae_needed_ps": None, "setting": None,
                         "slack_ps": None})
            print(f"  {bits:2d},{strips},{depth:3d}: not measured "
                  f"(flight {flight}, clk->WL {clk_to_wl})")
            continue
        need = clk_to_wl + flight + split + a.margin_ps
        choice, slack = None, None
        for setting in SETTINGS:
            m = sweep.get(setting)
            if m and m["clk_to_sae_ps"] is not None and m["clk_to_sae_ps"] >= need:
                choice, slack = setting, round(m["clk_to_sae_ps"] - need, 1)
                break
        rows.append({"bits": bits, "strips": strips, "depth": depth,
                     "wl_flight_ps": flight, "bl_split_ps": split,
                     "sae_needed_ps": round(need, 1), "setting": choice,
                     "slack_ps": slack})
        print(f"  {bits:2d},{strips},{depth:3d}: flight {flight:4d} split {split:3d} "
              f"need {need:6.1f} -> {choice or 'NONE (delay line too short)'}"
              f"{'' if slack is None else f' slack {slack} ps'}")
    out = {"config_measured": spec.name, "corner": a.corner,
           "margin_ps": a.margin_ps, "codes": a.codes, "settings": SETTINGS,
           "sweep": sweep,
           "table": rows,
           "method": ("sweep: stage A at 20 ps clk slew, second cycle, "
                      "sdel taps as digital constants; need: clk-to-WL of "
                      "the 11111 run + far-row wordline t50 (timing.wordline"
                      "_bench at the configuration's row pitch) + rung 5 "
                      "bitline d100 by bits + margin")}
    if a.record:
        record(spec, a.corner, sweep, rows, grid)
    tag = f"{a.corner}" if a.codes == "taps" else f"{a.codes}_{a.corner}"
    (REPO / "out" / f"sae_margin_{tag}.json").write_text(
        json.dumps(out, indent=1))
    print(f"\nwrote out/sae_margin_{tag}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
