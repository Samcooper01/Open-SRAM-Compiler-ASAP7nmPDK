"""Thin placement helpers over klayout.db.

Orientation codes follow kdb.Trans: "r0", "r90", "r180", "r270" are
rotations; "m0" mirrors about the x axis (y flip), "m90" mirrors about
the y axis (x flip).  All coordinates are integer database units.
"""
import klayout.db as kdb

_ORIENT = {
    "r0": kdb.Trans.R0, "r90": kdb.Trans.R90,
    "r180": kdb.Trans.R180, "r270": kdb.Trans.R270,
    "m0": kdb.Trans.M0, "m45": kdb.Trans.M45,
    "m90": kdb.Trans.M90, "m135": kdb.Trans.M135,
}

def place(parent: kdb.Cell, child: kdb.Cell, x: int, y: int,
          orient: str = "r0") -> kdb.Instance:
    """Place one instance of child in parent at (x, y) dbu."""
    t = kdb.Trans(_ORIENT[orient], kdb.Vector(x, y))
    return parent.insert(kdb.CellInstArray(child.cell_index(), t))

def place_array(parent: kdb.Cell, child: kdb.Cell, x: int, y: int,
                orient: str, na: int, a: tuple[int, int],
                nb: int = 1, b: tuple[int, int] = (0, 0)) -> kdb.Instance:
    """Place a regular na x nb array of child, stepping by vectors a and b."""
    t = kdb.Trans(_ORIENT[orient], kdb.Vector(x, y))
    return parent.insert(kdb.CellInstArray(
        child.cell_index(), t,
        kdb.Vector(*a), kdb.Vector(*b), na, nb))
