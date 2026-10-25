#!/usr/bin/env python3
"""Golden test: at reference parameters the generated bank must be
geometrically IDENTICAL to the BSD-3 reference srambank_32b.gds.

Both layouts are flattened and each layer is XOR'd; the test passes only
if every XOR is empty.  This proves every measured tiling rule (bitcell
half-pitch interleave, mirrored row pairs, dummy guard rows, io stack,
column-group assembly, bank stacking, decoder/filler anchors) is exact.

Usage:  python3 tests/test_golden.py [path/to/asap7_sram_0p0]
"""
import sys
import pathlib
import tempfile
import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from sramforge import BankSpec, generate  # noqa: E402


def flatten_region_per_layer(path: str, top_name: str | None = None):
    ly = kdb.Layout()
    ly.read(path)
    tops = [ly.cell(c) for c in ly.each_top_cell()]
    top = (ly.cell(top_name) if top_name
           else max(tops, key=lambda c: c.bbox().area()))
    out = {}
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        reg = kdb.Region(top.begin_shapes_rec(li))
        reg.merge()
        out[(info.layer, info.datatype)] = reg
    return out


def main() -> int:
    ref_dir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                           else REPO.parent / "asap7_sram_0p0")
    ref_gds = ref_dir / "gds" / "srambank_32b.gds"
    if not ref_gds.exists():
        print(f"FAIL: the reference macro is not at {ref_gds}.\n"
              "Rung 0 compares the generated layout against the official "
              "ASAP7 SRAM release, which lives in its own repository.  "
              "Clone it next to this one:\n\n"
              "    git clone https://github.com/The-OpenROAD-Project/"
              "asap7_sram_0p0.git ../asap7_sram_0p0\n\n"
              "or pass its path: python3 tests/test_golden.py "
              "/path/to/asap7_sram_0p0")
        return 1

    with tempfile.TemporaryDirectory() as td:
        gen_gds = str(pathlib.Path(td) / "gen.gds")
        manifest = generate(BankSpec(), gen_gds)
        print(f"generated {manifest['top_cell']}: "
              f"{manifest['width_um']} x {manifest['height_um']} um, "
              f"{manifest['bitcells']} bitcells")

        ours = flatten_region_per_layer(gen_gds)
        ref = flatten_region_per_layer(str(ref_gds), "srambank_32b")

    layers = sorted(set(ours) | set(ref))
    failures = 0
    for ld in layers:
        a = ours.get(ld, kdb.Region())
        b = ref.get(ld, kdb.Region())
        diff = a ^ b
        status = "OK " if diff.is_empty() else "DIFF"
        if not diff.is_empty():
            failures += 1
            print(f"  [{status}] layer {ld[0]}/{ld[1]}: "
                  f"{diff.count()} xor polygons "
                  f"(ours {a.count()} vs ref {b.count()})")
        else:
            print(f"  [{status}] layer {ld[0]}/{ld[1]}: "
                  f"{a.count()} polygons match")

    if failures:
        print(f"\nFAIL: {failures}/{len(layers)} layers differ")
        return 1
    print(f"\nPASS: all {len(layers)} layers XOR-identical to the reference")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
