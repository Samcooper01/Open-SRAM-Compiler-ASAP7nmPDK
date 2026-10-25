* WRITE transient: the addressed cell stores 0 (Q=0); write a 1 by pulling
* BLN low through the write NMOS while BL stays precharged high, wordline
* on.  Watch Q/QB cross over - the moment the bit flips.
.include MODELS
.include sram6t122.sp
.param vdd=0.7
.global 0

Vdd vdd 0 {vdd}
Xcell0 bl bln wl vdd 0 sram6t122
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
Cblw  bl  0 1.4f
Cblnw bln 0 1.4f

Npc1 bl  prechn vdd vdd pmos_rvt nfin=4
Npc2 bln prechn vdd vdd pmos_rvt nfin=4
Npeq bl  prechn bln vdd pmos_rvt nfin=2

* write driver: strong pulldown on BLN (writing a 1 into Q)
Nwr bln wen 0 0 nmos_rvt nfin=6

Vwl     wl     0 PWL(0 0   2n 0    2.05n {vdd}  4n {vdd}  4.05n 0)
Vprechn prechn 0 PWL(0 0   1.5n 0  1.55n {vdd}  5n {vdd}  5.05n 0)
Vwen    wen    0 PWL(0 0   1.8n 0  1.85n {vdd}  4n {vdd}  4.05n 0)

.ic v(xcell0.q)=0 v(xcell0.qb)={vdd}
.tran 2p 6n uic

.control
run
set filetype=ascii
write RAWOUT v(wl) v(prechn) v(wen) v(bl) v(bln) v(xcell0.q) v(xcell0.qb)
.endc
.end
