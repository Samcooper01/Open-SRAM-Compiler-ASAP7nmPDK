#!/usr/bin/env python3
"""Patch the extracted control netlist for the dual-decoder (bits>32).

Each decoder copy needs BOTH enable polarities, like the reference:
wlenat_hi/wlenab_hi (active-high AND) feed the copy WLENA rails, and
wlenat_hi_n/wlenab_hi_n (INV complements) feed the copies' PC<1>
verticals whose 16-finger stage strobes each column pair.  The _n pads
sit DEEPER (ctl y -3150) than the _hi pads so their M5 laterals pass
BELOW the rail-feed riser's bottom - same-x pads welded enable to its
own complement on M5 in the first attempt (all-dead bits=64).  The
copies' own PC receivers (inputs lost with the overlap tail) are
FEOL-cut in band.py.

Original docstring: dual-decoder (bits>32)
scheme: ADDRESS[8] takes over the half select, ADDRESS[7]'s latched
signal becomes the decode-bank select, and the wordline enables split
into lo (old ports, original decoders) and hi (new ports, the outer
decoder copies).

Idempotent: skips if already patched.  Run after
scripts/extract_control_netlist.py, before pnr/gen_inputs.py.
"""
import pathlib
import re

V = pathlib.Path(__file__).resolve().parent.parent / "out" / "control_netlist.v"
s = V.read_text()
if "wlenat_hi" in s:
    print("already patched")
    raise SystemExit(0)

# 1. ports
s = s.replace("module control_sram_2kB (address_0,",
              "module control_sram_2kB (address_8, wlenat_hi, wlenab_hi, "
              "wlenat_hi_n, wlenab_hi_n, "
              "address_0,")
s = s.replace("  inout address_7;",
              "  inout address_7;\n  inout address_8;\n"
              "  inout wlenat_hi;\n  inout wlenab_hi;\n"
              "  inout wlenat_hi_n;\n  inout wlenab_hi_n;")
s = s.replace("  wire nm3_229;",
              "  wire nm3_229;\n  wire aq8;\n  wire aq7n;\n"
              "  wire wlenat_i;\n  wire wlenab_i;")

# 2. half select root now uses aq8
old = "INVx2_ASAP7_75t_R U12 (.A(nm3_229), .Y(nm3_210));"
assert old in s
s = s.replace(old, "INVx2_ASAP7_75t_R U12 (.A(aq8), .Y(nm3_210));")

# 3. enable drivers renamed to _i; internal consumers follow
for old2, new2 in [
    ("INVx1_ASAP7_75t_R U733 (.A(nm3_127), .Y(wlenat));",
     "INVx1_ASAP7_75t_R U733 (.A(nm3_127), .Y(wlenat_i));"),
    ("INVx1_ASAP7_75t_R U1189 (.A(nm3_200), .Y(wlenab));",
     "INVx1_ASAP7_75t_R U1189 (.A(nm3_200), .Y(wlenab_i));"),
    ("NOR2x1_ASAP7_75t_R U730 (.A(wlenat), .B(wlenab), .Y(nm3_126));",
     "NOR2x1_ASAP7_75t_R U730 (.A(wlenat_i), .B(wlenab_i), .Y(nm3_126));"),
    ("INVxp33_ASAP7_75t_R U418 (.A(wlenat), .Y(nm3_67));",
     "INVxp33_ASAP7_75t_R U418 (.A(wlenat_i), .Y(nm3_67));"),
    ("INVxp33_ASAP7_75t_R U1395 (.A(wlenab), .Y(nm2_405));",
     "INVxp33_ASAP7_75t_R U1395 (.A(wlenab_i), .Y(nm2_405));"),
]:
    assert old2 in s, old2
    s = s.replace(old2, new2)

# 4. new cells
s = s.replace("endmodule", """\
  DLLx1_ASAP7_75t_R UPA8 (.CLK(nm3_245), .D(address_8), .Q(aq8));
  INVx1_ASAP7_75t_R UPA7N (.A(nm3_229), .Y(aq7n));
  AND2x2_ASAP7_75t_R UWTL (.A(wlenat_i), .B(aq7n), .Y(wlenat));
  AND2x2_ASAP7_75t_R UWTH (.A(wlenat_i), .B(nm3_229), .Y(wlenat_hi));
  AND2x2_ASAP7_75t_R UWBL (.A(wlenab_i), .B(aq7n), .Y(wlenab));
  AND2x2_ASAP7_75t_R UWBH (.A(wlenab_i), .B(nm3_229), .Y(wlenab_hi));
  INVx1_ASAP7_75t_R UWTHN (.A(wlenat_hi), .Y(wlenat_hi_n));
  INVx1_ASAP7_75t_R UWBHN (.A(wlenab_hi), .Y(wlenab_hi_n));
endmodule""")
V.write_text(s)
print("patched: address_8 + wlena lo/hi split")
