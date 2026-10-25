# External sign-off: independent DRC (Design Rule Check) and LVS (Layout Versus Schematic)

The in-house rung 3 DRC (Design Rule Check) deck (`sramforge/drc.py`) and rung 4
LVS (Layout Versus Schematic) extraction (`sramforge/lvs.py`) were written from
the ASAP7 (Arizona State University 7 nm predictive Process Design Kit) DRM
(Design Rule Manual) by the same people who wrote the generator, and the DRC
(Design Rule Check) floors were calibrated to the golden reference bank.  This
document records the first corroboration of both rungs by tools and rule
transcriptions written by somebody else, what agrees, what does not and why,
and how much the external run actually proves.

Everything here is reproducible with three scripts and one design record:

| file | purpose |
|---|---|
| `scripts/external_drc.py` | run the external deck (flat + hierarchical), per-rule count report |
| `scripts/reconcile_drc.py` | rule-by-rule table against the in-house rung 3 deck |
| `scripts/external_lvs.py` | KLayout netlist extraction, device census and net degrees against rung 4 |

## 1. Tools and decks

| tool | version | role |
|---|---|---|
| [KLayout](https://www.klayout.de/) standalone binary | 0.30.7 (`/usr/bin/klayout`) | runs the external DRC (Design Rule Check) deck (Ruby DSL (Domain Specific Language) macro) |
| KLayout pip module (`klayout.db`, `klayout.rdb`) | 0.30.9 | reads the marker database, flattens layouts, runs LayoutToNetlist extraction |
| [laurentc2/ASAP7_for_KLayout](https://github.com/laurentc2/ASAP7_for_KLayout) | commit ef77f08, BSD-2-Clause | the external DRC (Design Rule Check) deck `drc/drc_ASAP7.lydrc` (541 lines, 258 output categories) |
| [OpenROAD-flow-scripts](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts) `flow/platforms/asap7/drc/asap7.lydrc` | local checkout | fallback: a re-licensed copy of the same deck (see below) |
| [ASAP7 (Arizona State University 7 nm predictive Process Design Kit) r1p7](https://github.com/The-OpenROAD-Project/asap7) DRM (Design Rule Manual) `asap7_drm.pdf` | release 1p7 | the arbiter for rule ids and values where the two decks disagree |
| [asap7_sram_0p0](https://github.com/The-OpenROAD-Project/asap7sram) `srambank_32b.gds` | sibling checkout | the golden reference bank |

### Why this deck

The survey found exactly one open ASAP7 (Arizona State University 7 nm
predictive Process Design Kit) DRC (Design Rule Check) deck for KLayout, in two
copies.  A byte-level diff shows the OpenROAD-flow-scripts file is the laurentc2
deck (its header says "Based on work by @laurentc2") with renamed input
variables, `threads(8)` instead of `threads(4)`, the timing footer removed, and
one regression: its M1.S.2 category label is the empty string.  The upstream
original is therefore used and the OpenROAD-flow-scripts copy accepted as a
fallback path.  The official Calibre decks are not an option:
`asap7_pdk_r1p7/calibre/ruledirs/*/README.txt` says they must be downloaded
separately from asap.asu.edu, and they are encrypted SVRF (Standard Verification
Rule Format), which is Siemens IP (Intellectual Property).

No open ASAP7 (Arizona State University 7 nm predictive Process Design Kit) LVS
(Layout Versus Schematic) deck exists: the laurentc2 README mentions an `lvs`
directory that is not in the repository, its `asap7.lyt` connectivity is a
metal-only stub whose front-end layer numbers are not the ASAP7 (Arizona State
University 7 nm predictive Process Design Kit) ones, and there is no ASAP7
(Arizona State University 7 nm predictive Process Design Kit) technology file
for Magic, so Magic/Netgen could not be used for extraction.  Section 4 explains what was done instead.

### Why the KLayout binary and a flattened layout

The deck is a KLayout DRC (Design Rule Check) macro in the Ruby DSL (Domain
Specific Language); the Ruby interpreter and the DRC (Design Rule Check) engine
exist only in the full application, not in the pip module.  So the deck runs as

    klayout -b -r deck.lydrc -rd input=bank.oas -rd topcell=TOP -rd output=TOP.lyrdb

and the pip module reads the resulting `.lyrdb` marker database.

The deck runs in `deep` (hierarchical) mode and reports each violation once per
CELL in cell-local coordinates (2333 markers on the 16,4,32 bank, of which one
marker inside `sram_cell_6t_122` stands for thousands of instances).  Our rung 3
deck runs flat and counts every instance.  `external_drc.py` therefore flattens
the top cell into a single-cell OASIS (Open Artwork System Interchange Standard)
file first (1.13 M shapes for the 16 bank, 1.90 M for the golden), which makes
counts and coordinates directly comparable and guarantees both decks see the
same merged geometry.  One rule does not survive flattening: ACTIVE.LUP.1
(latch-up distance) sizes all device ACTIVE by 30 um and merges it; on a flat
bank the merged blob has millions of vertices and the rule ran more than 15
minutes before being stopped (20 s hierarchically).  The flat run comments out
that one rule and a companion hierarchical run of the complete deck supplies
its count (per cell) and cross-checks the fired rule set.  ACTIVE.LUP.1 fired
nothing on any bank in the hierarchical runs, and the hierarchical runs fire
exactly the same rule set as the flat runs (62 rules on the 16 bank, 49 on the
golden and the 32 bank, 67 on the 64 bank), so flattening changed counts, not
findings.

### Defects found in the external deck

Reading and running the deck against the DRM (Design Rule Manual) turned up
the following.  Label defects are handled by normalizing category names when
the report is read, plus one label patch applied to a COPY of the deck before
running (`PATCHES` in `external_drc.py`); no rule geometry is changed.
Semantic defects are recorded as `ext-deck-bug` in the reconciliation and their
counts are not taken as evidence either way.

Label defects:

* 17 `output("X" "X : ...")` calls lack the comma between category and
  description, so Ruby concatenates the two strings and the category is named
  `V1.AUX.1V1.AUX.1 : V1 must ...`.
* LIG.S.2 is written into the `LISD.S.2` category (collision with the real
  LISD.S.2).  Patched to its own category.
* GATE.S.2 and GATE.S.3 are swapped relative to DRM (Design Rule Manual) table
  3.4.1 (GATE.S.2 is the 34 nm minimum spacing, GATE.S.3 the "another GATE
  within 54 nm" rule).  LIG.GATE.S.9A/9B are labeled LIG.LISD.S.9A/9B,
  SRAM.ACTIVE.A.2A is labeled SRAM.ACTIVE.A.1A, SRAM.LIG.GATE.A.4 is labeled
  LIG.GATE.A.4.  Our deck follows the DRM (Design Rule Manual) ids; the
  reconciliation maps them.

Semantic defects:

* LIG.LISD.A.2, LIG.GATE.A.3, LIG.GATE.A.4, LIG.LISD.OV.1 and
  SRAM.LIG.GATE.OV.2 take the UNION (`+`) of the two layers where the DRM
  (Design Rule Manual) says area or width of the OVERLAP.  They measure whole
  polygons, not overlaps.
* ACTIVE.W.2 and SDT.W.3 ("height is an integer multiple of 27 nm") are coded
  as "bounding-box height is none of k x 27 nm for k = 1..12", which flags every
  merged polygon taller than 324 nm and, for SDT, every 17 nm SRAM (Static
  Random-Access Memory) source/drain contact that the DRM (Design Rule Manual)
  explicitly allows through SRAM.SDT.W.4.  SDT.W.3 alone produces 103,780 of the
  golden's 107,544 external markers.
* The net-conditional spacings (LIG.LISD.S.6/S.7, LIG.SDT.S.8, ACTIVE.S.2A,
  "not on the same net") are checked without the net condition, so same-net
  geometry is flagged.
* GCUT.GATE.S.2 measures GCUT to every gate including the one it cuts (1 nm
  slivers).

## 2. How to run

    # one bank: generate, flatten, external deck (flat + hierarchical), report
    python3 scripts/external_drc.py --spec 32,4,32
    python3 scripts/external_drc.py --golden
    python3 scripts/external_drc.py out/external_signoff/sramforge_16bx4x64.gds sramforge_16bx4x64

    # reconciliation against the in-house rung 3 deck (runs it live)
    python3 scripts/reconcile_drc.py out/external_signoff/sramforge_16bx4x64.gds sramforge_16bx4x64

    # device census and wordline/bitline degrees against rung 4
    python3 scripts/external_lvs.py --golden
    python3 scripts/external_lvs.py out/external_signoff/sramforge_16bx4x64.gds sramforge_16bx4x64

The deck is found at `../ASAP7_for_KLayout/drc/drc_ASAP7.lydrc` (sibling
checkout, the same convention as `../asap7_sram_0p0`), at
`tools/ASAP7_for_KLayout/...`, or through `SRAMFORGE_EXTERNAL_DRC_DECK` /
`ORFS_ROOT`.  Outputs land in `out/external_signoff/` (git-ignored):
`<top>.external_drc.json` (per-rule counts and example boxes in dbu (database
units), 1 dbu = 0.25 nm), `<top>.external.lyrdb` (open in the KLayout marker
browser), `<top>.reconcile.md/.json`, `<top>.external.spice`,
`<top>.external_lvs.json`.

Runtimes on the shared 32-core machine at load 30 (the deck uses 4 threads):
16 bank 160 s flat + 134 s hierarchical; golden 236 s + 139 s; 32 bank 283 s +
141 s; 64 bank 1141 s + 303 s (peak 2.6 gigabytes); our rung 3 deck 90 s to 120 s per
bank; KLayout extraction 1.8 s (16 bank) and 3.7 s (32 bank) against 85 s and
140 to 150 s for the in-house extraction.

## 3. DRC (Design Rule Check) reconciliation

Classes: `agree-flag` both decks flag the rule; `ext-only` / `ours-only` one
side flags it while the other implements it and finds it clean; `ours-not-impl`
/ `ext-not-impl` only one side implements it.  A rule the external deck does
not implement is NOT evidence of cleanliness for that rule; a rule it
implements and finds clean is.

### 3.1 Coverage of the two decks

The external deck implements 258 categories, ours reports 125 rule ids.  Of the
external categories, 76 concern layers the banks do not use (M6 to M9, V5 to
V9, PAD, SLVT/LVT); they run and are trivially clean.  Rules ours implements and the external
deck does not (6 rows, no external evidence): SDT.ACTIVE.OV.1, SDT.LISD.OV.2,
SRAM.SDT.ACTIVE.OV.3, SRAM.SDT.LISD.OV.4, SDT.GATE.AUX.1 and SDT.LISD.AUX.4 (the
SDT overlap and containment rules).  Rules the external deck implements and
ours does not, that FIRE on at least one bank (so they are not covered by our
"deliberately not implemented" list by accident): see 3.4.

### 3.2 Golden reference bank (`srambank_32b`, identical to our 32,4,32)

Our deck: 0 markers (clean by construction of the calibration).  External deck:
107,544 markers in 49 rules, 26 rows `ext-only`, 0 `ours-only`, 83 `agree-clean`.
The 32,4,32 bank regenerated by sramforge gives the same external counts rule
for rule (the two layouts are XOR (exclusive or) identical, and the external
run confirms it independently of our own XOR (exclusive or) test).

Every one of the 26 `ext-only` rows on the golden falls in one of four classes,
which is exactly the claim `sramforge/drc.py` makes about itself:

| class | rows | rules |
|---|---|---|
| calibrated floor (ours enforces the golden-observed value, the external deck the printed DRM (Design Rule Manual) value) | 16 | WELL.S.1, GATE.S.2, GATE.ACTIVE.EX.2, GATE.ACTIVE.S.4, GCUT.GATE.EX.1, SDT.GATE.S.2, ACTIVE.FIN.EX.1, SRAM.NSELECT/PSELECT.ACTIVE.EN.3/4, NSELECT.W.1, LIG.S.2, LIG.S.4/S.5, M1.A.1, M2.W.1, V1.S.1, M4.S.1, M5.S.1 |
| waived or cal-allowed (recorded in `WAIVED` / `CAL_ALLOWED`) | 2 | NSELECT.PSELECT.AUX.1 (12 spots, the same 12 the waiver names), GATE.W.1 (20.5 nm edge stripes) |
| different formulation of the same rule (reasons in the table) | 7 | GATE.S.1 (every off-34 nm gap versus one centre grid), ACTIVE.AUX.1 (edge coincidence half), SRAM.ACTIVE.AUX.2 (crossing versus touching the marker), GCUT.AUX.3 (edges versus channel area), GCUT.GATE.S.2 (includes the cut gate), GCUT.ACTIVE.S.1 (any ACTIVE versus channel), SRAM.ACTIVE.WELL.S.5 (includes ACTIVE inside the well) |
| variant ours does not implement | 1 | V0.S.1 (the 27 nm non-aligned-track output; the 18 nm base rule agrees) |

The numbers agree where they should: the external deck finds 12
NSELECT/PSELECT overlaps, the waiver in `drc.py` says 12; it finds 8 WELL.S.1
gaps at the stack seams, 8 NSELECT.W.1 fingers, 16 M2.W.1 slivers, all at the
coordinates our calibration comments describe.  The 23 rules ours does not
implement that fire on the golden (SDT.W.3, LIG.LISD.S.7, V0.LIG.A.1,
V0.LISD.EN.3, V1.S.4, V1.M2.EN.2, ACTIVE.W.2, M1/M2.S.6, LIG.GATE.AUX.1,
LIG.GATE.EX.1, GCUT.AUX.2, V2.M2.EN.1, M4.S.3/4/5, V3.M4.EN.2, V0.M1.AUX.3,
GATE.AUX.1, LIG.LISD.S.6, LIG.SDT.S.8, V1.S.2) are all either deck defects
(union, multiple-of-27, net condition ignored) or rules the header of `drc.py`
lists as deliberately not transcribed (corner-to-corner variants, via end-cap
enclosures, bend rules).  The golden itself violates them as printed, so no
generated bank can be held to them without a waiver.

### 3.3 16,4,32 bank (`sramforge_16bx4x64`): where the two decks disagree

Our deck: 119 markers in 14 rules (this configuration is NOT clean under the
calibrated deck; `tests/test_drc.py` only checks the reference configuration).
External: 59,728 markers in 62 rules.  Rows: 7 `agree-flag`, 28 `ext-only`,
7 `ours-only`, 67 `agree-clean`.

Agreement (7 rows, same spots): V3.AUX.1 (34 V3 vias outside M3/M4, identical
boxes), V4.AUX.1 (2), V4.S.1 (1, identical box), M2.S.2, M3.S.2, M4.S.1, M5.S.1
(the M4/M5 counts differ by the factor two because the external deck outputs
edges and ours edge pairs).

`ours-only` (7 rows): M1/M2/M3.S.4/S.5 and M2/M3.S.3 (the external deck's
tip-to-tip tier filter "polygon not interacting with any short or long edge"
also excludes the perpendicular corner edges of the marker polygon and so
under-reports the tiers; ours classifies by the two participating edge
lengths), M4.S.2 (edge filter drops the spots), and WELL.W.1 (ours measures
width with the Euclidean metric where the DRM (Design Rule Manual) says
horizontal width; the 4 flagged pairs are staircase edges with zero projected
overlap, so ours over-reports).

`ext-only` beyond the golden's inherited classes: 14 external categories (13
rows) fire on the 16 bank that do not fire on the golden.  Three of them are
genuine generator-introduced geometry that our deck's formulation cannot see:

| external rule | count | what it is | why ours misses it |
|---|---|---|---|
| LIG.GATE.A.3 / .A.4 | 64 | 10.5 nm tall GATE remnants left above a GCUT (gate minus gcut piece 80 x 42 dbu, e.g. (4820,171808;4900,171850) = (1.205, 42.952) um) | GATE.W.2 is checked on the uncut gate polygon, which is 303 nm tall |
| ACTIVE.WELL.EN.1 | 16 | non-SRAM (Static Random-Access Memory) ACTIVE enclosed by WELL by only 9 nm on one side (e.g. (30704,156168;30740,156492)) | the calibrated floor of 36 dbu came from golden band CORNERS measured Euclidean; a real 9 nm projected shortfall passes it |
| V0.AUX.1/2 | 2 | V0 fully on M1 but hanging 10 nm off its LISD/LIG landing (2880 of 5184 dbu² uncovered), at both bank edges y = 39.4 um | ours checks `not_interacting` (via touches nothing), not partial coverage |

The other ten are rules ours does not transcribe (V3.M4.AUX.2 39 and
V4.M5.AUX.2 2: via width must equal the track width; M4.AUX.3 25 and M5.AUX.3
22: metal bends; M3.S.6 11: corner-to-corner) or agree with ours (M2.S.2,
M3.S.2, V3.AUX.1, V4.AUX.1, V4.S.1-2-3).

### 3.4 64,4,64 bank (`sramforge_64bx4x128`)

Run once, last: 1141 s flat (6.73 M flat shapes) + 303 s hierarchical.  Our
deck: 680 markers in 21 rules (this configuration is not clean under the
calibrated deck either).  External: 412,067 markers in 67 rules (403,468 of
them SDT.W.3).  Rows: 13 `agree-flag`, 24 `ext-only`, 8 `ours-only`, 64
`agree-clean`.

Agreement is the strongest of the three banks: V3.AUX.1 546 = 546 (V3 vias
outside M3/M4, the largest single finding of our deck on this bank), LIG.A.1
4 = 4, M1.S.2 4 = 4, M3.W.1 2 = 2 (an 11 nm wide, 4.4 um long M3 sliver at
x = 0.196 um), V2.W.1 8 = 8, V3.W.1 2 = 2, V4.AUX.1 2 = 2, V4.S.1 1 = 1, plus
M2.S.2, M3.S.2, M4.S.1, M5.S.1 and NSELECT.W.1 at the same spots with the
count-unit and tier differences already explained.  Every `ext-only` row is
one of the golden's inherited classes (calibrated floor, waiver, formulation)
with one addition, LIG.GATE.S.9B (2 markers at (784,167368), the same M3
sliver region, LIG to uncut GATE horizontal spacing under 17 nm; ours has the
rule and measures it against gate that does not interact with the LIG, which
this gate does).  The `ours-only` rows are the tip-to-tip tier, M4.S.2 and
WELL.W.1 formulation rows plus LISD.S.2 (4, tier classification).  The three
16-bank-specific findings of 3.3 (gate remnants, 9 nm well enclosure,
partially landed V0) do not occur on the 64 bank: they belong to the
sparse-bits configuration.

## 4. LVS (Layout Versus Schematic): device census

What is independent and what is not: the in-house rung 4 is a first-principles
union-find over merged islands with its own device recognition.  The external
side uses KLayout's LayoutToNetlist engine
(https://www.klayout.de/doc/manual/lvs_overview.html): its hierarchical
connectivity extractor and its MOS (Metal-Oxide-Semiconductor) device extractor
(`DeviceExtractorMOS3Transistor`), the same engine the KLayout LVS (Layout
Versus Schematic) DSL (Domain Specific Language) drives, called through the pip
module so the result can be compared in Python.  NOT independent: the layer
recipe (channel = ACTIVE and (GATE minus GCUT), source/drain = ACTIVE minus
that, implant split by NSELECT/PSELECT, flavor by the SRAMVT marker, the
SD -> SDT -> LISD -> {LIG, V0}, gate -> LIG -> V0, V0 -> M1 -> ... -> M5
stack) was written for this script from the DRM (Design Rule Manual) layer
semantics, because no public ASAP7 (Arizona State University 7 nm predictive
Process Design Kit) LVS (Layout Versus Schematic) deck exists.  It mirrors the
in-house CONNECTIONS list, which is the physical stack, not a copy of the code.
Device width is KLayout's geometric channel width; nfin = W / 27 nm.

Wordline and bitline degrees are found structurally on the external side
(storage net = gate of a pmos_sram; access transistor = nmos_sram whose gate
is not a storage net; WL = its gate net; BL/BLN = its non-storage source/drain
net) and by pin label on the in-house side, so the two agree on the counts for
independent reasons.

| layout | devices (both) | census bins | (model, nfin) census | access per WL net | access drains per BL/BLN net |
|---|---|---|---|---|---|
| `sram_cell_6t_122` | 6 | 2 | identical (4 nmos_sram 2-fin, 2 pmos_sram 1-fin); KLayout netlist also names WL, BL, BLN, vdd!, vss! and the two storage nodes exactly as the golden 6T (six-transistor) schematic | 1 net x 2 | 2 nets x 1 |
| 16,4,32 | 64,176 | 19 | identical, every bin | 32 nets x 512, identical | 1024 nets x 16, identical |
| golden `srambank_32b` | 114,454 | 17 | identical, every bin (65,536 nmos_sram 2-fin, 32,768 pmos_sram 1-fin, 15 rvt bins from 1 to 18 fins) | 64 nets x 512, identical | 1024 nets x 32, identical |
| 32,4,32 | 114,454 | 17 | identical to the golden and to the in-house census | 64 nets x 512, identical | 1024 nets x 32, identical |
| 64,4,64 | 422,124 | 19 | identical, every bin (262,144 nmos_sram 2-fin, 131,072 pmos_sram 1-fin, 192 + 192 3-fin dummy-cell devices, 15 rvt bins); KLayout 5.8 s, in-house 234 s | 128 nets x 1024, identical | 2048 nets x 64, identical |

The full KLayout SPICE (Simulation Program with Integrated Circuit Emphasis)
netlist is written next to the report.  A full netlist
match (isomorphism of the 64k-device bank against a schematic) was not
attempted: there is no reference schematic of the bank beyond the 6T
(six-transistor) cell, and the acceptance bar set for this task was the device
census plus per-net degrees, which is met exactly.

## 5. Verdict

Rung 3 (DRC (Design Rule Check)).  On the golden and our 32,4,32 bank the
external deck corroborates the in-house deck's central claim: every external
finding on the reference is either a rule whose printed value the golden itself
violates and our deck floors at the golden value (14 rules, with the counts
matching the calibration comments spot for spot), a recorded waiver, a
formulation difference, or a defect of the external deck.  No rule that both
decks implement with the same formulation is flagged by one and not the other
on the reference.  On the 16,4,32 bank the two decks agree on the 7 rules both
flag (same coordinates), and the external deck additionally exposes three real
generator-introduced deviations that the in-house formulation cannot see (gate
remnants above cuts, a 9 nm projected well enclosure hidden behind a
Euclidean-calibrated floor, partially landed V0).  On the 64,4,64 bank the two
decks agree on 13 rules, with identical counts for the exact-shape rules (546
V3 vias outside M3/M4, 8 narrow V2, 2 narrow V3, an 11 nm M3 sliver, 4 small
LIG), and the external deck adds nothing beyond the inherited classes.  Those
three 16-bank findings, plus the fact that the 16 and 64 configurations fire
14 and 21 rules under our own deck while `tests/test_drc.py` only checks the
reference, are the actionable findings.
What the external run does NOT prove: cleanliness of the 6 SDT overlap rules
(the external deck lacks them), of the net-conditional spacings (both decks
skip the net condition in different ways), or of anything the union and
multiple-of-27 defects render meaningless (LIG.GATE.A.3/4, LIG.LISD.A.2,
LIG.LISD.OV.1, SRAM.LIG.GATE.OV.2, ACTIVE.W.2, SDT.W.3).

Rung 4 (LVS (Layout Versus Schematic)).  KLayout's netlist extractor produces
the identical device census, bin for bin, on the 6T (six-transistor) cell and
the banks, and the identical wordline and bitline degrees found by a different
method (structure instead of labels).  That corroborates the in-house device
recognition, fin counting and connectivity as strongly as a census can; it
does not corroborate net NAMES (KLayout's flattened netlist keeps only the top
level names) and it shares the layer recipe with the in-house extraction, so an
error in the recipe itself (for example a missing via layer) would be common to
both.
