#!/usr/bin/env python3
"""Convert the ASAP7 HSPICE model decks (.pm, BSIM-CMG level=72) into
ngspice-OSDI (Open Simulator Device Interface) model cards.

Every calibrated parameter is carried over verbatim; only the framing
changes: `.model <name> nmos level = 72` becomes
`.model <name> bsimcmg ( + TYPE = 1 ... )` (TYPE 0 for pmos), which is
how ngspice binds a model card to the OpenVAF-compiled bsimcmg.osdi.

Usage: python3 pm2osdi.py <in.pm> <out.sp>
"""
import re
import sys


def convert(text):
    out = ["* Converted from ASAP7 HSPICE deck by sramforge pm2osdi.py",
           "* (BSD-3 ASU/ARM model data; framing adapted for ngspice OSDI)"]
    blocks = re.split(r"(?=^\.model )", text, flags=re.M)
    for b in blocks:
        m = re.match(r"^\.model\s+(\S+)\s+(nmos|pmos)\s+level\s*=\s*72\s*$",
                     b.splitlines()[0].strip() if b.strip() else "")
        if not m:
            continue
        name, kind = m.group(1), m.group(2)
        params = [ln for ln in b.splitlines()[1:] if ln.strip().startswith("+")]
        out.append(f"\n.model {name} bsimcmg (")
        out.append(f"+ TYPE = {1 if kind == 'nmos' else 0}")
        # BSIM-CMG 110 declares JTWEFF with an open (0,inf) bound but a
        # 0.0 default; give it a legal dummy (the trap-assisted
        # tunneling term it scales is off in the ASAP7 cards)
        out.append("+ jtweff = 1e-9")
        out.extend(params)
        out.append("+ )")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    src = open(sys.argv[1]).read()
    open(sys.argv[2], "w").write(convert(src))
    n = convert(src).count(".model")
    print(f"{sys.argv[2]}: {n} models converted")
