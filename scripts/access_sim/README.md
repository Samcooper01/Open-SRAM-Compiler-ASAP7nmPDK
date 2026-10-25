# End-to-end analog access simulation (rung 5)

Simulates a full read access on the EXTRACTED netlist, in two coupled
stages (the monolithic slice defeats ngspice convergence; the split is
clean because the hand-off is the wordline/control boundary):

* `stageA_emit.py` - the decode/control band only (cone from the
  wordline + control labels, truncated at the bitline/sense boundary;
  2,098 devices, pure logic).  Measures the REAL decode timing:
  clk 50% -> wlb<0> rise = **55 ps**; clk -> sense enable = **170 ps**
  (TT, 0.7 V, sdel=all-1).
* `stageB_emit.py` - the column/sense/output slice (cone from
  dataout<0>, truncated at the control nets; 1,654 devices, 256 real
  cells).  Driven by stage A's measured waveforms as PWL sources
  (tails past stage A's end are modeled continuations).  Emits _hi/_lo
  decks differing only in the addressed cells' stored bit; dataout<0>
  must land on that bit (hi -> 700 mV, lo -> 0 mV - demonstrated).

Convergence recipe that works: seed every net with the switch-level
simulator's settled digital state (.ic), preset the sense-amp analog
nodes to their precharged values, gear integration, cshunt=2e-16,
rshunt=1e9.  Discovered architecture facts: dataout<0> IS the sense
buffer output (saob); ONE sense amp serves both halves through an
8-way column mux (yselt/yselb pick strip AND half); an SR (set-reset)
latch (sao/saon) holds dataout between accesses.

Requires: out/refbank_netlist.json (rung 4 dump), tools/ngspice-install,
tools/bsimcmg110.osdi, and a .spiceinit with `osdi <path>` in the cwd.
