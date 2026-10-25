#!/usr/bin/env python3
"""Rung 7 characterization driver: measure one generated bank with ngspice
(the open-source SPICE, Simulation Program with Integrated Circuit Emphasis,
simulator) and write out/char/<name>_<corner>.json, the single source every
number in the Liberty (the Synopsys timing library format) file comes from.

    python3 scripts/characterize.py --config 32,4,32 [--corner TT]
                                    [--steps a,b,...] [--quick]

Flow (all on the bank's OWN extracted netlist, rung 4 engine):

  netlist   generate + extract the bank (cached in the work dir), record the
            bounding box (the Liberty area).
  gatecap   per-fin gate capacitance of each model from the BSIM-CMG
            (Berkeley Short-channel IGFET Model, Common Multi-Gate) card.
  pins      input pin capacitance = gate cap of the devices the pin net
            drives + wire length x a stated per-length figure (ESTIMATE).
  stagea    decode band, two read cycles, at each input slew: clk to WL
            (wordline), clk to sae (sense enable), clk to precharge release,
            wordline retirement after the clock falls.
  read      stage B (column / sense / output latch) driven by the stage A
            waveforms: clk to dataout (both polarities, the cycle 2 edge from
            a latch state the circuit itself produced) and the dataout
            transition table over {input slew} x {output load}.
  period    minimum clock period: t_high (clk rise to dataout valid) +
            t_low (clk fall to both addressed bitlines back within 10% of
            VDD), verified by re-simulating at exactly that period, then
            plus 20% margin.  Details in docs/liberty_characterization.md.
  setup     setup and hold of ADDRESS[*], banksel, read, byte_wrenab
            (stage A, bisection on the input arrival) and of wd[*] (stage A
            write-then-read waveforms driving stage B, bisection).
  leakage   band slice + one column slice operating points, the single-cell
            leakage, and the extrapolated bank total.

Every step writes its results into the record as soon as it finishes, so a
crash or a --steps rerun never loses the others.  A measurement that does
not converge is recorded as null with a note; nothing is invented.

Runtime: about 45 minutes for 32,4,32 on a shared 32-core machine, one
ngspice job at a time.
"""
import argparse
import datetime
import json
import math
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
from access_sim import char_stages as cs                     # noqa: E402
from access_sim.char_stages import (VDD, StageA, StageB,     # noqa: E402
                                    BandSlice, Runner, Waves, pwl,
                                    cross_time, slew_10_90, level_in)
from sramforge import pins as contract                       # noqa: E402

SLEWS_PS = [20, 60]            # clk input slew (10% to 90%)
LOADS_FF = [5, 20, 50]         # dataout load
C_WIRE_PER_UM = 0.2e-15        # stated wire capacitance figure (M2-class)
TOL_PS = 10.0                  # setup/hold: allowed decode-timing degradation
T_CLK1 = 0.5                   # ns, first clk 50% crossing in every deck


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}",
          flush=True)


# ---------------------------------------------------------------------------
# step: netlist
def step_netlist(spec, work, res):
    from sramforge import generate
    from sramforge.lvs import extract
    from sramforge.tech import DBU
    from dump_netlist import dump, net_str
    nl = work / "netlist.json"
    meta = work / "manifest.json"
    # STALENESS: the extraction is cached in the work dir, but it is a
    # picture of the CURRENT generator and the CURRENT vendored cells.
    # Re-vendoring a control block (or editing the generator) invalidates
    # it, and a stale cache silently characterizes the PREVIOUS macro (a
    # doubled sense delay line measured as its old self, 2026-09-20).
    sources = (list((REPO / "cells").glob("*.gds"))
               + list((REPO / "cells").glob("*.json"))
               + list((REPO / "sramforge").glob("*.py"))
               + list((REPO / "sramforge").glob("*.json")))
    newest = max((f.stat().st_mtime for f in sources), default=0)
    stale = nl.exists() and nl.stat().st_mtime < newest
    if stale:
        log("cached extraction is older than the cells or the generator: "
            "re-extracting")
    if stale or not (nl.exists() and meta.exists()):
        log(f"generating + extracting {spec.name} (about 2 minutes at 32)")
        gds = work / f"{spec.name}.gds"
        manifest = generate(spec, str(gds))
        ex = extract(str(gds), spec.name)
        dump(ex, str(nl))
        # metal wire length per net (um), for the pin capacitance estimate:
        # every metal island of the net contributes its longer bbox side
        wires = {}
        g = ex.graph
        for key in list(g.uf.parent):
            cond, idx = key
            if cond not in ("m1", "m2", "m3", "m4", "m5"):
                continue
            root = g.uf.find(key)
            b = g.idx[cond].polys[idx].bbox()
            wires[net_str(root)] = wires.get(net_str(root), 0.0) + \
                max(b.width(), b.height()) * DBU
        pins = {}
        for net, labels in ex.net_labels.items():
            for l in labels:
                if l in ("clk", "banksel", "read", "byte_wrenab") or \
                        l.startswith(("ADDRESS<", "wd<", "sdel<", "dataout<")):
                    pins[l] = net_str(net)
        manifest["wire_len_um"] = {n: round(w, 3) for n, w in wires.items()
                                   if n in set(pins.values())}
        manifest["pin_nets"] = pins
        manifest["dbu_um"] = DBU
        meta.write_text(json.dumps(manifest, indent=1))
        gds.unlink()
    manifest = json.loads(meta.read_text())
    res["config"].update({
        "bitcells": manifest["bitcells"],
        "width_um": manifest["width_um"], "height_um": manifest["height_um"],
        "area_um2": round(manifest["width_um"] * manifest["height_um"], 3),
    })
    res["provenance"]["netlist_devices"] = None
    return manifest


# ---------------------------------------------------------------------------
# step: gate cap
def step_gatecap(runner, corner, res):
    cf = cs.measure_gate_cap_per_fin(runner, corner)
    res["estimates"]["gate_cap_per_fin_f"] = cf
    res["estimates"]["gate_cap_method"] = (
        "ngspice transient on one device of each model card (nfin=4, "
        "source = drain = its rail): gate ramped over the full 0..VDD "
        "swing in 1 ns, gate current integrated, C = Q / VDD / nfin; "
        "the large-signal average gate capacitance per fin")
    log("per-fin gate cap (aF): " + ", ".join(
        f"{m} {v * 1e18:.1f}" for m, v in cf.items() if v))
    return cf


# ---------------------------------------------------------------------------
# step: pin capacitance (estimate)
def step_pins(ex, manifest, cfin, res):
    pins = manifest["pin_nets"]
    wires = manifest.get("wire_len_um", {})
    by_gate = {}
    for d in ex.devices:
        by_gate.setdefault(d.gate, []).append(d)
    out = {}
    for label, net in sorted(pins.items()):
        if label.startswith("dataout<"):
            continue
        devs = by_gate.get(net, [])
        cg = sum(d.nfin * cfin[d.model] for d in devs)
        wl = wires.get(net, 0.0)
        cw = wl * C_WIRE_PER_UM
        out[label.replace("<", "[").replace(">", "]")] = {
            "gate_ff": round(cg * 1e15, 4), "gate_devices": len(devs),
            "gate_fins": sum(d.nfin for d in devs),
            "wire_len_um": round(wl, 2), "wire_ff": round(cw * 1e15, 4),
            "total_ff": round((cg + cw) * 1e15, 4)}
    res["pin_capacitance_ff"] = out
    res["estimates"]["wire_c_per_um_f"] = C_WIRE_PER_UM
    res["estimates"]["pin_capacitance_method"] = (
        "ESTIMATE, not simulated: sum over the devices whose gate is the "
        "pin's net of nfin x per-fin gate cap (gate_cap_per_fin_f), plus "
        "the net's metal length (sum of the longer bounding-box side of "
        "every M1..M5 island of the net) x wire_c_per_um_f")
    log(f"pin caps: clk {out['clk']['total_ff']} fF, ADDRESS[0] "
        f"{out['ADDRESS[0]']['total_ff']} fF")


# ---------------------------------------------------------------------------
# stage A helpers
def clk_events(rises, falls):
    ev = []
    for r, f in zip(rises, falls):
        ev.append((r, VDD))
        ev.append((f, 0.0))
    return sorted(ev)


# Solver option ladder.  The rung 5 recipe first; when ngspice aborts with
# "timestep too small" (it does on a few percent of the decks, typically
# when an input toggles while the address latch is transparent), the same
# deck is re-run with heavier damping, then with trapezoidal integration.
# Both fallbacks converged on every deck that aborted during development;
# the option set used is recorded per run in provenance.runs.
#
# Only an option set that reproduces the rung 5 timing may stand in for it.
# Measured on the same aborted decks: trapezoidal integration with the same
# tolerances and shunts lands the wordline within 3 ps and the sense enable
# within 5 ps of the gear runs; a "damped" gear set (reltol 5e-3, abstol
# 1e-9, vntol 1e-4, cshunt 5e-16) converges too but moves the wordline by
# +15 ps and the sense enable by +55 ps (the larger shunt capacitors load
# every internal node), so it is NOT in the ladder.
FALLBACKS = [
    ("rung5", None),
    ("trap", ".options reltol=2e-3 abstol=1e-10 vntol=1e-5 rshunt=1e9 "
             "itl4=300 cshunt=2e-16 method=trap xmu=0.492"),
]


def run_converged(runner, emit, t_end):
    """emit(options) writes a deck and returns its path.  Runs it through
    the option ladder until the transient reaches t_end; returns the Waves
    or None.  The runner log entry gets the option tag."""
    for tag, opts in FALLBACKS:
        deck = emit(opts)
        raw = runner.run(deck)
        if runner.log:
            runner.log[-1]["options"] = tag
        if raw:
            wv = Waves(raw)
            if wv.t_end >= (t_end - 0.05) * 1e-9:
                return wv
            runner.log[-1]["status"] = f"stopped at {wv.t_end * 1e9:.3f} ns"
    return None


def run_stage_a(A, runner, tag, ic, waves, t_end, loads, saves=None):
    return run_converged(
        runner,
        lambda opts: A.emit(runner.workdir / f"{tag}.sp", ic, A.sources(waves),
                            f"5p {t_end}n", saves or A.save_nets(),
                            options=opts, extra_lines=loads),
        t_end)


def run_stage_b(B, runner, tag, ic, src, t_end, loads, saves):
    return run_converged(
        runner,
        lambda opts: B.emit(runner.workdir / f"{tag}.sp", ic, src,
                            f"5p {t_end}n", saves, options=opts,
                            extra_lines=loads),
        t_end)


def clk_edges(A, wv):
    """Rising and falling 50% crossings of the clk source trace (ns)."""
    v, _ = A.label_trace(wv, "clk")
    t = [x * 1e9 for x in wv.t]
    rises, falls, after = [], [], 0.0
    while True:
        r = cross_time(t, v, VDD / 2, True, after)
        if r is None:
            break
        rises.append(r)
        f = cross_time(t, v, VDD / 2, False, r)
        if f is None:
            break
        falls.append(f)
        after = f
    return rises, falls


def edge_after(wv, trace, t0, rising=True, t1=None):
    t = [x * 1e9 for x in wv.t]
    c = cross_time(t, trace, VDD / 2, rising, t0, t1)
    return None if c is None else round((c - t0) * 1000, 1)


def peak_in(wv, trace, t0, t1):
    t = [x * 1e9 for x in wv.t]
    sel = [v for tt, v in zip(t, trace) if t0 <= tt <= t1]
    return max(sel) if sel else None


def step_stagea(A, runner, loads, res, quick=False):
    """Two read cycles per input slew (address 0 then address 1 so the
    column mux switches between the cycles, as the read table needs)."""
    out = {}
    T_END = 4.2
    ic = A.prestate(address=0)
    for slew in SLEWS_PS:
        waves = A.dc_waves(address=0)
        waves["clk"] = pwl(clk_events([0.5, 2.5], [1.5, 3.5]), 0.0, slew,
                           T_END)
        waves["ADDRESS<0>"] = pwl([(2.0, VDD)], 0.0, 20, T_END)
        wv = run_stage_a(A, runner, f"stageA_read_s{slew}", ic, waves,
                         T_END, loads)
        if wv is None:
            out[str(slew)] = None
            res["notes"].append(f"stage A read at slew {slew} ps did not "
                                f"converge to {T_END} ns")
            continue
        rises, falls = clk_edges(A, wv)
        m = {"clk_rise_ns": [round(r, 4) for r in rises],
             "clk_fall_ns": [round(f, 4) for f in falls]}
        for lbl, key in (("wlb<0>", "clk_to_wl_ps"),
                         ("sae", "clk_to_sae_ps"),
                         ("blprechbn", "clk_to_prech_release_ps"),
                         ("WLENA", "clk_to_wlena_ps")):
            tr, _ = A.label_trace(wv, lbl)
            m[key] = [edge_after(wv, tr, r, True, f) for r, f in
                      zip(rises, falls)]
        tr, _ = A.label_trace(wv, "wlb<0>")
        m["wl_retire_after_clk_fall_ps"] = [edge_after(wv, tr, f, False)
                                            for f in falls]
        t = [x * 1e9 for x in wv.t]
        m["wl_slew_ps"] = [round(s * 1000, 1) if s else None for s in
                           (slew_10_90(t, tr, r, True) for r in rises)]
        tr, _ = A.label_trace(wv, "sae")
        m["sae_retire_after_clk_fall_ps"] = [edge_after(wv, tr, f, False)
                                             for f in falls]
        ys1, _ = A.label_trace(wv, "yselb<1>")
        m["ysel_switch_after_address_ps"] = edge_after(wv, ys1, 2.0, True)
        out[str(slew)] = m
        log(f"stage A slew {slew}: clk->WL {m['clk_to_wl_ps']} ps, "
            f"clk->sae {m['clk_to_sae_ps']} ps, WL retires "
            f"{m['wl_retire_after_clk_fall_ps']} ps after clk fall")
    res["stage_a"] = out
    res["stage_a"]["method"] = (
        "decode band cone (stage A) with the cone-cut gate loads restored "
        "as capacitors (cut_loads_ff); clk rising 50% to the 50% rising "
        "crossing of the named control net; two cycles listed")
    return out


# ---------------------------------------------------------------------------
def step_read(A, B, runner, loadsB, res):
    """clk -> dataout and the transition table."""
    table = {"input_slew_ps": SLEWS_PS, "load_ff": LOADS_FF,
             "cell_rise_ps": [], "cell_fall_ps": [],
             "rise_transition_ps": [], "fall_transition_ps": [],
             "first_cycle_cell_rise_ps": [], "first_cycle_cell_fall_ps": [],
             "correct": []}
    restore = []
    T_END = 4.2
    for slew in SLEWS_PS:
        rawA = runner.workdir / f"stageA_read_s{slew}.raw"
        # a stage A that did not converge leaves a PARTIAL raw on disk
        # (the aborted run's points); its record entry is null, and the
        # read table for that slew must be null too, never indexed
        stage_a_ok = (res.get("stage_a") or {}).get(str(slew)) is not None
        if not rawA.exists() or not stage_a_ok:
            if not stage_a_ok:
                res["notes"].append(f"read table at slew {slew} ps skipped: "
                                    f"stage A did not converge")
            for k in ("cell_rise_ps", "cell_fall_ps", "rise_transition_ps",
                      "fall_transition_ps", "first_cycle_cell_rise_ps",
                      "first_cycle_cell_fall_ps", "correct"):
                table[k].append([None] * len(LOADS_FF))
            continue
        wvA = Waves(rawA)
        rises, falls = clk_edges(A, wvA)
        rows = {k: [] for k in ("cell_rise_ps", "cell_fall_ps",
                                "rise_transition_ps", "fall_transition_ps",
                                "first_cycle_cell_rise_ps",
                                "first_cycle_cell_fall_ps", "correct")}
        for load in LOADS_FF:
            got = {}
            for tag, presets in (("rise", {0: 0, 1: 1}),
                                 ("fall", {0: 1, 1: 0})):
                ic = B.ic_for(presets)
                src = B.boundary_sources(A, wvA)
                src.append(f"Cload {B.node(B.dataout)} 0 {load}e-15")
                saves = B.save_nets((0, 1))
                wv = run_stage_b(B, runner, f"stageB_{tag}_s{slew}_c{load}",
                                 ic, src, T_END, loadsB, saves)
                if wv is None:
                    got[tag] = None
                    res["notes"].append(
                        f"stage B {tag} slew {slew} load {load} did not "
                        f"converge with any option set")
                    continue
                t = [x * 1e9 for x in wv.t]
                do = wv.v(B.node(B.dataout))
                rising = (tag == "rise")
                # the cycle-2 edge: from a latch state produced by cycle 1
                c2 = cross_time(t, do, VDD / 2, rising, rises[1], falls[1] + 0.3)
                c1 = cross_time(t, do, VDD / 2, not rising, rises[0],
                                falls[0] + 0.3)
                tr2 = slew_10_90(t, do, rises[1], rising)
                final = level_in(t, do, T_END - 0.3, T_END)
                ok = (final > 0.9 * VDD) if rising else (final < 0.1 * VDD)
                # bitline restore after each cycle's clk fall (col 0 then 1)
                for cyc, col in ((0, 0), (1, 1)):
                    pp, bl, bln = B.addressed_pair(col)
                    worst = None
                    for n in (bl, bln):
                        if n is None:
                            continue
                        v = wv.v(B.node(n))
                        lo = min(vv for tt, vv in zip(t, v)
                                 if rises[cyc] <= tt <= falls[cyc] + 0.1)
                        if lo > 0.9 * VDD:
                            continue      # this bitline never discharged
                        back = cross_time(t, v, 0.9 * VDD, True, falls[cyc])
                        if back is None:
                            worst = None
                            break
                        worst = max(worst or 0, back - falls[cyc])
                    restore.append({"slew": slew, "load": load, "dir": tag,
                                    "cycle": cyc + 1,
                                    "restore_after_clk_fall_ps":
                                    None if worst is None
                                    else round(worst * 1000, 1)})
                got[tag] = {
                    "cell_ps": None if c2 is None else round((c2 - rises[1]) * 1000, 1),
                    "first_ps": None if c1 is None else round((c1 - rises[0]) * 1000, 1),
                    "tran_ps": None if tr2 is None else round(tr2 * 1000, 1),
                    "ok": ok}
                log(f"read slew {slew} load {load} {tag}: clk->dataout "
                    f"{got[tag]['cell_ps']} ps, transition "
                    f"{got[tag]['tran_ps']} ps, correct={ok}")
            r, f = got.get("rise"), got.get("fall")
            rows["cell_rise_ps"].append(r and r["cell_ps"])
            rows["cell_fall_ps"].append(f and f["cell_ps"])
            rows["rise_transition_ps"].append(r and r["tran_ps"])
            rows["fall_transition_ps"].append(f and f["tran_ps"])
            rows["first_cycle_cell_rise_ps"].append(f and f["first_ps"])
            rows["first_cycle_cell_fall_ps"].append(r and r["first_ps"])
            rows["correct"].append(bool(r and f and r["ok"] and f["ok"]))
        for k, v in rows.items():
            table[k].append(v)
    table["method"] = (
        "stage B (column/sense/latch cone from dataout<0>, cut gate loads "
        "restored) driven by the stage A waveforms of the same slew; two "
        "reads: cycle 1 reads mux column 0, cycle 2 reads mux column 1 "
        "holding the opposite bit, so the cycle-2 dataout edge starts from "
        "a latch state the circuit produced.  cell_* = clk 50% rise to "
        "dataout 50%; *_transition = dataout 10%..90%; rows = input slew, "
        "columns = load")
    table["bitline_restore"] = restore
    res["read_access"] = table
    return table


# ---------------------------------------------------------------------------
def step_period(A, B, runner, loadsA, loadsB, res):
    ra = res.get("read_access") or {}
    rest = [r["restore_after_clk_fall_ps"] for r in ra.get("bitline_restore", [])
            if r["restore_after_clk_fall_ps"] is not None]
    cr = (ra.get("cell_rise_ps") or [[None]])[0]
    cf = (ra.get("cell_fall_ps") or [[None]])[0]
    acc = [x for x in (cr[0] if cr else None, cf[0] if cf else None) if x]
    if not rest or not acc:
        res["min_period"] = {"min_period_ps": None,
                             "note": "read table or bitline restore missing"}
        return
    t_high = math.ceil(max(acc) / 5) * 5          # ps, rounded up
    t_low = math.ceil(max(rest) / 5) * 5
    out = {"t_high_min_ps": t_high, "t_low_min_ps": t_low,
           "definition": (
               "t_high = clk rise to dataout valid (50% crossing, 5 fF, "
               "20 ps slew, worst polarity); t_low = clk fall to both "
               "addressed bitlines restored to >= 90% VDD (worst over the "
               "read table); the candidate period t_high + t_low is then "
               "re-simulated (stage A + stage B, both polarities, two "
               "cycles with the column mux switching between them): pass "
               "= cycle-2 dataout correct and cycle-1 bitlines restored "
               "before the cycle-2 rise.  min_period = 1.2 x the verified "
               "period.  The wordline and precharge are level-controlled "
               "by clk in this design (measured: they retire after the "
               "clock falls), so the period is a high-phase plus a low-"
               "phase requirement, not a single self-timed chain."),
           "attempts": []}
    ic = A.prestate(address=0)
    verified = None
    for attempt in range(3):
        P = t_high + t_low
        th, tl = t_high / 1000, t_low / 1000
        r1, f1 = T_CLK1, T_CLK1 + th
        r2, f2 = r1 + P / 1000, r1 + P / 1000 + th
        T_END = round(f2 + tl + 0.3, 3)
        waves = A.dc_waves(address=0)
        waves["clk"] = pwl(clk_events([r1, r2], [f1, f2]), 0.0, SLEWS_PS[0],
                           T_END)
        waves["ADDRESS<0>"] = pwl([(f1 + 0.03, VDD)], 0.0, 20, T_END)
        wvA = run_stage_a(A, runner, f"stageA_period_{P}", ic, waves, T_END,
                          loadsA)
        rec = {"period_ps": P, "t_high_ps": t_high, "t_low_ps": t_low}
        if wvA is None:
            rec["result"] = "stage A did not converge"
            out["attempts"].append(rec)
            break
        rises, falls = clk_edges(A, wvA)
        ok_all = True
        for tag, presets in (("rise", {0: 0, 1: 1}), ("fall", {0: 1, 1: 0})):
            icb = B.ic_for(presets)
            src = B.boundary_sources(A, wvA)
            src.append(f"Cload {B.node(B.dataout)} 0 5e-15")
            wv = run_stage_b(B, runner, f"stageB_period_{P}_{tag}", icb, src,
                             T_END, loadsB, B.save_nets((0, 1)))
            if wv is None:
                rec[tag] = "did not converge"
                ok_all = False
                continue
            t = [x * 1e9 for x in wv.t]
            do = wv.v(B.node(B.dataout))
            final = level_in(t, do, T_END - 0.2, T_END)
            ok_do = (final > 0.9 * VDD) if tag == "rise" else (final < 0.1 * VDD)
            pp, bl, bln = B.addressed_pair(0)
            ok_bl = True
            for n in (bl, bln):
                v = wv.v(B.node(n))
                if level_in(t, v, rises[1] - 0.005, rises[1]) < 0.9 * VDD:
                    ok_bl = False
            first = cross_time(t, do, VDD / 2, tag == "fall", rises[0], falls[0] + 0.4)
            rec[tag] = {"dataout_final_mv": round(final * 1000, 1),
                        "dataout_ok": ok_do, "bitlines_restored": ok_bl,
                        "cycle1_dataout_ps": None if first is None
                        else round((first - rises[0]) * 1000, 1)}
            ok_all = ok_all and ok_do and ok_bl
        rec["result"] = "pass" if ok_all else "fail"
        out["attempts"].append(rec)
        log(f"period {P} ps (high {t_high}, low {t_low}): {rec['result']}")
        if ok_all:
            verified = P
            break
        t_low = math.ceil(t_low * 1.25 / 5) * 5
    out["verified_period_ps"] = verified
    out["min_period_ps"] = None if verified is None else round(verified * 1.2, 1)
    if verified is None:
        res["notes"].append("minimum period: no candidate period verified")
    res["min_period"] = out


# ---------------------------------------------------------------------------
# setup / hold
class Scenario:
    """One input's setup/hold experiment on stage A.

    label: the input pin; base: the other inputs; old -> new: the change.
    watch: control nets whose rising edge must come no later than the
    scenario's own stable-input reference plus TOL_PS; quiet: nets that
    must stay below 10% VDD; mux: the column-select criterion instead
    (the selected/deselected ysel pair settled by the time sae fires)."""

    def __init__(self, name, label, base, old, new, watch=(), quiet=(),
                 mux=None):
        self.name, self.label, self.base = name, label, base
        self.old, self.new = old, new
        self.watch, self.quiet, self.mux = tuple(watch), tuple(quiet), mux

    def inputs_with(self, value):
        """The input keyword set with this scenario's pin at `value`."""
        kw = dict(self.base)
        key = {"banksel": "banksel", "read": "read",
               "byte_wrenab": "byte_wrenab"}.get(self.label)
        if key:
            kw[key] = value
        else:
            bit = int(self.label[8:-1])
            kw["address"] = (kw["address"] & ~(1 << bit)) | (value << bit)
        return kw

    def edges(self, A, wv, rises, falls):
        out = {}
        for lbl in self.watch:
            tr, _ = A.label_trace(wv, lbl)
            out[lbl] = edge_after(wv, tr, rises[0], True, falls[0] + 0.05)
        return out

    def judge(self, A, wv, rises, falls, ref):
        """(pass, measured edges)."""
        edges = self.edges(A, wv, rises, falls)
        for lbl, e in edges.items():
            r = ref.get(lbl)
            if e is None or r is None or e > r + TOL_PS:
                return False, edges
        for q in self.quiet:
            tq, _ = A.label_trace(wv, q)
            if peak_in(wv, tq, rises[0] - 0.1, falls[0] + 0.3) > 0.1 * VDD:
                return False, edges
        if self.mux:
            on_lbl, off_lbl = self.mux
            sae, _ = A.label_trace(wv, "sae")
            t = [x * 1e9 for x in wv.t]
            ts = cross_time(t, sae, VDD / 2, True, rises[0], falls[0])
            if ts is None:
                return False, edges
            on, _ = A.label_trace(wv, on_lbl)
            off, _ = A.label_trace(wv, off_lbl)
            ok = (min(v for tt, v in zip(t, on) if ts <= tt <= falls[0]) > 0.9 * VDD
                  and max(v for tt, v in zip(t, off) if ts <= tt <= falls[0]) < 0.1 * VDD)
            edges["sae"] = round((ts - rises[0]) * 1000, 1)
            return ok, edges
        return True, edges


def make_scenarios(A):
    # measured map (docs/pin_interface.md section 3): the low mux bits,
    # then the wordline column, then the half select in the top bit
    col_bit, half_bit = contract.mux_bits(A.spec), A.n_addr - 1
    return [
        Scenario(f"ADDRESS<{col_bit}> (column)", f"ADDRESS<{col_bit}>",
                 dict(address=0), 0, 1, watch=("wlb<1>",), quiet=("wlb<0>",)),
        Scenario(f"ADDRESS<{half_bit}> (half)", f"ADDRESS<{half_bit}>",
                 dict(address=0), 0, 1, watch=("wlt<0>",), quiet=("wlb<0>",)),
        Scenario("ADDRESS<0> (mux)", "ADDRESS<0>", dict(address=0), 0, 1,
                 watch=("sae",), mux=("yselb<1>", "yselb<0>")),
        Scenario("banksel", "banksel", dict(address=0), 0, 1,
                 watch=("wlb<0>", "sae")),
        Scenario("read", "read", dict(address=0, read=0), 0, 1,
                 watch=("sae",)),
        Scenario("byte_wrenab", "byte_wrenab",
                 dict(address=0, read=0, byte_wrenab=0), 0, 1,
                 watch=("wrena",)),
    ]


def bisect(lo, hi, test, iters):
    """Invariant: lo passes, hi fails (assumed at the bracket ends).  test
    returns True, False or None (the simulation did not converge).  An
    unknown narrows toward lo like a failure (the pessimistic side for a
    constraint) but is recorded, and if the final boundary rests on an
    unknown the caller reports null.  Returns (lo, hi, record, hi_known)."""
    rec = []
    hi_known = True
    for _ in range(iters):
        mid = round((lo + hi) / 2, 1)
        r = test(mid)
        ok, detail = r if isinstance(r, tuple) else (r, None)
        rec.append({"t_ps": mid, "pass": ok, "edges_ps": detail})
        if ok:
            lo = mid
        else:
            hi = mid
            hi_known = ok is not None
    return lo, hi, rec, hi_known


def _finish(entry, key, lo, hi, lo_end, hi_end, hi_known, sign):
    """Fill a setup/hold entry from a bisection result; null when the
    boundary is a bracket end or rests on a non-converged run."""
    entry.update({"last_pass_ps": lo, "first_fail_ps": hi})
    if lo == lo_end:
        entry[key] = None
        entry["note"] = f"no passing point found down to {lo_end} ps"
    elif not hi_known:
        entry[key] = None
        entry["note"] = (f"the failing side of the final bracket ({hi} ps) "
                         f"is a run that did not converge; last verified "
                         f"pass at {lo} ps")
    else:
        entry[key] = sign * lo
        if hi == hi_end:
            entry["note"] = (f"passes even at {hi_end} ps; the value is a "
                             f"bound, not a measurement")
    return entry


def step_setup(A, runner, loadsA, res, quick=False):
    T_END = 1.45
    R1, F1 = T_CLK1, 1.1
    iters = 3 if quick else 5
    setup, hold = {}, {}
    for sc in make_scenarios(A):
        # the scenario's own reference: the same cycle with the NEW value
        # held stable (the wordline of the other half, the write enable,
        # ... each has its own nominal timing)
        kw = sc.inputs_with(sc.new)
        ic = A.prestate(**kw)
        waves = A.dc_waves(**kw)
        waves["clk"] = pwl(clk_events([R1], [F1]), 0.0, SLEWS_PS[0], T_END)
        tag = f"stageA_ref_{sc.label.replace('<', '').replace('>', '')}"
        wv = run_stage_a(A, runner, tag, ic, waves, T_END, loadsA)
        if wv is None:
            res["notes"].append(f"setup {sc.name}: the stable reference run "
                                f"did not converge")
            setup[sc.name] = hold[sc.name] = None
            continue
        rises, falls = clk_edges(A, wv)
        ref = sc.edges(A, wv, rises, falls)
        log(f"reference {sc.name}: {ref}")
        if sc.label == "byte_wrenab" and ref.get("wrena") is not None:
            res.setdefault("stage_a", {})["clk_to_wrena_ps"] = ref["wrena"]

        def run_at(t_rel, mode):
            # setup: input = old before, switches to new at t_rel (<0 = before edge)
            # hold:  input = new before, switches back to old at t_rel (>0 = after)
            before = sc.old if mode == "setup" else sc.new
            after = sc.new if mode == "setup" else sc.old
            kw = sc.inputs_with(before)
            ic = A.prestate(**kw)
            waves = A.dc_waves(**kw)
            waves["clk"] = pwl(clk_events([R1], [F1]), 0.0, SLEWS_PS[0], T_END)
            waves[sc.label] = pwl([(R1 + t_rel / 1000, VDD * after)],
                                  VDD * before, 20, T_END)
            tag = f"stageA_{mode}_{sc.label.replace('<', '').replace('>', '')}_{t_rel:+.0f}"
            wv = run_stage_a(A, runner, tag, ic, waves, T_END, loadsA)
            if wv is None:
                return None, None
            rises, falls = clk_edges(A, wv)
            return sc.judge(A, wv, rises, falls, ref)

        # setup: pass region is t <= -setup.  bracket [-320, +80] ps
        lo, hi, rec, known = bisect(-320.0, 80.0,
                                    lambda t: run_at(t, "setup"), iters)
        entry = _finish({"reference_ps": ref, "search": rec}, "setup_ps",
                        lo, hi, -320.0, 80.0, known, -1)
        setup[sc.name] = entry
        log(f"setup {sc.name}: {entry['setup_ps']} ps {entry.get('note', '')}")
        # hold: pass region is t >= hold.  bracket [+240 (pass), -80 (fail)]
        lo, hi, rec, known = bisect(240.0, -80.0,
                                    lambda t: run_at(t, "hold"), iters)
        entry = _finish({"reference_ps": ref, "search": rec}, "hold_ps",
                        lo, hi, 240.0, -80.0, known, 1)
        hold[sc.name] = entry
        log(f"hold {sc.name}: {entry['hold_ps']} ps {entry.get('note', '')}")
    res["setup_ps"] = setup
    res["hold_ps"] = hold
    res["setup_hold_method"] = (
        f"stage A, one cycle (clk high {R1} to {F1} ns), input edges 20 ps; "
        f"bisection ({iters} iterations) on the input's 50% crossing relative "
        f"to the clk 50% rise.  Each scenario first runs its own REFERENCE "
        f"(the new value held stable) and records the clk-to-edge of the "
        f"watched control nets (reference_ps).  Setup: the input switches to "
        f"the new value at t (negative = before the edge) and PASSES if the "
        f"watched edges come no later than the reference plus {TOL_PS:.0f} ps, "
        f"the quiet nets (the wrong wordline) stay below 10% VDD, and for the "
        f"mux bit the column selects are settled when sae fires.  Hold: the "
        f"input holds the new value through the edge and switches away at t "
        f"(positive = after the edge); PASS = same criterion.  setup_ps = "
        f"-(latest passing arrival); hold_ps = earliest passing departure; "
        f"the measured edges of every bisection point are kept in search[]. "
        f"Resolution = bracket / 2^iterations.")


def step_wd(A, B, runner, loadsA, loadsB, res, quick=False):
    """wd setup/hold: a stage A write-then-read run supplies the control
    waveforms; stage B writes the addressed cell (preset to NOT wd) and
    reads it back; wd's arrival is bisected."""
    T_END = 4.2
    iters = 3 if quick else 5
    ic = A.prestate(address=0, read=0, byte_wrenab=1)
    waves = A.dc_waves(address=0, read=0, byte_wrenab=1)
    waves["clk"] = pwl(clk_events([0.5, 2.5], [1.5, 3.5]), 0.0, SLEWS_PS[0], T_END)
    waves["read"] = pwl([(2.0, VDD)], 0.0, 20, T_END)
    waves["byte_wrenab"] = pwl([(2.0, 0.0)], VDD, 20, T_END)
    wvA = run_stage_a(A, runner, "stageA_writeread", ic, waves, T_END, loadsA)
    if wvA is None:
        res["setup_ps"]["wd"] = res["hold_ps"]["wd"] = None
        res["notes"].append("wd: stage A write-then-read run did not converge")
        return
    rises, falls = clk_edges(A, wvA)
    tr, _ = A.label_trace(wvA, "wrena")
    wr = edge_after(wvA, tr, rises[0], True, falls[0] + 0.05)
    wr_off = edge_after(wvA, tr, falls[0], False)
    res.setdefault("stage_a", {})["write_cycle"] = {
        "clk_to_wrena_ps": wr, "wrena_retire_after_clk_fall_ps": wr_off}
    pp, bl, bln = B.addressed_pair(0)
    stor = B.orient.get(pp, pp[0])
    log(f"write cycle: clk->wrena {wr} ps")

    def run_at(t_rel, mode, wd_val=1):
        before = (1 - wd_val) if mode == "setup" else wd_val
        after = wd_val if mode == "setup" else (1 - wd_val)
        icb = B.ic_for({0: 1 - wd_val, 1: 1 - wd_val})
        src = B.boundary_sources(A, wvA, wd=None)
        src.append(f"Vwd {B.node(B.sim.net_of('wd<0>'))} 0 "
                   + pwl([(rises[0] + t_rel / 1000, VDD * after)], VDD * before,
                         20, T_END))
        src.append(f"Cload {B.node(B.dataout)} 0 5e-15")
        wv = run_stage_b(B, runner, f"stageB_wd_{mode}_{t_rel:+.0f}", icb, src,
                         T_END, loadsB, B.save_nets((0,)))
        if wv is None:
            return None, None
        t = [x * 1e9 for x in wv.t]
        q = level_in(t, wv.v(B.node(stor)), rises[1] - 0.2, rises[1])
        do = level_in(t, wv.v(B.node(B.dataout)), T_END - 0.2, T_END)
        want_hi = bool(wd_val)
        cell_ok = (q > 0.9 * VDD) if want_hi else (q < 0.1 * VDD)
        do_ok = (do > 0.9 * VDD) if want_hi else (do < 0.1 * VDD)
        return cell_ok and do_ok, {"cell_mv": round(q * 1000, 1),
                                   "dataout_mv": round(do * 1000, 1)}

    lo, hi, rec, known = bisect(-320.0, 80.0, lambda t: run_at(t, "setup"),
                                iters)
    e = _finish({"search": rec,
                 "polarity": "write 1 into a cell holding 0, read back"},
                "setup_ps", lo, hi, -320.0, 80.0, known, -1)
    res["setup_ps"]["wd"] = e
    log(f"setup wd: {e['setup_ps']} ps {e.get('note', '')}")
    lo, hi, rec, known = bisect(240.0, -80.0, lambda t: run_at(t, "hold"),
                                iters)
    e = _finish({"search": rec,
                 "polarity": "wd = 1 held through the edge, then dropped"},
                "hold_ps", lo, hi, 240.0, -80.0, known, 1)
    res["hold_ps"]["wd"] = e
    log(f"hold wd: {e['hold_ps']} ps {e.get('note', '')}")
    res["wd_method"] = (
        "stage A two-cycle write (cycle 1: read=0, byte_wrenab=1) then read "
        "(cycle 2) waveforms drive stage B; the addressed cell is preset to "
        "the complement of wd; PASS = the cell's bitline-side node is at wd "
        "before the readback edge AND cycle-2 dataout returns wd.  wd's 50% "
        "crossing is bisected relative to the cycle-1 clk rise, same "
        "brackets and iteration count as the stage A inputs.")


# ---------------------------------------------------------------------------
LEAK_SETTLE_NS = 100.0
LEAK_DRIFT_MAX = 0.10       # last decade (30 to 100 ns) may still fall this much


def read_op_current(raw, settle_ns=LEAK_SETTLE_NS):
    """Idle supply current from the settling transient: the value at the
    end of the window, the decay curve at 1, 3, 10, 30, ... ns, and the
    drift over the last decade.  The result is None (with the curve in the
    note) when the transient stopped early or the last-decade drift
    exceeds LEAK_DRIFT_MAX: a current still falling that fast is not an
    idle value."""
    names, pts = cs.read_raw(raw)
    if not pts:
        return None, {"note": "no data"}
    j = names.index("i(vdd)")
    t = [p[0] for p in pts]
    i = [abs(p[j]) for p in pts]
    t_end = t[-1]
    curve = {}
    for tp in (1, 3, 10, 30, 100, 300, 1000):
        if tp * 1e-9 <= t_end * 1.001:
            k = min(range(len(t)), key=lambda q: abs(t[q] - tp * 1e-9))
            curve[f"{tp}ns"] = i[k]
    info = {"curve_a": curve, "t_end_ns": round(t_end * 1e9, 3)}
    if t_end < settle_ns * 1e-9 * 0.99:
        info["note"] = f"transient stopped at {t_end * 1e9:.2f} ns"
        return None, info
    last = [v for tt, v in zip(t, i) if tt >= 0.95 * t_end]
    end = sum(last) / len(last)
    k30 = min(range(len(t)), key=lambda q: abs(t[q] - 0.3 * t_end))
    drift = (i[k30] - end) / end
    info["drift_last_decade"] = round(drift, 4)
    if abs(drift) > LEAK_DRIFT_MAX:
        info["note"] = (f"still falling {drift * 100:.1f}% over the last "
                        f"decade ({0.3 * t_end * 1e9:.0f} to {t_end * 1e9:.0f} ns)")
        return None, info
    info["note"] = (f"value at {t_end * 1e9:.0f} ns; fell {drift * 100:.1f}% "
                    f"over the last decade")
    return end, info


def _run_leak(stage, runner, name, ic, sources):
    """Leakage deck: gear integration only.  Trapezoidal integration from a
    uic preset rings on this deck (the column slice reads microamps that
    decay for the whole window), so it is not a valid fallback here; the
    small first step plus explicit maximum step in emit_op is what makes
    gear complete the 100 ns window (rshunt 1e14 measured against 1e12:
    the shunts add 0.8 nA to a 15.8 nA column, so 1e14 it is)."""
    deck = stage.emit_op(runner.workdir / f"{name}.sp", ic, sources, [],
                         settle_ns=LEAK_SETTLE_NS)
    raw = runner.run(deck, timeout=1800)
    if runner.log:
        runner.log[-1]["options"] = "leak_gear"
    if raw:
        return read_op_current(raw)
    return None, {"note": "the settling transient produced no raw file"}


def step_leakage(ex, A, B, runner, spec, corner, res):
    from sramforge import timing as t5
    out = {}
    # single cell (rung 5 bench, unchanged)
    deck = t5.leakage_bench(corner)
    raw = t5.run_deck(deck, runner.workdir, "leak_cell")
    m = t5.measure_leakage(raw)
    out["cell_leak_a"] = m.get("cell_leak_a")
    # the whole band at idle
    band = BandSlice(ex, spec, corner)
    idle, forced = band.idle_state()
    out["band_slice_a"], info = _run_leak(band, runner, "leak_band", idle,
                                          band.idle_sources(forced))
    out["band_slice_detail"] = info
    out["band_slice_devices"] = len(band.sl.devices)
    if out["band_slice_a"] is None:
        res["notes"].append(f"leakage: band idle point: {info.get('note')}")
    # one column slice at idle (256 cells + the io column periphery)
    out["column_slice_a"], info = _run_leak(B, runner, "leak_column",
                                            B.ic_for({}),
                                            B.idle_sources(band, idle))
    out["column_slice_detail"] = info
    out["column_slice_devices"] = len(B.sl.devices)
    out["column_slice_cells"] = len(B.pairs)
    if out["column_slice_a"] is None:
        res["notes"].append(f"leakage: column idle point: {info.get('note')}")
    n_cols = 2 * spec.depth_groups
    n_cells = res["config"]["bitcells"]
    cl = out["cell_leak_a"]
    if cl is not None and out["column_slice_a"] is not None:
        out["column_periphery_a"] = out["column_slice_a"] - len(B.pairs) * cl
    else:
        out["column_periphery_a"] = None
    out["cells_total_a"] = None if cl is None else cl * n_cells
    if None not in (out["band_slice_a"], out["column_periphery_a"], cl):
        total = (out["band_slice_a"] + n_cols * out["column_periphery_a"]
                 + n_cells * cl)
        out["bank_total_a"] = total
        out["bank_total_nw"] = total * VDD * 1e9
    else:
        out["bank_total_a"] = out["bank_total_nw"] = None
    out["measured_slices_sum_a"] = (
        None if None in (out["band_slice_a"], out["column_slice_a"])
        else out["band_slice_a"] + out["column_slice_a"])
    out["method"] = (
        f"idle points (clk 0, banksel 0, address 0, sdel all 1) reached by "
        f"a {LEAK_SETTLE_NS:.0f} ns settling transient from the digital "
        f"state (.ic, uic; a Newton .op of the band does not converge); the "
        f"value is the supply current at the end of the window, the decay "
        f"curve is in *_detail.curve_a, and the value is null if it still "
        f"fell more than {LEAK_DRIFT_MAX * 100:.0f}% over the last decade: "
        f"band_slice_a = the whole "
        f"decode/control band; column_slice_a = one dataout column slice "
        f"({len(B.pairs)} cells + its io column); cell_leak_a = the rung 5 "
        f"single-cell bench.  measured_slices_sum_a = band + one column, "
        f"as simulated.  bank_total_a = band + {n_cols} columns x "
        f"(column_slice - {len(B.pairs)} x cell) + {n_cells} cells x cell: "
        f"an extrapolation, stated as such.  Power = current x {VDD} V.")
    res["leakage"] = out
    log(f"leakage: cell {cl} A, band {out['band_slice_a']} A, column "
        f"{out['column_slice_a']} A, bank total {out['bank_total_nw']} nW")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="32,4,32", help="bits,strips,depth")
    ap.add_argument("--corner", default="TT", choices=["TT", "FF", "SS"])
    ap.add_argument("--out", default=str(REPO / "out" / "char"))
    ap.add_argument("--steps", default="netlist,gatecap,pins,stagea,read,"
                    "period,setup,wd,leakage")
    ap.add_argument("--quick", action="store_true",
                    help="fewer bisection iterations (development)")
    a = ap.parse_args()
    from sramforge import BankSpec
    bits, strips, depth = map(int, a.config.split(","))
    spec = BankSpec(bits_per_group=bits, strips_per_group=strips,
                    depth_groups=depth)
    out_dir = pathlib.Path(a.out)
    work = out_dir / "work" / f"{spec.name}_{a.corner}"
    work.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"{spec.name}_{a.corner}.json"
    res = json.loads(out_json.read_text()) if out_json.exists() else {}
    res.setdefault("config", {}).update({
        "bits": bits, "strips": strips, "depth": depth, "name": spec.name,
        "corner": a.corner, "corner_name": f"{a.corner}_0p7V_25C",
        "vdd_v": VDD, "temp_c": 25,
        "data_width": contract.data_width(spec),
        "n_address": contract.address_width(spec),
        "words": contract.word_count(spec)})
    res.setdefault("provenance", {}).update({
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        # tool and model file names only: where they live is local
        "ngspice": pathlib.Path(cs.NGSPICE).name,
        "osdi": pathlib.Path(cs.OSDI).name,
        "models": pathlib.Path(cs.models_for(a.corner)).name})
    res.setdefault("estimates", {})
    res.setdefault("notes", [])
    res.setdefault("setup_ps", {})
    res.setdefault("hold_ps", {})
    steps = a.steps.split(",")
    t_start = time.time()
    # ngspice runs accumulate across invocations (a --steps rerun of one
    # step must not erase the provenance of the others); a step that is
    # rerun replaces its own decks' earlier entries by deck name.
    prev_runs = list(res["provenance"].get("runs", []))
    prev_wall = res["provenance"].get("wall_s_total", 0.0)

    def save():
        res["provenance"]["wall_s_total"] = round(
            prev_wall + time.time() - t_start, 1)
        redone = {r["deck"] for r in runner.log}
        res["provenance"]["runs"] = ([r for r in prev_runs
                                      if r["deck"] not in redone]
                                     + list(runner.log))
        out_json.write_text(json.dumps(res, indent=1))

    runner = Runner(work)
    manifest = step_netlist(spec, work, res)
    ex = cs.load_netlist(work / "netlist.json")
    res["provenance"]["netlist_devices"] = len(ex.devices)
    save()

    cfin = res["estimates"].get("gate_cap_per_fin_f")
    if "gatecap" in steps or not cfin:
        cfin = step_gatecap(runner, a.corner, res)
        save()
    if "pins" in steps:
        step_pins(ex, manifest, cfin, res)
        save()

    need_a = {"stagea", "read", "period", "setup", "wd", "leakage"} & set(steps)
    if not need_a:
        return 0
    log("building stage A and stage B slices")
    A = StageA(ex, spec, a.corner)
    B = StageB(ex, spec, a.corner)
    loadsA, tabA = A.cut_load_lines(cfin)
    loadsB, tabB = B.cut_load_lines(cfin)
    res["provenance"]["stage_a_devices"] = len(A.sl.devices)
    res["provenance"]["stage_b_devices"] = len(B.sl.devices)
    res["provenance"]["stage_b_cells"] = len(B.pairs)
    res["estimates"]["cut_loads_ff"] = {
        "stage_a_total": round(sum(tabA.values()) * 1e15, 2),
        "stage_b_total": round(sum(tabB.values()) * 1e15, 2),
        "note": ("capacitors restoring the gate loads the cone truncation "
                 "removed (fin count x per-fin gate cap, no wire); the "
                 "largest sit on sae, the precharge lines and the wordline")}
    for lbl in ("sae", "wlb<0>", "blprechbn", "saprechn"):
        for n in A.nets_of(lbl):
            if n in tabA:
                res["estimates"]["cut_loads_ff"][lbl] = round(tabA[n] * 1e15, 2)
    log(f"stage A {len(A.sl.devices)} devices, stage B {len(B.sl.devices)} "
        f"devices / {len(B.pairs)} cells; cut loads A "
        f"{res['estimates']['cut_loads_ff']['stage_a_total']} fF")
    save()
    if "stagea" in steps:
        step_stagea(A, runner, loadsA, res, a.quick)
        save()
    if "read" in steps:
        step_read(A, B, runner, loadsB, res)
        save()
    if "period" in steps:
        step_period(A, B, runner, loadsA, loadsB, res)
        save()
    if "setup" in steps:
        step_setup(A, runner, loadsA, res, a.quick)
        save()
    if "wd" in steps:
        step_wd(A, B, runner, loadsA, loadsB, res, a.quick)
        save()
    if "leakage" in steps:
        step_leakage(ex, A, B, runner, spec, a.corner, res)
        save()
    log(f"done: {out_json} ({res['provenance']['wall_s_total']} s total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
