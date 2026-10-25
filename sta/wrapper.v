// A minimal consumer of the generated macro, so its own timing library
// can be linked and checked by static timing analysis: registers on
// every input the macro samples and on the data it returns, clocked by
// the same clock the macro uses.  This is the smallest design that
// makes the library's setup, hold and clock-to-output arcs load-bearing.
module sram_wrapper (
    input               clk,
    input       [7:0]   addr_i,
    input               banksel_i,
    input               read_i,
    input               wren_i,
    input       [63:0]  wd_i,
    output reg  [63:0]  dout_o
);
  reg  [7:0]  addr_q;
  reg         banksel_q, read_q, wren_q;
  reg  [63:0] wd_q;
  wire [63:0] dout;

  always @(posedge clk) begin
    addr_q    <= addr_i;
    banksel_q <= banksel_i;
    read_q    <= read_i;
    wren_q    <= wren_i;
    wd_q      <= wd_i;
    dout_o    <= dout;
  end

  sramforge_32bx4x64 u_sram (
      .clk(clk), .ADDRESS(addr_q), .banksel(banksel_q), .read(read_q),
      .byte_wrenab(wren_q), .sdel(5'b11111), .wd(wd_q), .dataout(dout));
endmodule
