* The 6T (six-transistor) bitcell, exactly as extracted by rung 4 (LVS -
* Layout Versus Schematic): models nmos_sram/pmos_sram, fin counts 1/2/2,
* the measured cross-couple, ACC-L on BLN<->QB and ACC-R on BL<->Q.
* Device port order follows the bsimcmg module: drain gate source bulk.
.subckt sram6t122 bl bln wl vdd vss
Naccl bln wl  qb  vss nmos_sram nfin=2
Naccr bl  wl  q   vss nmos_sram nfin=2
Npua  q   qb  vdd vdd pmos_sram nfin=1
Npda  q   qb  vss vss nmos_sram nfin=2
Npub  qb  q   vdd vdd pmos_sram nfin=1
Npdb  qb  q   vss vss nmos_sram nfin=2
.ends
