# sramforge rung 5 - electrical (SPICE) collateral

* `sram6t122.sp` - the 6T (six-transistor) bitcell subcircuit, exactly as
  extracted by rung 4 (models `nmos_sram`/`pmos_sram`, fins 1/2/2, the
  measured cross-couple).
* `tb_read.sp` / `tb_write.sp` - column testbenches: one addressed cell +
  31 real unaddressed cells on the bitline pair + 1.4 fF wire, precharge
  and (write) a 6-fin pulldown driver.
* `models/asap7_{TT,FF,SS}.osdi.sp` - the ASAP7 BSIM-CMG (Berkeley
  Short-channel IGFET Model - Common Multi-Gate) cards converted for
  ngspice-OSDI (Open Simulator Device Interface) by
  `scripts/pm2osdi.py` (every calibrated parameter verbatim; framing
  changed; `jtweff` shim documented in the script).

## Toolchain (all open, no root needed)

1. ngspice ≥ 46 built from source (`--enable-osdi`).
2. OpenVAF (prebuilt binary, openvaf.semimod.de) compiles
   VA-Models' BSIM-CMG `vacode110/bsimcmg.va` → `bsimcmg110.osdi`
   (github.com/dwarning/VA-Models).  The ASAP7 cards say version=107;
   under the 110 model three 107-era knobs (version/coremod/capmod) are
   ignored - the OpenVAF-recommended route.
3. A `.spiceinit` in the run directory loads the module:
   `osdi /path/to/bsimcmg110.osdi`
4. `ngspice -b tb_read.sp` (with MODELS/RAWOUT paths substituted).

