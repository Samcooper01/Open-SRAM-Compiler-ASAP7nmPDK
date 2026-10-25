# OpenROAD re-place-and-route of the sramforge control block (drop-in
# frame).  Run inside the openroad/orfs container from /work/sramforge.
set P /OpenROAD-flow-scripts/flow/platforms/asap7
read_lef $P/lef/asap7_tech_1x_201209.lef
read_lef $P/lef/asap7sc7p5t_28_R_1x_220121a.lef
read_lef $P/lef/asap7sc7p5t_28_SRAM_1x_220121a.lef
foreach lib {SIMPLE AO OA} {
  read_liberty $P/lib/NLDM/asap7sc7p5t_${lib}_RVT_TT_nldm_211120.lib.gz
}
read_liberty $P/lib/NLDM/asap7sc7p5t_INVBUF_RVT_TT_nldm_220122.lib.gz
read_liberty $P/lib/NLDM/asap7sc7p5t_SEQ_RVT_TT_nldm_220123.lib
read_liberty $P/lib/NLDM/asap7sc7p5t_INVBUF_SRAM_TT_nldm_220122.lib.gz
read_verilog out/control_netlist.v
link_design control_sram_2kB

initialize_floorplan -die_area {0 0 9.396 5.33} \
                     -core_area {0.378 0.1 7.722 5.23} \
                     -site asap7sc7p5t
source $P/openRoad/make_tracks.tcl
source pnr/out/pins.tcl
source pnr/out/obstructions.tcl

# power: M1 follow-pin rails on every row (the band's via rails drop
# onto these at the original row positions)
add_global_connection -net VDD -pin_pattern {^VDD$} -power
add_global_connection -net VSS -pin_pattern {^VSS$} -ground
global_connect
set_voltage_domain -name CORE -power VDD -ground VSS
define_pdn_grid -name grid -voltage_domains CORE
add_pdn_stripe -grid grid -layer M1 -width 0.018 -pitch 0.54 -offset 0 -followpins
pdngen

global_placement -density 0.45
detailed_placement
check_placement

set_routing_layers -signal M2-M5
pin_access
puts "PIN_ACCESS_DONE"
exit
global_route -congestion_iterations 30
if {[catch {detailed_route -output_drc pnr/out/route_drc.rpt} err]} { puts "DRT_ERR: $err" }

write_def pnr/out/control_pnr.def
write_db  pnr/out/control_pnr.odb
puts "PNR_DONE"
