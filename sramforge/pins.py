"""The pin contract (docs/pin_interface.md) in code.

Every exporter of a bank's interface, the LEF (Library Exchange Format)
abstract, the Verilog model and later the Liberty view, derives its pin
list from the rules here, never from a hand-kept table of its own.  The
layout stays the authority on WHERE the pins are; this module fixes only
the vocabulary (which names are external), the name conversion, the
direction/use of each family, and the census a spec demands, so that the
three views cannot drift apart.

Nothing here reads a layout.  The layout-facing half lives in lef.py.
"""
import math
import re
from dataclasses import dataclass

from .generator import BankSpec

# Layout texts write bus bits as NAME<n>; the export writes NAME[n].
_LAYOUT_BUS = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)<(\d+)>$")
# Supply texts carry a layout-only "!" and export under the plain names.
SUPPLY_TEXTS = {"vdd!": "VDD", "vss!": "VSS"}


@dataclass(frozen=True)
class PinFamily:
    """One row of the contract's pin table (section 3)."""
    name: str          # exported base name (bus families without [n])
    direction: str     # LEF (Library Exchange Format) DIRECTION keyword
    use: str           # LEF (Library Exchange Format) USE keyword
    is_bus: bool
    layer: str         # layer the contract says the texts sit on


# Order = the contract table's order = the order pins are written.
FAMILIES = (
    PinFamily("ADDRESS", "INPUT", "SIGNAL", True, "M5"),
    PinFamily("clk", "INPUT", "CLOCK", False, "M5"),
    PinFamily("banksel", "INPUT", "SIGNAL", False, "M5"),
    PinFamily("read", "INPUT", "SIGNAL", False, "M5"),
    PinFamily("byte_wrenab", "INPUT", "SIGNAL", False, "M5"),
    PinFamily("sdel", "INPUT", "SIGNAL", True, "M5"),
    PinFamily("wd", "INPUT", "SIGNAL", True, "M3"),
    PinFamily("dataout", "OUTPUT", "SIGNAL", True, "M3"),
    PinFamily("VDD", "INOUT", "POWER", False, "M3"),
    PinFamily("VSS", "INOUT", "GROUND", False, "M3"),
)
FAMILY_BY_NAME = {f.name: f for f in FAMILIES}
SUPPLY_NAMES = frozenset(SUPPLY_TEXTS.values())
# Families whose texts may legitimately be found in the decode band cell
# (the contract's reason for reading the band at all is ADDRESS<8>).  The
# band hierarchy also carries thousands of standard-cell pin texts (A, Y,
# B, CLK, VDD, VSS, wl<n>, ...); those are internal and never exported.
BAND_SIGNAL_FAMILIES = frozenset(
    f.name for f in FAMILIES if f.layer == "M5")


# ---------------------------------------------------------------------------
# Widths, derived from the spec exactly as the layout builds them

def addressable_strips(spec: BankSpec) -> int:
    """Strips the control block can select: all of them.  At strips = 4
    the placed control exports four column-select lines; at strips = 8
    the bank places the eight-strip control variant (generator.control_
    variant, band v2 "s8"), whose 3:8 column-select decode drives the
    mux legs 4..7 through eight more select lines."""
    return spec.strips_per_group


def mux_bits(spec: BankSpec) -> int:
    """Column-mux select width: log2(strips); 2 at four strips, 3 at
    eight (ADDRESS[1:0] plus ADDRESS[7], see column_bits)."""
    return max(1, math.ceil(math.log2(addressable_strips(spec))))


def column_bits(spec: BankSpec) -> int:
    """Wordline-column select width, as the placed decode band decodes it.

    The reference band (bits = 32, strips = 4, depth <= 48: ctrl_decode_32)
    decodes 5 column bits.  Every other configuration places the band-v2
    control (ctrl_decode_v2_<bits>; generator.uses_band_v2, including
    the four-strip banks deeper than 48 rows, whose sense timing the
    reference delay line cannot reach),
    whose measured map is 6 column bits + half in ADDRESS[8] at EVERY
    bits, including 16: rung 4.5 sweeps the 9-bit map there and the
    layout carries nine ADDRESS texts.  docs/pin_interface.md section 3
    says "8 at bits <= 32"; that is true only of the reference band and
    the exporters follow the layout (see docs/lef_export.md).  Column
    codes >= bits are dead (sparse-bits: no cells, no driver).

    At strips = 8 (the "s8" control variant, bits = 32 only) ADDRESS[7],
    the sixth column bit of the band-v2 map (idle at 32 columns), is
    the third column-mux bit instead: 5 column bits, 3 mux bits, half
    in ADDRESS[8], nine address bits and all 512 words live.
    """
    from .generator import uses_band_v2
    if spec.strips_per_group == 8:
        return 5
    return 6 if uses_band_v2(spec) else 5


def address_width(spec: BankSpec) -> int:
    """a = mux + column + 1 half-select bit: 8 at the reference, 9 for
    every band-v2 width."""
    return mux_bits(spec) + column_bits(spec) + 1


def data_width(spec: BankSpec) -> int:
    """w = 2 * depth: one bit per column-group row, both halves."""
    return 2 * spec.depth_groups


def word_count(spec: BankSpec) -> int:
    """Words actually implemented: 2 * bits * strips.  Below 64 bits on
    the band-v2 frame (and below 32 on the reference frame) this is fewer
    than 2**address_width: the sparse-bits scheme keeps the decode frame
    and leaves the column codes >= bits dead."""
    return 2 * spec.bits_per_group * addressable_strips(spec)


def expected_census(spec: BankSpec) -> dict:
    """Exported base name -> number of bits (None for a scalar pin)."""
    a, w = address_width(spec), data_width(spec)
    return {"ADDRESS": a, "clk": None, "banksel": None, "read": None,
            "byte_wrenab": None, "sdel": 5, "wd": w, "dataout": w,
            "VDD": None, "VSS": None}


# ---------------------------------------------------------------------------
# Names

def split_layout_text(text: str):
    """Layout text -> (exported base name, bit index or None).

    'ADDRESS<7>' -> ('ADDRESS', 7); 'vdd!' -> ('VDD', None); 'clk' ->
    ('clk', None).  Purely syntactic: nothing is renamed by hand.
    """
    if text in SUPPLY_TEXTS:
        return SUPPLY_TEXTS[text], None
    m = _LAYOUT_BUS.match(text)
    if m:
        return m.group(1), int(m.group(2))
    return text, None


def export_name(text: str) -> str:
    """'ADDRESS<7>' -> 'ADDRESS[7]', 'vdd!' -> 'VDD', 'clk' -> 'clk'."""
    base, index = split_layout_text(text)
    return base if index is None else f"{base}[{index}]"


def is_band_signal_text(text: str) -> bool:
    """True for a band-cell text that the contract exports (an external
    control/address label); False for the band's internal texts."""
    base, _ = split_layout_text(text)
    return base in BAND_SIGNAL_FAMILIES


def ordered_pin_names(spec: BankSpec) -> list:
    """Every exported pin name, in contract-table order, bus bits
    ascending: the order the LEF (Library Exchange Format) writes them
    and the order the Verilog port list follows."""
    names = []
    for family in FAMILIES:
        n = expected_census(spec)[family.name]
        if n is None:
            names.append(family.name)
        else:
            names.extend(f"{family.name}[{i}]" for i in range(n))
    return names
