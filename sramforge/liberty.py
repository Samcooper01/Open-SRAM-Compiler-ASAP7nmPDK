"""Liberty (the Synopsys timing library format) emitter for generated banks.

    emit_liberty(char_json, spec, out_path)

char_json is the characterization record written by scripts/characterize.py
(a path or an already-loaded dict).  Every number in the .lib is copied
from a field of that record; nothing is computed here beyond unit
conversion (ps to ns, fF to pF, A to nW at VDD).  A field that the
characterization recorded as null is OMITTED from the library, with a
comment naming the missing field, so a downstream tool sees an absent arc
rather than an invented one.

Pin names, bus notation and units follow docs/pin_interface.md exactly:
`bus_naming_style : "%s[%d]"`, ADDRESS[a-1:0], wd/dataout[w-1:0],
sdel[4:0], clk, banksel, read, byte_wrenab, pg_pins VDD and VSS; time in
ns, capacitance in pF, voltage in V, current in mA, leakage power in nW;
nominal 0.7 V and 25 C; corner name <corner>_0p7V_25C.

Command line (emit only, no simulation):

    python3 -m sramforge.liberty --config 64,4,64 [--corner TT]
                                 [--json out/char/<name>_<corner>.json]
                                 [--out  out/char/<name>_<corner>.lib]
"""
import argparse
import json
import pathlib
import sys

from .generator import BankSpec
from . import pins as contract

VDD = 0.7


# ---------------------------------------------------------------------------
# small formatting helpers
def _ns(ps):
    return f"{ps / 1000.0:.4f}"


def _pf(ff):
    return f"{ff / 1000.0:.6f}"


def _q(s):
    return '"' + s + '"'


class _W:
    """Indented writer."""

    def __init__(self):
        self.lines = []
        self.depth = 0

    def line(self, s=""):
        self.lines.append("  " * self.depth + s if s else "")

    def comment(self, s):
        self.line(f"/* {s} */")

    def open(self, s):
        self.line(s + " {")
        self.depth += 1

    def close(self):
        self.depth -= 1
        self.line("}")

    def text(self):
        return "\n".join(self.lines) + "\n"


# ---------------------------------------------------------------------------
def _pin_list(spec):
    """The contract's signal pin census for this spec, from
    sramforge/pins.py (the same rule the LEF (Library Exchange Format)
    and Verilog exporters use): name -> (direction, width)."""
    out = {}
    for fam in contract.FAMILIES:
        if fam.name in contract.SUPPLY_NAMES:
            continue          # VDD/VSS are pg_pins, written separately
        n = contract.expected_census(spec)[fam.name]
        out[fam.name] = (fam.direction.lower(), 1 if n is None else n)
    return out


def _cap_of(rec, name):
    """Pin capacitance (fF) from the record, or None."""
    pc = rec.get("pin_capacitance_ff") or {}
    e = pc.get(name)
    return None if e is None else e.get("total_ff")


def _setup_hold(rec, key):
    """(setup_ps, hold_ps, notes) for a record setup/hold key, None if absent."""
    s = (rec.get("setup_ps") or {}).get(key)
    h = (rec.get("hold_ps") or {}).get(key)
    sv = None if not s else s.get("setup_ps")
    hv = None if not h else h.get("hold_ps")
    notes = []
    for tag, e in (("setup", s), ("hold", h)):
        if e and e.get("note"):
            notes.append(f"{tag}: {e['note']}")
    return sv, hv, notes


def _address_setup_hold(rec):
    """ADDRESS[*] setup/hold = the worst over the measured address bits
    (the record keys start with 'ADDRESS<')."""
    out = {}
    for kind in ("setup_ps", "hold_ps"):
        vals, notes = [], []
        for k, e in (rec.get(kind) or {}).items():
            if not k.startswith("ADDRESS<") or not e:
                continue
            v = e.get(kind)
            if v is None:
                notes.append(f"{k}: {e.get('note', 'null')}")
            else:
                vals.append((v, k))
        # the worst constraint is the LARGEST setup and the LARGEST hold
        out[kind] = (max(v for v, _ in vals) if vals else None, notes,
                     [k for _, k in vals])
    return out


def _write_constraint(w, related, setup_ps, hold_ps, notes, what):
    """setup_rising / hold_rising groups (scalar tables), omitting nulls."""
    for note in notes:
        w.comment(f"{what} {note}")
    if setup_ps is None:
        w.comment(f"{what}: setup omitted (characterization field is null)")
    else:
        w.open("timing()")
        w.line(f"related_pin : {_q(related)};")
        w.line("timing_type : setup_rising;")
        w.open("rise_constraint(scalar)")
        w.line(f"values({_q(_ns(setup_ps))});")
        w.close()
        w.open("fall_constraint(scalar)")
        w.line(f"values({_q(_ns(setup_ps))});")
        w.close()
        w.close()
    if hold_ps is None:
        w.comment(f"{what}: hold omitted (characterization field is null)")
    else:
        w.open("timing()")
        w.line(f"related_pin : {_q(related)};")
        w.line("timing_type : hold_rising;")
        w.open("rise_constraint(scalar)")
        w.line(f"values({_q(_ns(hold_ps))});")
        w.close()
        w.open("fall_constraint(scalar)")
        w.line(f"values({_q(_ns(hold_ps))});")
        w.close()
        w.close()


def _table_ok(rows):
    return rows and all(v is not None for r in rows for v in r)


def _write_table(w, name, template, slews, loads, rows):
    w.open(f"{name}({template})")
    w.line("index_1(" + _q(", ".join(_ns(s) for s in slews)) + ");")
    w.line("index_2(" + _q(", ".join(_pf(c) for c in loads)) + ");")
    vals = ", ".join(_q(", ".join(_ns(v) for v in r)) for r in rows)
    w.line(f"values({vals});")
    w.close()


# ---------------------------------------------------------------------------
def emit_liberty(char_json, spec: BankSpec, out_path):
    rec = (json.loads(pathlib.Path(char_json).read_text())
           if not isinstance(char_json, dict) else char_json)
    cfg = rec.get("config", {})
    corner = cfg.get("corner", "TT")
    corner_name = cfg.get("corner_name", f"{corner}_0p7V_25C")
    if cfg.get("name") and cfg["name"] != spec.name:
        raise ValueError(f"characterization is for {cfg['name']}, "
                         f"spec is {spec.name}")
    pins = _pin_list(spec)
    if cfg.get("n_address") not in (None, pins["ADDRESS"][1]):
        raise ValueError(f"record says {cfg['n_address']} address bits, "
                         f"the pin contract says {pins['ADDRESS'][1]}")
    ra = rec.get("read_access") or {}
    slews = ra.get("input_slew_ps") or []
    loads = ra.get("load_ff") or []
    lib_name = f"{spec.name}_{corner_name}"

    w = _W()
    w.comment(f"Liberty (the Synopsys timing library format) view of "
              f"{spec.name}, generated by sramforge/liberty.py")
    w.comment("Every value below is copied from the characterization record "
              f"{cfg.get('name', '?')}_{corner}.json written by "
              "scripts/characterize.py; see docs/liberty_characterization.md "
              "for how each arc was measured and which numbers are estimates")
    w.comment("Pin names and units follow docs/pin_interface.md")
    w.open(f"library({lib_name})")
    w.line("technology (cmos);")
    w.line("delay_model : table_lookup;")
    w.line("date : " + _q(rec.get("provenance", {}).get("date", "unknown")) + ";")
    w.line("comment : " + _q("sramforge open-source SRAM (Static Random-Access "
                             "Memory) bank generator for ASAP7 (Arizona State "
                             "University 7 nm predictive process design kit); "
                             "characterized with ngspice on the extracted "
                             "layout") + ";")
    w.line("revision : 1.0;")
    w.line('bus_naming_style : "%s[%d]";')
    w.line('time_unit : "1ns";')
    w.line('voltage_unit : "1V";')
    w.line('current_unit : "1mA";')
    w.line('leakage_power_unit : "1nW";')
    w.line('pulling_resistance_unit : "1kohm";')
    w.line("capacitive_load_unit (1, pf);")
    w.line(f"nom_process : 1.0;")
    w.line(f"nom_voltage : {cfg.get('vdd_v', VDD)};")
    w.line(f"nom_temperature : {cfg.get('temp_c', 25)};")
    w.line(f"voltage_map(VDD, {cfg.get('vdd_v', VDD)});")
    w.line("voltage_map(VSS, 0.0);")
    w.open(f"operating_conditions({corner_name})")
    w.line("process : 1.0;")
    w.line(f"voltage : {cfg.get('vdd_v', VDD)};")
    w.line(f"temperature : {cfg.get('temp_c', 25)};")
    w.line("tree_type : balanced_tree;")
    w.close()
    w.line(f"default_operating_conditions : {corner_name};")
    w.line("slew_lower_threshold_pct_rise : 10.0;")
    w.line("slew_upper_threshold_pct_rise : 90.0;")
    w.line("slew_lower_threshold_pct_fall : 10.0;")
    w.line("slew_upper_threshold_pct_fall : 90.0;")
    w.line("input_threshold_pct_rise : 50.0;")
    w.line("input_threshold_pct_fall : 50.0;")
    w.line("output_threshold_pct_rise : 50.0;")
    w.line("output_threshold_pct_fall : 50.0;")
    w.line("slew_derate_from_library : 1.0;")
    w.line("default_max_transition : 0.2;")
    w.line("default_input_pin_cap : 0.0005;")
    w.line("default_output_pin_cap : 0.0;")
    w.line("default_leakage_power_density : 0.0;")
    w.line("default_cell_leakage_power : 0.0;")
    if slews and loads:
        w.open(f"lu_table_template(delay_{len(slews)}x{len(loads)})")
        w.line("variable_1 : input_net_transition;")
        w.line("variable_2 : total_output_net_capacitance;")
        w.line("index_1(" + _q(", ".join(_ns(s) for s in slews)) + ");")
        w.line("index_2(" + _q(", ".join(_pf(c) for c in loads)) + ");")
        w.close()
    # bus types
    for name in ("ADDRESS", "wd", "dataout", "sdel"):
        _, width = pins[name]
        w.open(f"type({name}_bus_{width})")
        w.line("base_type : array;")
        w.line("data_type : bit;")
        w.line(f"bit_width : {width};")
        w.line(f"bit_from : {width - 1};")
        w.line("bit_to : 0;")
        w.line("downto : true;")
        w.close()

    # ---- the cell ---------------------------------------------------------
    w.open(f"cell({spec.name})")
    w.line("is_macro_cell : true;")
    w.line("dont_use : false;")
    w.line("dont_touch : true;")
    area = cfg.get("area_um2")
    if area is None:
        w.comment("area omitted: config.area_um2 is null")
    else:
        w.line(f"area : {area};")
    leak = (rec.get("leakage") or {}).get("bank_total_nw")
    if leak is None:
        w.comment("cell_leakage_power omitted: leakage.bank_total_nw is null")
    else:
        w.comment("cell_leakage_power = leakage.bank_total_nw: the band and "
                  "one column operating points extrapolated over all "
                  "columns plus cell leakage x cell count (an extrapolation, "
                  "see the record's leakage.method)")
        w.line(f"cell_leakage_power : {leak:.4f};")
    w.open("memory()")
    w.line("type : ram;")
    w.line(f"address_width : {pins['ADDRESS'][1]};")
    w.line(f"word_width : {pins['wd'][1]};")
    w.close()
    w.open("pg_pin(VDD)")
    w.line("pg_type : primary_power;")
    w.line("voltage_name : VDD;")
    w.close()
    w.open("pg_pin(VSS)")
    w.line("pg_type : primary_ground;")
    w.line("voltage_name : VSS;")
    w.close()

    def cap_line(name):
        c = _cap_of(rec, name)
        if c is None:
            w.comment(f"capacitance omitted for {name}: no "
                      f"pin_capacitance_ff entry")
        else:
            w.line(f"capacitance : {_pf(c)};")

    # ---- clk ---------------------------------------------------------------
    w.open("pin(clk)")
    w.line("direction : input;")
    w.line("clock : true;")
    w.line("related_power_pin : VDD;")
    w.line("related_ground_pin : VSS;")
    cap_line("clk")
    mp = rec.get("min_period") or {}
    if mp.get("min_period_ps") is None:
        w.comment("minimum_period omitted: min_period.min_period_ps is null"
                  + (f" ({mp['note']})" if mp.get("note") else ""))
    else:
        w.comment("minimum_period = min_period.min_period_ps: verified "
                  f"period {mp.get('verified_period_ps')} ps plus 20% margin; "
                  "definition in the record's min_period.definition")
        w.line(f"minimum_period : {_ns(mp['min_period_ps'])};")
        if mp.get("t_high_min_ps") is not None:
            w.comment("min_pulse_width_high = min_period.t_high_min_ps "
                      "(clk rise to dataout valid, the read completes "
                      "while clk is high)")
            w.line(f"min_pulse_width_high : {_ns(mp['t_high_min_ps'])};")
        if mp.get("t_low_min_ps") is not None:
            w.comment("min_pulse_width_low = min_period.t_low_min_ps "
                      "(clk fall to bitlines restored within 10% of VDD)")
            w.line(f"min_pulse_width_low : {_ns(mp['t_low_min_ps'])};")
    w.close()

    # ---- single-bit control inputs ------------------------------------------
    for name in ("banksel", "read", "byte_wrenab"):
        w.open(f"pin({name})")
        w.line("direction : input;")
        w.line("related_power_pin : VDD;")
        w.line("related_ground_pin : VSS;")
        cap_line(name)
        s, h, notes = _setup_hold(rec, name)
        _write_constraint(w, "clk", s, h, notes, name)
        w.close()

    # ---- ADDRESS bus ---------------------------------------------------------
    def bus_open(name, direction):
        w.open(f"bus({name})")
        w.line(f"bus_type : {name}_bus_{pins[name][1]};")
        w.line(f"direction : {direction};")
        w.line("related_power_pin : VDD;")
        w.line("related_ground_pin : VSS;")

    def bit_caps(name, width):
        caps = [_cap_of(rec, f"{name}[{k}]") for k in range(width)]
        known = [c for c in caps if c is not None]
        if known:
            w.comment(f"bus capacitance = the largest per-bit estimate; "
                      f"per-bit values follow")
            w.line(f"capacitance : {_pf(max(known))};")
        else:
            w.comment(f"capacitance omitted for {name}: no per-bit entries")
        return caps

    def bit_pins(name, caps):
        for k, c in enumerate(caps):
            if c is not None:
                w.open(f"pin({name}[{k}])")
                w.line("direction : input;")
                w.line(f"capacitance : {_pf(c)};")
                w.close()

    bus_open("ADDRESS", "input")
    caps = bit_caps("ADDRESS", pins["ADDRESS"][1])
    ash = _address_setup_hold(rec)
    (sv, snotes, sbits), (hv, hnotes, hbits) = ash["setup_ps"], ash["hold_ps"]
    if sbits:
        w.comment("ADDRESS setup/hold = the worst of the measured bits "
                  + ", ".join(sorted(set(sbits + hbits))))
    _write_constraint(w, "clk", sv, hv, snotes + hnotes, "ADDRESS")
    bit_pins("ADDRESS", caps)
    w.close()

    # ---- sdel bus -------------------------------------------------------------
    bus_open("sdel", "input")
    caps = bit_caps("sdel", 5)
    w.comment("sdel are the sense-timing delay-line taps: tie to a constant "
              "(all 1 in every proof); no timing arc is characterized and "
              "none is emitted")
    bit_pins("sdel", caps)
    w.close()

    # ---- wd bus -----------------------------------------------------------------
    bus_open("wd", "input")
    caps = bit_caps("wd", pins["wd"][1])
    s, h, notes = _setup_hold(rec, "wd")
    w.comment("wd setup/hold measured on wd[0] (the characterized column) "
              "and applied to every bit")
    _write_constraint(w, "clk", s, h, notes, "wd")
    bit_pins("wd", caps)
    w.close()

    # ---- dataout bus ------------------------------------------------------------
    bus_open("dataout", "output")
    if loads:
        w.comment("max_capacitance = the largest characterized load "
                  "(read_access.load_ff)")
        w.line(f"max_capacitance : {_pf(max(loads))};")
    tables = {k: ra.get(k) for k in ("cell_rise_ps", "cell_fall_ps",
                                     "rise_transition_ps", "fall_transition_ps")}
    if slews and loads and all(_table_ok(t) for t in tables.values()):
        w.comment("clk -> dataout: measured on dataout[0] (the characterized "
                  "column), the cycle-2 edge of a two-read simulation; rows "
                  "= clk input slew, columns = dataout load "
                  "(read_access.cell_*_ps and *_transition_ps)")
        w.open("timing()")
        w.line('related_pin : "clk";')
        w.line("timing_type : rising_edge;")
        w.line("timing_sense : non_unate;")
        tmpl = f"delay_{len(slews)}x{len(loads)}"
        _write_table(w, "cell_rise", tmpl, slews, loads, tables["cell_rise_ps"])
        _write_table(w, "cell_fall", tmpl, slews, loads, tables["cell_fall_ps"])
        _write_table(w, "rise_transition", tmpl, slews, loads,
                     tables["rise_transition_ps"])
        _write_table(w, "fall_transition", tmpl, slews, loads,
                     tables["fall_transition_ps"])
        w.close()
    else:
        missing = [k for k, t in tables.items() if not _table_ok(t)]
        w.comment("clk -> dataout timing omitted: read_access tables missing "
                  "or containing null (" + ", ".join(missing or ["no table"])
                  + ")")
    w.close()
    w.close()   # cell
    w.close()   # library
    pathlib.Path(out_path).write_text(w.text())
    return out_path


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="emit a .lib from a "
                                 "characterization record (no simulation)")
    ap.add_argument("--config", default="32,4,32", help="bits,strips,depth")
    ap.add_argument("--corner", default="TT")
    ap.add_argument("--json", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    bits, strips, depth = map(int, a.config.split(","))
    spec = BankSpec(bits_per_group=bits, strips_per_group=strips,
                    depth_groups=depth)
    repo = pathlib.Path(__file__).resolve().parent.parent
    js = a.json or str(repo / "out" / "char" / f"{spec.name}_{a.corner}.json")
    out = a.out or str(repo / "out" / "char" / f"{spec.name}_{a.corner}.lib")
    emit_liberty(js, spec, out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
