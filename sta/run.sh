#!/bin/bash
# Static timing analysis of a small design that instantiates the
# generated macro, driven by the macro's OWN generated timing library.
set -e
cd "$(dirname "$0")/.."
python3 sta/make_wrapper.py
# the macro abstract this run links against, in the 1000-unit variant the
# ASAP7 technology file requires (docs/lef_export.md)
if [ ! -f sta/sramforge_32bx4x64.lef ]; then
  python3 -m sramforge --bits 32 --strips 4 --depth 32 \
      --out sta/_gen --lef --lef-units 1000 >/dev/null
  cp sta/_gen/sramforge_32bx4x64.lef sta/sramforge_32bx4x64.lef
  rm -rf sta/_gen
fi
# the parent of the repository, which is what the container mounts
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
docker run --rm -u $(id -u):$(id -g) -v $PROJ:/work -w /work/sramforge \
  -e STA_PERIOD openroad/orfs:latest \
  /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad \
  -no_init -exit sta/run_sta.tcl
