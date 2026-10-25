"""Rung 5 - parameterized timing benches: the compiler's electrical
diagnosis for a given configuration.

Every bench is built from MEASURED pieces of the layout:

  * column bench (scales with --bits): one addressed 6T (six-transistor)
    bitcell + (bits-1) real unaddressed cells on the bitline pair, wire
    capacitance proportional to the strip length (bits x 216 nm of M2 -
    Metal 2 - at ~0.2 fF/um), precharge devices, and for the write a
    6-fin pulldown driver,
  * wordline bench (scales with --depth): the MEASURED wordline driver
    (parallel 18+18+13+13-fin devices, read from the rung 4 extraction of
    ctrl_decode_32) driving a distributed RC (resistance-capacitance)
    ladder for the M3 (Metal 3) wordline column - depth x 1.08 um at
    ~130 ohm/um and ~0.22 fF/um - loaded by the real access-transistor
    gates it crosses (2 gates x 8 strips per colgrp row),
  * leakage bench: one holding cell, operating-point current.

All three corners (TT/FF/SS: typical/fast/slow).  Results are written
to out/timing_grid.json.

Wire RC values are the documented approximations for ASAP7's M2/M3
36 nm-pitch layers; they are the only non-extracted numbers in the deck
and are called out wherever they are used.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess

REPO = pathlib.Path(__file__).resolve().parent.parent


def find_tools():
    """Locate the simulator and the compiled FinFET compact model.

    Both are needed only by the measurement scripts, never by the
    generator or the verification ladder, and neither is pip-installable:
    ngspice must be built with OSDI (Open Source Device Interface)
    support, and the BSIM-CMG model compiled to an .osdi file with
    OpenVAF.  Looked for, in order:

      1. SRAMFORGE_NGSPICE and SRAMFORGE_OSDI in the environment;
      2. a tools/ directory beside the repository or any parent of it,
         holding ngspice-install/bin/ngspice and bsimcmg110.osdi;
      3. ngspice on PATH (the model file must then come from 1 or 2).

    Returns (ngspice, osdi); either may be None, and require_tools()
    turns that into a message saying exactly what is missing.
    """
    ng = os.environ.get("SRAMFORGE_NGSPICE")
    osdi = os.environ.get("SRAMFORGE_OSDI")
    for base in [REPO] + list(REPO.parents):
        tools = base / "tools"
        if not ng and (tools / "ngspice-install" / "bin" / "ngspice").exists():
            ng = str(tools / "ngspice-install" / "bin" / "ngspice")
        if not osdi and (tools / "bsimcmg110.osdi").exists():
            osdi = str(tools / "bsimcmg110.osdi")
    if not ng:
        ng = shutil.which("ngspice")
    return ng, osdi


def require_tools():
    """Fail with instructions, not a traceback, when a measurement is run
    without the simulator or the model."""
    missing = []
    if not NGSPICE or not pathlib.Path(NGSPICE).exists():
        missing.append("ngspice built with OSDI support "
                       "(set SRAMFORGE_NGSPICE to its path)")
    if not OSDI or not pathlib.Path(OSDI).exists():
        missing.append("the BSIM-CMG model compiled with OpenVAF to "
                       "bsimcmg110.osdi (set SRAMFORGE_OSDI to its path)")
    if missing:
        raise SystemExit("this measurement needs a circuit simulator that "
                         "is not installed:\n  - " + "\n  - ".join(missing)
                         + "\nThe generator and the verification ladder do "
                         "not need either; see the README's quick start.")


NGSPICE, OSDI = find_tools()
MODELS = REPO / "spice" / "models"
CELL_SP = REPO / "spice" / "sram6t122.sp"

VDD = 0.7
CELL_PITCH_UM = 0.216       # bitline length per cell (2 half-pitch columns)
BL_C_PER_UM = 0.2e-15       # M2 wire capacitance
WL_UM_PER_ROW = 1.08        # colgrp row pitch
WL_R_PER_UM = 130.0         # M3 wire resistance
WL_C_PER_UM = 0.22e-15      # M3 wire capacitance
WL_SEGS = 8


def _header(corner):
    return (f".include {MODELS}/asap7_{corner}.osdi.sp\n"
            f".include {CELL_SP}\n"
            f".param vdd={VDD}\n.global 0\nVdd vdd 0 {{vdd}}\n")


def column_bench(bits, corner, write=False):
    wire_c = bits * CELL_PITCH_UM * BL_C_PER_UM
    d = ["* column bench: 1 addressed + real loads, scaled by bits",
         _header(corner),
         "Xcell0 bl bln wl vdd 0 sram6t122"]
    for i in range(1, bits):
        d.append(f"Xload{i} bl bln 0 vdd 0 sram6t122")
    d += [f"Cblw  bl  0 {wire_c:.3e}",
          f"Cblnw bln 0 {wire_c:.3e}",
          "Npc1 bl  prechn vdd vdd pmos_rvt nfin=4",
          "Npc2 bln prechn vdd vdd pmos_rvt nfin=4",
          "Npeq bl  prechn bln vdd pmos_rvt nfin=2"]
    if write:
        d += ["Nwr bln wen 0 0 nmos_rvt nfin=6",
              "Vwen wen 0 PWL(0 0 1.8n 0 1.85n {vdd} 4n {vdd} 4.05n 0)",
              ".ic v(xcell0.q)=0 v(xcell0.qb)={vdd}"]
    else:
        d += [".ic v(xcell0.q)={vdd} v(xcell0.qb)=0"]
    d += ["Vwl wl 0 PWL(0 0 2n 0 2.05n {vdd} 4n {vdd} 4.05n 0)",
          "Vprechn prechn 0 PWL(0 0 1.5n 0 1.55n {vdd} 5n {vdd} 5.05n 0)",
          ".tran 2p 6n uic",
          ".control", "run", "set filetype=ascii",
          "write OUT.raw v(wl) v(bl) v(bln) v(xcell0.q) v(xcell0.qb)",
          ".endc", ".end"]
    return "\n".join(d)


def wordline_bench(depth, strips, corner):
    """The measured driver, the depth-scaled wire, the real gate load.
    The wire runs the whole column-group stack: depth rows at the row
    pitch, which grows by one strip height (0.27 um) per strip beyond
    four (generator.colgrp_pitch), so eight strips at depth 32 give the
    same wire as four strips at depth 64."""
    row_um = WL_UM_PER_ROW * (1 + (strips - 4) * 0.27 / WL_UM_PER_ROW)
    length_um = depth * row_um
    seg_r = length_um * WL_R_PER_UM / WL_SEGS
    seg_c = length_um * WL_C_PER_UM / WL_SEGS
    gates_total = 2 * 2 * strips * depth      # 2 ACC x (2 arrays x strips) per row
    gates_per_seg = max(1, gates_total // WL_SEGS)
    d = ["* wordline bench: measured driver + depth-scaled RC + gate load",
         _header(corner),
         "* the measured wlt driver: parallel 18+18+13+13-fin devices",
         "Npu1 wl0 inb vdd vdd pmos_rvt nfin=18",
         "Npu2 wl0 inb vdd vdd pmos_rvt nfin=18",
         "Npu3 wl0 inb vdd vdd pmos_rvt nfin=13",
         "Npu4 wl0 inb vdd vdd pmos_rvt nfin=13",
         "Npd1 wl0 inb 0 0 nmos_rvt nfin=18",
         "Npd2 wl0 inb 0 0 nmos_rvt nfin=18",
         "Npd3 wl0 inb 0 0 nmos_rvt nfin=13",
         "Npd4 wl0 inb 0 0 nmos_rvt nfin=13"]
    for s in range(1, WL_SEGS + 1):
        d.append(f"Rw{s} wl{s-1} wl{s} {seg_r:.2f}")
        d.append(f"Cw{s} wl{s} 0 {seg_c:.3e}")
        for g in range(gates_per_seg):
            d.append(f"Ng{s}_{g} vdd wl{s} vdd 0 nmos_sram nfin=2")
    d += ["Vin inb 0 PWL(0 {vdd} 1n {vdd} 1.02n 0)",
          ".tran 1p 3n",
          ".control", "run", "set filetype=ascii",
          "write OUT.raw v(inb) v(wl0) v(wl%d)" % WL_SEGS,
          ".endc", ".end"]
    return "\n".join(d)


def energy_bench(kind, bits, corner):
    """Energy per access, from the same benches the timing comes from.

    kind "read" or "write" is the column bench with a supply probe: one
    addressed cell, the other bits-1 cells of the column as real loads,
    the bitline wire capacitance, and the precharge devices, so the
    integral of the supply current over a cycle is the charge the column
    actually draws and puts back.  kind "wordline" is the wordline bench
    with the same probe, giving the energy to drive one row's wordline
    at this depth and strip count.

    A bank's access energy is then columns x column energy + one
    wordline, plus the control block's own switching, which is measured
    separately on the decode slice and stated apart rather than folded
    in silently.
    """
    if kind in ("read", "write"):
        d = column_bench(bits, corner, write=(kind == "write"))
    else:
        raise ValueError(kind)
    return d.replace("write OUT.raw", "write OUT.raw i(Vdd)")


def measure_energy(raw, vdd=VDD):
    """Charge drawn from the supply over the whole run, and the energy
    it represents: E = VDD x integral of |i| dt, trapezoidal."""
    names, pts = read_raw(raw)
    ts = col(names, pts, "time")
    cur = col(names, pts, "i(vdd)") or col(names, pts, "vdd#branch")
    if not ts or not cur:
        return None
    q = 0.0
    for i in range(1, len(ts)):
        dt = ts[i] - ts[i - 1]
        q += 0.5 * (abs(cur[i]) + abs(cur[i - 1])) * dt
    return {"charge_fc": round(q * 1e15, 3),
            "energy_fj": round(q * vdd * 1e15, 3)}


def margin_bench(kind, corner):
    """Static margin of the 6T (six-transistor) bitcell.

    Three decks, all quasi-static, all on the same devices the extraction
    found (spice/sram6t122.sp: access and pull-down two fins each,
    pull-up one fin, so the cell ratio is 1.0 and the pull-up ratio 0.5 -
    fin quantization leaves no other choice at this pitch):

      hold   half-cell voltage transfer curve with the wordline low: the
             storage node's inverter driving the other node, swept.
      read   the same with the wordline HIGH and the bitline held at VDD,
             so the access device fights the pull-down.  This is the
             read-disturb condition and gives the read static noise
             margin.
      write  the whole cell, wordline high, one bitline at VDD, the other
             ramped down.  The bitline voltage at which the cell flips is
             the write trip point; the write margin is VDD minus it.

    The cell is symmetric, so one half-cell curve determines both lobes
    of the butterfly and the noise margin is computed from it
    (measure_snm).
    """
    d = ["* bitcell static margin bench: " + kind, _header(corner)]
    if kind in ("hold", "read"):
        wl = "{vdd}" if kind == "read" else "0"
        d += ["* half cell: input vin (the far storage node), output q",
              "Npua q vin vdd vdd pmos_sram nfin=1",
              "Npda q vin 0   0   nmos_sram nfin=2",
              "Naccr bl wl q 0 nmos_sram nfin=2",
              f"Vwl wl 0 {wl}",
              "Vbl bl 0 {vdd}",
              "Vin vin 0 0",
              ".dc Vin 0 {vdd} 0.002",
              ".control", "run", "set filetype=ascii",
              "write OUT.raw v(vin) v(q)", ".endc", ".end"]
    elif kind == "write":
        d += ["Xcell bl bln wl vdd 0 sram6t122",
              "Vwl wl 0 {vdd}",
              "Vbln bln 0 {vdd}",
              "* quasi-static ramp: 20 ns is slow against every cell time",
              "Vbl bl 0 PWL(0 {vdd} 1n {vdd} 21n 0)",
              ".ic v(xcell.q)={vdd} v(xcell.qb)=0",
              ".tran 10p 21n uic",
              ".control", "run", "set filetype=ascii",
              "write OUT.raw v(bl) v(xcell.q) v(xcell.qb)", ".endc", ".end"]
    else:
        raise ValueError(kind)
    return "\n".join(d)


def measure_snm(raw):
    """Static noise margin (mV) from one half-cell transfer curve.

    The butterfly is the curve and its mirror about the diagonal, so in
    coordinates rotated 45 degrees (u along the diagonal, v across it)
    the mirror is v -> -v.  The largest square that fits in a lobe has
    the diagonal |u(v) - u(-v)|, so the noise margin is the largest such
    separation divided by the square root of two.
    """
    import math
    names, pts = read_raw(raw)
    vin = col(names, pts, "v(vin)")
    q = col(names, pts, "v(q)")
    if not vin or not q:
        return None
    r2 = math.sqrt(2.0)
    u = [(a + b) / r2 for a, b in zip(vin, q)]
    v = [(b - a) / r2 for a, b in zip(vin, q)]
    # v decreases monotonically with vin; interpolate u at a given v
    pairs = sorted(zip(v, u))

    def u_at(vq):
        if vq < pairs[0][0] or vq > pairs[-1][0]:
            return None
        for i in range(1, len(pairs)):
            if pairs[i][0] >= vq:
                (v0, u0), (v1, u1) = pairs[i - 1], pairs[i]
                if v1 == v0:
                    return u0
                return u0 + (u1 - u0) * (vq - v0) / (v1 - v0)
        return pairs[-1][1]
    best = 0.0
    step = (pairs[-1][0] - pairs[0][0]) / 400.0
    vq = 0.0
    while vq <= pairs[-1][0]:
        a, b = u_at(vq), u_at(-vq)
        if a is not None and b is not None:
            best = max(best, abs(a - b) / r2)
        vq += step
    return round(best * 1000, 1)


def measure_write_trip(raw):
    """Write trip point (mV): the bitline voltage at which the cell flips,
    and the write margin, VDD minus it.  None if it never flips."""
    names, pts = read_raw(raw)
    bl = col(names, pts, "v(bl)")
    q = col(names, pts, "v(xcell.q)")
    qb = col(names, pts, "v(xcell.qb)")
    if not bl or not q:
        return {"trip_mv": None, "write_margin_mv": None,
                "note": "no bitline or storage trace"}
    for i in range(1, len(q)):
        if q[i] < qb[i] and q[i - 1] >= qb[i - 1]:      # the crossing
            return {"trip_mv": round(bl[i] * 1000, 1),
                    "write_margin_mv": round((VDD - bl[i]) * 1000, 1)}
    return {"trip_mv": None, "write_margin_mv": None,
            "note": "the cell never flipped down to 0 V on the bitline"}


def leakage_bench(corner):
    return "\n".join([
        "* one holding cell, operating point",
        _header(corner),
        "Xcell bl bln 0 vdd 0 sram6t122",
        "Vbl bl 0 {vdd}",
        "Vbln bln 0 {vdd}",
        ".ic v(xcell.q)={vdd} v(xcell.qb)=0",
        ".op",
        ".control", "run", "set filetype=ascii",
        "print abs(i(Vdd)) > OUT.raw",
        ".endc", ".end"])


def run_deck(deck, workdir, tag):
    require_tools()
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / ".spiceinit").write_text(f"osdi {OSDI}\n")
    raw = workdir / f"{tag}.raw"
    deck = deck.replace("OUT.raw", str(raw))
    sp = workdir / f"{tag}.sp"
    sp.write_text(deck)
    subprocess.run([NGSPICE, "-b", str(sp)], cwd=workdir,
                   capture_output=True, text=True, timeout=600)
    return raw


def read_raw(path):
    lines = open(path).read().splitlines()
    nvars = 0
    names = []
    for i, ln in enumerate(lines):
        if ln.startswith("No. Variables"):
            nvars = int(ln.split(":")[1])
        elif ln.startswith("Variables:"):
            names = [lines[i + 1 + j].split()[1] for j in range(nvars)]
        elif ln.startswith("Values:"):
            toks = " ".join(lines[i + 1:]).split()
            pts, k = [], 0
            while k < len(toks):
                k += 1
                pts.append([float(toks[k + j]) for j in range(nvars)])
                k += nvars
            return names, pts
    return names, []


def col(names, pts, name):
    j = names.index(name)
    return [p[j] for p in pts]


def first_time(ts, vs, cond, after=0.0):
    for t, v in zip(ts, vs):
        if t > after and cond(v):
            return t
    return None


def downsample(ts, sigs, keep=3e-3):
    idx = [0]
    for i in range(1, len(ts)):
        if any(abs(s[i] - s[idx[-1]]) > keep for s in sigs):
            idx.append(i)
    if idx[-1] != len(ts) - 1:
        idx.append(len(ts) - 1)
    return idx


def measure_column(raw, write=False):
    names, pts = read_raw(raw)
    ts = col(names, pts, "time")
    wl = col(names, pts, "v(wl)")
    bl = col(names, pts, "v(bl)")
    bln = col(names, pts, "v(bln)")
    q = col(names, pts, "v(xcell0.q)")
    qb = col(names, pts, "v(xcell0.qb)")
    wl50 = first_time(ts, wl, lambda v: v >= VDD / 2, 1.9e-9)
    m = {"wl50_ns": round(wl50 * 1e9, 4)}
    if write:
        cross = None
        for t, a, b in zip(ts, q, qb):
            if t > 2e-9 and a >= b:
                cross = t
                break
        q90 = first_time(ts, q, lambda v: v >= 0.9 * VDD, 2e-9)
        m["flip_ps"] = round((cross - wl50) * 1e12) if cross else None
        m["q90_ps"] = round((q90 - wl50) * 1e12) if q90 else None
        m["flipped"] = cross is not None
    else:
        diff = [a - b for a, b in zip(bl, bln)]
        d50 = first_time(ts, diff, lambda v: v >= 0.05, 2e-9)
        d100 = first_time(ts, diff, lambda v: v >= 0.10, 2e-9)
        m["d50_ps"] = round((d50 - wl50) * 1e12) if d50 else None
        m["d100_ps"] = round((d100 - wl50) * 1e12) if d100 else None
        m["disturb_mv"] = round(max(v for t, v in zip(ts, qb)
                                    if 2e-9 < t < 4e-9) * 1000, 1)
    # waves for the dashboard (mV / ns, downsampled)
    idx = downsample(ts, [wl, bl, bln, q, qb])
    m["waves"] = {
        "t": [round(ts[i] * 1e9, 4) for i in idx],
        "WL": [round(wl[i] * 1000, 1) for i in idx],
        "BL": [round(bl[i] * 1000, 1) for i in idx],
        "BLN": [round(bln[i] * 1000, 1) for i in idx],
        "Q": [round(q[i] * 1000, 1) for i in idx],
        "QB": [round(qb[i] * 1000, 1) for i in idx],
    }
    return m


def measure_wordline(raw):
    names, pts = read_raw(raw)
    ts = col(names, pts, "time")
    far = col(names, pts, f"v(wl{WL_SEGS})")
    t0 = 1.0e-9  # input edge
    t50 = first_time(ts, far, lambda v: v >= VDD / 2, t0)
    t10 = first_time(ts, far, lambda v: v >= 0.1 * VDD, t0)
    t90 = first_time(ts, far, lambda v: v >= 0.9 * VDD, t0)
    idx = downsample(ts, [far])
    return {
        "wl_t50_ps": round((t50 - t0) * 1e12) if t50 else None,
        "wl_slew_ps": round((t90 - t10) * 1e12) if t90 and t10 else None,
        "waves": {"t": [round(ts[i] * 1e9, 4) for i in idx],
                  "WLFAR": [round(far[i] * 1000, 1) for i in idx]},
    }


def measure_leakage(raw):
    txt = open(raw).read()
    m = re.search(r"([0-9.]+e[-+][0-9]+)", txt)
    return {"cell_leak_a": float(m.group(1))} if m else {}


def sweep(bits_list=(16, 32, 64), depth_list=(8, 16, 32, 40, 64),
          corners=("TT", "FF", "SS"), strips=4, out_json=None):
    work = REPO / "out" / "timing"
    grid = {"columns": {}, "wordlines": {}, "leakage": {},
            "params": {"strips": strips, "vdd": VDD,
                       "bl_c_per_um_ff": BL_C_PER_UM * 1e15,
                       "wl_r_per_um": WL_R_PER_UM,
                       "wl_c_per_um_ff": WL_C_PER_UM * 1e15}}
    for corner in corners:
        raw = run_deck(leakage_bench(corner), work, f"leak_{corner}")
        grid["leakage"][corner] = measure_leakage(raw)
        print(f"leak {corner}: {grid['leakage'][corner]}")
        for bits in bits_list:
            key = f"{bits}_{corner}"
            r = run_deck(column_bench(bits, corner), work, f"read_{key}")
            w = run_deck(column_bench(bits, corner, write=True), work,
                         f"write_{key}")
            grid["columns"][key] = {"read": measure_column(r),
                                    "write": measure_column(w, write=True)}
            print(f"column bits={bits} {corner}: "
                  f"d100 {grid['columns'][key]['read']['d100_ps']} ps, "
                  f"flip {grid['columns'][key]['write']['flip_ps']} ps")
        for depth in depth_list:
            key = f"{depth}_{corner}"
            raw = run_deck(wordline_bench(depth, strips, corner), work,
                           f"wl_{key}")
            grid["wordlines"][key] = measure_wordline(raw)
            print(f"wordline depth={depth} {corner}: "
                  f"t50 {grid['wordlines'][key]['wl_t50_ps']} ps, "
                  f"slew {grid['wordlines'][key]['wl_slew_ps']} ps")
    if out_json:
        pathlib.Path(out_json).write_text(json.dumps(grid))
        print(f"grid written to {out_json}")
    return grid


if __name__ == "__main__":
    sweep(out_json=str(REPO / "out" / "timing_grid.json"))
