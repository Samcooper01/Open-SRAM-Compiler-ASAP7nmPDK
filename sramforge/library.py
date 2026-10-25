"""Leaf-cell library: loads the vendored BSD-3 cells into a target layout.

The cells live as individual GDS files under cells/ (see
scripts/extract_cells.py).  A CellLibrary imports each requested cell -
with its full subtree - into the layout being generated, exactly once,
and hands back kdb.Cell handles for placement.
"""
import json
import pathlib
import klayout.db as kdb

CELLS_DIR = pathlib.Path(__file__).resolve().parent.parent / "cells"
# The overlays (the reference's direct per-level shapes: routing straps,
# pin texts, markers) live as an explicit, role-tagged table rather than
# as GDS (Graphic Design System II) blobs: sramforge/overlays.json,
# produced once by scripts/harvest_overlays.py.  Every builder pastes from
# it through the same paste_overlay rules, and the banks generated from
# the table are geometrically identical to those pasted from the files
# (per-layer XOR of six configurations, 2026-09-19).
OVERLAYS_JSON = pathlib.Path(__file__).resolve().parent / "overlays.json"


class CellLibrary:
    def __init__(self, layout: kdb.Layout, cells_dir: pathlib.Path = CELLS_DIR):
        self.layout = layout
        self.cells_dir = pathlib.Path(cells_dir)
        self._cache: dict[str, kdb.Cell] = {}
        self._overlay_table = None
        self._overlay_cells: dict[str, tuple] = {}

    def _overlay_source(self, overlay_name: str):
        """(layout, cell) holding one overlay's shapes, rebuilt from the
        table in table order.  The private layout stays alive in the
        cache for as long as this library does."""
        if overlay_name in self._overlay_cells:
            return self._overlay_cells[overlay_name]
        if self._overlay_table is None:
            self._overlay_table = json.loads(OVERLAYS_JSON.read_text())
        if overlay_name not in self._overlay_table:
            raise KeyError(f"overlay {overlay_name!r} is not in "
                           f"{OVERLAYS_JSON}; run scripts/harvest_overlays.py")
        rec = self._overlay_table[overlay_name]
        src = kdb.Layout()
        src.dbu = rec["dbu"]
        top = src.create_cell(f"overlay_{overlay_name}")
        for s in rec["shapes"]:
            li = src.layer(s["layer"], s["datatype"])
            if "text" in s:
                top.shapes(li).insert(kdb.Text(
                    s["text"], kdb.Trans(kdb.Vector(s["x"], s["y"]))))
            elif "box" in s:
                top.shapes(li).insert(kdb.Box(*s["box"]))
            else:
                top.shapes(li).insert(kdb.Polygon(
                    [kdb.Point(x, y) for x, y in s["points"]]))
        self._overlay_cells[overlay_name] = (src, top)
        return src, top

    def get(self, name: str) -> kdb.Cell:
        """Return the named leaf cell, importing it on first use."""
        if name in self._cache:
            return self._cache[name]
        path = self.cells_dir / f"{name}.gds"
        if not path.exists():
            raise FileNotFoundError(
                f"leaf cell {name!r} not found at {path}. "
                "Run scripts/extract_cells.py first.")
        src = kdb.Layout()
        src.read(str(path))
        if abs(src.dbu - self.layout.dbu) > 1e-12:
            raise ValueError(f"dbu mismatch for {name}: "
                             f"{src.dbu} vs {self.layout.dbu}")
        src_top = src.cell(src.cell_by_name(name))
        dst = self.layout.create_cell(name)
        dst.copy_tree(src_top)
        self._cache[name] = dst
        return dst

    def overlay_shapes(self, overlay_name: str):
        """Yield ((layer, datatype), kdb.Shape) for every direct shape of an
        overlay, texts included, in table order."""
        src, src_top = self._overlay_source(overlay_name)
        for li in src.layer_indexes():
            info = src.get_info(li)
            for sh in src_top.shapes(li).each():
                yield (info.layer, info.datatype), sh

    def paste_overlay(self, target: kdb.Cell, overlay_name: str,
                      text_filter=None, widen=None, heighten=None,
                      repeat_texts=None) -> int:
        """Insert an overlay's direct shapes flat into target.

        Overlays carry the routing straps, pin shapes and marker layers the
        reference bank draws directly on each hierarchy level (extracted by
        scripts/extract_cells.py).  Returns the number of shapes pasted.

        text_filter(layer, datatype, string, x, y) -> bool, when given,
        decides which TEXT shapes are pasted (polygons always paste) -
        used by the sparse-bits scheme to drop dead columns' pin labels.

        heighten (strips > 4): {"dh": extra dbu, "span": (y0, y1),
        "tol": dbu}: every polygon whose bbox spans the reference height
        (bottom <= y0 + tol and top >= y1 - tol) is drawn as its bbox with
        the top raised by dh; nothing else moves.  This is how per-group
        verticals and the SRAM marker follow a taller column group.

        repeat_texts (strips > 4): {"window": (y0, y1), "dy": dbu,
        "steps": n, "rename": f(string, k) -> string or None}: every text
        whose y lies in the window is copied n times at +k*dy with the
        renamed string (None skips a copy).  Per-strip pin labels (the
        io column's bl/bln) get their copies for the extra strips this way.

        """
        src, src_top = self._overlay_source(overlay_name)
        n = 0
        # widen (bits > 32): {"wd": dbu per side,
        #   "left_x": shift shapes wholly left of this by -wd,
        #   "right_x": shift shapes wholly right of this by +wd (optional),
        #   "tile_lo": (x0, x1) one column-pair window replicated outward
        #              LEFT at -864 steps (optional),
        #   "tile_hi": same for the right side, replicated at +864
        #              (optional),
        #   "rename": f(string, k) -> new string for tiled texts (k = tile
        #             step, 1-based); None keeps the string}
        w = widen or {}
        wd = w.get("wd", 0)
        steps = wd // 864 if wd else 0
        for li in src.layer_indexes():
            info = src.get_info(li)
            dli = self.layout.layer(info.layer, info.datatype)
            for sh in src_top.shapes(li).each():
                if sh.is_text():
                    d = sh.text_trans.disp
                    if text_filter is not None and not text_filter(
                            info.layer, info.datatype,
                            sh.text_string, d.x, d.y):
                        continue
                    dx = 0
                    if wd:
                        if d.x <= w.get("left_x", -10**9):
                            dx = -wd
                        elif d.x >= w.get("right_x", 10**9):
                            dx = wd
                    if dx:
                        target.shapes(dli).insert(
                            kdb.Text(sh.text_string,
                                     kdb.Trans(kdb.Vector(d.x + dx, d.y))))
                    else:
                        target.shapes(dli).insert(sh)
                    n += 1
                    if repeat_texts:
                        ry0, ry1 = repeat_texts["window"]
                        if ry0 <= d.y < ry1:
                            rn2 = repeat_texts.get("rename")
                            for k in range(1, repeat_texts["steps"] + 1):
                                name3 = (rn2(sh.text_string, k) if rn2
                                         else sh.text_string)
                                if name3 is None:
                                    continue
                                target.shapes(dli).insert(kdb.Text(
                                    name3, kdb.Trans(kdb.Vector(
                                        d.x + dx,
                                        d.y + k * repeat_texts["dy"]))))
                                n += 1
                    # tiled text copies
                    for (win, sgn) in (("tile_lo", -1), ("tile_hi", 1)):
                        tw = w.get(win)
                        if not tw or not (tw[0] <= d.x < tw[1]):
                            continue
                        rn = w.get("rename")
                        for k in range(1, steps + 1):
                            name2 = rn(sh.text_string, k) if rn else                                 sh.text_string
                            if name2 is None:
                                continue
                            target.shapes(dli).insert(kdb.Text(
                                name2, kdb.Trans(kdb.Vector(
                                    d.x + sgn * k * 864, d.y))))
                            n += 1
                    continue
                b = sh.bbox()
                dx = 0
                if wd:
                    if b.right <= w.get("left_x", -10**9):
                        dx = -wd
                    elif b.left >= w.get("right_x", 10**9):
                        dx = wd
                poly = sh.polygon
                if heighten:
                    hy0, hy1 = heighten["span"]
                    htol = heighten.get("tol", 100)
                    if b.bottom <= hy0 + htol and b.top >= hy1 - htol:
                        poly = kdb.Polygon(kdb.Box(
                            b.left, b.bottom, b.right,
                            b.top + heighten["dh"]))
                target.shapes(dli).insert(
                    poly.transformed(kdb.Trans(kdb.Vector(dx, 0)))
                    if dx else poly)
                n += 1
            # tiled polygon content (region-clipped to the window)
            if steps:
                if (info.layer, info.datatype) in w.get("tile_skip", ()):
                    continue
                for (win, sgn) in (("tile_lo", -1), ("tile_hi", 1)):
                    tw = w.get(win)
                    if not tw:
                        continue
                    reg = kdb.Region()
                    for sh in src_top.shapes(li).each():
                        if not sh.is_text():
                            reg.insert(sh.polygon)
                    reg &= kdb.Region(kdb.Box(tw[0], -10**8, tw[1], 10**8))
                    for k in range(1, steps + 1):
                        for poly in reg.each():
                            target.shapes(dli).insert(poly.transformed(
                                kdb.Trans(kdb.Vector(sgn * k * 864, 0))))
                            n += 1
        return n
