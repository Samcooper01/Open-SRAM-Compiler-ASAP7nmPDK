#!/usr/bin/env python3
"""Dump a rung-4 extraction to the JSON (JavaScript Object Notation) schema
the analog access stages consume: {"devices": [...], "labels": {...}}.

Usage:
  python3 scripts/dump_netlist.py <bank.gds> <topcell> <out.json>
  python3 scripts/dump_netlist.py --generate <out.json>   # our compiler's bank

The --generate form builds the default BankSpec with OUR generator, extracts
it, and dumps - this is the netlist that proves the analog access results
apply to the compiled layout, not just the golden reference.
"""
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge.lvs import extract                    # noqa: E402


def net_str(net):
    """Net ID tuple (layer, island) -> the 'layer:island' string the
    access-stage scripts key on."""
    return f"{net[0]}:{net[1]}"


def dump(ex, out_path):
    data = {
        "devices": [{"model": d.model, "nfin": d.nfin,
                     "gate": net_str(d.gate),
                     "sd": sorted(net_str(n) for n in d.sd)}
                    for d in ex.devices],
        "labels": {net_str(net): sorted(labs)
                   for net, labs in ex.net_labels.items()},
    }
    pathlib.Path(out_path).write_text(json.dumps(data))
    print(f"wrote {out_path}: {len(data['devices'])} devices, "
          f"{len(data['labels'])} labeled nets")


def main():
    if sys.argv[1] == "--generate":
        from sramforge import BankSpec, generate
        spec = BankSpec()
        with tempfile.TemporaryDirectory() as td:
            gds = str(pathlib.Path(td) / f"{spec.name}.gds")
            generate(spec, gds)
            ex = extract(gds, spec.name)
        dump(ex, sys.argv[2])
    else:
        gds, top, out = sys.argv[1:4]
        dump(extract(gds, top), out)


if __name__ == "__main__":
    main()
