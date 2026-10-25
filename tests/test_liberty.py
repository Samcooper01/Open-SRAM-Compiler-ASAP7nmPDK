#!/usr/bin/env python3
"""Rung 7: the Liberty (the Synopsys timing library format) view.

Emits a .lib from EVERY checked-in characterization record of the 32,4,32
bank (out/char/sramforge_32bx4x64_<corner>.json for each corner measured
with ngspice by scripts/characterize.py: TT (typical-typical corner), FF
(fast-fast corner), SS (slow-slow corner)) and of the 64,4,64 bank, so the
test runs in seconds without a simulator, then checks, per record:

  * the file parses: with the liberty-parser package
    (https://pypi.org/project/liberty-parser/) when it is installed, and
    always with the strict structural parser below (balanced groups, every
    statement terminated, no stray text);
  * the pin census equals the contract (docs/pin_interface.md, in code as
    sramforge/pins.py, shared with the LEF (Library Exchange Format) and
    Verilog exporters) for the spec: ADDRESS[a-1:0] (a = 8 at the
    reference 32, 9 at every other width), clk, banksel, read,
    byte_wrenab, sdel[4:0], wd[w-1:0], dataout[w-1:0], pg_pins VDD and
    VSS, bus_naming_style "%s[%d]", the contract's units and corner name;
  * every clk -> dataout table (cell_rise, cell_fall, rise_transition,
    fall_transition) is monotonic non-decreasing in load;
  * every emitted number traces to a field of the record (spot-checked:
    area, capacitance, minimum_period, the delay tables);
  * the emit-only path for 64,4,64 works from its record (no simulation);
    arcs the record does not have are omitted with a comment, never
    invented;
  * the operating_conditions group carries the record's own corner name
    (<corner>_0p7V_25C) and the file name's corner equals the record's;
  * no em dash or en dash anywhere in the output.

Cross-corner sanity (reported, never a failure): for every timing quantity
present and non-null in all three 32,4,32 corner records, the delay must
not shrink from FF (fast-fast corner) through TT (typical-typical corner)
to SS (slow-slow corner), FF <= TT <= SS; every quantity that violates the
ordering is printed with its three values.  Setup and hold constraints are
included in the report but tagged, because their bisection is quantized
(12.5 ps and 10 ps steps) and a hold requirement has no physical reason to
order with process speed.  Leakage is printed with the opposite expectation
(FF >= TT >= SS).

Usage:  python3 tests/test_liberty.py
"""
import json
import pathlib
import re
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import BankSpec                     # noqa: E402
from sramforge import pins as contract             # noqa: E402
from sramforge.liberty import emit_liberty         # noqa: E402

CHAR_DIR = REPO / "out" / "char"
CONTRACT = REPO / "docs" / "pin_interface.md"
CORNERS = ("FF", "TT", "SS")      # fast-fast, typical-typical, slow-slow


# ---------------------------------------------------------------------------
# strict structural parser: Liberty is groups `name(args) { statements }`,
# simple attributes `name : value ;` and complex attributes `name(args) ;`
class Group:
    def __init__(self, kind, args):
        self.kind, self.args = kind, args
        self.attrs = {}        # simple attributes (last wins)
        self.complex = []      # (name, args) complex attributes
        self.groups = []

    def find(self, kind, arg=None):
        return [g for g in self.groups
                if g.kind == kind and (arg is None or g.args == arg)]


def parse_lib(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"\\\n", " ", text)
    pos = 0
    n = len(text)
    tok_group = re.compile(r"\s*([A-Za-z_][\w]*)\s*\(([^)]*)\)\s*\{")
    tok_cattr = re.compile(
        r"\s*([A-Za-z_][\w]*)\s*\(((?:\"[^\"]*\"|[^()\"]|\([^()]*\))*)\)\s*;")
    tok_sattr = re.compile(
        r"\s*([A-Za-z_][\w]*)\s*:\s*((?:\"[^\"]*\"|[^;\"])*?)\s*;")
    tok_close = re.compile(r"\s*\}")
    root = Group("ROOT", "")
    stack = [root]
    while pos < n:
        if text[pos:].strip() == "":
            break
        m = tok_group.match(text, pos)
        if m:
            g = Group(m.group(1), m.group(2).strip())
            stack[-1].groups.append(g)
            stack.append(g)
            pos = m.end()
            continue
        m = tok_cattr.match(text, pos)
        if m:
            stack[-1].complex.append((m.group(1), m.group(2).strip()))
            pos = m.end()
            continue
        m = tok_sattr.match(text, pos)
        if m:
            stack[-1].attrs[m.group(1)] = m.group(2).strip()
            pos = m.end()
            continue
        m = tok_close.match(text, pos)
        if m:
            if len(stack) == 1:
                raise SyntaxError(f"unbalanced '}}' at {pos}")
            stack.pop()
            pos = m.end()
            continue
        raise SyntaxError(f"cannot parse near: {text[pos:pos + 60]!r}")
    if len(stack) != 1:
        raise SyntaxError(f"{len(stack) - 1} unclosed group(s)")
    return root


def values_of(table):
    """Rows of floats from a table group's values("...", "...")."""
    for name, args in table.complex:
        if name == "values":
            rows = re.findall(r'"([^"]*)"', args)
            return [[float(x) for x in r.split(",")] for r in rows]
    return []


def unq(s):
    return s.strip().strip('"')


# ---------------------------------------------------------------------------
def contract_pins(spec):
    """Expected census: sramforge/pins.py (the contract in code, shared
    with the LEF (Library Exchange Format) and Verilog exporters), cross-
    checked against the names in docs/pin_interface.md section 3."""
    text = CONTRACT.read_text()
    rows = [ln for ln in text.splitlines() if ln.startswith("| `")]
    names = [re.match(r"\| `([^`\[]+)", ln).group(1) for ln in rows]
    expected = {name: (1 if n is None else n)
                for name, n in contract.expected_census(spec).items()}
    assert set(names) == set(expected), \
        f"contract table names {names} vs pins.py census {sorted(expected)}"
    return expected


def check_lib(path, spec, expect_tables, corner_name="TT_0p7V_25C"):
    text = pathlib.Path(path).read_text()
    fails = []
    for bad, nm in (("\u2014", "em dash"), ("\u2013", "en dash")):
        if bad in text:
            fails.append(f"{nm} present in the library")
    root = parse_lib(text)
    libs = root.find("library")
    if len(libs) != 1:
        return [f"expected one library group, found {len(libs)}"]
    lib = libs[0]
    if unq(lib.attrs.get("bus_naming_style", "")) != "%s[%d]":
        fails.append("bus_naming_style is not %s[%d]")
    for key, want in (("time_unit", "1ns"), ("voltage_unit", "1V"),
                      ("current_unit", "1mA"), ("leakage_power_unit", "1nW")):
        if unq(lib.attrs.get(key, "")) != want:
            fails.append(f"{key} = {lib.attrs.get(key)} (want {want})")
    if not any(n == "capacitive_load_unit" and a.replace(" ", "") == "1,pf"
               for n, a in lib.complex):
        fails.append("capacitive_load_unit (1, pf) missing")
    if lib.attrs.get("nom_voltage") != "0.7" or lib.attrs.get("nom_temperature") != "25":
        fails.append("nominal conditions are not 0.7 V / 25 C")
    if not lib.find("operating_conditions", corner_name):
        fails.append(f"operating_conditions({corner_name}) missing")
    if lib.attrs.get("default_operating_conditions") != corner_name:
        fails.append(f"default_operating_conditions is "
                     f"{lib.attrs.get('default_operating_conditions')}, "
                     f"want {corner_name}")
    cells = lib.find("cell")
    if len(cells) != 1 or cells[0].args != spec.name:
        fails.append(f"cell census {[c.args for c in cells]} != [{spec.name}]")
        return fails
    cell = cells[0]
    # ---- pin census ------------------------------------------------------
    expected = contract_pins(spec)
    census = {}
    for pg in cell.find("pg_pin"):
        census[pg.args] = 1
    for p in cell.find("pin"):
        census[p.args] = 1
    for b in cell.find("bus"):
        bt = b.attrs.get("bus_type", "")
        types = lib.find("type", bt)
        if len(types) != 1:
            fails.append(f"bus {b.args}: type {bt} not defined exactly once")
            continue
        width = int(types[0].attrs["bit_width"])
        census[b.args] = width
        want_dir = "output" if b.args == "dataout" else "input"
        if b.attrs.get("direction") != want_dir:
            fails.append(f"bus {b.args} direction {b.attrs.get('direction')}")
        # per-bit pins inside the bus must be within range and bracketed
        for p in b.find("pin"):
            m = re.fullmatch(rf"{b.args}\[(\d+)\]", p.args)
            if not m or int(m.group(1)) >= width:
                fails.append(f"bus {b.args}: stray bit pin {p.args}")
    if census != expected:
        fails.append(f"pin census {census} != contract {expected}")
    clk = cell.find("pin", "clk")
    if not clk or clk[0].attrs.get("clock") != "true":
        fails.append("clk is not marked clock : true")
    for name in ("VDD", "VSS"):
        pg = cell.find("pg_pin", name)
        if not pg or pg[0].attrs.get("voltage_name") != name:
            fails.append(f"pg_pin {name} missing or wrong voltage_name")
    # ---- timing tables monotonic in load -----------------------------------
    dout = cell.find("bus", "dataout")
    timings = dout[0].find("timing") if dout else []
    arcs = [t for t in timings if t.attrs.get("timing_type") == "rising_edge"
            and unq(t.attrs.get("related_pin", "")) == "clk"]
    if expect_tables:
        if len(arcs) != 1:
            fails.append(f"dataout: expected one rising_edge arc from clk, "
                         f"found {len(arcs)}")
        else:
            arc = arcs[0]
            for tname in ("cell_rise", "cell_fall", "rise_transition",
                          "fall_transition"):
                tg = arc.find(tname)
                if len(tg) != 1:
                    fails.append(f"dataout arc: {tname} missing")
                    continue
                rows = values_of(tg[0])
                if not rows:
                    fails.append(f"{tname}: no values")
                for r in rows:
                    if any(b < a for a, b in zip(r, r[1:])):
                        fails.append(f"{tname} not monotonic in load: {r}")
                    if any(v <= 0 for v in r):
                        fails.append(f"{tname} has a non-positive entry: {r}")
    else:
        if arcs:
            fails.append("dataout arc emitted although the record has no table")
        if "omitted" not in text:
            fails.append("omitted arcs must be announced by a comment")
    return fails


def check_traceability(path, rec):
    """Spot-check that emitted numbers equal record fields (unit-converted)."""
    text = pathlib.Path(path).read_text()
    fails = []
    area = rec["config"].get("area_um2")
    if area is not None and f"area : {area};" not in text:
        fails.append("area not traceable to config.area_um2")
    mp = (rec.get("min_period") or {}).get("min_period_ps")
    if mp is not None and f"minimum_period : {mp / 1000:.4f};" not in text:
        fails.append("minimum_period not traceable to min_period.min_period_ps")
    ra = rec.get("read_access") or {}
    for key in ("cell_rise_ps", "cell_fall_ps"):
        rows = ra.get(key)
        if rows and all(v is not None for r in rows for v in r):
            want = ", ".join('"' + ", ".join(f"{v / 1000:.4f}" for v in r) + '"'
                             for r in rows)
            if f"values({want});" not in text:
                fails.append(f"{key} table not traceable to the record")
    cap = ((rec.get("pin_capacitance_ff") or {}).get("clk") or {}).get("total_ff")
    if cap is not None and f"capacitance : {cap / 1000:.6f};" not in text:
        fails.append("clk capacitance not traceable to pin_capacitance_ff.clk")
    # ADDRESS setup/hold = the worst (largest) over the measured bits
    for kind, ttype in (("setup_ps", "setup_rising"), ("hold_ps", "hold_rising")):
        vals = [e[kind] for k, e in (rec.get(kind) or {}).items()
                if k.startswith("ADDRESS<") and e and e.get(kind) is not None]
        if not vals:
            continue
        m = re.search(r"bus\(ADDRESS\).*?\n(.*?)\n\s*pin\(ADDRESS\[0\]\)", text, re.S)
        block = m.group(1) if m else ""
        want = f"values(\"{max(vals) / 1000:.4f}\")"
        if ttype not in block or want not in block:
            fails.append(f"ADDRESS {ttype} is not the worst measured bit "
                         f"({max(vals)} ps)")
    leak = (rec.get("leakage") or {}).get("bank_total_nw")
    if leak is not None and f"cell_leakage_power : {leak:.4f};" not in text:
        fails.append("cell_leakage_power not traceable to leakage.bank_total_nw")
    return fails


def timing_arcs(rec):
    """Flatten a record into {quantity name: ps} for the cross-corner
    ordering check.  Delays (stage A edges, the clk -> dataout tables, the
    minimum period) are expected to grow with a slower corner; setup and
    hold constraints are listed under a 'constraint' prefix so the report
    can tag them.  A null stays None and the caller skips it."""
    out = {}
    sa = rec.get("stage_a") or {}
    for slew, d in sa.items():
        if not isinstance(d, dict):
            continue
        for key in ("clk_to_wl_ps", "clk_to_sae_ps", "clk_to_prech_release_ps",
                    "clk_to_wlena_ps", "wl_retire_after_clk_fall_ps",
                    "sae_retire_after_clk_fall_ps", "wl_slew_ps"):
            v = d.get(key)
            if isinstance(v, list):
                for n, x in enumerate(v):
                    out[f"stage_a.{key} ({slew} ps slew, cycle {n + 1})"] = x
            else:
                out[f"stage_a.{key} ({slew} ps slew)"] = v
        out[f"stage_a.ysel_switch_after_address_ps ({slew} ps slew)"] = \
            d.get("ysel_switch_after_address_ps")
    out["stage_a.clk_to_wrena_ps"] = sa.get("clk_to_wrena_ps")
    ra = rec.get("read_access") or {}
    slews, loads = ra.get("input_slew_ps") or [], ra.get("load_ff") or []
    for key in ("cell_rise_ps", "cell_fall_ps", "rise_transition_ps",
                "fall_transition_ps"):
        for i, row in enumerate(ra.get(key) or []):
            for j, v in enumerate(row or []):
                s = slews[i] if i < len(slews) else f"row {i}"
                c = loads[j] if j < len(loads) else f"col {j}"
                out[f"read_access.{key} ({s} ps slew, {c} fF)"] = v
    mp = rec.get("min_period") or {}
    for key in ("t_high_min_ps", "t_low_min_ps", "verified_period_ps",
                "min_period_ps"):
        out[f"min_period.{key}"] = mp.get(key)
    for kind in ("setup_ps", "hold_ps"):
        for k, e in (rec.get(kind) or {}).items():
            out[f"constraint {kind}[{k}]"] = None if not e else e.get(kind)
    return out


def cross_corner_report(recs):
    """Print every quantity present in all three corners whose values do
    not satisfy FF <= TT <= SS (fast-fast, typical-typical, slow-slow), and
    the leakage totals with the opposite expectation.  Returns the number
    of violating DELAY quantities; the caller reports it and never fails on
    it (the task's rule: report, do not fail)."""
    missing = [c for c in CORNERS if c not in recs]
    if missing:
        print(f"    cross-corner check skipped: no record for {missing}")
        return 0
    arcs = {c: timing_arcs(recs[c]) for c in CORNERS}
    names = [n for n in arcs["TT"]
             if all(arcs[c].get(n) is not None for c in CORNERS)]
    delay_viol, constraint_viol = [], []
    for n in names:
        ff, tt, ss = (arcs[c][n] for c in CORNERS)
        if ff <= tt <= ss:
            continue
        (constraint_viol if n.startswith("constraint ")
         else delay_viol).append((n, ff, tt, ss))
    n_delay = sum(1 for n in names if not n.startswith("constraint "))
    n_con = len(names) - n_delay
    print(f"    cross-corner: {n_delay} delay quantities and {n_con} "
          f"constraints present in all of {', '.join(CORNERS)}; "
          f"{len(delay_viol)} delay and {len(constraint_viol)} constraint "
          f"violations of FF <= TT <= SS (reported, not failed)")
    for n, ff, tt, ss in delay_viol + constraint_viol:
        print(f"      ! {n}: FF {ff} / TT {tt} / SS {ss} ps")
    leak = {c: (recs[c].get("leakage") or {}).get("bank_total_nw")
            for c in CORNERS}
    if all(v is not None for v in leak.values()):
        ok = leak["FF"] >= leak["TT"] >= leak["SS"]
        print(f"    leakage bank_total_nw FF {leak['FF']:.1f} / TT "
              f"{leak['TT']:.1f} / SS {leak['SS']:.1f} nW "
              f"({'orders' if ok else 'does NOT order'} FF >= TT >= SS)")
    return len(delay_viol)


def try_liberty_parser(path):
    try:
        from liberty.parser import parse_liberty
    except ImportError:
        return None
    lib = parse_liberty(pathlib.Path(path).read_text())
    cells = lib.get_groups("cell")
    return [c.args[0] for c in cells]


def main():
    fails = 0
    recs32 = {}          # corner -> record of the reference 32,4,32 bank
    with tempfile.TemporaryDirectory() as td:
        # (spec, must the checked-in record carry the read table?)
        for cfg, tables_required in (((32, 4, 32), True), ((64, 4, 64), False)):
            spec = BankSpec(*cfg)
            files = sorted(CHAR_DIR.glob(f"{spec.name}_*.json"))
            if not files:
                print(f"[FAIL] no {spec.name}_<corner>.json in {CHAR_DIR} "
                      f"(checked-in characterization)")
                fails += 1
                continue
            for char in files:
                rec = json.loads(char.read_text())
                cfg_rec = rec.get("config") or {}
                corner = cfg_rec.get("corner", "TT")
                corner_name = cfg_rec.get("corner_name", f"{corner}_0p7V_25C")
                f = []
                if char.stem != f"{spec.name}_{corner}":
                    f.append(f"file {char.name} but config.corner is {corner}")
                if corner not in CORNERS:
                    f.append(f"config.corner {corner} is not one of {CORNERS}")
                ra = rec.get("read_access") or {}
                has_tables = bool(ra.get("cell_rise_ps")) and all(
                    v is not None for row in ra["cell_rise_ps"] for v in row)
                # the TT (typical-typical corner) reference record must carry
                # the read table; another corner may legitimately lack it
                # when its stage A did not converge (recorded null + note)
                if tables_required and corner == "TT" and not has_tables:
                    f.append("the checked-in record has no read_access table")
                out = pathlib.Path(td) / f"{spec.name}_{corner}.lib"
                emit_liberty(str(char), spec, str(out))
                f += check_lib(out, spec, has_tables, corner_name)
                f += check_traceability(out, rec)
                parsed = try_liberty_parser(out)
                if parsed is None:
                    print("    (liberty-parser not installed; structural "
                          "parser only)")
                elif parsed != [spec.name]:
                    f.append(f"liberty-parser saw cells {parsed}")
                for msg in f:
                    print(f"    - {msg}")
                print(f"[{'FAIL' if f else 'PASS'}] {spec.name} {corner_name}: "
                      f"{out.stat().st_size} bytes, census + units + "
                      f"{'monotonic tables' if has_tables else 'omitted arcs announced'}"
                      f"{' + liberty-parser' if parsed else ''}")
                fails += len(f)
                if cfg == (32, 4, 32) and not f:
                    recs32[corner] = rec
    n_viol = cross_corner_report(recs32)
    print(f"[{'WARN' if n_viol else 'PASS'}] cross-corner ordering "
          f"FF <= TT <= SS on 32,4,32: {n_viol} delay violation(s) "
          f"(reported only)")
    if fails:
        print(f"\nFAIL: {fails} rung-7 failures")
        return 1
    print("\nPASS: rung 7, the Liberty view matches the pin contract and the "
          "characterization record")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
