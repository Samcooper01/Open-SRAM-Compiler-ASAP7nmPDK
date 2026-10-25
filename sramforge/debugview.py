"""Debug views: one layout per building block, plus an abstract view.

Three emitters, all driven from a generated bank:

* emit_block_gds - writes EVERY building block of the macro (leaf cells
  and composite blocks alike) to its own numbered GDS file with a
  descriptive name, plus a README.txt explaining each one.

* emit_abstract - the whole bank with every library cell reduced to
  outline + name + pin shapes (the industry "abstract"/frame view),
  fully flattened, with outlines on one debug layer PER GROUP so a whole
  class of cells can be shown/hidden by toggling a single layer.

* emit_debug_lyp - the KLayout layer-properties file that renders the
  abstract's groups as filled, labelled tiles.
"""
import re
import pathlib
import klayout.db as kdb

from .generator import BankSpec, build_bank
from .library import CellLibrary
from .tech import DBU

# ---------------------------------------------------------------------------
# Every building block of the macro, smallest to largest.
# (file slug, cell name in the generated layout, one-line description)
BUILDING_BLOCKS = [
    ("01_bitcell_6t",
     "sram_cell_6t_122",
     "The 6T (six-transistor) storage cell: two cross-coupled inverters "
     "+ two access transistors. Stores one bit. 0.163 x 0.350 um drawn."),
    ("02_dummy_bitcell",
     "dummy_sram_6t122",
     "Non-storing copy of the bitcell placed at strip ends so real edge "
     "cells print with identical neighbours."),
    ("03_well_tap_cell",
     "tapcell_sram_6t122",
     "Well tap: ties the wells to the supplies to prevent latch-up; one "
     "per strip end."),
    ("04_dummy_guard_row_a",
     "dummy_topbot_v1",
     "Dummy guard row placed above/below each strip pair (variant A) - "
     "lithographic protection for the array edges."),
    ("05_dummy_guard_row_b",
     "dummy_topbot_v2",
     "Dummy guard row, variant B (alternates with variant A per pair)."),
    ("06_sense_amplifier",
     "senseamp_sram_6t122",
     "Differential sense amplifier: snaps the ~100 mV bitline split to a "
     "full logic level on a read. One per column group = one data bit."),
    ("07_write_driver_latch",
     "write_draslatch_02",
     "Write driver + data latch: forces the selected bitline pair to "
     "strong complementary levels to flip the cell on a write."),
    ("08_precharge_column_mux",
     "wrasst_prech_ymux_x4_sram_6t122_v2",
     "Precharge + 4:1 column multiplexer with write assist: equalises "
     "bitlines to the supply before an access, then selects one of the "
     "four bitline pairs of its flanking array."),
    ("09_output_sr_latch",
     "srlatch_sram_6t122",
     "Set-reset output latch: holds the read data bit until the next "
     "read (the latched-dataout behaviour in the Verilog model)."),
    ("10_edge_filler",
     "FILLER_cgedge",
     "Column-group edge filler closing the left/right boundaries."),
    ("11_bitcell_strip",
     "bitcell_strip_x{bits}",
     "One strip: {bits} bitcells as interleaved mirrored pairs at 0.108 "
     "um pitch + dummy + tap. Electrically ONE bitline column - all "
     "cells share one horizontal bitline pair."),
    ("12_bitcell_array",
     "bitcell_array_x{bits}x{strips}",
     "{strips} strips stacked as mirrored pairs + dummy guard rows: the "
     "storage tile on each side of the io column."),
    ("13_readwrite_io_column",
     "readwrite_io_column",
     "The per-row periphery: precharge/mux on both flanks, write driver, "
     "sense amplifier, output latch. Produces one data bit per row."),
    ("14_column_group",
     "column_group_{bits}b",
     "One full row of the bank: array | io column | mirrored array with "
     "edge fillers. The bank stacks 2 x depth of these."),
    ("15_decode_control_band",
     "ctrl_decode_32",
     "The full-width central band: address decode, word-select drivers "
     "and control/self-timing (81-cell standard-cell subtree)."),
    ("16_top_bottom_filler",
     "FILLER_32x2_top",
     "Bank top filler row (the bottom filler is its mirror complement)."),
]

# Abstract-view groups: one debug layer per class of cell.
GROUPS = {
    "BITCELL":   (1000, 0),
    "DUMMY_TAP": (1001, 0),
    "IO":        (1002, 0),
    "STDCELL":   (1003, 0),
    "DECODER":   (1004, 0),
    "FILLER":    (1005, 0),
}
GROUP_COLORS = {
    "BITCELL": "#2b8b80", "DUMMY_TAP": "#6b7482", "IO": "#2f6db0",
    "STDCELL": "#8e44ad", "DECODER": "#bd7a2b", "FILLER": "#9aa6b5",
}


def classify(cell_name: str) -> str | None:
    """Map a cell name to its abstract group, or None to keep structural."""
    if cell_name == "sram_cell_6t_122":
        return "BITCELL"
    if re.match(r"(dummy|tapcell)", cell_name):
        return "DUMMY_TAP"
    if re.match(r"(senseamp|write_|srlatch|wrasst)", cell_name):
        return "IO"
    if "_ASAP7_75t" in cell_name:
        return "STDCELL"
    if re.match(r"(dec_|io_|TAP_|predecode|post_Decode|control_|decoder_"
                r"|ctrl_decode_edge)", cell_name):
        return "DECODER"
    if cell_name.startswith("FILLER"):
        return "FILLER"
    # Generated hierarchy cells, the top, via cells, the decoder wrapper:
    # keep structural so placement stays intact.
    return None


def _is_kept_in_abstract(layer_info) -> bool:
    """Layers preserved in the abstract view: pins, labels, text."""
    return (layer_info.datatype in (251, 2)
            or (layer_info.layer, layer_info.datatype) == (101, 0))


def emit_block_gds(spec: BankSpec, debug_dir: pathlib.Path) -> list[dict]:
    """Write every building block to its own descriptive GDS file."""
    layout = kdb.Layout(); layout.dbu = DBU
    cell_library = CellLibrary(layout)
    build_bank(layout, cell_library, spec)

    blocks_dir = debug_dir / "blocks"
    blocks_dir.mkdir(parents=True, exist_ok=True)
    fmt = {"bits": spec.bits_per_group, "strips": spec.strips_per_group}

    written = []
    readme_lines = ["sramforge debug blocks - one layout per building block",
                    "=" * 56, ""]
    for file_slug, cell_pattern, description in BUILDING_BLOCKS:
        cell_name = cell_pattern.format(**fmt)
        cell_index = layout.cell_by_name(cell_name)
        if cell_index is None:
            continue
        block_layout = kdb.Layout(); block_layout.dbu = layout.dbu
        block_layout.create_cell(cell_name).copy_tree(layout.cell(cell_index))
        gds_path = blocks_dir / f"{file_slug}.gds"
        block_layout.write(str(gds_path))
        desc = description.format(**fmt)
        written.append({"file": str(gds_path), "cell": cell_name,
                        "description": desc})
        readme_lines += [f"{file_slug}.gds", f"  cell: {cell_name}",
                         f"  {desc}", ""]
    readme_lines += ["View any block with named/colored layers:",
                     "  klayout -l ../../asap7.lyp <block>.gds", ""]
    (blocks_dir / "README.txt").write_text("\n".join(readme_lines))
    return written


def emit_abstract(spec: BankSpec, out_gds: pathlib.Path) -> dict:
    """Generate the bank, then strip it down to the abstract view."""
    layout = kdb.Layout(); layout.dbu = DBU
    cell_library = CellLibrary(layout)
    bank = build_bank(layout, cell_library, spec)

    group_layer_index = {g: layout.layer(*ld) for g, ld in GROUPS.items()}
    cells_abstracted = 0

    for cell in list(layout.each_cell()):
        group = classify(cell.name)
        # Decoder-side cells that still contain children stay structural,
        # so the standard cells INSIDE them get abstracted individually
        # (otherwise clear_insts would orphan them and no per-standard-cell
        # frames would survive).
        if group in ("DECODER", "STDCELL") and cell.child_cells() > 0:
            group = None
        bounding_box = cell.bbox()  # capture BEFORE clearing shapes
        # Strip drawing geometry everywhere; keep pins/labels/text.
        for layer_index in layout.layer_indexes():
            if layer_index in group_layer_index.values():
                continue
            if not _is_kept_in_abstract(layout.get_info(layer_index)):
                cell.shapes(layer_index).clear()
        if group is None:
            continue
        # Abstracted cell: drop its subtree, draw outline + name.
        cell.clear_insts()
        outline_layer = group_layer_index[group]
        if not bounding_box.empty():
            cell.shapes(outline_layer).insert(kdb.Box(bounding_box))
            center = bounding_box.center()
            name_label = kdb.Text(cell.name,
                                  kdb.Trans(kdb.Vector(center.x, center.y)))
            name_label.size = max(40, int(min(bounding_box.width(),
                                              bounding_box.height()) * 0.5))
            try:
                name_label.halign, name_label.valign = 1, 1
            except Exception:
                pass
            cell.shapes(outline_layer).insert(name_label)
        cells_abstracted += 1

    # Remove cells orphaned by clear_insts (e.g. children of abstracted io).
    for orphan in [c for c in layout.each_top_cell()
                   if c != bank.cell_index()]:
        try:
            layout.prune_cell(orphan, -1)
        except Exception:
            pass

    # Flatten completely: the abstract is a DEBUG artifact, and flattening
    # means the filled boxes are visible at ANY hierarchy-depth setting -
    # a viewer can never accidentally see only hollow cell frames.
    bank.flatten(-1, True)

    out_gds.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(out_gds))
    return {"abstract_gds": str(out_gds),
            "cells_abstracted": cells_abstracted,
            "groups": {g: f"{l}/{d}" for g, (l, d) in GROUPS.items()}}


def emit_debug_lyp(lyp_path: pathlib.Path) -> None:
    """Layer-properties file for the abstract view: named group layers
    rendered as filled tiles + pin layers - load with klayout -l."""
    entries = []
    for group, (layer, datatype) in GROUPS.items():
        entries.append(_lyp_entry(f"GRP_{group}", layer, datatype,
                                  GROUP_COLORS[group], "I0", 2,
                                  fill_bright=40))
    for pin_name, layer in [("M1.pin", 19), ("M2.pin", 20), ("M3.pin", 30),
                            ("M4.pin", 40), ("M5.pin", 50), ("well.pin", 1),
                            ("P_SUB.pin", 3), ("V0.pin", 18), ("V1.pin", 21)]:
        entries.append(_lyp_entry(pin_name, layer, 251, "#c0504d", "I0", 1,
                                  fill_bright=32))
    entries.append(_lyp_entry("TEXT", 101, 0, "#e0b25a", "C35", 1))
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n<layer-properties>\n'
           + "\n".join(entries) + "\n</layer-properties>\n")
    lyp_path.write_text(xml)


def _lyp_entry(name, layer, datatype, color, dither, width,
               fill_bright=0, transparent=False) -> str:
    return f"""  <properties>
   <frame-color>{color}</frame-color>
   <fill-color>{color}</fill-color>
   <frame-brightness>0</frame-brightness>
   <fill-brightness>{fill_bright}</fill-brightness>
   <dither-pattern>{dither}</dither-pattern>
   <line-style>C0</line-style>
   <valid>true</valid>
   <visible>true</visible>
   <transparent>{'true' if transparent else 'false'}</transparent>
   <width>{width}</width>
   <marked>false</marked>
   <xfill>false</xfill>
   <animation>0</animation>
   <name>{name} - {layer}/{datatype}</name>
   <source>{layer}/{datatype}@1</source>
  </properties>"""


def emit_debug_views(spec: BankSpec, out_dir: pathlib.Path) -> dict:
    """All debug artifacts: per-block GDS + abstract GDS + layer file."""
    debug_dir = pathlib.Path(out_dir) / "debug"
    blocks = emit_block_gds(spec, debug_dir)
    info = emit_abstract(spec, debug_dir / f"{spec.name}_abstract.gds")
    emit_debug_lyp(debug_dir / "abstract.lyp")
    info.update(blocks=blocks, lyp=str(debug_dir / "abstract.lyp"))
    return info
