#!/usr/bin/env python3
"""Rung 6: LEF (Library Exchange Format) abstract agrees with the layout.

The exporter (sramforge/lef.py) reads pins and obstructions off the
signed-off GDS (Graphic Design System II) file under the pin contract
(docs/pin_interface.md).  This rung proves, for the 32,4,32 reference,
the 16,4,32 sparse-bits bank and the 64,4,64 dual-decoder bank, that
what it wrote is what the layout holds:

  (i)   round trip: KLayout's own LEF reader (an independent parser)
        accepts the file; the macro is there, its outline is the bank
        bounding box, every PIN shape carries its pin name;
  (ii)  geometry: every PIN rectangle, shifted back to GDS coordinates,
        is covered by drawn metal of the same layer, and every pin text
        of the layout lies inside its pin's rectangles; the OBS shapes
        never touch a pin shape, and OBS + pins reproduce the merged
        drawn metal of every layer exactly (XOR (exclusive or) empty);
  (iii) census: the pin names are exactly the contract's list for the
        spec (2 * depth wd and dataout bits, 8 or 9 address bits, five
        sdel taps, the four control scalars, VDD and VSS);
  (iv)  mutations on a copy of the GDS: deleting a pin text, moving one
        off its metal, and duplicating a name onto another island must
        each make the exporter refuse and write nothing;
  (v)   the 1000-unit variant (emit_lef(..., units=1000), the outward
        rounded export OpenROAD can load): the same round trip and census,
        every coordinate on the 1 nm grid, and instead of the exact XOR
        (exclusive or): (a) every pin's rectangles, mapped back to the
        layout's 0.25 nm dbu, CONTAIN the pin's original metal island
        (outward rounding proven), (b) OBS (obstruction, the LEF (Library
        Exchange Format) keyword) plus pins cover all drawn metal (no
        under-coverage), (c) the over-coverage area per layer is reported
        and bounded by a 1 nm halo times the perimeter of the layer's
        drawn metal.  Why that bound: outward rounding moves an edge by
        at most 0.75 nm, so the over-cover lies inside a 0.75 nm halo of
        the metal whose area is 0.75 nm times the perimeter plus corner
        squares of at most 0.75 x 0.75 nm; a 1 nm halo times the perimeter
        absorbs the corners as long as every metal edge is longer than
        2.25 nm, which every ASAP7 (Arizona State University 7 nm
        predictive Process Design Kit) width or spacing rule guarantees.
        The variant runs at 32 and 16 by default (about 40 s and 26 s for
        the whole rung); at 64 it adds about 75 s to a rung that already
        takes about 90 s, so it runs there only with --full (measured
        once, PASS, numbers in docs/lef_export.md).

Usage:  python3 tests/test_lef.py [--config bits,strips,depth] [--full]
        (default: all three configurations, sequentially)
"""
import argparse
import pathlib
import re
import sys
import tempfile

import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge import BankSpec, generate                      # noqa: E402
from sramforge.lef import emit_lef, LefExportError, METALS    # noqa: E402
from sramforge import pins as contract                        # noqa: E402

CONFIGS = ((32, 4, 32), (16, 4, 32), (64, 4, 64))
GDS_OF_METAL = dict(METALS)
DBU = 0.00025
NM = 4                  # dbu per nm: the 1000-unit grid step in layout dbu
UM2_PER_DBU2 = DBU * DBU


def read_lef_back(lef_path: str) -> kdb.Layout:
    """Parse the LEF with KLayout's LEF/DEF reader.  Pin shapes land on
    layers named '<metal>.PIN' with the pin name as property 1, labels
    on '<metal>.LABEL', obstructions on '<metal>.OBS', SIZE on OUTLINE."""
    opt = kdb.LoadLayoutOptions()
    cfg = opt.lefdef_config
    cfg.dbu = DBU
    cfg.pin_property_name = 1
    ly = kdb.Layout()
    ly.read(lef_path, opt)
    return ly


def layout_pin_texts(ly: kdb.Layout, top: kdb.Cell, names: set) -> dict:
    """Pin name -> [(layer, x, y)] of every text anywhere in the bank
    whose exported name is a pin.  Supplies are taken from the bank
    cell's own texts only: 'vdd!'/'vss!' are also labeled inside every
    leaf cell, and those thousands of internal labels are not pins."""
    out = {}
    for lname, lnum in METALS:
        li = ly.layer(lnum, 251)
        it = top.begin_shapes_rec(li)
        while not it.at_end():
            sh = it.shape()
            if sh.is_text():
                name = contract.export_name(sh.text_string)
                is_supply = name in contract.SUPPLY_NAMES
                if name in names and (not is_supply
                                      or it.cell().name == top.name):
                    p = it.trans() * kdb.Point(sh.text_trans.disp.x,
                                               sh.text_trans.disp.y)
                    out.setdefault(name, []).append((lname, p.x, p.y))
            it.next()
    return out


def island_under(metal: kdb.Region, x: int, y: int) -> kdb.Region:
    """The merged metal polygon containing the point (x, y)."""
    return metal.interacting(kdb.Region(kdb.Box(x, y, x + 1, y + 1)))


def check_config(spec: BankSpec, gds: str, lef: str, units: int = 4000) -> list:
    """Prove one LEF (Library Exchange Format) file against the layout.
    units = 4000: the exact contract export, XOR (exclusive or) empty.
    units = 1000: the outward-rounded variant, checks (v) of the module
    docstring instead of the XOR (exclusive or)."""
    fails = []
    exact = units == 4000
    expected_names = set(contract.ordered_pin_names(spec))

    # ---- the layout side, computed here independently of the exporter
    gl = kdb.Layout(); gl.read(gds)
    top = gl.cell(spec.name)
    bbox = top.bbox()
    dx, dy = -bbox.left, -bbox.bottom
    metal = {lname: kdb.Region(top.begin_shapes_rec(gl.layer(lnum, 0)))
             .merged() for lname, lnum in METALS}
    texts = layout_pin_texts(gl, top, expected_names)

    # ---- (i) round trip through KLayout's LEF reader
    rl = read_lef_back(lef)
    macro = rl.cell(spec.name)
    if macro is None:
        return [f"LEF reader found no macro {spec.name} "
                f"(cells: {[c.name for c in rl.each_cell()]})"]
    layers = {rl.get_info(li).name: li for li in rl.layer_indexes()}
    outline = kdb.Region(macro.shapes(layers["OUTLINE"])).bbox()
    want = kdb.Box(0, 0, bbox.width(), bbox.height())
    if not exact:                       # SIZE rounds up to the 1 nm grid
        want = kdb.Box(0, 0, -(-bbox.width() // NM) * NM,
                       -(-bbox.height() // NM) * NM)
    if outline != want:
        fails.append(f"SIZE {outline} != bank bbox shifted {want}")
    if not exact:
        # every coordinate of the file on the 1 nm grid: the reader
        # (dbu 0.25 nm) must return only multiples of 4 dbu
        off = 0
        for li in rl.layer_indexes():
            for sh in macro.shapes(li).each():
                if sh.is_text():
                    continue
                off += sum(p.x % NM != 0 or p.y % NM != 0
                           for p in sh.polygon.each_point_hull())
        if off:
            fails.append(f"{off} shape vertices are off the 1 nm grid")

    pins = {}           # name -> (layer, Region in GDS coordinates)
    for lname, _ in METALS:
        li = layers.get(f"{lname}.PIN")
        if li is None:
            continue
        for sh in macro.shapes(li).each():
            props = rl.properties(sh.prop_id) if sh.prop_id else []
            name = next((v for k, v in props if k == 1), None)
            if name is None:
                fails.append(f"{lname} pin shape {sh} without a pin name")
                continue
            layer, reg = pins.setdefault(name, (lname, kdb.Region()))
            if layer != lname:
                fails.append(f"pin {name} on two layers {layer}, {lname}")
            reg.insert(sh.polygon.moved(-dx, -dy))

    # ---- (iii) census
    found = set(pins)
    if found != expected_names:
        fails.append(f"census: missing {sorted(expected_names - found)[:6]}"
                     f", unexpected {sorted(found - expected_names)[:6]}")
    a, w = contract.address_width(spec), contract.data_width(spec)
    print(f"    {len(found)} pins: {a} ADDRESS, {w} wd, {w} dataout, "
          f"5 sdel, clk/banksel/read/byte_wrenab, VDD/VSS")

    # ---- (ii) geometry: pins on metal (exact) or containing their
    #      island (1000 units, (v)(a)), texts inside pins
    uncovered, orphan_texts, all_pin = 0, 0, {l: kdb.Region() for l, _ in METALS}
    for name, (lname, reg) in pins.items():
        if exact and not (reg - metal[lname]).is_empty():
            uncovered += 1
            if uncovered <= 3:
                fails.append(f"pin {name}: shape not covered by {lname} "
                             f"metal ({(reg - metal[lname]).bbox()})")
        all_pin[lname] += reg
        for tl, x, y in texts.get(name, []):
            if tl != lname or not reg.merged().interacting(
                    kdb.Region(kdb.Box(x, y, x + 1, y + 1))).count():
                orphan_texts += 1
                if orphan_texts <= 3:
                    fails.append(f"text {name} at ({x},{y}) on {tl} is not "
                                 f"inside its pin's {lname} rectangles")
            elif not exact:
                # (v)(a): the original island under the text, computed
                # here from the merged metal, lies inside the rounded pin
                island = island_under(metal[lname], x, y)
                if not (island - reg).is_empty():
                    uncovered += 1
                    if uncovered <= 3:
                        fails.append(f"pin {name}: rounded shape does not "
                                     f"contain its {lname} island "
                                     f"({(island - reg).bbox()})")
        if not texts.get(name):
            fails.append(f"pin {name}: no layout text found for it")
    if uncovered > 3 or orphan_texts > 3:
        fails.append(f"... {uncovered} uncovered pins, {orphan_texts} "
                     f"texts outside their pins in total")

    # ---- (ii) obstructions: disjoint from pins, and together exact
    for lname, _ in METALS:
        li = layers.get(f"{lname}.OBS")
        obs = kdb.Region()
        if li is not None:
            for sh in macro.shapes(li).each():
                obs.insert(sh.polygon.moved(-dx, -dy))
        if not (obs & all_pin[lname]).is_empty():
            fails.append(f"{lname}: OBS overlaps a pin shape")
        if exact:
            leftover = (obs + all_pin[lname]) ^ metal[lname]
            if not leftover.is_empty():
                fails.append(f"{lname}: OBS + pins != drawn metal "
                             f"({leftover.count()} pieces, e.g. "
                             f"{leftover.bbox()})")
            print(f"    {lname}: {obs.count()} OBS shapes + "
                  f"{all_pin[lname].merged().count()} pin islands "
                  f"({all_pin[lname].count()} rectangles) = "
                  f"{metal[lname].count()} merged islands, XOR empty")
            continue
        # (v)(b): no under-coverage; (v)(c): over-coverage bounded by a
        # 1 nm halo times the drawn-metal perimeter (module docstring)
        cover = obs + all_pin[lname]
        under = metal[lname] - cover
        if not under.is_empty():
            fails.append(f"{lname}: OBS + pins under-cover drawn metal "
                         f"({under.count()} pieces, e.g. {under.bbox()})")
        over = (cover - metal[lname]).area()
        bound = 1 * NM * metal[lname].perimeter()
        if over > bound:
            fails.append(f"{lname}: over-coverage {over * UM2_PER_DBU2:.4f} "
                         f"um2 exceeds the 1 nm halo bound "
                         f"{bound * UM2_PER_DBU2:.4f} um2")
        print(f"    {lname}: {obs.count()} OBS shapes + "
              f"{all_pin[lname].count()} pin rectangles cover "
              f"{metal[lname].count()} merged islands; over-coverage "
              f"{over * UM2_PER_DBU2:.4f} um2 = "
              f"{100 * over / max(1, metal[lname].area()):.2f}% of metal, "
              f"{100 * over / bound:.1f}% of the 1 nm halo bound "
              f"({bound * UM2_PER_DBU2:.3f} um2)")

    # ---- header contract: units, bus chars, class, the shift statement
    head = pathlib.Path(lef).read_text()[:4000]
    for token in (f"DATABASE MICRONS {units} ;", 'BUSBITCHARS "[]" ;',
                  'DIVIDERCHAR "/" ;', "CLASS BLOCK ;", "ORIGIN 0 0 ;",
                  f"({bbox.left}, {bbox.bottom}) dbu") + (
                      () if exact else ("# ROUNDING:", "rounded OUTWARD")):
        if token not in head:
            fails.append(f"header lacks '{token}'")
    return fails


def mutate(gds: str, top_name: str, out: str, kind: str) -> None:
    """Write a copy of the GDS with one defect at the top cell."""
    ly = kdb.Layout(); ly.read(gds)
    top = ly.cell(top_name)
    li = ly.layer(30, 251)
    victim = next(sh for sh in top.shapes(li).each()
                  if sh.is_text() and sh.text_string == "wd<3>")
    if kind == "delete":
        top.shapes(li).erase(victim)
    elif kind == "off_metal":
        x = top.bbox().right + 4000            # 1 um outside the bank
        y = victim.text_trans.disp.y
        top.shapes(li).erase(victim)
        top.shapes(li).insert(kdb.Text("wd<3>", kdb.Trans(kdb.Vector(x, y))))
    elif kind == "duplicate":
        other = next(sh for sh in top.shapes(li).each()
                     if sh.is_text() and sh.text_string == "wd<5>")
        top.shapes(li).insert(kdb.Text("wd<3>", other.text_trans))
    ly.write(out)


def run(spec: BankSpec, variant_1000: bool = True) -> list:
    fails = []
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        gds = str(td / f"{spec.name}.gds")
        lef = str(td / f"{spec.name}.lef")
        generate(spec, gds)
        info = emit_lef(gds, spec.name, spec, lef)
        print(f"[{spec.name}] LEF {pathlib.Path(lef).stat().st_size // 1024} "
              f"kB, shift {info['shift_dbu']} dbu, size {info['size_um']} um")
        fails += check_config(spec, gds, lef)

        # ---- (v) the 1000-unit outward-rounded variant
        if variant_1000:
            lef1000 = str(td / f"{spec.name}_1000.lef")
            info = emit_lef(gds, spec.name, spec, lef1000, units=1000)
            print(f"[{spec.name}] LEF at 1000 units: "
                  f"{pathlib.Path(lef1000).stat().st_size // 1024} kB, "
                  f"{info['rounded_coordinates']} rectangle coordinates "
                  f"rounded outward, size {info['size_um']} um")
            fails += [f"units 1000: {f}"
                      for f in check_config(spec, gds, lef1000, units=1000)]
        else:
            print(f"[{spec.name}] 1000-unit variant skipped (pass --full)")

        # ---- (iv) mutations must be refused, nothing written
        for kind in ("delete", "off_metal", "duplicate"):
            mgds = str(td / f"mut_{kind}.gds")
            mlef = td / f"mut_{kind}.lef"
            mutate(gds, spec.name, mgds, kind)
            try:
                emit_lef(mgds, spec.name, spec, mlef)
                fails.append(f"mutation {kind}: exporter did not refuse")
            except LefExportError as e:
                msg = re.sub(r"\s+", " ", str(e))
                print(f"    mutation {kind}: refused ({msg[:90]}...)")
            if mlef.exists():
                fails.append(f"mutation {kind}: a LEF was written anyway")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None,
                    help="bits,strips,depth (default: all three)")
    ap.add_argument("--full", action="store_true",
                    help="also run the 1000-unit variant at 64 bits "
                         "(about 75 s more)")
    a = ap.parse_args()
    configs = ([tuple(map(int, a.config.split(",")))] if a.config
               else CONFIGS)
    total = []
    for bits, strips, depth in configs:
        spec = BankSpec(bits_per_group=bits, strips_per_group=strips,
                        depth_groups=depth)
        fails = run(spec, variant_1000=(bits < 64 or a.full))
        total += [f"{spec.name}: {f}" for f in fails]
        print(f"[{'FAIL' if fails else 'PASS'}] {spec.name}")
    if total:
        print("[FAIL]")
        for f in total:
            print("    -", f)
        return 1
    print("[PASS] LEF abstracts agree with the layout at "
          f"{len(configs)} configuration(s); all mutations refused")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
