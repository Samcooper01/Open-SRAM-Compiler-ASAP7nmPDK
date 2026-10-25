# Liberty characterization (rung 7)

How `scripts/characterize.py` measures a generated bank with ngspice (the
open-source SPICE, Simulation Program with Integrated Circuit Emphasis,
simulator, https://ngspice.sourceforge.io/) and how `sramforge/liberty.py`
turns the record into a Liberty (the Synopsys timing library format,
reference: the Liberty User Guide distributed with the open Liberty parser
kit at https://www.synopsys.com/community/interoperability-programs/tap-in.html)
file.  The pin names, units and corner name are the binding contract of
`docs/pin_interface.md`.

Every number in the `.lib` is a field of `out/char/<bank>_<corner>.json`.
Every field of that record is either read from an ngspice raw file or is an
estimate that names its inputs.  A measurement that does not converge is
stored as `null` with a note and the emitter omits the arc with a comment.

## 1. The measured object and the two stages

The characterization runs on the bank's OWN layout: the bank is generated,
extracted with the rung 4 engine (`sramforge.lvs.extract`, 114,454 devices
at 32,4,32) and dumped in the rung 5 JSON (JavaScript Object Notation)
schema.  A monolithic analog simulation of the whole access path does not
converge (rung 5 finding), so the rung 5 split is reused unchanged:

* **stage A**, the decode/control band: the backward cone from the wordline
  and control labels (`wlb<0>`, `wlb<1>`, `wlt<0>`, `sae`, `saprechn`,
  `wrena`, `wrenan`, `blprechtn`, `blprechbn`, `prechn`, `WLENA`, the eight
  `ysel*` selects and their complements) truncated at the bitline / sense
  boundary; 2,526 devices at 32,4,32.  Inputs are the bank pins (`clk`,
  `banksel`, `read`, `byte_wrenab`, `wd<0>`, `ADDRESS<*>`, `sdel<*>`).
* **stage B**, the column / sense / output-latch slice: the cone from
  `dataout<0>` truncated at the control nets; 1,654 devices, 256 real cells
  (the 8 bitline pairs of one data column, 32 cells each).  Its boundary
  nets are driven by PWL (piecewise-linear) copies of the stage A waveforms
  of the same label.
* a **band slice** (every wordline and control label as a root, the whole
  decode/control band) is used only for the leakage operating point.

Convergence recipe (rung 5, unchanged): every net seeded with the
switch-level simulator's settled digital state as `.ic`, sense-amplifier and
bitline nodes preset to VDD, gear integration, `cshunt=2e-16`,
`rshunt=1e9`.

**Cut loads (new in rung 7).**  A cone truncation removes every device that
hangs on a slice net by its gate only.  In stage A that is 716 fF over 53
nets: `sae` drives 128 twelve-fin sense footers (66 fF), each precharge
line 512 three-fin devices (69 fF), each wordline 512 access gates (37 fF),
each column select 128 three-fin devices (17 fF).  The rung 5 stage A deck
had these drivers unloaded; their sub-picosecond edges are what aborted that
run at 0.6975 ns ("timestep too small"), and the recorded 55 ps / 170 ps
came from the unloaded circuit.  Rung 7 restores every cut gate load as a
capacitor on the slice net: fin count times a per-fin gate capacitance
measured from the model card (section 4).  With the loads in place both
stages converge over two full clock cycles.  Wire capacitance of the
control lines is NOT added to the cut loads (the gate loads dominate them by
an order of magnitude; the M3 (metal 3) wordline flight is a separate rung 5
bench).

## 2. Arcs, one by one

All times are 50% crossings unless stated.  "clk" means the 50% crossing of
the clk pin source.  The stimulus is two read cycles (clk rises at 0.5 and
2.5 ns, falls at 1.5 and 3.5 ns); `ADDRESS<0>` toggles at 2.0 ns so cycle 1
reads column-mux column 0 and cycle 2 reads column 1.

| Arc / value | Stage | How it is measured | Measured or estimated |
|---|---|---|---|
| clk to WL (wordline) | A | clk rise to `wlb<0>` 50% rise; both cycles recorded | measured |
| clk to sae | A | clk rise to `sae` 50% rise | measured |
| clk to precharge release, clk to WLENA, clk to wrena | A | same, for the report | measured |
| WL (wordline) / sae retirement | A | clk FALL to the 50% fall of the net (they are level-controlled by clk) | measured |
| clk to dataout (cell_rise, cell_fall) | A + B | stage B driven by the stage A waveforms of the same clk slew, output load attached; the CYCLE 2 dataout edge (column 1 holds the opposite bit of column 0, so the output latch state entering cycle 2 was produced by cycle 1). cell_rise from the run where column 0 holds 0 and column 1 holds 1; cell_fall from the mirror run | measured |
| rise_transition, fall_transition | B | 10% to 90% of the same cycle 2 dataout edge | measured |
| table axes | | clk input slew {20, 60} ps (10% to 90%, linear ramp) times dataout load {5, 20, 50} fF | stimulus |
| minimum_period | A + B | section 3 | measured, then verified |
| setup / hold of ADDRESS, banksel, read, byte_wrenab | A | bisection on the input's arrival, section 5 | measured |
| setup / hold of wd | A + B | write-then-read stimulus, bisection on wd's arrival, section 5 | measured |
| input pin capacitance | netlist | section 4 | ESTIMATE |
| cell_leakage_power | band + B + cell bench | section 6 | measured slices, extrapolated total |
| area | layout | bank bounding box width times height (um^2) | measured (geometry) |

`dataout[k]` for every k and `wd[k]` for every k carry the arcs measured on
column 0 (`dataout<0>`, `wd<0>`); the columns are identical instances.

## 3. Minimum clock period: definition

The task's definition ("the time from clk rise until the read path has
completed and precharge has restored the bitlines to within 10% of VDD,
plus 20% margin") is applied to what the circuit actually does.  Stage A
shows that the wordline, the precharge release and the sense enable are
LEVEL signals gated by clk high: they retire about 100, 110 and 220 ps
after clk FALLS, whatever the high time.  So the period splits into two
requirements:

* `t_high_min` = clk rise to dataout valid (the 50% crossing of the cycle 2
  dataout edge at 5 fF and 20 ps slew, worst polarity), rounded up to 5 ps.
  The read has completed when the output latch has flipped; dataout is its
  buffered copy, so this is a conservative proxy.
* `t_low_min` = clk fall to BOTH addressed bitlines back at or above 90%
  of VDD, worst over every run of the read table, rounded up to 5 ps.

The candidate period `P = t_high_min + t_low_min` is then RE-SIMULATED:
stage A with clk high exactly `t_high_min` and low exactly `t_low_min` for
two cycles (the address toggling 30 ps after the first fall), then stage B
for both polarities.  The candidate passes when the cycle 2 dataout is
correct and the cycle 1 bitlines are restored before the cycle 2 rise.  If
it fails, `t_low_min` grows by 25% and the check repeats (at most three
attempts); if nothing passes the field is `null`.

`minimum_period = 1.2 x P` (the 20% margin).  `min_pulse_width_high` and
`min_pulse_width_low` carry `t_high_min` and `t_low_min` themselves.

## 4. Estimates: what is NOT simulated

**Per-fin gate capacitance.**  One device of each model card, nfin = 4,
source = drain = its rail; the gate ramped over the full 0 to 0.7 V swing in
1 ns; the gate current integrated; C = Q / VDD / nfin.  This is the
large-signal average gate capacitance per fin of the BSIM-CMG (Berkeley
Short-channel IGFET Model, Common Multi-Gate) card at 0.7 V.  Measured at
TT (typical-typical corner): nmos_rvt 42.9 aF, pmos_rvt 45.1 aF, nmos_sram
35.9 aF, pmos_sram 41.0 aF per fin; linear in nfin (nfin = 1 gives the same
per-fin value).  Analytic cross-check: Cox (3.9 x 8.85e-12 F/m divided by
the 1 nm equivalent oxide) times the per-fin gate area (2 x 32 nm fin height
+ 6.5 nm fin width) x 21 nm gate length gives about 51 aF of intrinsic
capacitance per fin, plus the card's 0.16 nF/m overlap on both sides
(about 23 aF); the ramp measurement averages the depletion and inversion
regimes, hence somewhat below the sum.

**Input pin capacitance** (`pin_capacitance_ff`): for each pin net, the sum
over the devices whose gate is that net of nfin x per-fin cap (from the
extraction: every input pin drives one inverter of 3 + 3 fins, clk drives
16 devices / 24 fins), PLUS wire capacitance = metal length x 0.2 fF/um.
The length is the sum, over every M1 to M5 island of the net in the
extraction's conductor graph, of the island's longer bounding-box side.
0.2 fF/um is the rung 5 figure for ASAP7 (Arizona State University 7 nm
predictive process design kit) 36 nm-pitch wires and is the only
per-length number in the record.  This is an estimate, not a simulation;
no coupling, no via capacitance.

**Cut loads** (section 1): capacitors, no wire.

**Bank leakage total** (section 6): an extrapolation from three simulated
pieces.

## 5. Setup and hold

Stage A, one clock cycle (clk high 0.5 to 1.1 ns, 20 ps slews), the input
edge placed at time t relative to the clk 50% rise; bisection over the
bracket with 5 iterations.

* **Setup**: the input switches from the old to the new value at t
  (negative = before the edge).  PASS when the decode result is correct and
  no later than the stable-input reference plus 10 ps: for an address bit
  the new wordline (`wlb<1>` for ADDRESS<2>, `wlt<0>` for the half bit,
  which is the top address bit: ADDRESS<7> at the reference 32,
  ADDRESS<8> at every band-v2 width, per `sramforge/pins.py`) rises
  within 10 ps of the
  reference and the old wordline stays below 10% of VDD; for the column-mux
  bit ADDRESS<0>, `yselb<1>` is above 90% and `yselb<0>` below 10% of VDD
  from the sae rise to the clk fall; banksel: wordline and sae timely;
  read: sae timely; byte_wrenab (with read = 0): wrena timely against a
  write reference.  `setup = -(latest passing t)`; bracket -320 to +80 ps.
* **Hold**: the input holds the correct value through the edge and switches
  away at t (positive = after the edge); PASS by the same criterion.
  `hold = earliest passing t`; bracket +240 to -80 ps.
* The ADDRESS bus value in the `.lib` is the worst over the three measured
  bits (column, half, mux); the record keeps each bit.
* **Hold is measured, not declared zero**: the address latch is transparent
  while clk is low (the column selects follow an address change 106 ps
  after it, during clk low), so a change right after the edge can leak
  through until the internal clock closes the latch.
* **wd**: stage A is run for a write cycle followed by a read cycle (read
  and byte_wrenab swap at 2.0 ns) and its waveforms drive stage B; the
  addressed cell is preset to the complement of wd; PASS when the cell's
  bitline-side node is at wd before the readback edge AND the cycle 2
  dataout returns wd.  wd's arrival is bisected relative to the cycle 1 clk
  rise with the same brackets.  Setup measured writing 1 into 0; hold with
  wd = 1 held through the edge then dropped.

Resolution: bracket width / 2^5 = 12.5 ps (setup) and 10 ps (hold).  A
result equal to a bracket end is flagged in the record as a bound.

**Convergence ladder.**  A few percent of the stage A decks abort with
ngspice's "timestep too small" when an input toggles while the address
latch is transparent (the trouble nodes reported were the `WLENA` rail and
a source/drain node of the predecoder).  Every deck is therefore run
through an option ladder: the rung 5 options first, then trapezoidal
integration (`method=trap xmu=0.492`) with the SAME tolerances and shunts.
Only a fallback that reproduces the rung 5 timing is allowed: on the same
aborted decks, trapezoidal integration lands the wordline within 3 ps and
the sense enable within 5 ps of the gear runs, whereas a "damped" gear set
(`reltol 5e-3`, `abstol 1e-9`, `vntol 1e-4`, `cshunt 5e-16`) also converges
but moves the wordline by +15 ps and the sense enable by +55 ps (the 2.5x
larger shunt capacitor is a real load on nodes carrying a few fF), so it
is not used.  The option set of every run is in `provenance.runs`, and the
watched edge times of every bisection point are in `search[]`, so a setup
verdict can be checked without re-simulating.  A run that fails the whole
ladder counts as "unknown"
in the bisection: it narrows the bracket pessimistically, and if the
final boundary rests on an unknown the constraint is recorded as `null`
with a note rather than as a number.

## 6. Leakage

Idle: clk 0, banksel 0, read 0, byte_wrenab 0, address 0, sdel all 1.  A
Newton operating point (`.op`) of the 4622-device band did not converge in
30 minutes, so each idle point is reached by a SETTLING TRANSIENT from the
digital state (`.ic`, `uic`, `rshunt = 1e14` so the shunts add only
femtoamps, `abstol = 1e-12` A).  The supply current keeps falling slowly
for tens of nanoseconds: nodes the last access left charged (precharged
bitlines behind off column-mux devices, dynamic decode nodes) leak toward
their true idle level through off transistors, with time constants set by
subthreshold currents.  Measured on the band: 312 nA at 1 ns, 224 at 3,
170 at 10, 155 at 30, 147 at 100, 138 at 300 ns.  The record therefore
stores the DECAY CURVE (`*_detail.curve_a`) and reports the value at the
end of a 100 ns window; if the current still fell more than 10% over the
last decade (30 to 100 ns) the value is `null` with the curve in the note.
The library's `cell_leakage_power` is thus a 100 ns-after-last-access
figure, an upper bound on the true steady-state idle current, and the
record says so.

* `cell_leak_a`: the rung 5 single-cell bench (`sramforge.timing.leakage_bench`),
  one holding 6T (six-transistor) cell, bitlines at VDD.
* `band_slice_a`: the whole decode/control band (every wordline driver,
  the control macro, the delay line).
* `column_slice_a`: one data column (its 256 cells plus the io column:
  precharge, column mux, sense amplifier, write driver, output latch).
* `measured_slices_sum_a` = band + one column, exactly as simulated.
* `bank_total_a` = band + (2 x depth) x (column_slice - 256 x cell)
  + bitcells x cell.  This is the number in `cell_leakage_power`
  (times 0.7 V, in nW); it is an extrapolation and the record says so.

## 7. Corners

The three model cards `spice/models/asap7_{TT,FF,SS}.osdi.sp` (the OSDI,
Open Source Device Interface, conversions of the ASAP7 (Arizona State
University 7 nm predictive process design kit) HSPICE decks) each specify
`tnom = 25` (25 C) on every device and carry NO supply voltage: VDD is the
deck parameter `.param vdd=0.7` that the stage emitter writes, the same at
every corner.  So all three corners are 0.7 V, 25 C and the records and
libraries are named `TT_0p7V_25C`, `FF_0p7V_25C` and `SS_0p7V_25C`
(TT: typical-typical corner, FF: fast-fast corner, SS: slow-slow corner).
No thermal corner exists until the model conversion carries a temperature
sweep.

What the cards change between corners (the `nmos_rvt` header, FF / TT / SS,
fast-fast / typical-typical / slow-slow corner): equivalent oxide thickness `eot` 0.75 / 1.0 / 1.25 nm, fin thickness
`tfin` 7 / 6.5 / 6 nm, fin height `hfin` 34 / 32 / 30 nm, gate-length
offset `xl` -0.5 / +1.0 / +2.5 nm, and the DIBL (Drain-Induced Barrier
Lowering) coefficient `dsub` 0.338 / 0.350 / 0.362.  The fast corner is a
thinner oxide on a taller, wider fin with a shorter gate; the per-fin gate
capacitance the driver re-measures per corner follows it (nmos_rvt 50.3 /
42.9 / 37.5 aF, pmos_rvt 52.6 / 45.1 / 39.7 aF at FF / TT / SS), and so
do the restored cut loads of stage A (836 / 716 / 629 fF) and the estimated
clk pin capacitance (2.38 / 2.20 / 2.07 fF).

`characterize.py --corner FF|SS` runs the identical flow and option ladder
and writes `<bank>_FF.json` / `<bank>_SS.json`, from which
`python3 -m sramforge.liberty --corner FF|SS` emits the corner library;
nothing is tuned per corner.  Results and the corner that did not converge
are in section 9.

## 8. Results: 32,4,32 at TT (typical-typical corner) next to the rung 5 numbers

Bank `sramforge_32bx4x64` (32 bits x 4 strips x depth 32: 256 words x 64
bits, 16,384 cells, 9.612 x 77.804 um = 747.85 um^2), TT (typical-typical
corner), 0.7 V, 25 C.  Record: `out/char/sramforge_32bx4x64_TT.json`.

| Quantity | Rung 7 (this record) | Rung 5 (recorded earlier) | Why they differ |
|---|---|---|---|
| clk to WL (wordline) | 87.4 ps (20 ps clk slew), 94.4 ps (60 ps) | 55 ps | rung 5's stage A drove the wordline and every control net into NOTHING (the cone truncation removed 716 fF of gate load); rung 7 restores those loads (section 1) |
| clk to sae | 204.9 ps (20 ps), 211.0 ps (60 ps) | 170 ps | same: sae alone lost 66 fF of sense-footer gates; the rung 5 run also aborted at 0.6975 ns, just as sae rose |
| clk to precharge release | 109.6 ps | not recorded | new |
| clk to WLENA (wordline enable) | 36.4 ps | not recorded | new |
| clk to wrena (write enable) | 59.8 ps | 40 ps (stage C assumption) | rung 5's stage C used an ASSUMED 40 ps edge; rung 7 measures it |
| WL (wordline) retire after clk fall | 102 ps | (not measured; tails were modeled) | new: the wordline is level-controlled by clk |
| sae retire after clk fall | 222 ps | modeled | new |
| clk to dataout, rising | 243.3 / 254.5 / 275.4 ps at 5 / 20 / 50 fF (20 ps slew); 250.6 / 261.8 / 282.6 (60 ps) | "~195 ps" (page estimate) | the 195 ps was read off the unloaded stage A + B run; rung 7's value carries the restored loads and a real output load |
| clk to dataout, falling | 250.2 / 260.4 / 277.9 ps (20 ps); 257.3 / 267.5 / 285.0 (60 ps) | (same) | same |
| dataout rise / fall transition | 12.3 / 33.7 / 78.0 and 12.8 / 28.5 / 61.7 ps at 5 / 20 / 50 fF | not recorded | new |
| bitline 100 mV split after WL (wordline) | not re-measured | 22 ps | rung 5 column bench, unchanged (bitline pair with real cells; no control loading involved) |
| write flip after WL (wordline) | not re-measured | 22 ps | rung 5 column bench, unchanged |
| wordline flight along the column | not re-measured | 19 ps (depth 8) to 239 ps (depth 64) | rung 5 wordline bench, unchanged; NOT included in clk to WL (wordline) above (stage A measures the driver output at the band) |
| minimum period | t_high 255 + t_low 175 = 430 ps verified; 516 ps with the 20% margin | not recorded | new (section 3) |
| setup, ADDRESS | 45 ps (worst bit: the half select; column bit 7.5 ps, mux bit -17.5 ps) | not recorded | new (section 5) |
| hold, ADDRESS | 30 ps (worst: column and mux bits; half bit 20 ps) | not recorded | measured, not declared zero (the address latch is transparent while clk is low) |
| setup / hold, banksel | 32.5 / -10 ps | not recorded | new |
| setup / hold, read | 32.5 / 20 ps | not recorded | new |
| setup / hold, byte_wrenab | 20 / 10 ps | not recorded | new |
| setup / hold, wd | -17.5 / 40 ps | not recorded | new: wd may arrive after the edge (the write driver is enabled 60 ps after clk) but must stay 40 ps |
| input capacitance, clk | 2.20 fF (1.05 gate + 1.15 wire, ESTIMATE) | not recorded | new |
| input capacitance, ADDRESS / control / sdel | 1.03 to 1.25 fF each (0.26 gate + 0.77 to 0.99 wire, ESTIMATE) | not recorded | new |
| input capacitance, wd | 0.61 fF (0.26 gate + 0.34 wire, ESTIMATE) | not recorded | new |
| cell leakage | 29.6 pA | 29.6 pA | same bench |
| band leakage (4622 devices) | 164 nA at 100 ns after the last access (263 nA at 1 ns, 202 at 10, 171 at 30; still falling 4.4% per decade) | not recorded | new (section 6) |
| one column slice (256 cells + io column) | 14.7 nA at 100 ns (settled within 1.3% per decade); periphery alone 7.1 nA after subtracting 256 cells | not recorded | new |
| bank leakage total | 1.10 uA = 773 nW (band + 64 columns x 7.1 nA + 16,384 cells x 29.6 pA; the cells are 44%, the band 15%, the column periphery 41%) | not recorded | extrapolation, stated in the record |

Run statistics: 106 ngspice runs, 690 s wall on the shared machine, six
stage A decks needed the trapezoidal fallback (all input-toggle decks at
+30 ps or during the transparent phase), none failed.

Notes on the table:

* The 55 / 170 ps rung 5 numbers are NOT wrong measurements of a different
  circuit; they are the same circuit with its control loads missing.  The
  rung 7 numbers supersede them for the library.  The timing-dashboard
  and the timing-read page still show the rung 5 values.
* The wordline flight (rung 5) is a separate physical effect (the M3 (metal
  3) wire along the column) that the stage A cone does not model: stage A
  ends at the band's wordline driver.  For a full-column access add the
  flight for the row farthest from the band; at depth 32 that is 73 ps
  (rung 5 grid), which is already inside the clk-to-sae margin (205 - 87 =
  118 ps) but not by much.  This is a known limitation of the record, not
  a hidden number: `dataout` was measured on the column nearest the band.
* `first_cycle_cell_*` in the record are the cycle 1 dataout edges, which
  start from an artificial `.ic` preset of the output latch; they are kept
  for comparison (within 1.5 ps of the cycle 2 values) and not used.

## 9. Results: 32,4,32 at the three corners

Same bank, same flow, same option ladder; 0.7 V and 25 C at every corner
(section 7).  Records: `out/char/sramforge_32bx4x64_{FF,TT,SS}.json`;
libraries `..._{FF,TT,SS}.lib`.  A `null` is a measurement the driver could
not complete; the emitter omits that arc with a comment.

| Quantity (ps unless stated) | FF (fast-fast corner) | TT (typical-typical corner) | SS (slow-slow corner) |
|---|---|---|---|
| clk to WL (wordline), 20 ps clk slew | null | 87.4 | 100.7 |
| clk to WL (wordline), 60 ps clk slew | null | 94.4 | 108.4 |
| clk to sae, 20 ps | null | 204.9 | 239.0 |
| clk to sae, 60 ps | null | 211.0 | 245.0 |
| clk to precharge release, 20 ps | null | 109.6 | 128.1 |
| clk to WLENA (wordline enable), 20 ps | null | 36.4 | 41.7 |
| clk to wrena (write enable) | null | 59.8 | 68.4 |
| WL (wordline) retire after clk fall | null | 102.0 | 117.4 |
| sae retire after clk fall | null | 221.8 | 260.2 |
| clk to dataout rise, 20 fF, 20 ps slew | null | 254.5 | 299.5 |
| clk to dataout fall, 20 fF, 20 ps slew | null | 260.4 | 306.2 |
| clk to dataout rise, 20 fF, 60 ps slew | null | 261.8 | 306.1 |
| clk to dataout fall, 20 fF, 60 ps slew | null | 267.5 | 312.8 |
| dataout rise transition, 20 fF | null | 33.7 | 42.8 |
| dataout fall transition, 20 fF | null | 28.5 | 34.7 |
| t_high_min | null | 255 | 295 |
| t_low_min | null | 175 | 205 |
| verified period | null | 430 | 500 |
| minimum_period (x 1.2) | null | 516.0 | 600.0 |
| worst setup (bit) | null | 45.0 (ADDRESS<7> (half)) | 45.0 (ADDRESS<7> (half)) |
| setup ADDRESS<2> (column) | null | 7.5 | 7.5 |
| setup ADDRESS<7> (half) | null | 45.0 | 45.0 |
| setup ADDRESS<0> (mux) | null | -17.5 | -17.5 |
| setup banksel | null | 32.5 | 45.0 |
| setup read | null | 32.5 | 45.0 |
| setup byte_wrenab | null | 20.0 | 20.0 |
| setup wd | null | -17.5 | -30.0 |
| hold ADDRESS<2> (column) | null | 30.0 | 30.0 |
| hold ADDRESS<7> (half) | null | 20.0 | 30.0 |
| hold ADDRESS<0> (mux) | null | 30.0 | 30.0 |
| hold banksel | null | -10.0 | -20.0 |
| hold read | null | 20.0 | 30.0 |
| hold byte_wrenab | null | 10.0 | 10.0 |
| hold wd | null | 40.0 | 40.0 |
| bank leakage (nW) | null | 772.7 | null |
| band slice leakage (nA) | null | 163.9 | 93.6 |
| one column slice leakage (nA) | null | 14.69 | null |
| cell leakage (pA) | null | 29.6 | 22.3 |
| ngspice runs / wall time / ladder fallbacks | 8 / 205 s / 2 trap (both aborted) | 106 / 690 s / 6 trap | 100 / 1891 s / 0 |
| per-fin gate capacitance nmos_rvt / pmos_rvt (aF, re-measured per corner) | 50.3 / 52.6 | 42.9 / 45.1 | 37.5 / 39.7 |
| clk pin capacitance (fF, ESTIMATE) | 2.38 | 2.20 | 2.07 |

**FF (fast-fast corner) did not converge in stage A.**  Both stage A read
decks abort with ngspice's "timestep too small" at 0.58 ns, 80 ps after
the clk rise, on node `nm3_626`, under both ladder rungs (rung 5 gear, then
trapezoidal).  `nm3_626` is an unlabeled band node: the output of two
parallel 3-fin inverters that drives two 14-fin nmos footers and 7-fin
pmos gates, so it is one of the sharpest and most heavily loaded control
edges, and at FF (fast-fast corner) the edge is faster while the restored
cut load is larger (836 fF against 716 at TT (typical-typical corner)).
The driver records stage A as null; the read table, minimum period and
setup / hold depend on stage A and are null or absent too, so the FF
(fast-fast corner) library carries the pin census, the pin capacitances,
the area and the leakage only, every timing arc omitted and announced.
A scratch experiment on the aborted deck (numerical options only, no shunt
or tolerance loosening, per section 5's rule) found: gear with reltol
tightened to 1e-3 aborts at the same 0.584 ns on the same node; gear with
`itl4 = 2000` (more Newton iterations per time point) runs 526 s and
aborts at 0.585 ns on the neighbouring node `nm3_3`; gear and trapezoidal
with a 1 ps maximum step were killed by a 240 s scratch timeout before
reaching a verdict.  So the failure is not an iteration-count or
tolerance artefact; it sits in the circuit's fastest control edge at this
corner and a real fix is more likely a finer time step or a wire model on
the control lines than a solver option.  The ladder was not changed,
because no per-corner tuning is allowed unless the option is one the TT
(typical-typical corner) run also uses.

**Ordering FF <= TT <= SS.**  Rung 7 prints, for every quantity present
and non-null in all three records, whether the delays grow from FF
(fast-fast corner) through TT (typical-typical corner) to SS (slow-slow
corner).  With FF (fast-fast corner) timing null, the delay comparison
covers no arc (rung 7 prints "0 delay quantities present in all of FF, TT,
SS", the three corner names); the TT (typical-typical corner) to SS (slow-slow corner) half of the
ordering holds for every stage A edge, every entry of the four clk to
dataout tables (all 24 cells) and the minimum period: SS (slow-slow
corner) is 13 to 18% slower on the control edges and 16 to 18% slower on
clk to dataout, and needs a 500 ps period against 430.  Two CONSTRAINTS
move the other way between TT (typical-typical corner) and SS (slow-slow
corner), and both are the circuit, not noise: wd setup goes from -17.5 to
-30 ps (wd may arrive later because the write enable it must beat arrives
later, 68.4 ps against 59.8), and banksel hold goes from -10 to -20 ps
(the slower internal clock closes the address latch later, so banksel may
release earlier relative to the edge).  The other constraints are equal
(the three address bits, byte_wrenab, wd hold: the bisection step is
12.5 / 10 ps) or larger at SS (slow-slow corner) (banksel and read setup
32.5 to 45 ps, half-bit and read hold 20 to 30 ps).  Leakage orders as
expected where measured: band 164 nA at TT (typical-typical corner) to 93.6 nA at SS
(slow-slow corner), cell 29.6 pA to 22.3 pA.

**SS (slow-slow corner) column leakage is null.**  The column-slice
settling transient (section 6) aborted at 19.6 ps ("timestep too small"
on column node `nm2_10446`) on the leakage option set, which has no ladder
(it is gear only by design, section 6); the band slice and the cell bench
converged, so the record carries `band_slice_a` and `cell_leak_a` and
leaves `column_slice_a` and `bank_total_nw` null with the note, and the
SS (slow-slow corner) library omits `cell_leakage_power` with a comment.
Not tuned, per the corner rule.
