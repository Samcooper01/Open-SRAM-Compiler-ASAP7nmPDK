# Static timing analysis of a small design that instantiates the
# generated macro, driven by the macro's OWN generated timing library.
# It proves three things at once: the library parses, its arcs are
# complete enough to constrain a real path, and the period the
# characterization claims is the period a timer agrees with.
set P /OpenROAD-flow-scripts/flow/platforms/asap7
# the abstract views: the technology, the standard cells, and the
# generated macro's OWN abstract (1000-unit variant, which this
# technology file requires) - so the run exercises both generated views
read_lef $P/lef/asap7_tech_1x_201209.lef
read_lef $P/lef/asap7sc7p5t_28_R_1x_220121a.lef
read_lef sta/sramforge_32bx4x64.lef
foreach lib {SIMPLE AO OA} {
  read_liberty $P/lib/NLDM/asap7sc7p5t_${lib}_RVT_TT_nldm_211120.lib.gz
}
read_liberty $P/lib/NLDM/asap7sc7p5t_INVBUF_RVT_TT_nldm_220122.lib.gz
read_liberty $P/lib/NLDM/asap7sc7p5t_SEQ_RVT_TT_nldm_220123.lib
read_liberty out/char/sramforge_32bx4x64_TT.lib

read_verilog sta/synth_wrapper.v
link_design sram_wrapper

# UNITS: the timer reports in the unit of the FIRST library read, and the
# ASAP7 standard-cell libraries declare picoseconds, so every number in
# this script and in the report below is in picoseconds.  (The generated
# macro library declares nanoseconds, which is legal and converted
# internally, but it makes the report unit easy to misread.)
set period [expr {[info exists ::env(STA_PERIOD)] ? $::env(STA_PERIOD) : 600}]
create_clock -name clk -period $period [get_ports clk]
set_input_delay  [expr {$period * 0.1}] -clock clk [all_inputs -no_clocks]
set_output_delay [expr {$period * 0.1}] -clock clk [all_outputs]
set_load 0.005 [all_outputs]
puts "== the macro's own arcs, as its generated library declares them =="
report_checks -through [get_pins u_sram/dataout*] -path_delay max -digits 3 -group_count 1

puts "== period $period ps =="
report_checks -path_delay max -digits 3 -group_count 2
report_checks -path_delay min -digits 3 -group_count 1
report_worst_slack -max
report_worst_slack -min
report_tns
