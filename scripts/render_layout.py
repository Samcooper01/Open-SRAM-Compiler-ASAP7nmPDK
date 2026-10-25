# Render a generated bank to PNG images for the documentation.
#
# Run under the KLayout binary, which brings the layout view with it:
#   klayout -z -nc -rd gds=<file> -rd top=<cell> -rd out=<dir> \
#           -rd lyp=<asap7.lyp> -r scripts/render_layout.py
#
# Produces a full-bank image and two zooms: the array-to-periphery
# boundary, where the bitcells meet the read/write column, and the
# decode band at the centre of the macro.
import os
import pya

gds = globals().get("gds")
top = globals().get("top")
out = globals().get("out", ".")
lyp = globals().get("lyp")

view = pya.LayoutView()
view.load_layout(gds, 0)
if lyp and os.path.exists(lyp):
    view.load_layer_props(lyp)
view.max_hier()
ly = view.cellview(0).layout()
cell = ly.cell(top) if top else view.cellview(0).cell
view.cellview(0).cell = cell
bb = cell.dbbox()

# (width, height) chosen per shot: the bank is tall and narrow, the
# zooms are landscape
shots = [
    ("bank", bb, 760, 2400),
    # the read/write column sits at the middle of a column group, with
    # bitcell arrays on both sides: this window spans that boundary
    ("periphery", pya.DBox(bb.center().x - 3.0,
                           bb.bottom + bb.height() * 0.62,
                           bb.center().x + 3.0,
                           bb.bottom + bb.height() * 0.62 + 4.5), 1800, 1350),
    # the decode and control band across the middle of the macro
    ("band", pya.DBox(bb.left, bb.center().y - 4.2,
                      bb.right, bb.center().y + 4.2), 1800, 1500),
]
for name, box, w, h in shots:
    view.zoom_box(box)
    path = os.path.join(out, f"{name}.png")
    view.save_image_with_options(path, w, h, 0, 0, 1.0, box, False)
    print(f"wrote {path}")
