"""Command-line interface.

    python3 -m sramforge --out out/            # reference 32-bit bank
    python3 -m sramforge --bits 64 --out out/  # experimental wider bank
    python3 -m sramforge --lef --out out/      # also the LEF abstract
    python3 -m sramforge --lef --lef-units 1000 --out out/
                   # LEF (Library Exchange Format) rounded outward to 1 nm
"""
import argparse
import json
import pathlib
import sys

from .generator import BankSpec, generate
from .verilog import emit_verilog
from . import pins as contract


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="sramforge",
        description="Single-port SRAM bank generator for ASAP7 "
                    "(open-source tools only).")
    p.add_argument("--bits", type=int, default=32,
                   help="bitcells per strip (reference: 32)")
    p.add_argument("--strips", type=int, default=4,
                   help="bitcell rows per column group (reference: 4)")
    p.add_argument("--depth", type=int, default=32,
                   help="column-group rows per mirrored half (reference: 32)")
    p.add_argument("--out", type=pathlib.Path, default=pathlib.Path("out"),
                   help="output directory")
    p.add_argument("--debug-views", action="store_true",
                   help="also emit per-block GDS dumps and an abstract "
                        "(outline+pins+name) view under out/debug/")
    p.add_argument("--lef", action="store_true",
                   help="also write the LEF (Library Exchange Format) "
                        "abstract <name>.lef next to the GDS (pins from "
                        "the layout's pin texts, merged metal as OBS)")
    p.add_argument("--lef-units", type=int, choices=(4000, 1000),
                   default=4000,
                   help="DATABASE MICRONS of the LEF (Library Exchange "
                        "Format) file: 4000 = exact 0.25 nm coordinates "
                        "(the contract, default); 1000 = every pin and "
                        "obstruction rectangle rounded OUTWARD onto the "
                        "1 nm grid, which OpenROAD's ASAP7 technology "
                        "LEF (Library Exchange Format) requires")
    args = p.parse_args(argv)

    spec = BankSpec(bits_per_group=args.bits, strips_per_group=args.strips,
                    depth_groups=args.depth)
    args.out.mkdir(parents=True, exist_ok=True)

    gds = args.out / f"{spec.name}.gds"
    manifest = generate(spec, str(gds))

    # The measured logical organization (confirmed by the family's own
    # models, e.g. srambank_64x4x16 = 16-bit data, 64x4x80 = 80-bit data,
    # both 8-bit address): words = wordline columns x mux x halves, and
    # DATA WIDTH = colgrp rows (each row contributes 2 sense-amp bits,
    # one per array side), so --depth grows the word and --bits/--strips
    # grow the word count.
    data_bits = contract.data_width(spec)
    num_words = contract.word_count(spec)
    # every physical bitcell is addressable (pins.addressable_strips)
    assert data_bits * num_words == manifest["bitcells"]
    v = args.out / f"{spec.name}.v"
    v.write_text(emit_verilog(spec))
    # sense-enable timing: the delay-line code the bank needs (measured
    # rule, sramforge/sense.py); None when the placed control cannot
    # strobe late enough for this depth and strip count
    from . import sense
    timing = {c: sense.sdel_code(spec, c) for c in ("TT", "SS")}
    manifest.update(data_bits=data_bits, num_words=num_words,
                    address_bits=contract.address_width(spec),
                    verilog=v.name, sense_timing=timing)
    for c, r in timing.items():
        if r["code"] is None:
            print(f"WARNING: sense timing at {c}: {r['note']}")

    if args.lef:
        from .lef import emit_lef
        manifest["lef"] = emit_lef(gds, spec.name, spec,
                                   args.out / f"{spec.name}.lef",
                                   units=args.lef_units)

    if args.debug_views:
        from .debugview import emit_debug_views
        manifest["debug"] = emit_debug_views(spec, args.out)

    (args.out / f"{spec.name}.json").write_text(json.dumps(manifest, indent=2))

    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
