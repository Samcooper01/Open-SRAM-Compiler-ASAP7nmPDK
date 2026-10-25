"""LEF (Library Exchange Format) abstract emitter for generated banks.

One LEF 5.8 MACRO per bank, derived from the signed-off GDS (Graphic
Design System II) file and nothing else.  The layout is the authority
(docs/pin_interface.md, section 1): a pin is a text on a pin-text layer
(datatype 251 of M1..M5) that belongs to the bank cell itself or, for the
external control/address labels only, to the decode band cell placed in
it; its physical shape is the merged metal island (same layer number,
datatype 0) that contains the text point.  Everything else drawn on
M1..M5 becomes an obstruction, merged per layer, exactly as drawn.

Why the band is read with a vocabulary filter: the band hierarchy holds
thousands of standard-cell pin texts (A, Y, B, CLK, VDD, VSS, wl<n>, ...)
that are internal to the macro.  The contract's reason for reading the
band at all is the ninth address bit (ADDRESS<8>, placed in a band
subcell at bits > 32), so only the contract's M5 control/address families
are accepted from there (pins.BAND_SIGNAL_FAMILIES).

Refusals (section 6): a text with no metal beneath it, a signal name on
two disconnected islands, two names on one island, a name outside the
contract's vocabulary, and a census that does not match the spec (2 *
depth data pins each way, 8 or 9 address pins, 5 sdel taps, the four
scalars, both supplies).  On any of these nothing is written: the layout
has a defect and the export must expose it, not paper over it.

Geometry conventions (section 4): DATABASE MICRONS 4000 (1 dbu = 0.25 nm,
so every coordinate is exact to 5 decimals), the macro's lower-left is
shifted to 0 0 and the shift is stated in the header and carried by the
FOREIGN statement, vias are not exported.  LEF (Library Exchange Format)
syntax follows the LEF (Library Exchange Format)/DEF (Design Exchange
Format) 5.8 Language Reference (https://si2.org/oa-tools-utils-libs/,
the "LEF/DEF 5.8" download); the reader used by the round-trip test is
KLayout's (https://www.klayout.de/doc-qt5/manual/lefdef_import.html).

Units and the rounding policy (`units` argument of emit_lef):

* units = 4000 (default, the contract): exact.  Every coordinate is the
  layout's own 0.25 nm dbu after the origin shift, no rounding at all.
* units = 1000 (consumer variant for OpenROAD, whose ASAP7 (Arizona State
  University 7 nm predictive Process Design Kit) technology LEF (Library
  Exchange Format) is at DATABASE MICRONS 1000 and discards a 4000-unit
  macro): every coordinate is rounded onto the 1 nm macro grid AFTER the
  origin shift, so the macro's lower-left stays at 0 0.  The rounding is
  outward, never to nearest: each pin island and each obstruction island
  is cut into rectangles and each rectangle's left/bottom edge is rounded
  down, its right/top edge up (floor and ceil to the next multiple of
  4 dbu).  A pin may therefore grow by up to 0.75 nm per edge but never
  shrinks below its metal, and an obstruction may grow the same way but
  never under-covers metal (an abstract that under-covers is a routing
  short waiting to happen; over-cover is safe).  Obstructions are merged
  per layer after rounding, so rectangles that came to touch or overlap
  become one polygon instead of slivers, and the rounded pin shapes are
  subtracted from the merged obstruction so OBS (obstruction, the LEF
  (Library Exchange Format) keyword) and PIN stay disjoint as in the exact
  export.  SIZE rounds up; FOREIGN (the origin shift) rounds to nearest,
  and the header says so when it moved.  Coordinates are written with
  3 decimals, exact on that grid.
"""
import pathlib
from dataclasses import dataclass, field

import klayout.db as kdb

from .generator import BankSpec
from .nets import IslandIndex
from . import pins as contract

LEF_VERSION = "5.8"
DATABASE_MICRONS = 4000            # 1 / 0.00025 um per dbu: the exact export
LAYOUT_DBU = 0.00025               # the only dbu the header above is true for
UNITS_CHOICES = (4000, 1000)       # exact, or rounded outward onto 1 nm
DECIMALS_OF_UNITS = {4000: 5, 1000: 3}
METALS = (("M1", 19), ("M2", 20), ("M3", 30), ("M4", 40), ("M5", 50))
GDS_LAYER_OF = dict(METALS)
METAL_OF_GDS = {num: name for name, num in METALS}
DRAWN_DATATYPE = 0
PIN_TEXT_DATATYPE = 251
BAND_CELL_PREFIX = "ctrl_decode"   # ctrl_decode_32 or ctrl_decode_v2_<bits>


class LefExportError(ValueError):
    """The layout does not satisfy the pin contract; nothing was written."""


@dataclass
class PinText:
    """One pin-text occurrence, in bank (top-cell) coordinates."""
    text: str
    layer: str
    x: int
    y: int
    source: str        # "bank" or "band:<cell name>"


@dataclass
class Pin:
    name: str                       # exported name, e.g. ADDRESS[7]
    family: contract.PinFamily
    layer: str
    islands: list = field(default_factory=list)   # island indices on layer
    texts: list = field(default_factory=list)


@dataclass
class Abstract:
    """Everything the writer needs, still in unshifted dbu."""
    macro: str
    spec: BankSpec
    bbox: kdb.Box
    pins: list
    obstructions: dict          # layer name -> list of kdb.Polygon
    islands: dict               # layer name -> IslandIndex

    @property
    def shift(self):
        """(dx, dy) dbu added to every coordinate: bbox lower-left -> 0 0."""
        return -self.bbox.left, -self.bbox.bottom


# ---------------------------------------------------------------------------
# Reading the layout

def _texts_of_cell(layout, cell, trans, recursive, keep, source_of):
    """Pin texts of one cell on the five metal pin-text layers, mapped
    through `trans` into bank coordinates."""
    out = []
    for lname, lnum in METALS:
        li = layout.layer(lnum, PIN_TEXT_DATATYPE)
        if recursive:
            it = cell.begin_shapes_rec(li)
            while not it.at_end():
                sh = it.shape()
                if sh.is_text() and keep(sh.text_string):
                    p = trans * (it.trans() * kdb.Point(
                        sh.text_trans.disp.x, sh.text_trans.disp.y))
                    out.append(PinText(sh.text_string, lname, p.x, p.y,
                                       source_of(it.cell().name)))
                it.next()
        else:
            for sh in cell.shapes(li).each():
                if sh.is_text() and keep(sh.text_string):
                    p = trans * kdb.Point(sh.text_trans.disp.x,
                                          sh.text_trans.disp.y)
                    out.append(PinText(sh.text_string, lname, p.x, p.y,
                                       source_of(cell.name)))
    return out


def collect_pin_texts(layout: kdb.Layout, top: kdb.Cell) -> list:
    """All candidate pin texts: every text the bank cell owns directly,
    plus the external control/address texts anywhere under the one
    decode band instance (contract section 1)."""
    texts = _texts_of_cell(layout, top, kdb.Trans(), False,
                           lambda s: True, lambda c: "bank")
    bands = [inst for inst in top.each_inst()
             if inst.cell.name.startswith(BAND_CELL_PREFIX)]
    if len(bands) != 1:
        raise LefExportError(
            f"{top.name}: expected exactly one decode band instance "
            f"({BAND_CELL_PREFIX}*), found {len(bands)}")
    band = bands[0]
    texts += _texts_of_cell(layout, band.cell, band.trans, True,
                            contract.is_band_signal_text,
                            lambda c: f"band:{c}")
    return texts


def build_islands(layout: kdb.Layout, top: kdb.Cell) -> dict:
    """Merged drawn-metal islands per layer, flattened over the bank."""
    return {lname: IslandIndex(kdb.Region(
                top.begin_shapes_rec(layout.layer(lnum, DRAWN_DATATYPE))))
            for lname, lnum in METALS}


def resolve_pins(texts: list, islands: dict, spec: BankSpec) -> list:
    """Texts -> pins, enforcing every refusal of contract section 6.

    Supplies may own several islands (VDD is two M3 rails, one net: the
    contract says one PIN, one PORT, both rectangles); a signal name must
    own exactly one.  Duplicate labels on the same island (the band's
    copy of the bank's ADDRESS texts at bits = 32) are the same pin.
    """
    pins = {}
    owner = {}                      # (layer, island index) -> pin name
    for t in texts:
        base, index = contract.split_layout_text(t.text)
        family = contract.FAMILY_BY_NAME.get(base)
        if family is None or family.is_bus != (index is not None):
            raise LefExportError(
                f"text '{t.text}' at ({t.x}, {t.y}) on {t.layer} "
                f"({t.source}) is not in the pin contract's vocabulary")
        idx = islands[t.layer].locate(t.x, t.y)
        if idx is None:
            raise LefExportError(
                f"text '{t.text}' at ({t.x}, {t.y}) on {t.layer} "
                f"({t.source}) has no metal beneath it")
        name = contract.export_name(t.text)
        pin = pins.get(name)
        if pin is None:
            pin = pins[name] = Pin(name, family, t.layer)
        key = (t.layer, idx)
        other = owner.get(key)
        if other is not None and other != name:
            raise LefExportError(
                f"pins '{other}' and '{name}' label the same {t.layer} "
                f"polygon (text '{t.text}' at ({t.x}, {t.y}), {t.source})")
        owner[key] = name
        pin.texts.append(t)
        if key not in [(pin.layer, i) for i in pin.islands]:
            if pin.islands and name not in contract.SUPPLY_NAMES:
                raise LefExportError(
                    f"pin '{name}' is labeled on two disconnected polygons "
                    f"({pin.layer} island at {pin.texts[0].x},{pin.texts[0].y}"
                    f" and {t.layer} island at {t.x},{t.y}, {t.source})")
            if t.layer != pin.layer:
                raise LefExportError(
                    f"pin '{name}' is labeled on two layers "
                    f"({pin.layer} and {t.layer})")
            pin.islands.append(idx)
    _check_census(pins, spec)
    return [pins[n] for n in contract.ordered_pin_names(spec)]


def _check_census(pins: dict, spec: BankSpec) -> None:
    """The text census must match the spec bit for bit."""
    expected = set(contract.ordered_pin_names(spec))
    found = set(pins)
    missing, extra = sorted(expected - found), sorted(found - expected)
    if missing or extra:
        a, w = contract.address_width(spec), contract.data_width(spec)
        raise LefExportError(
            f"pin census does not match spec {spec.name} "
            f"(expects {a} address, {w} wd, {w} dataout, 5 sdel): "
            f"missing {missing[:8]}{'...' if len(missing) > 8 else ''}, "
            f"unexpected {extra[:8]}{'...' if len(extra) > 8 else ''}")


def build_abstract(layout: kdb.Layout, top: kdb.Cell,
                   spec: BankSpec) -> Abstract:
    """Read pins and obstructions of a generated bank."""
    islands = build_islands(layout, top)
    pins = resolve_pins(collect_pin_texts(layout, top), islands, spec)
    claimed = {(p.layer, i) for p in pins for i in p.islands}
    obstructions = {
        lname: [poly for i, poly in enumerate(islands[lname].polys)
                if (lname, i) not in claimed]
        for lname, _ in METALS}
    return Abstract(top.name, spec, top.bbox(), pins, obstructions, islands)




# ---------------------------------------------------------------------------
# Shifting and, at 1000 units, rounding

STEP_OF_UNITS = {4000: 1, 1000: 4}       # dbu per LEF (Library Exchange Format) unit


@dataclass
class Shapes:
    """Geometry ready to write: shifted to the macro origin and, at 1000
    units, rounded outward onto the 1 nm grid (module docstring)."""
    units: int
    pins: dict            # pin name -> list of kdb.Polygon
    obstructions: dict    # layer name -> list of kdb.Polygon
    size: kdb.Box         # 0 0 to SIZE
    foreign: tuple        # (dx, dy) dbu written in FOREIGN
    moved: int            # rectangle coordinates the rounding changed


def _boxes(poly: kdb.Polygon) -> list:
    """Rectangles whose union contains the polygon: the polygon itself
    when it is a box, else the bounding box of each trapezoid piece (a
    rectilinear polygon cuts into boxes exactly; a non-rectilinear one,
    none exist here, would be over-covered, which is the safe side)."""
    if poly.is_box():
        return [poly.bbox()]
    return [p.bbox() for p in
            poly.decompose_trapezoids(kdb.Polygon.TD_htrapezoids)]


def _round_outward(box: kdb.Box, step: int) -> kdb.Box:
    """The smallest box on the `step`-dbu grid that contains `box`:
    left and bottom round down, right and top round up."""
    return kdb.Box(box.left // step * step, box.bottom // step * step,
                   -(-box.right // step) * step, -(-box.top // step) * step)


def _round_boxes(polys: list, step: int) -> tuple:
    """Outward-rounded rectangles covering the polygons, and how many of
    their coordinates the rounding moved."""
    out, moved = [], 0
    for poly in polys:
        for b in _boxes(poly):
            r = _round_outward(b, step)
            moved += sum(a != c for a, c in
                         ((b.left, r.left), (b.bottom, r.bottom),
                          (b.right, r.right), (b.top, r.top)))
            out.append(r)
    return out, moved


def prepare_shapes(abstract: Abstract, units: int) -> Shapes:
    """Apply the origin shift; at 1000 units also apply the rounding
    policy of the module docstring: pins and obstructions outward, the
    obstructions merged after rounding and minus the rounded pins, SIZE
    up, FOREIGN to nearest."""
    if units not in UNITS_CHOICES:
        raise LefExportError(f"units must be one of {UNITS_CHOICES}, "
                             f"not {units}")
    step = STEP_OF_UNITS[units]
    dx, dy = abstract.shift
    b = abstract.bbox
    pins, obstructions, moved = {}, {}, 0
    pin_region = {lname: kdb.Region() for lname, _ in METALS}
    for pin in abstract.pins:
        polys = [abstract.islands[pin.layer].polys[i].moved(dx, dy)
                 for i in pin.islands]
        if step == 1:
            pins[pin.name] = polys
            continue
        boxes, n = _round_boxes(polys, step)
        moved += n
        pins[pin.name] = [kdb.Polygon(bx) for bx in boxes]
        for bx in boxes:
            pin_region[pin.layer].insert(bx)
    for lname, _ in METALS:
        polys = [p.moved(dx, dy) for p in abstract.obstructions[lname]]
        if step == 1:
            obstructions[lname] = polys
            continue
        boxes, n = _round_boxes(polys, step)
        moved += n
        region = kdb.Region()
        for bx in boxes:
            region.insert(bx)
        region.merge()                      # touching rectangles: one polygon
        region -= pin_region[lname]         # obstruction and pin disjoint
        obstructions[lname] = list(region.each())
    size = kdb.Box(0, 0, -(-b.width() // step) * step,
                   -(-b.height() // step) * step)
    foreign = (round(dx / step) * step, round(dy / step) * step)
    return Shapes(units, pins, obstructions, size, foreign, moved)


# ---------------------------------------------------------------------------
# Writing

def _um(dbu: int, units: int = DATABASE_MICRONS) -> str:
    """Microns for a dbu count (1 dbu = 0.00025 um).  At 4000 units 5
    decimals are exact; at 1000 units every dbu count handed in is a
    multiple of 4 (prepare_shapes), so 3 decimals are exact as well.
    Trailing zeros dropped for readability."""
    s = f"{dbu / DATABASE_MICRONS:.{DECIMALS_OF_UNITS[units]}f}"
    s = s.rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _rect(box: kdb.Box, indent: str, units: int) -> str:
    return (f"{indent}RECT {_um(box.left, units)} {_um(box.bottom, units)} "
            f"{_um(box.right, units)} {_um(box.top, units)} ;")


def _polygon(poly: kdb.Polygon, indent: str, units: int) -> str:
    pts = " ".join(f"{_um(p.x, units)} {_um(p.y, units)}"
                   for p in poly.each_point_hull())
    return f"{indent}POLYGON {pts} ;"


def _pin_shape_lines(poly: kdb.Polygon, indent: str, units: int) -> list:
    """A pin island as RECTs.  Most islands are boxes; the band-v2 M5
    risers (rail + foot, 8 vertices) are cut into rectangles inside the
    same PORT, which every LEF (Library Exchange Format) consumer treats
    as one connected pin.  A polygon with holes or a non-rectilinear one
    (none exist in this layout) would fall back to POLYGON."""
    if poly.is_box():
        return [_rect(poly.bbox(), indent, units)]
    pieces = poly.decompose_trapezoids(kdb.Polygon.TD_htrapezoids)
    if all(p.is_box() for p in pieces):
        return [_rect(p.bbox(), indent, units) for p in pieces]
    return [_polygon(poly, indent, units)]


def _obs_shape_lines(poly: kdb.Polygon, indent: str, units: int) -> list:
    """An obstruction island exactly as merged: RECT for a box, POLYGON
    otherwise (LEF (Library Exchange Format) 5.6+), rectangles if it has
    holes (a LEF (Library Exchange Format) POLYGON cannot express a
    hole; the exact export has none, measured; the rounded one may
    close a ring)."""
    if poly.is_box():
        return [_rect(poly.bbox(), indent, units)]
    if poly.holes():
        return [_rect(p.bbox(), indent, units) for p in
                poly.decompose_trapezoids(kdb.Polygon.TD_htrapezoids)]
    return [_polygon(poly, indent, units)]


def _rounding_header(shapes: Shapes, abstract: Abstract) -> list:
    """Header comment stating the rounding policy of a 1000-unit file."""
    if shapes.units == DATABASE_MICRONS:
        return []
    dx, dy = abstract.shift
    fx, fy = shapes.foreign
    lines = [
        f"# ROUNDING: DATABASE MICRONS {shapes.units} (1 nm grid) is a "
        "consumer variant of the exact 4000-unit export.",
        "#   After the origin shift every pin and obstruction rectangle "
        "was rounded OUTWARD onto the 1 nm grid",
        "#   (left/bottom down, right/top up, at most 0.75 nm per edge): "
        "no pin is smaller than its metal and no",
        "#   obstruction under-covers metal.  Obstructions were merged "
        "after rounding and the rounded pins subtracted",
        f"#   from them, so OBS (obstruction) and PIN stay disjoint.  "
        f"SIZE rounded up.  "
        f"{shapes.moved} rectangle coordinates moved.",
    ]
    if (fx, fy) != (dx, dy):
        lines.append(f"#   FOREIGN rounded to nearest: shift ({dx}, {dy}) "
                     f"dbu written as ({fx}, {fy}) dbu.")
    return lines


def render_lef(abstract: Abstract, gds_path: str,
               units: int = DATABASE_MICRONS, shapes: Shapes = None) -> str:
    """The LEF (Library Exchange Format) text; `shapes` may be passed in
    when the caller already prepared them (emit_lef does, for its
    manifest)."""
    if shapes is None:
        shapes = prepare_shapes(abstract, units)
    units = shapes.units
    dx, dy = abstract.shift
    fx, fy = shapes.foreign
    b = abstract.bbox
    spec = abstract.spec
    n_texts = sum(len(p.texts) for p in abstract.pins)
    obs_counts = {l: len(v) for l, v in shapes.obstructions.items()}
    lines = [
        "# sramforge LEF (Library Exchange Format) abstract, LEF 5.8",
        f"# macro {abstract.macro}: bits_per_group={spec.bits_per_group} "
        f"strips_per_group={spec.strips_per_group} "
        f"depth_groups={spec.depth_groups}",
        f"# source: GDS (Graphic Design System II) {gds_path}, "
        f"top cell {abstract.macro}, 1 dbu = 0.00025 um",
        "# ORIGIN SHIFT: the bank's GDS bounding box lower-left is "
        f"({b.left}, {b.bottom}) dbu = ({_um(b.left)}, {_um(b.bottom)}) um.",
        f"#   Every coordinate below is the GDS coordinate plus "
        f"({dx}, {dy}) dbu = ({_um(dx)}, {_um(dy)}) um, so the macro's",
        "#   lower-left is 0 0.  FOREIGN carries the same offset: the GDS "
        "cell origin sits at that point of the macro.",
    ] + _rounding_header(shapes, abstract) + [
        f"# pins: {len(abstract.pins)} from {n_texts} pin texts; "
        "obstructions (merged islands): "
        + ", ".join(f"{l} {n}" for l, n in obs_counts.items()),
        f"VERSION {LEF_VERSION} ;",
        'BUSBITCHARS "[]" ;',
        'DIVIDERCHAR "/" ;',
        "UNITS",
        f"  DATABASE MICRONS {units} ;",
        "END UNITS",
        "",
        f"MACRO {abstract.macro}",
        "  CLASS BLOCK ;",
        f"  FOREIGN {abstract.macro} {_um(fx, units)} {_um(fy, units)} ;",
        "  ORIGIN 0 0 ;",
        f"  SIZE {_um(shapes.size.width(), units)} BY "
        f"{_um(shapes.size.height(), units)} ;",
        "  SYMMETRY X Y ;",
    ]
    for pin in abstract.pins:
        lines += [f"  PIN {pin.name}",
                  f"    DIRECTION {pin.family.direction} ;",
                  f"    USE {pin.family.use} ;",
                  "    PORT",
                  f"      LAYER {pin.layer} ;"]
        for poly in shapes.pins[pin.name]:
            lines += _pin_shape_lines(poly, "        ", units)
        lines += ["    END", f"  END {pin.name}"]
    lines.append("  OBS")
    for lname, _ in METALS:
        polys = shapes.obstructions[lname]
        if not polys:
            continue
        lines.append(f"    LAYER {lname} ;")
        for poly in polys:
            lines += _obs_shape_lines(poly, "      ", units)
    lines += ["  END", f"END {abstract.macro}", "", "END LIBRARY", ""]
    return "\n".join(lines)


def emit_lef(gds_path, top_name: str, spec: BankSpec, out_path,
             units: int = DATABASE_MICRONS) -> dict:
    """Read the generated bank from gds_path and write its LEF (Library
    Exchange Format) abstract to out_path.  `units` is 4000 (exact, the
    contract) or 1000 (rounded outward onto 1 nm, module docstring).
    Raises LefExportError (and writes nothing) when the layout breaks
    the pin contract or `units` is not one of the two."""
    if units not in UNITS_CHOICES:
        raise LefExportError(f"units must be one of {UNITS_CHOICES}, "
                             f"not {units}")
    layout = kdb.Layout()
    layout.read(str(gds_path))
    if abs(layout.dbu - LAYOUT_DBU) > 1e-12:
        raise LefExportError(
            f"{gds_path}: dbu is {layout.dbu} um, the exporter's "
            f"DATABASE MICRONS {DATABASE_MICRONS} assumes {LAYOUT_DBU}")
    top = layout.cell(top_name)
    if top is None:
        raise LefExportError(f"{gds_path}: no cell named {top_name}")
    abstract = build_abstract(layout, top, spec)
    shapes = prepare_shapes(abstract, units)
    text = render_lef(abstract, pathlib.Path(gds_path).name, units, shapes)
    out_path = pathlib.Path(out_path)
    out_path.write_text(text)
    dx, dy = abstract.shift
    return {"lef": out_path.name, "macro": abstract.macro,
            "units": units,
            "rounded_coordinates": shapes.moved,
            "pins": len(abstract.pins),
            "pin_texts": sum(len(p.texts) for p in abstract.pins),
            "obstructions": {l: len(v)
                             for l, v in shapes.obstructions.items()},
            "shift_dbu": [dx, dy],
            "size_um": [float(_um(shapes.size.width(), units)),
                        float(_um(shapes.size.height(), units))]}
