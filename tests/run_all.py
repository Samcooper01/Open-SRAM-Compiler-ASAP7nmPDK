#!/usr/bin/env python3
"""Run the whole verification ladder. Exit nonzero on any failure.

  rung 0  golden XOR vs the reference bank   (needs ../asap7_sram_0p0)
  rung 1  structural invariants               (5-config sweep)
  rung 2  connectivity: shorts & opens        (reference config)
  rung 3  DRC deck: golden-clean + mutations  (reference config)
  rung 3b DRC deck: 16 and 64 configs no worse than tests/drc_baselines.json
  rung 4  LVS: devices vs schematic           (bitcell + bank accounting)
  rung 4.4 band decode: per-decoder one-hot   (decode band alone, seconds)
  rung 4.5 functional: switch-level decode    (256-address sweep)
  rung 6  LEF abstract vs layout               (32/16/64 configs, seconds)
  rung 7  Liberty view vs contract + record    (checked-in record, seconds)
"""
import subprocess, sys, pathlib
HERE = pathlib.Path(__file__).parent
rungs = [
    ("rung 0: golden XOR", ["python3", str(HERE/"test_golden.py")]),
    ("rung 1: invariants", ["python3", str(HERE/"test_invariants.py")]),
    ("rung 2: connectivity", ["python3", str(HERE/"test_connectivity.py")]),
    ("rung 3: DRC deck", ["python3", str(HERE/"test_drc.py")]),
    ("rung 3b: DRC non-reference configs vs baseline",
     ["python3", str(HERE/"test_drc_configs.py")]),
    ("rung 4: LVS", ["python3", str(HERE/"test_lvs.py")]),
    ("rung 4.4: band decode", ["python3", str(HERE/"test_band_decode.py")]),
    ("rung 4.5: functional", ["python3", str(HERE/"test_functional.py")]),
    ("rung 6: LEF abstract", ["python3", str(HERE/"test_lef.py")]),
    ("rung 7: Liberty view", ["python3", str(HERE/"test_liberty.py")]),
    ("rung 7.5: sense timing reach", ["python3", str(HERE/"test_sense_timing.py")]),
]
failed = []
for name, cmd in rungs:
    print(f"\n=== {name} ===")
    r = subprocess.run(cmd, cwd=HERE.parent)
    if r.returncode != 0:
        failed.append(name)
print("\n" + ("FAILED: " + ", ".join(failed) if failed else
              "ALL RUNGS PASS"))
sys.exit(1 if failed else 0)
