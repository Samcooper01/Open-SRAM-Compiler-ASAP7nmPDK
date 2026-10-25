# Render "where one word is" onto the real artwork.
#
# Three views of the same generated 2 kilobyte block, each a picture of
# the layout itself rather than a drawing of it: the whole block with
# the word's column of cells marked, four column groups around that
# column, and one six-transistor cell.  The marks are placed from the
# bitcell placements read back out of the GDSII (Graphic Design System
# II), so they land on the artwork by construction and not by eye.
#
#   python3 scripts/render_word.py
import pathlib
import sys

import klayout.db as db
import klayout.lay as lay
from PIL import Image, ImageDraw

REPO = pathlib.Path(__file__).resolve().parent.parent
GDS = REPO / "out" / "sramforge_32bx4x64.gds"      # 32 bits, 4 strips, 32 rows
LYP = REPO.parent / "asap7.lyp"
OUT = REPO / "docs" / "images"
CELL = "sram_cell_6t_122"
COL = 11            # the word's column, counted from the left of the array
PITCH_X, PITCH_Y = 0.108, 0.27
AMBER = (214, 162, 58)


def placements(ly, top):
    """Every bitcell's box in top coordinates, in micrometres."""
    cb = ly.cell(CELL).bbox()
    out = []

    def walk(cell, t):
        for inst in cell.each_inst():
            for it in inst.cell_inst.each_trans():
                tt = t * it
                if inst.cell.name == CELL:
                    out.append(tt * cb)
                else:
                    walk(inst.cell, tt)

    walk(top, db.Trans())
    return [(b.center().x * ly.dbu, b.center().y * ly.dbu) for b in out]


# The cell view keeps only the layers a transistor is made of.  With the
# wiring and the blanket implant and threshold layers left in, a cell at
# this zoom is an unreadable plaid.
CELL_LAYERS = ("Gate", "Active", "LIG", "LISD")
# above the second metal layer the wiring runs across the array and hides
# the cells, which is the whole subject of the group view
GROUP_HIDE = ("M3", "M4", "M5", "M6", "M7", "M8", "M9",
              "V3", "V4", "V5", "V6", "V7", "V8", "V9")


def hide_layers(view, names):
    it = view.begin_layers()
    while not it.at_end():
        lp = it.current()
        lp.visible = lp.name.split(" - ")[0].split(".")[0] not in names
        it.next()


def only_layers(view, names):
    """Visibility has to be written through the iterator's own reference:
    set_layer_properties leaves the view unchanged here."""
    it = view.begin_layers()
    while not it.at_end():
        lp = it.current()
        lp.visible = lp.name.split(" - ")[0].split(".")[0] in names
        it.next()


def render(view, box, w, h, path):
    """Save one window.  The box is given the image's aspect ratio first,
    so the mapping from micrometres to pixels stays linear and known."""
    cx, cy = box.center().x, box.center().y
    bw, bh = box.width(), box.height()
    if bw / bh > w / h:
        bh = bw * h / w
    else:
        bw = bh * w / h
    box = db.DBox(cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
    view.zoom_box(box)
    view.save_image_with_options(str(path), w, h, 0, 0, 1.0, box, False)
    return box


def mapper(box, w, h):
    def to_px(x, y):
        return ((x - box.left) / box.width() * w,
                (box.top - y) / box.height() * h)
    return to_px


def main() -> int:
    if not GDS.exists():
        print(f"missing {GDS}")
        return 1
    ly = db.Layout()
    ly.read(str(GDS))
    top = ly.top_cells()[0]
    pts = placements(ly, top)
    xs = sorted(set(round(x, 4) for x, _ in pts))
    ys = sorted(set(round(y, 4) for _, y in pts))
    left = xs[:len(xs) // 2]
    colx = left[COL]
    print(f"{len(pts)} cells, {len(xs)} columns, {len(ys)} strip rows; "
          f"the word's column is at x = {colx:.4f} um")

    view = lay.LayoutView()
    view.load_layout(str(GDS), 0)
    if LYP.exists():
        view.load_layer_props(str(LYP))
    view.max_hier()
    bb = top.dbbox()

    # ---- the whole block, with the word's column marked
    W, H = 420, 3400
    box = render(view, bb, W, H, OUT / "word_bank.png")
    im = Image.open(OUT / "word_bank.png").convert("RGB")
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    to_px = mapper(box, W, H)
    # the column is 0.108 um wide and the picture is 9.6 um across, so a
    # true-width mark would be a single pixel once the page shows this
    # small: it is drawn about twice its width, and the caption says so
    mid = (to_px(colx, 0)[0])
    x0, x1 = mid - 6, mid + 6
    gap = next(i for i in range(len(ys) - 1) if ys[i + 1] - ys[i] > 1.0)
    for lo, hi in ((ys[0], ys[gap]), (ys[gap + 1], ys[-1])):
        _, ytop = to_px(0, hi + PITCH_Y / 2)
        _, ybot = to_px(0, lo - PITCH_Y / 2)
        d.rectangle([x0, ytop, x1, ybot], fill=AMBER + (120,),
                    outline=AMBER + (255,), width=2)
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    im.save(OUT / "word_bank.png")

    # ---- one whole column group: both arrays and the read and write
    # column they share, with a row of each neighbour above and below so
    # the group's own boundaries are visible
    hide_layers(view, GROUP_HIDE)
    gi = ys.index(ys[len(ys) // 2 + 6])
    gi -= gi % 4                                  # the group's bottom row
    gtop, gbot = ys[gi + 3] + PITCH_Y / 2, ys[gi] - PITCH_Y / 2
    cellx, celly = colx, ys[gi + 1]               # the word's cell in it
    W, H = 1720, 290
    box = render(view, db.DBox(bb.left, gbot - PITCH_Y, bb.right,
                               gtop + PITCH_Y), W, H, OUT / "word_group.png")
    im = Image.open(OUT / "word_group.png").convert("RGB")
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    to_px = mapper(box, W, H)
    for yb in (gtop, gbot):                       # where this group ends
        _, yy = to_px(0, yb)
        d.line([0, yy, W, yy], fill=(255, 255, 255, 150), width=3)
    x0, _ = to_px(colx - PITCH_X / 2, 0)
    x1, _ = to_px(colx + PITCH_X / 2, 0)
    d.rectangle([x0 - 2, 0, x1 + 2, H], fill=AMBER + (70,),
                outline=AMBER + (235,), width=2)
    a = to_px(cellx - PITCH_X / 2, celly + PITCH_Y / 2)
    b = to_px(cellx + PITCH_X / 2, celly - PITCH_Y / 2)
    d.rectangle([a[0] - 1, a[1] - 1, b[0] + 1, b[1] + 1],
                outline=AMBER + (255,), width=3)
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    im.save(OUT / "word_group.png")
    print(f"group window x {box.left:.3f}..{box.right:.3f} um, "
          f"y {box.bottom:.4f}..{box.top:.4f}; the word's cell is row "
          f"{(gi + 1) % 4} of the group, column {COL}")

    # ---- one cell
    only_layers(view, CELL_LAYERS)
    W, H = 760, 760
    box = render(view, db.DBox(cellx - 0.21, celly - 0.21,
                               cellx + 0.21, celly + 0.21),
                 W, H, OUT / "word_cell.png")
    im = Image.open(OUT / "word_cell.png").convert("RGB")
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    to_px = mapper(box, W, H)
    a = to_px(cellx - PITCH_X / 2, celly + PITCH_Y / 2)
    b = to_px(cellx + PITCH_X / 2, celly - PITCH_Y / 2)
    d.rectangle([a[0], a[1], b[0], b[1]], outline=AMBER + (255,), width=4)
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    im.save(OUT / "word_cell.png")

    # a layout view is flat colour, so a palette costs nothing visible
    # and saves about two thirds of the page weight
    for n in ("word_bank.png", "word_group.png", "word_cell.png"):
        p = OUT / n
        Image.open(p).convert("RGB").quantize(
            colors=255, method=Image.MAXCOVERAGE).save(p, optimize=True)
        print(f"wrote {p} ({p.stat().st_size // 1024} kilobytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
