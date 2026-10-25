// Self-checking testbench for the sramforge bank.
//
// THREE models run in lockstep on identical stimulus:
//   dut_frame - srambank_framework, the structural mock whose hierarchy
//               mirrors the layout (rtl/srambank_framework.v)
//   dut_gen - the compiler-emitted behavioral netlist for the reference
//               configuration (out/sramforge_32bx4x64.v)
//   gold - an inline golden memory in this bench
// Every read must agree three ways.
//
// Phases: (1) a short DIRECTED demo - writes and read-backs at addresses
// covering both halves and all mux ways, with recognizable data patterns
// (this is the window to look at in the wave viewer); (2) write every
// word, read every word back; (3) random traffic; (4) idle tail.
//
// Run:  TMPDIR=/tmp tools/iverilog-install/bin/iverilog -g2012 -o out/tb \
//           rtl/srambank_framework.v rtl/tb_srambank_framework.v \
//           out/sramforge_32bx4x64.v
//       tools/iverilog-install/bin/vvp out/tb
// Waves land in out/tb_srambank.vcd.
`timescale 1ps/1ps

module tb_srambank_framework;
    localparam BITS = 32, STRIPS = 4, DEPTH = 32;
    localparam AW = 8, DW = 2 * DEPTH, WORDS = 2 * BITS * STRIPS;

    reg clk = 0, banksel = 0, read = 0, write = 0;
    reg  [AW-1:0] ADDRESS = 0;
    reg  [DW-1:0] wd = 0;
    wire [DW-1:0] frame_out, gen_out;

    srambank_framework #(.BITS(BITS), .STRIPS(STRIPS), .DEPTH(DEPTH))
    dut_frame (
        .clk(clk), .ADDRESS(ADDRESS), .wd(wd), .banksel(banksel),
        .read(read), .write(write), .dataout(frame_out));

    // the compiler's emitted behavioral netlist (reference configuration).
    // Its ports follow docs/pin_interface.md (the layout's pin names):
    // the write enable is byte_wrenab and the five sense-timing delay
    // taps sdel[4:0] are tied high, as in every layout-level proof.
    sramforge_32bx4x64 dut_gen (
        .clk(clk), .ADDRESS(ADDRESS), .wd(wd), .banksel(banksel),
        .read(read), .byte_wrenab(write), .sdel(5'b11111),
        .dataout(gen_out));

    // inline golden memory
    reg [DW-1:0] mem [0:WORDS-1];
    reg [DW-1:0] gold_out;
    always @(posedge clk) begin
        if (banksel & write) mem[ADDRESS] <= wd;
        if (banksel & read)  gold_out <= mem[ADDRESS];
    end

    always #500 clk = ~clk;   // 1 ns period

    integer errors = 0;
    task check;
        begin
            if (frame_out !== gold_out) begin
                errors = errors + 1;
                $display("FRAME MISMATCH @%0t addr=%0h frame=%h gold=%h",
                         $time, ADDRESS, frame_out, gold_out);
            end
            if (gen_out !== gold_out) begin
                errors = errors + 1;
                $display("GEN MISMATCH @%0t addr=%0h gen=%h gold=%h",
                         $time, ADDRESS, gen_out, gold_out);
            end
        end
    endtask

    task idle(input integer n);
        begin
            banksel = 0; read = 0; write = 0;
            repeat (n) @(negedge clk);
        end
    endtask

    task do_write(input [AW-1:0] a, input [DW-1:0] d);
        begin
            banksel = 1; write = 1; read = 0; ADDRESS = a; wd = d;
            @(negedge clk);
        end
    endtask

    task do_read(input [AW-1:0] a);
        begin
            banksel = 1; write = 0; read = 1; ADDRESS = a;
            @(negedge clk); #1 check;
        end
    endtask

    function [DW-1:0] pattern(input integer i);
        pattern = {DW{1'b0}} | (i * 64'h0123_4567_89ab_cdef + i);
    endfunction

    integer i;
    initial begin
        $dumpfile("out/tb_srambank.vcd");
        $dumpvars(0, tb_srambank_framework);

        // ---- phase 1: directed demo (the wave-viewer window) ----
        // addresses picked to exercise the measured map: ADDRESS[1:0]=mux,
        // ADDRESS[6:2]=wordline column, ADDRESS[7]=half
        idle(2);
        do_write(8'h00, 64'hDEAD_BEEF_CAFE_F00D);  // half0 col0  mux0
        do_write(8'h03, 64'hA5A5_A5A5_A5A5_A5A5);  // half0 col0  mux3
        do_write(8'h54, 64'h0000_0000_0000_0001);  // half0 col21 mux0
        do_write(8'h83, 64'hFFFF_FFFF_0000_FFFF);  // half1 col0  mux3
        do_write(8'hFF, 64'h0123_4567_89AB_CDEF);  // half1 col31 mux3
        idle(2);
        do_read(8'h00);
        do_read(8'h03);
        do_read(8'h54);
        do_read(8'h83);
        do_read(8'hFF);
        idle(2);
        // back-to-back write/read on the same address (write-then-read)
        do_write(8'h2A, 64'h1111_2222_3333_4444);
        do_read (8'h2A);
        idle(2);

        // ---- phase 2: write every word, read every word back ----
        for (i = 0; i < WORDS; i = i + 1)
            do_write(i[AW-1:0], pattern(i));
        for (i = 0; i < WORDS; i = i + 1)
            do_read(i[AW-1:0]);

        // ---- phase 3: random traffic ----
        for (i = 0; i < 2000; i = i + 1) begin
            ADDRESS = $random; wd = {$random, $random};
            write = $random & 1; read = ~write; banksel = $random & 1;
            @(negedge clk); #1 if (banksel & read) check;
        end

        // ---- phase 4: idle tail ----
        idle(4);

        if (errors == 0)
            $display("PASS: framework RTL == generated netlist == golden (%0d words x %0d bits, demo + full sweep + %0d random ops)",
                     WORDS, DW, 2000);
        else
            $display("FAIL: %0d mismatches", errors);
        $finish;
    end
endmodule
