#!/bin/bash
# DEF -> GDS for the control block (uses the pre-generated klayout.lyt).
set -e
# the parent of the repository, which is what the container mounts
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT=${SRAMFORGE_PNR_OUT:-pnr/out}
rm -f $PROJ/sramforge/$OUT/control_pnr.gds
docker run --rm -u $(id -u):$(id -g) -v $PROJ:/work -w /work/sramforge \
  -e OUT=$OUT openroad/orfs:latest bash -c '
P=/OpenROAD-flow-scripts/flow/platforms/asap7
U=/OpenROAD-flow-scripts/flow/util
klayout -zz \
  -rd design_name=control_sram_2kB \
  -rd in_def=$OUT/control_pnr.def \
  -rd in_files="$P/gds/asap7sc7p5t_28_R_220121a.gds $P/gds/asap7sc7p5t_28_SRAM_220121a.gds" \
  -rd seal_file= \
  -rd out_file=$OUT/control_pnr.gds \
  -rd tech_file=$OUT/obj/klayout.lyt \
  -rd layer_map= -rd config_file= \
  -r $U/def2stream.py'
ls -la $PROJ/sramforge/$OUT/control_pnr.gds
