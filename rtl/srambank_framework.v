// ===========================================================================
// srambank_framework.v - structural framework RTL (Register Transfer Level)
// for the sramforge single-port SRAM (Static Random-Access Memory) bank.
//
// PURPOSE: a readable, simulable mock of how the macro is ORGANIZED - the
// module hierarchy mirrors the physical hierarchy one-to-one, and every
// block that is NOT synthesized logic (it is a hardened layout cell) is a
// module instantiation whose header names the GDS (Graphic Design System)
// cell it stands in for.  Behavioral bodies inside those modules are mocks;
// the structure, buses and control flow are the measured architecture.
//
// THE MEASURED ORGANIZATION (established by rungs 1-4 + the family models):
//   * WL (Word Line) columns run VERTICALLY: BITS columns per half-bank,
//     2 halves (upper/lower stack) => 2*BITS wordlines.  One WL activates
//     one cell in every strip it crosses (256 cells at reference).
//   * Bitlines run HORIZONTALLY, one pair per strip, only BITS cells long
//     - depth does NOT lengthen them (that is why depth is geometrically
//     cheap).
//   * Each colgrp (column-group) row = 2 arrays x STRIPS strips + one io
//     (Input/Output) column, and contributes exactly TWO data bits (one
//     4-to-1 column mux + sense amplifier per array side).
//   * Therefore: WORDS  = 2 * BITS * STRIPS   (words are BITS x MUX x HALF)
//                DWIDTH = 2 * DEPTH           (data bits are colgrp rows!)
//     The family's own models confirm it: srambank_64x4x16 has 16-bit
//     data, srambank_64x4x80 has 80-bit data, BOTH 8-bit address.
//
// ASSUMPTIONS MARKED [MOCK]: the data-bit ORDER along the rows and all
// timing are plausible mocks - the layout does not pin them down (yet).
// Everything labeled [MEASURED] is.  The address field order graduated
// from [MOCK] to [MEASURED] when rung 4.5's switch-level sweep read it
// off the silicon geometry: ADDRESS[1:0] = column-mux select,
// ADDRESS[6:2] = wordline column, ADDRESS[7] = half select.
// ===========================================================================

`timescale 1ps/1ps

// ---------------------------------------------------------------------------
// TOP: the bank.  Interface matches the asap7_sram_0p0 behavioral models.
// ---------------------------------------------------------------------------
module srambank_framework #(
    parameter BITS   = 32,             // cells per strip side  (--bits)
    parameter STRIPS = 4,              // strips per array = column-mux ratio
    parameter DEPTH  = 32              // colgrp rows PER STACK (--depth)
) (
    input                        clk,
    input  [AW-1:0]              ADDRESS,
    input  [DW-1:0]              wd,       // data to write
    input                        banksel,  // access enable
    input                        read,     // read enable
    input                        write,    // write enable
    output [DW-1:0]              dataout   // latched; updates on read only
);
    localparam MUXW = $clog2(STRIPS);       // column-mux select width (2)
    localparam COLW = $clog2(BITS);         // WL-column select width  (5)
    localparam AW   = COLW + 1 + MUXW;      // + 1 half-select bit     (8)
    localparam DW   = 2 * DEPTH;            // one colgrp row = 2 bits (64)

    // [MEASURED by rung 4.5] address field order: {half, column, mux} -
    // ADDRESS[1:0] column-mux, ADDRESS[6:2] wordline column, ADDRESS[7] half
    wire [MUXW-1:0] mux_addr  = ADDRESS[MUXW-1:0];
    wire [COLW-1:0] col_addr  = ADDRESS[MUXW +: COLW];
    wire            half_addr = ADDRESS[AW-1];

    // control + decode band (the physical center spine of the bank)
    wire [BITS-1:0] wl_lower, wl_upper;   // one-hot WL (Word Line) pulses
    wire            prech_n, sense_en, write_en;

    ctrl_decode #(.BITS(BITS)) u_ctrl_decode (
        .clk(clk), .banksel(banksel), .read(read), .write(write),
        .col_addr(col_addr), .half_addr(half_addr),
        .wl_lower(wl_lower), .wl_upper(wl_upper),
        .prech_n(prech_n), .sense_en(sense_en), .write_en(write_en));

    // the two stacks of colgrp rows, DEPTH rows each.  A row is a
    // 2-bit slice of every word in its half; the halves share the
    // vertical data lines through the center band, selected by half_addr.
    wire [DW-1:0] rd_lower, rd_upper;

    genvar g;
    generate
        for (g = 0; g < DEPTH; g = g + 1) begin : rows
            // [MOCK] data-bit order: row g owns bits {2g+1, 2g}
            colgrp_row #(.BITS(BITS), .STRIPS(STRIPS)) u_lower (
                .clk(clk), .wl(wl_lower), .mux_sel(mux_addr),
                .prech_n(prech_n), .sense_en(sense_en), .write_en(write_en),
                .wbits(wd[2*g +: 2]), .rbits(rd_lower[2*g +: 2]));
            colgrp_row #(.BITS(BITS), .STRIPS(STRIPS)) u_upper (
                .clk(clk), .wl(wl_upper), .mux_sel(mux_addr),
                .prech_n(prech_n), .sense_en(sense_en), .write_en(write_en),
                .wbits(wd[2*g +: 2]), .rbits(rd_upper[2*g +: 2]));
        end
    endgenerate

    // latched output, updated on read only (matches the behavioral model)
    reg [DW-1:0] dataout_q;
    always @(posedge clk)
        if (banksel & read)
            dataout_q <= half_addr ? rd_upper : rd_lower;
    assign dataout = dataout_q;
endmodule


// ---------------------------------------------------------------------------
// ctrl_decode - stands in for the hardened cell `ctrl_decode_32` (the
// full-width band at the bank's vertical center).  [MEASURED] it contains
// TWO INTERLEAVED column decoders (one per half) plus the control block
// with a 16-stage delay line.  The RTL half is real logic (predecode +
// binary-to-one-hot); the timing half is a NON-RTL mock (see delay_line).
// ---------------------------------------------------------------------------
module ctrl_decode #(parameter BITS = 32) (
    input                  clk,
    input                  banksel, read, write,
    input  [$clog2(BITS)-1:0] col_addr,
    input                  half_addr,
    output [BITS-1:0]      wl_lower, wl_upper,
    output                 prech_n, sense_en, write_en
);
    wire access = banksel & (read | write);

    // binary -> one-hot column decode, gated by the half select.
    // (physically: interleaved twin 5-to-32 decoders; growing DEPTH does
    // NOT touch this, growing BITS past 32 needs a wider decoder.)
    assign wl_lower = (access & ~half_addr) ? ({{BITS-1{1'b0}}, 1'b1} << col_addr)
                                            : {BITS{1'b0}};
    assign wl_upper = (access &  half_addr) ? ({{BITS-1{1'b0}}, 1'b1} << col_addr)
                                            : {BITS{1'b0}};

    // self-timed control: precharge off while accessing; the sense-enable
    // edge comes from the delay line, not from a bitline replica.
    wire dly;
    delay_line u_delay_line (.clk(clk), .trigger(access & read), .out(dly));
    assign prech_n  = access;         // [MOCK] release precharge on access
    assign sense_en = dly;            // fire sense amps after the dial-in delay
    assign write_en = banksel & write;
endmodule


// ---------------------------------------------------------------------------
// delay_line - NON-RTL BLOCK.  Stands in for the 16 DLL (Delay-Locked
// Loop) delay cells measured inside `ctrl_decode_32`.  Physically an
// inverter/wire chain whose tap sets when the sense amplifiers fire
// relative to WL (Word Line) rise; here a zero-cost mock (same cycle).
// Rung 5's job is to check the real tap covers the real bitline swing.
// ---------------------------------------------------------------------------
module delay_line (input clk, input trigger, output out);
    assign out = trigger;   // [MOCK] ideal: sense fires the same cycle
endmodule


// ---------------------------------------------------------------------------
// colgrp_row - one column-group row: | array | io column | mirrored array |.
// [MEASURED] contributes 2 data bits: each array side has STRIPS strips
// whose bitline pairs meet one precharge/column-mux + sense amp + write
// driver in the shared io column.
// ---------------------------------------------------------------------------
module colgrp_row #(parameter BITS = 32, STRIPS = 4) (
    input                       clk,
    input  [BITS-1:0]           wl,        // this half's one-hot wordlines
    input  [$clog2(STRIPS)-1:0] mux_sel,
    input                       prech_n, sense_en, write_en,
    input  [1:0]                wbits,     // {right array, left array}
    output [1:0]                rbits
);
    array_side #(.BITS(BITS), .STRIPS(STRIPS)) u_left (
        .clk(clk), .wl(wl), .mux_sel(mux_sel), .prech_n(prech_n),
        .sense_en(sense_en), .write_en(write_en),
        .wbit(wbits[0]), .rbit(rbits[0]));
    array_side #(.BITS(BITS), .STRIPS(STRIPS)) u_right (
        .clk(clk), .wl(wl), .mux_sel(mux_sel), .prech_n(prech_n),
        .sense_en(sense_en), .write_en(write_en),
        .wbit(wbits[1]), .rbit(rbits[1]));
endmodule


// ---------------------------------------------------------------------------
// array_side - STRIPS bitcell strips + the io slice that serves them.
// ---------------------------------------------------------------------------
module array_side #(parameter BITS = 32, STRIPS = 4) (
    input                       clk,
    input  [BITS-1:0]           wl,
    input  [$clog2(STRIPS)-1:0] mux_sel,
    input                       prech_n, sense_en, write_en,
    input                       wbit,
    output                      rbit
);
    // the strips' abstract bitline values (one sensed bit per strip)
    wire [STRIPS-1:0] strip_rd;
    wire [STRIPS-1:0] strip_we;

    genvar s;
    generate
        for (s = 0; s < STRIPS; s = s + 1) begin : strips
            assign strip_we[s] = write_en & (mux_sel == s);
            bitcell_strip #(.BITS(BITS)) u_strip (
                .clk(clk), .wl(wl), .we(strip_we[s]),
                .din(wbit), .dout(strip_rd[s]));
        end
    endgenerate

    wire muxed;
    prech_ymux #(.STRIPS(STRIPS)) u_prech_ymux (
        .prech_n(prech_n), .sel(mux_sel), .in(strip_rd), .out_bl(muxed));
    sense_amp u_sense_amp (.sense_en(sense_en), .bl(muxed), .q(rbit));
    write_driver u_write_driver (.write_en(write_en), .din(wbit));
endmodule


// ---------------------------------------------------------------------------
// bitcell_strip - NON-RTL BLOCK.  Stands in for one strip of BITS
// hardened 6T (six-transistor) bitcells `sram_cell_6t_122` (plus its
// `dummy_sram_6t122` + `tapcell_sram_6t122` end cells).  [MEASURED] the
// strip shares one horizontal bitline pair; WL (Word Line) columns cross
// it vertically, selecting one cell.  Mocked as a register file.
// ---------------------------------------------------------------------------
module bitcell_strip #(parameter BITS = 32) (
    input             clk,
    input  [BITS-1:0] wl,       // one-hot cell select
    input             we,
    input             din,
    output            dout
);
    reg store [0:BITS-1];
    integer i;
    always @(posedge clk)
        if (we)
            for (i = 0; i < BITS; i = i + 1)
                if (wl[i]) store[i] <= din;
    // read: the selected cell develops the bitline (mock: direct mux)
    reg rd;
    always @* begin
        rd = 1'b0;
        for (i = 0; i < BITS; i = i + 1)
            if (wl[i]) rd = store[i];
    end
    assign dout = rd;
endmodule


// ---------------------------------------------------------------------------
// prech_ymux - NON-RTL BLOCK.  Stands in for the hardened cell
// `wrasst_prech_ymux_x4_sram_6t122_v2`: bitline precharge + 4-to-1
// column mux with write assist.  One slice per strip stacks in the io
// column.  Mocked as a plain mux (precharge is electrical, not logical).
// ---------------------------------------------------------------------------
module prech_ymux #(parameter STRIPS = 4) (
    input                       prech_n,
    input  [$clog2(STRIPS)-1:0] sel,
    input  [STRIPS-1:0]         in,
    output                      out_bl
);
    assign out_bl = in[sel];
endmodule


// ---------------------------------------------------------------------------
// sense_amp - NON-RTL BLOCK.  Stands in for `senseamp_sram_6t122` (+ the
// `srlatch_sram_6t122` output latch).  Fires on sense_en, resolves the
// millivolt bitline split to a full-rail bit.  Mocked as a gate.
// ---------------------------------------------------------------------------
module sense_amp (input sense_en, input bl, output q);
    assign q = sense_en ? bl : 1'b0;   // [MOCK] ideal comparator
endmodule


// ---------------------------------------------------------------------------
// write_driver - NON-RTL BLOCK.  Stands in for `write_draslatch_02`: the
// strong driver that slams the selected bitline pair full-rail during a
// write.  In this mock the write path lives in bitcell_strip directly.
// ---------------------------------------------------------------------------
module write_driver (input write_en, input din);
endmodule
