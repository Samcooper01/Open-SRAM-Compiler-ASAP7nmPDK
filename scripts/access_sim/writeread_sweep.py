#!/usr/bin/env python3
"""Write-then-readback proof across the bits sweep.

For every bits in {4, 8, ..., 28} (band v2) plus the 32 reference:
generate the bank, extract it (rung-4 engine), and run the stage-C
two-cycle analog simulation twice - write 1 into cells storing 0, and
write 0 into cells storing 1.  PASS iff the addressed cell's storage
nodes FLIP during the write cycle and dataout<0> lands on the written
bit after the readback cycle.

This proves each configuration's OWN extracted layout stores data and
returns it - the data-path complement to rung 4.5's decode proof.

Usage: python3 scripts/access_sim/writeread_sweep.py [bits ...]
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
from sramforge import BankSpec, generate            # noqa: E402
from sramforge.lvs import extract                   # noqa: E402
from dump_netlist import dump                       # noqa: E402
from access_sim.access_analyze import read_raw      # noqa: E402

NGSPICE = str(REPO.parent / "tools" / "ngspice-install" / "bin" / "ngspice")
OSDI = str(REPO.parent / "tools" / "bsimcmg110.osdi")
SWEEP = REPO / "out" / "wr_sweep"


def vec(names, pts, node):
    for i, n in enumerate(names):
        if n == f"v({node})" or n == node:
            return [p[i] for p in pts]
    raise KeyError(node)


def run_cfg(bits):
    tag = f"b{bits}"
    work = SWEEP / tag
    work.mkdir(parents=True, exist_ok=True)
    nl = work / "netlist.json"
    if not nl.exists():
        spec = BankSpec(bits_per_group=bits, strips_per_group=4,
                        depth_groups=32)
        with tempfile.TemporaryDirectory() as td:
            gds = str(pathlib.Path(td) / "bank.gds")
            generate(spec, gds)
            ex = extract(gds, spec.name)
        dump(ex, str(nl))
    # proven-converging options (full 6 ns at bits=16): heavier damping,
    # verdicts are LEVELS so the ~ps edge slowdown is irrelevant
    ROBUST = (".options reltol=5e-3 abstol=1e-9 vntol=1e-4 rshunt=1e8 "
              "itl4=1000 cshunt=1e-15 method=gear")
    (work / ".spiceinit").write_text(f"osdi {OSDI}\n")

    def emit(opts=None):
        env = dict(os.environ,
                   SRAMFORGE_NETLIST=str(nl), SRAMFORGE_SCRATCH=str(work))
        if opts:
            env["SRAMFORGE_SPICE_OPTS"] = opts
        return subprocess.run([sys.executable,
                               str(REPO / "scripts/access_sim/stageC_emit.py")],
                              env=env, capture_output=True, text=True)

    r = emit(ROBUST)
    if r.returncode:
        print(r.stdout[-800:]); print(r.stderr[-800:])
        return (bits, "EMIT-FAIL", None, None)
    results = {}
    for run in ("w1", "w0"):
        sp = work / f"stageC_{run}.sp"
        raw = work / f"stageC_{run}.raw"
        s = subprocess.run([NGSPICE, "-b", str(sp)], cwd=str(work),
                           capture_output=True, text=True, timeout=3600)
        if not raw.exists():
            # convergence retry: heavier damping, more iterations
            emit(ROBUST)
            s = subprocess.run([NGSPICE, "-b", str(sp)], cwd=str(work),
                               capture_output=True, text=True, timeout=3600)
        if not raw.exists():
            print(s.stdout[-500:]); print(s.stderr[-500:])
            return (bits, f"{run}-SIM-FAIL", None, None)
        names, pts = read_raw(str(raw))
        m = json.load(open(work / f"stageC_{run}_map.json"))
        t = vec(names, pts, "time" if "time" in names else names[0])
        do = vec(names, pts, m["dataout"])
        # final value after the readback cycle (t > 4.5 ns)
        tail = [v for tt, v in zip(t, do) if tt > 4.5e-9]
        final = sum(tail[-20:]) / len(tail[-20:])
        # cell flip during the write cycle (Q ends the write at the bit)
        q_ok = None
        if m["sel"]:
            q = vec(names, pts, m["sel"][0])
            qw = [v for tt, v in zip(t, q) if 2.0e-9 < tt < 2.4e-9]
            qv = sum(qw[-10:]) / len(qw[-10:])
            q_ok = qv > 0.55 if run == "w1" else qv < 0.15
        want_hi = (run == "w1")
        do_ok = final > 0.55 if want_hi else final < 0.15
        results[run] = (do_ok, q_ok, final)
    ok = all(r[0] and (r[1] is not False) for r in results.values())
    return (bits, "PASS" if ok else "FAIL",
            results["w1"], results["w0"])


def main():
    todo = ([int(a) for a in sys.argv[1:]]
            or [4, 8, 12, 16, 20, 24, 28, 32])
    print(f"{'bits':>5} {'verdict':>8}  "
          f"{'w1: dataout / cellQ':>22}  {'w0: dataout / cellQ':>22}")
    fails = 0
    for bits in todo:
        b, verdict, w1, w0 = run_cfg(bits)
        def fmt(r):
            if r is None:
                return "-"
            do_ok, q_ok, final = r
            return (f"{final*1000:5.0f} mV {'ok' if do_ok else 'BAD'}"
                    f" / {'ok' if q_ok else 'BAD' if q_ok is False else '?'}")
        print(f"{b:>5} {verdict:>8}  {fmt(w1):>22}  {fmt(w0):>22}",
              flush=True)
        if verdict != "PASS":
            fails += 1
    print("SWEEP", "PASS" if not fails else f"FAIL ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
