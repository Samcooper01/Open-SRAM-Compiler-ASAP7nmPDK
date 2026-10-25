#!/usr/bin/env python3
"""Patch the control netlist for eight column strips: a 3:8 column-select
decode for BOTH arrays.

Reads out/control_netlist.v (the extraction plus scripts/patch_control_64
.py and scripts/patch_control_yselb.py) and writes out/control_netlist_s8
.v, the netlist of the control variant the strips = 8 bank places.  The
variant is meant for bits = 32 (BankSpec allows strips = 8 only there),
where the dual-decoder machinery is idle, so the latched ADDRESS[7] (net
nm3_229, the decode-bank select of the 64-column scheme) is free to
become the third column-mux bit:

  * the eight existing select outputs yselt_i / yseltn_i (left array,
    half = 1) and yselb_i / yselbn_i (right array, half = 0), i = 0..3,
    are gated by aq7n (ADDRESS[7] low: mux legs 0..3);
  * sixteen new ports yselt_4..7 / yseltn_4..7 / yselb_4..7 / yselbn_4..7
    carry the same per-array 2:4 decode gated by nm3_229 (ADDRESS[7]
    high: legs 4..7), with the same drive (three parallel BUFx8 / INVx8
    per output, as the originals);
  * the wordline enables wlenat / wlenab lose their aq7n gate (in the
    64-column scheme ADDRESS[7] selects the decode bank and gates the lo
    enables; here every address must enable the one decoder pair).  The
    _hi outputs stay driven and unused, exactly as at bits < 32 today.

The address map of this variant: ADDRESS[1:0] and ADDRESS[7] = column
mux (3 bits), ADDRESS[6:2] = wordline column, ADDRESS[8] = half.

Idempotent: skips if the target already carries yselt_4.  Does NOT run
the place-and-route; see pnr/ for that.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "out" / "control_netlist.v"
DST = REPO / "out" / "control_netlist_s8.v"

# the 2:4 decode nodes behind each select output (measured in the
# netlist): the left array's (half = 1) and the right array's (half = 0,
# scripts/patch_control_yselb.py B_NODE)
NODE = {"t": {0: "nm3_62", 1: "nm3_58", 2: "nm3_60", 3: "nm3_63"},
        "b": {0: "nm3_173", 1: "nm3_1", 2: "nm3_166", 3: "nm3_152"}}


def main() -> int:
    s = SRC.read_text()
    assert "wlenat_hi" in s, "run scripts/patch_control_64.py first"
    if DST.exists() and "yselt_4" in DST.read_text():
        print("already patched")
        return 0
    assert "yselb_0" in s, "run scripts/patch_control_yselb.py first"
    new_ports = [f"ysel{fam}{pol}_{i}" for fam in ("t", "b")
                 for pol in ("", "n") for i in range(4, 8)]
    # 1. ports and declarations
    s = s.replace("module control_sram_2kB (yselb_0,",
                  "module control_sram_2kB (" + ", ".join(new_ports)
                  + ", yselb_0,")
    s = s.replace("  inout yseltn_3;",
                  "  inout yseltn_3;\n"
                  + "".join(f"  inout {p};\n" for p in new_ports).rstrip("\n"))
    s = s.replace("  wire aq8;",
                  "  wire aq8;\n"
                  + "".join(f"  wire sel{fam}_lo_{i};\n  wire sel{fam}_hi_{i};\n"
                            for fam in ("t", "b") for i in range(4)).rstrip("\n"))
    # 2. existing drivers of ysel{t,b}_i / ysel{t,b}n_i take the gated node
    n_rewired = 0
    for fam, nodes in NODE.items():
        for i, node in nodes.items():
            for port in (f"ysel{fam}_{i}", f"ysel{fam}n_{i}"):
                pat = re.compile(r"\(\.A\(" + node + r"\), \.Y\(" + port + r"\)\);")
                s, n = pat.subn(f"(.A(sel{fam}_lo_{i}), .Y({port}));", s)
                assert n == 3, f"{port}: expected 3 parallel drivers, found {n}"
                n_rewired += n
    # 3. new cells: the gates, and leg 4..7 drivers with the original drive
    cells = []
    for fam, nodes in NODE.items():
        for i, node in nodes.items():
            cells.append(f"  AND2x2_ASAP7_75t_R US{fam}L{i} (.A({node}), "
                         f".B(aq7n), .Y(sel{fam}_lo_{i}));")
            cells.append(f"  AND2x2_ASAP7_75t_R US{fam}H{i} (.A({node}), "
                         f".B(nm3_229), .Y(sel{fam}_hi_{i}));")
            for k in range(3):
                cells.append(f"  BUFx8_ASAP7_75t_R US{fam}B{i}_{k} "
                             f"(.A(sel{fam}_hi_{i}), .Y(ysel{fam}_{i + 4}));")
                cells.append(f"  INVx8_ASAP7_75t_R US{fam}N{i}_{k} "
                             f"(.A(sel{fam}_hi_{i}), .Y(ysel{fam}n_{i + 4}));")
    s = s.replace("endmodule", "\n".join(cells) + "\nendmodule")
    # 4. the lo wordline enables are no longer gated by the decode-bank bit
    for old, new in (
        ("AND2x2_ASAP7_75t_R UWTL (.A(wlenat_i), .B(aq7n), .Y(wlenat));",
         "BUFx2_ASAP7_75t_R UWTL (.A(wlenat_i), .Y(wlenat));"),
        ("AND2x2_ASAP7_75t_R UWBL (.A(wlenab_i), .B(aq7n), .Y(wlenab));",
         "BUFx2_ASAP7_75t_R UWBL (.A(wlenab_i), .Y(wlenab));"),
    ):
        assert old in s, old
        s = s.replace(old, new)
    DST.write_text(s)
    print(f"wrote {DST}: {len(new_ports)} new ports, {n_rewired} drivers "
          f"rewired, {len(cells)} cells added, wordline enables un-gated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
