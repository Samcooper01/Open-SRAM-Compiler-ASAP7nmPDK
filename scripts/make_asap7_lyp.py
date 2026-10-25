#!/usr/bin/env python3
"""Generate ../../asap7.lyp - KLayout layer properties for ASAP7 layouts.

Uses only KLayout BUILT-IN dither (stipple) patterns, referenced as I<n>:
    I0 solid   I1 hollow   I2 dotted        I3 coarse dots
    I4 left-hatch   I7 right-hatch   I10 cross-hatch  (+ light variants)
Custom "C<n>" pattern references without definitions render HOLLOW - the
bug this script fixes.  Layer/datatype numbers re-derived from the ASAP7
technology library layermap.
"""
import pathlib

# (name, layer, datatype, color, dither, line width, fill brightness)
# Final scheme (v1 base + targeted strengthening):
# device/metal layers = light fills at modest tint; context layers
# (well/implant/fin) = light but VISIBLE tints; vias solid; pins solid.
LAYERS = [
    # ---- front end of line ----
    ("well",      1,   0, "#8a8a8a", "I3", 1, 32),
    ("well.pin",  1, 251, "#8a8a8a", "I1", 1, 0),
    ("P_SUB.pin", 3, 251, "#5a6b7a", "I1", 1, 0),
    ("fin",       2,   0, "#c060c0", "I6", 1, 16),
    ("Gate",      7,   0, "#d40000", "I5", 1, 16),
    ("Dummy",     8,   0, "#9a9a9a", "I3", 1, 48),
    ("GCut",     10,   0, "#7a0000", "I2", 1, 32),
    ("Active",   11,   0, "#00b050", "I2", 1, 16),
    ("Nselect",  12,   0, "#4a86c8", "I3", 1, 40),
    ("Pselect",  13,   0, "#d05050", "I3", 1, 40),
    ("SLVT",     97,   0, "#d8a820", "I3", 1, 48),
    ("LVT",      98,   0, "#e69138", "I3", 1, 48),
    # ---- local interconnect ----
    ("LIG",      16,   0, "#b45f06", "I4", 1, 16),
    ("LISD",     17,   0, "#bf9000", "I7", 1, 16),
    ("SDT",      88,   0, "#7f6000", "I2", 1, 16),
    # ---- vias (solid) ----
    ("V0", 18, 0, "#999999", "I0", 1, 0), ("V1", 21, 0, "#a6a6a6", "I0", 1, 0),
    ("V2", 25, 0, "#8c8c8c", "I0", 1, 0), ("V3", 35, 0, "#b0b0b0", "I0", 1, 0),
    ("V4", 45, 0, "#7f7f7f", "I0", 1, 0), ("V5", 55, 0, "#bdbdbd", "I0", 1, 0),
    ("V6", 65, 0, "#6f6f6f", "I0", 1, 0), ("V7", 75, 0, "#c9c9c9", "I0", 1, 0),
    ("V8", 85, 0, "#606060", "I0", 1, 0), ("V9", 95, 0, "#d5d5d5", "I0", 1, 0),
    # ---- metals: light hatches, bright colors; pins solid ----
    ("M1",      19,   0, "#2e6cff", "I4",  1, 16),
    ("M1.pin",  19, 251, "#2e6cff", "I0",  1, 32),
    ("M2",      20,   0, "#00a0a0", "I7",  1, 16),
    ("M2.pin",  20, 251, "#00a0a0", "I0",  1, 32),
    ("M3",      30,   0, "#e83e8c", "I10", 1, 16),
    ("M3.pin",  30, 251, "#e83e8c", "I0",  1, 32),
    ("M4",      40,   0, "#8e44ad", "I4",  1, 16),
    ("M4.pin",  40, 251, "#8e44ad", "I0",  1, 32),
    ("M5",      50,   0, "#e67e22", "I7",  1, 16),
    ("M5.pin",  50, 251, "#e67e22", "I0",  1, 32),
    ("M6",      60,   0, "#27ae60", "I10", 1, 16),
    ("M7",      70,   0, "#c0392b", "I4",  1, 16),
    ("M8",      80,   0, "#2980b9", "I7",  1, 16),
    ("M9",      90,   0, "#b7950b", "I10", 1, 16),
    # ---- markers / boundary / text ----
    ("SRAMDRC", 99,   0, "#ff00ff", "I1", 2, 0),
    ("SRAMVT", 110,   0, "#00e5ff", "I1", 2, 0),
    ("BOUNDARY", 100, 0, "#888888", "I1", 2, 0),
    ("TEXT",    101,  0, "#c08820", "I0", 1, 0),
    ("DIEAREA", 235,  5, "#ff0000", "I1", 2, 0),
]

ENTRY = """  <properties>
   <frame-color>{color}</frame-color>
   <fill-color>{color}</fill-color>
   <frame-brightness>0</frame-brightness>
   <fill-brightness>{bright}</fill-brightness>
   <dither-pattern>{dither}</dither-pattern>
   <line-style>C0</line-style>
   <valid>true</valid>
   <visible>true</visible>
   <transparent>false</transparent>
   <width>{width}</width>
   <marked>false</marked>
   <xfill>false</xfill>
   <animation>0</animation>
   <name>{name} - {layer}/{dt}</name>
   <source>{layer}/{dt}@1</source>
  </properties>"""

def main() -> None:
    out = (pathlib.Path(__file__).resolve().parent.parent.parent
           / "asap7.lyp")
    body = "\n".join(
        ENTRY.format(name=n, layer=l, dt=d, color=c, dither=p,
                     width=w, bright=b)
        for n, l, d, c, p, w, b in LAYERS)
    out.write_text('<?xml version="1.0" encoding="utf-8"?>\n'
                   '<layer-properties>\n' + body
                   + "\n</layer-properties>\n")
    print(f"wrote {out} with {len(LAYERS)} layers (built-in patterns only)")

if __name__ == "__main__":
    main()
