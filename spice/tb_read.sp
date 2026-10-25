* READ transient: one addressed cell (stores 1: Q=VDD, QB=0) on a bitline
* loaded by 31 real unaddressed cells + wire capacitance.  Sequence:
* precharge both bitlines to VDD, release, pulse the wordline; ACC-L
* connects BLN to QB=0 and the cell discharges BLN - the differential the
* sense amplifier would resolve.
.include MODELS
.include sram6t122.sp
.param vdd=0.7
.global 0

Vdd vdd 0 {vdd}
* addressed cell
Xcell0 bl bln wl vdd 0 sram6t122
* 31 unaddressed cells: real junction load on the same bitlines
Xload1 bl bln 0 vdd 0 sram6t122
Xload2 bl bln 0 vdd 0 sram6t122
Xload3 bl bln 0 vdd 0 sram6t122
Xload4 bl bln 0 vdd 0 sram6t122
Xload5 bl bln 0 vdd 0 sram6t122
Xload6 bl bln 0 vdd 0 sram6t122
Xload7 bl bln 0 vdd 0 sram6t122
Xload8 bl bln 0 vdd 0 sram6t122
Xload9 bl bln 0 vdd 0 sram6t122
Xload10 bl bln 0 vdd 0 sram6t122
Xload11 bl bln 0 vdd 0 sram6t122
Xload12 bl bln 0 vdd 0 sram6t122
Xload13 bl bln 0 vdd 0 sram6t122
Xload14 bl bln 0 vdd 0 sram6t122
Xload15 bl bln 0 vdd 0 sram6t122
Xload16 bl bln 0 vdd 0 sram6t122
Xload17 bl bln 0 vdd 0 sram6t122
Xload18 bl bln 0 vdd 0 sram6t122
Xload19 bl bln 0 vdd 0 sram6t122
Xload20 bl bln 0 vdd 0 sram6t122
Xload21 bl bln 0 vdd 0 sram6t122
Xload22 bl bln 0 vdd 0 sram6t122
Xload23 bl bln 0 vdd 0 sram6t122
Xload24 bl bln 0 vdd 0 sram6t122
Xload25 bl bln 0 vdd 0 sram6t122
Xload26 bl bln 0 vdd 0 sram6t122
Xload27 bl bln 0 vdd 0 sram6t122
Xload28 bl bln 0 vdd 0 sram6t122
Xload29 bl bln 0 vdd 0 sram6t122
Xload30 bl bln 0 vdd 0 sram6t122
Xload31 bl bln 0 vdd 0 sram6t122
* bitline wire capacitance: ~7 um of M2 (Metal 2) at ~0.2 fF/um
Cblw  bl  0 1.4f
Cblnw bln 0 1.4f

* precharge/equalize (PMOS, on while prechn low)
Npc1 bl  prechn vdd vdd pmos_rvt nfin=4
Npc2 bln prechn vdd vdd pmos_rvt nfin=4
Npeq bl  prechn bln vdd pmos_rvt nfin=2

Vwl     wl     0 PWL(0 0        2n 0   2.05n {vdd}  4n {vdd}  4.05n 0)
Vprechn prechn 0 PWL(0 0        1.5n 0 1.55n {vdd}  5n {vdd}  5.05n 0)

.ic v(xcell0.q)={vdd} v(xcell0.qb)=0
.tran 2p 6n uic

.control
run
set filetype=ascii
write RAWOUT v(wl) v(prechn) v(bl) v(bln) v(xcell0.q) v(xcell0.qb)
.endc
.end
