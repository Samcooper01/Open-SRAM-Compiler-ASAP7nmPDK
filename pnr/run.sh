#!/bin/bash
# Run the control-block P&R inside the openroad/orfs container.
set -e
# the parent of the repository, which is what the container mounts
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
docker run --rm -u $(id -u):$(id -g) -v $PROJ:/work -w /work/sramforge \
  -e SRAMFORGE_NETLIST -e SRAMFORGE_PNR_OUT \
  openroad/orfs:latest \
  /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad \
  -no_init -exit pnr/control_pnr.tcl
