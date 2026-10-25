#!/usr/bin/env python3
"""Patch the control netlist to export the RIGHT array's column selects.

The reference bank has two select families: yselt<k> / yseltn<k> open
mux leg k of the LEFT array (gated by half = 1) and yselb<k> / yselbn<k>
open leg k of the RIGHT array (gated by half = 0); both run as M5 lines
through every column-group row, and the band-crossing yselb risers sit
at bank x 20520..21960.  The control extraction (scripts/extract_control
_netlist.py) kept the yselt drivers and ports, but the yselb output
buffers lived outside its cut: the netlist still holds the half-0
decode (nm3_5, nm3_6, nm3_146, nm3_225 and their BUFx2 triples onto
nm3_173, nm3_1, nm3_166, nm3_152) driving nothing, and the one-box
control had no yselb ports.  Measured 2026-09-19 on every band-v2 bank:
yselb<k> / yselbn<k> have zero driver devices (128 gates each), so the
right array's mux never opens and half of every band-v2 bank's words
were unreachable (rung 4.5 grades wordlines and enables, never the
column mux; it now grades the select lines too).

This patch adds the eight ports yselb_0..3 / yselbn_0..3 with the same
drive as the yselt family (three parallel BUFx8 / INVx8 per output)
from the half-0 pre-driver nets.  Their pads and landings sit ON the
existing band risers (pnr/gen_inputs.py YSELB_PORTS), so the strap is
a direct M5 merge.

Idempotent: skips if already patched.  Run after scripts/patch_control
_64.py, before scripts/patch_control_s8.py and pnr/gen_inputs.py.
"""
import pathlib

V = pathlib.Path(__file__).resolve().parent.parent / "out" / "control_netlist.v"

# half-0 select pre-driver nets (the T's yselb_k driver inputs), matched
# to k by the address-decode term they share with the yselt_k cone:
#   k: yselt node <- INV(NAND(term, nm3_222 = half-1 enable))
#      yselb node <- INV(NAND(term, nm3_198 = half-0 enable)) -> BUFx2 x3
B_NODE = {0: "nm3_173", 1: "nm3_1", 2: "nm3_166", 3: "nm3_152"}


def main() -> int:
    s = V.read_text()
    assert "wlenat_hi" in s, "run scripts/patch_control_64.py first"
    if "yselb_0" in s:
        print("already patched")
        return 0
    new_ports = ([f"yselb_{k}" for k in range(4)]
                 + [f"yselbn_{k}" for k in range(4)])
    for node in B_NODE.values():
        assert f".Y({node})" in s, f"pre-driver net {node} not in netlist"
    s = s.replace("module control_sram_2kB (address_8,",
                  "module control_sram_2kB (" + ", ".join(new_ports)
                  + ", address_8,")
    s = s.replace("  inout yseltn_3;",
                  "  inout yseltn_3;\n"
                  + "".join(f"  inout {p};\n" for p in new_ports).rstrip("\n"))
    cells = []
    for k, node in B_NODE.items():
        for i in range(3):
            cells.append(f"  BUFx8_ASAP7_75t_R UBB{k}_{i} (.A({node}), "
                         f".Y(yselb_{k}));")
            cells.append(f"  INVx8_ASAP7_75t_R UBN{k}_{i} (.A({node}), "
                         f".Y(yselbn_{k}));")
    s = s.replace("endmodule", "\n".join(cells) + "\nendmodule")
    V.write_text(s)
    print(f"patched {V}: {len(new_ports)} right-array select ports, "
          f"{len(cells)} driver cells")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
