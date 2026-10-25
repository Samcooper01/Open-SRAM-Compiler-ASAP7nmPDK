# Render the central band of two generated blocks side by side, at the
# same scale: the reference 2 kilobyte block, whose band is the kit's
# own hand-packed control, and a 16-bit block, whose band the compiler
# rebuilds (a generated decoder above, and below it the control logic
# recovered from the kit's layout, edited, then placed and routed again
# by OpenROAD).  The control logic is outlined in both, from instance
# boxes read back out of the GDSII (Graphic Design System II).
#
#   python3 scripts/render_bands.py
import pathlib
import subprocess
import sys
import tempfile

import klayout.db as db
import klayout.lay as lay
from PIL import Image, ImageDraw

REPO = pathlib.Path(__file__).resolve().parent.parent
LYP = REPO.parent / "asap7.lyp"
OUT = REPO / "docs" / "images"
PPU = 90                    # pixels per micrometre, the same for both
AMBER = (214, 162, 58)
# the upper metals run across the whole band and hide the cell rows,
# which are the subject here
HIDE = ("M4", "M5", "M6", "M7", "M8", "M9", "V4", "V5", "V6", "V7", "V8", "V9")
# (image name, generator arguments, top cell, band cell, control cell)
SHOTS = [
    ("band_kit.png", [], "sramforge_32bx4x64", "ctrl_decode_32",
     "control_sram_2kB_6t122_v3_orig10242022_lvspass_32"),
    ("band_rebuilt.png", ["--bits", "16"], "sramforge_16bx4x64",
     "ctrl_decode_v2_16", "control_onebox"),
]


def placed(top, band, name):
    """Box of the named instance inside the band, in top coordinates."""
    for bi in top.each_inst():
        if bi.cell.name != band:
            continue
        for ci in bi.cell.each_inst():
            if ci.cell.name == name:
                return ci.dbbox().transformed(bi.dcplx_trans)
    raise LookupError(name)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        for img, args, topname, band, ctrl in SHOTS:
            subprocess.run([sys.executable, "-m", "sramforge", "--out", tmp]
                           + args, cwd=REPO, check=True,
                           stdout=subprocess.DEVNULL)
            gds = pathlib.Path(tmp) / f"{topname}.gds"
            ly = db.Layout()
            ly.read(str(gds))
            top = ly.cell(topname)
            bandbox = next(i.dbbox() for i in top.each_inst()
                           if i.cell.name == band)
            cbox = placed(top, band, ctrl)

            view = lay.LayoutView()
            view.load_layout(str(gds), 0)
            if LYP.exists():
                view.load_layer_props(str(LYP))
            view.max_hier()
            view.cellview(0).cell = top
            it = view.begin_layers()
            while not it.at_end():
                lp = it.current()
                lp.visible = lp.name.split(" - ")[0].split(".")[0] not in HIDE
                it.next()
            box = db.DBox(0, bandbox.bottom, top.dbbox().right, bandbox.top)
            w, h = round(box.width() * PPU), round(box.height() * PPU)
            path = OUT / img
            view.zoom_box(box)
            view.save_image_with_options(str(path), w, h, 0, 0, 1.0, box, False)

            im = Image.open(path).convert("RGB")
            d = ImageDraw.Draw(im)
            x0 = (max(cbox.left, box.left) - box.left) * PPU
            x1 = (min(cbox.right, box.right) - box.left) * PPU
            y0 = (box.top - min(cbox.top, box.top)) * PPU
            y1 = (box.top - max(cbox.bottom, box.bottom)) * PPU
            d.rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], outline=AMBER, width=5)
            im.quantize(colors=255, method=Image.MAXCOVERAGE).save(path, optimize=True)
            print(f"wrote {path}: band {box.height():.3f} um tall, control "
                  f"y {cbox.bottom:.3f}..{cbox.top:.3f} "
                  f"({path.stat().st_size // 1024} kilobytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
