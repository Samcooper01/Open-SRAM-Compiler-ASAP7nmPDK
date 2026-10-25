#!/bin/bash
# Post-P&R pipeline: DEF->GDS, vendor the four one-box artifacts into
# cells/, then regenerate the bits=16 bank and run the rung-2
# connectivity oracle on it.
set -e
cd "$(dirname "$0")/.."
VARIANT=${SRAMFORGE_CTRL_VARIANT:-}
SFX=${VARIANT:+_$VARIANT}
export SRAMFORGE_PNR_OUT=pnr/out$SFX
export SFX

# route-quality gate: a Short violation means the router welded a net
# into an obstruction (real band metal above the die) - the assembled
# bank inherits it as an electrical weld.  Never vendor such a macro.
if grep -q "violation type: Short" $SRAMFORGE_PNR_OUT/route_drc.rpt 2>/dev/null; then
    echo "FATAL: detailed-route Short violations:"
    grep -A2 "violation type: Short" $SRAMFORGE_PNR_OUT/route_drc.rpt
    exit 1
fi
NVIOL=$(grep -c "violation type" $SRAMFORGE_PNR_OUT/route_drc.rpt 2>/dev/null || true)
echo "route violations (non-short): ${NVIOL:-0}"

./pnr/gds.sh

# M5 gate: the macro must contain NO M5 at all - signal routing is
# capped at M4 and pdngen's M5 stripes (whose M1->M5 stack-via towers
# welded crossing feedthroughs: ft_24, wlt<9>) are gone.  Any M5 shape
# here is an unexplained regression.
python3 - <<'EOF'
import klayout.db as kdb
ly = kdb.Layout(); import os; ly.read(os.environ['SRAMFORGE_PNR_OUT'] + '/control_pnr.gds')
top = ly.top_cell()
r = kdb.Region(top.begin_shapes_rec(ly.layer(50, 0))).merged()
boxes = [(p.bbox().left + 33500, p.bbox().right + 33500) for p in r.each()]
assert not boxes, f"macro contains unexpected M5 shapes (ctl): {boxes}"
print("M5 gate OK: macro has no M5")
EOF

python3 - <<'EOF'
import json, pathlib
import os
out = pathlib.Path(os.environ["SRAMFORGE_PNR_OUT"]); cells = pathlib.Path("cells"); sfx = os.environ.get("SFX", "")
# gds
import shutil
shutil.copy(out / "control_pnr.gds", cells / f"control_onebox{sfx}.gds")
# pins + extensions straight through
shutil.copy(out / "pin_map.json", cells / f"control_onebox_pins{sfx}.json")
shutil.copy(out / "extensions.json", cells / f"control_onebox_ext{sfx}.json")
# supply web from the extraction dump
d = json.load(open("out/control_netlist.json"))
(cells / f"control_onebox_supply{sfx}.json").write_text(json.dumps(
    {"straps": d["supply_glue"], "via_cells": d["supply_via_cells"]},
    indent=1))
# io-net glue: the ORIGINAL vertical interface stacks (v3/m4/v4/m5) that
# reconnect the band's control-distribution wires to the port pads
(cells / f"control_onebox_glue{sfx}.json").write_text(json.dumps(d["glue"]))
print("vendored 5 artifacts")
EOF

if [ -n "$VARIANT" ]; then python3 tests/test_connectivity.py --config 32,8,32; else python3 tests/test_connectivity.py --config 16,4,32; fi
