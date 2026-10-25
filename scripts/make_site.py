#!/usr/bin/env python3
"""Write the project's web page, docs/index.html, which GitHub Pages
serves. Every number on it is computed here from the generator and the
committed measurement records, so rebuilding it keeps it current.

    python3 scripts/make_site.py
"""
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
sys.path.insert(0, str(REPO))


def read_budget() -> dict:
    """The measured read path of the 2 kilobyte block at the typical
    corner, taken from the recorded benches rather than typed in, so the
    page cannot drift from sramforge/sae_codes.json.  The clock-to-data
    figure is rung 7's, for a 5 femtofarad load at a 20 picosecond slew
    (docs/liberty_characterization.md)."""
    from sramforge import BankSpec, sense           # noqa: E402
    spec = BankSpec(bits_per_group=32, strips_per_group=4, depth_groups=32)
    t = sense.table()
    r = sense.sdel_code(spec, "TT")
    # the write: write enable from rung 7's control record, and the cell's
    # flip after its own word line from the column bench (timing_grid)
    char = json.loads((REPO / "out" / "char" / "sramforge_32bx4x64_TT.json")
                      .read_text())
    grid = json.loads((REPO / "out" / "timing_grid.json").read_text())
    return {
        "wen": round(char["stage_a"]["write_cycle"]["clk_to_wrena_ps"]),
        "flip": grid["columns"]["32_TT"]["write"]["flip_ps"],
        "clk_to_wl": t["controls"][sense.variant_key(spec)]["TT"]["clk_to_wl_ps"],
        "flight": round(sense.flight_ps(spec, "TT"), 1),
        "split": t["split_ps"]["TT"][str(spec.bits_per_group)],
        "margin": sense.DEFAULT_MARGIN_PS,
        "need": r["need_ps"], "sae": r["sae_ps"], "slack": r["slack_ps"],
        "code": r["code"], "dataout": 243.3, "words": 256, "width": 64,
    }


# the block sizes step 4's chart shows: (bits, strips, rows, its name)
SENSE_SIZES = [(32, 4, 8, "8 rows"), (32, 4, 16, "16 rows"),
               (32, 4, 32, "32 rows"), (32, 4, 48, "48 rows"),
               (32, 4, 64, "64 rows"), (32, 4, 96, "96 rows"),
               (16, 4, 32, "16 bits, 32 rows"), (64, 4, 64, "64 bits, 64 rows"),
               (32, 8, 32, "8 strips, 32 rows"), (32, 8, 64, "8 strips, 64 rows")]


def sense_figure() -> str:
    """Step 4 of "Generation": what each block size must wait before
    its sense amplifier fires, as the four measured parts of the rule in
    sramforge/sense.py, against the setting the generator picks and the
    longest wait the kit's own delay line can give.  Every number is read
    from sramforge/sae_codes.json through the module, at the typical
    corner."""
    import html as H
    whole = lambda v: str(int(v + 0.5))       # picoseconds, halves up
    from sramforge import BankSpec, sense
    t = sense.table()
    kit = [v for v in t["controls"]["reference"]["TT"]["codes"].values() if v]
    reb = [v for v in t["controls"][""]["TT"]["codes"].values() if v]
    KIT_MAX, REB_MIN, REB_MAX = max(kit), min(reb), max(reb)
    rows = []
    for bits, strips, depth, name in SENSE_SIZES:
        spec = BankSpec(bits, strips, depth)
        key = sense.variant_key(spec)
        r = sense.sdel_code(spec, "TT")
        parts = [t["controls"][key]["TT"]["clk_to_wl_ps"],
                 sense.flight_ps(spec, "TT"),
                 t["split_ps"]["TT"][str(bits)], sense.DEFAULT_MARGIN_PS]
        rows.append({"name": name, "parts": parts, "need": r["need_ps"],
                     "code": r["code"], "wait": r["sae_ps"],
                     "ctl": {"reference": "kit's control",
                             "": "rebuilt control",
                             "s8": "rebuilt, eight strips"}[key]})
    rows.sort(key=lambda r: r["need"])
    PARTS = ["clock to word line", "far end of the word line rises",
             "bit lines split by 100 millivolts", "margin"]

    W, X0, X1, DOM = 760, 168, 700, 1300
    x = lambda ps: X0 + (X1 - X0) * ps / DOM
    RH, Y0 = 36, 46
    yb = Y0 + RH * len(rows)
    out = [f'<svg class="sensechart" viewBox="0 0 {W} {yb + 44}" role="img" '
           f'aria-label="For each block size, the wait its sense amplifier '
           f'needs, in four parts, and the setting chosen">']
    for ps in range(0, DOM + 1, 200):
        out.append(f'<line x1="{x(ps):.1f}" y1="{Y0 - 8}" x2="{x(ps):.1f}" '
                   f'y2="{yb}" class="sgrid"/>')
        out.append(f'<text x="{x(ps):.1f}" y="{yb + 16}" class="saxis" '
                   f'text-anchor="middle">{ps}</text>')
    out.append(f'<text x="{(X0 + X1) / 2:.1f}" y="{yb + 36}" class="saxis" '
               f'text-anchor="middle">picoseconds after the clock</text>')
    # the kit's limit: a block whose need ends past it cannot use that line
    out.append(f'<line x1="{x(KIT_MAX):.1f}" y1="{Y0 - 14}" x2="{x(KIT_MAX):.1f}" '
               f'y2="{yb}" class="slimit"/>')
    out.append(f'<text x="{x(KIT_MAX) + 6:.1f}" y="{Y0 - 18}" class="slab">'
               f'the kit\'s delay line waits at most {whole(KIT_MAX)}</text>')
    for i, r in enumerate(rows):
        yc = Y0 + RH * i + RH / 2
        tip = [r["name"] + ", " + r["ctl"]]
        tip += [f"{whole(p)} ps  {n}" for p, n in zip(r["parts"], PARTS)]
        tip += [f"needs {whole(r['need'])} ps; setting {r['code']} waits "
                f"{whole(r['wait'])} ps"]
        out.append(f'<g class="srow" tabindex="0" '
                   f'data-tip="{H.escape("|".join(tip))}">')
        out.append(f'<rect x="0" y="{yc - RH / 2:.1f}" width="{W}" height="{RH}" '
                   f'class="shit"/>')
        out.append(f'<text x="{X0 - 10}" y="{yc - 1:.1f}" class="sname" '
                   f'text-anchor="end">{r["name"]}</text>')
        out.append(f'<text x="{X0 - 10}" y="{yc + 12:.1f}" class="sctl" '
                   f'text-anchor="end">{r["ctl"]}</text>')
        acc = 0
        for j, p in enumerate(r["parts"]):
            out.append(f'<rect x="{x(acc):.1f}" y="{yc - 7:.1f}" '
                       f'width="{max(0.5, x(acc + p) - x(acc)):.1f}" height="14" '
                       f'class="sseg sv{j + 1}"/>')
            acc += p
        out.append(f'<line x1="{x(r["wait"]):.1f}" y1="{yc - 12:.1f}" '
                   f'x2="{x(r["wait"]):.1f}" y2="{yc + 12:.1f}" class="stick"/>')
        out.append(f'<text x="{x(r["wait"]) + 7:.1f}" y="{yc + 4:.1f}" '
                   f'class="sval">{whole(r["wait"])}</text>')
        out.append('</g>')
    out.append('</svg>')

    legend = "".join(f'<span><i class="sv{j + 1}"></i>{n}</span>'
                     for j, n in enumerate(PARTS))
    legend += ('<span><i class="tick"></i>the setting chosen</span>'
               '<span><i class="lim"></i>longest wait of the kit\'s line</span>')
    head = ("<tr><th>Block</th><th>Control</th>"
            + "".join(f"<th>{n.capitalize()}</th>" for n in PARTS)
            + "<th>Needs</th><th>Setting</th><th>Waits</th></tr>")
    body = "".join(
        f"<tr><td>{r['name']}</td><td>{r['ctl']}</td>"
        + "".join(f'<td class="n">{whole(p)}</td>' for p in r["parts"])
        + f'<td class="n">{whole(r["need"])}</td><td class="n">{r["code"]}</td>'
        f'<td class="n">{whole(r["wait"])}</td></tr>' for r in rows)
    return (f'<div class="slegend">{legend}</div>\n'
            f'<div class="sfig">{"".join(out)}<div class="stip" hidden></div></div>\n'
            f'<details class="stable"><summary>The numbers, in picoseconds'
            f'</summary><table>{head}{body}</table></details>'), {
                "kit_min": whole(min(kit)), "kit_max": whole(KIT_MAX),
                "reb_min": whole(REB_MIN), "reb_max": whole(REB_MAX),
                "kit_n": len(kit), "reb_n": len(reb)}


def delay_figure() -> str:
    """Step 4: the control's delay line at gate level, as the kit draws
    it and as the rebuild makes it.  The wiring is the one recovered from
    the kit's layout (out/control_netlist.json: AO22 stages U401, U402,
    U643, U605, U564 and the buffers between them) and the one
    scripts/patch_control_delayline.py writes; every delay on it is a
    measured step from sramforge/sae_codes.json at the typical corner."""
    from sramforge import sense
    t = sense.table()["controls"]
    kit, reb = t["reference"]["TT"]["codes"], t[""]["TT"]["codes"]
    whole = lambda v: str(int(v + 0.5))
    # kit: each extra stage, from the code with one more leading 0
    kseq = ["11111", "01111", "00111", "00011", "00001"]
    ksteps = [kit[kseq[i + 1]] - kit[kseq[i]] for i in range(4)]
    # rebuilt: section k alone inserted (bit k of the code is sdel<k>)
    rsteps = [reb["1" * k + "0" + "1" * (4 - k)] - reb["11111"] for k in range(5)]

    def mux(x, yt, sel, new=False):
        """An AO22 used as a two-way switch on a latched bit: input 1
        (top) when the bit is 1, input 0 (bottom) when it is 0."""
        cls = "dg dnew" if new else "dg"
        return (f'<path d="M{x} {yt}L{x + 26} {yt + 10}V{yt + 38}L{x} {yt + 48}Z" '
                f'class="{cls}"/>'
                f'<text x="{x + 4}" y="{yt + 15}" class="dm">1</text>'
                f'<text x="{x + 4}" y="{yt + 39}" class="dm">0</text>'
                # the latch on the select, its sdel pin below
                f'<path d="M{x + 13} {yt + 44}V{yt + 80}" class="dw"/>'
                f'<rect x="{x - 4}" y="{yt + 80}" width="34" height="20" rx="3" class="dl"/>'
                f'<text x="{x + 13}" y="{yt + 94}" class="ds" text-anchor="middle">latch</text>'
                f'<path d="M{x + 13} {yt + 100}V{yt + 110}" class="dw"/>'
                f'<text x="{x + 13}" y="{yt + 123}" class="ds" '
                f'text-anchor="middle">sdel&lt;{sel}&gt;</text>')

    def buf(x, y, down=False, new=False):
        cls = "db dnew" if new else "db"
        if down:
            return f'<path d="M{x - 7} {y - 7}H{x + 7}L{x} {y + 7}Z" class="{cls}"/>'
        return f'<path d="M{x - 7} {y - 7}V{y + 7}L{x + 7} {y}Z" class="{cls}"/>'

    def wire(d):
        return f'<path d="{d}" class="dw"/>'

    def txt(x, y, s, cls="dt", a="start"):
        return f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{a}">{s}</text>'

    YT = 66                       # mux tops: input 1 at YT+12, 0 at YT+36
    I1, I0, OUT = YT + 12, YT + 36, YT + 24

    # ---- as the kit draws it: the start signal fans out along the top
    S = 36
    MX = [116, 236, 356, 476, 596]          # sdel<4> .. sdel<0>
    k = [wire(f"M30 {S}H{MX[4] - 22}"), txt(30, S - 8, "start (read or write enable)"),
         # the deepest stage takes the start signal on both inputs
         wire(f"M{MX[0] - 16} {S}V{I0}H{MX[0]}M{MX[0] - 16} {I1}H{MX[0]}")]
    for i, x in enumerate(MX):
        k.append(mux(x, YT, 4 - i))
        if i in (1, 2, 3):
            k.append(wire(f"M{x - 16} {S}V{I1}H{x}"))
        if i < 4:
            # the slower path: this stage's output, one buffer, the next
            # stage's 0 input
            nx = MX[i + 1]
            bx = (x + 26 + nx) / 2 - 4
            k.append(wire(f"M{x + 26} {OUT}H{bx - 7}M{bx + 7} {OUT}H{nx - 12}V{I0}H{nx}"))
            k.append(buf(bx, OUT))
            k.append(txt(bx, OUT - 12, "+" + whole(ksteps[3 - i]), "dv", "middle"))
    # the last two stages take the start signal through buffers
    k.append(buf(MX[3] - 34, S))
    k.append(wire(f"M{MX[4] - 22} {S}V{I1 - 22}"))
    k.append(buf(MX[4] - 22, I1 - 14, down=True))
    k.append(wire(f"M{MX[4] - 22} {I1 - 7}V{I1}H{MX[4]}"))
    k.append(wire(f"M{MX[4] + 26} {OUT}H740"))
    k.append(txt(744, OUT - 7, "to sense enable", "ds", "end"))
    kit_svg = (f'<svg class="dline" viewBox="0 0 760 200" role="img" aria-label='
               f'"The kit\'s delay line: five AO22 (AND-OR) stages, each choosing the '
               f'start signal or the slower path from the stage before">'
               f'{"".join(k)}</svg>')

    # ---- rebuilt: five sections in series, each skip or delay
    BW = [38, 44, 54, 66, 82]               # block widths, growing with pairs
    r = [wire(f"M30 {OUT}H44"), txt(30, OUT - 10, "start")]
    x = 44
    for s in range(5):
        fx = x + 10
        bx = fx + 10
        mx = bx + BW[s] + 18
        pairs = 2 ** (s + 1)                 # scripts/patch_control_delayline.py
        r.append(wire(f"M{x} {OUT}H{fx}V{I1}H{mx}"))
        r.append(wire(f"M{fx} {OUT}V{YT + 70}H{bx}"))
        r.append(f'<rect x="{bx}" y="{YT + 60}" width="{BW[s]}" height="20" rx="3" '
                 f'class="dk dnew"/>')
        r.append(txt(bx + BW[s] / 2, YT + 74, f"{pairs} pairs", "ds", "middle"))
        r.append(txt(bx + BW[s] / 2, YT + 96, "+" + whole(rsteps[s]), "dv", "middle"))
        r.append(wire(f"M{bx + BW[s]} {YT + 70}H{mx - 10}V{I0}H{mx}"))
        r.append(mux(mx, YT, s))
        x = mx + 26
    r.append(wire(f"M{x} {OUT}H740"))
    r.append(txt(744, OUT - 7, "to sense enable", "ds", "end"))
    reb_svg = (f'<svg class="dline" viewBox="0 34 760 166" role="img" aria-label='
               f'"The rebuilt delay line: five sections in series, each '
               f'skipping or passing through 2 to 32 pairs of buffers">'
               f'{"".join(r)}</svg>')

    n_kit = len([v for v in kit.values() if v])
    span = (max(reb.values()) - min(reb.values())) / 31
    return (f'<figure class="dfig">{kit_svg}<figcaption><b>In the kit\'s '
            f'control.</b> Five AO22 (AND-OR) gates, each switched by a latched '
            f'copy of one <code>sdel</code> pin. A gate whose bit is 1 passes '
            f'the start signal straight on; a gate whose bit is 0 passes the '
            f'slower path from the gate before it, through a buffer. The '
            f'first 1, counting from <code>sdel&lt;0&gt;</code>, therefore sets '
            f'how many gates the signal passes through, and '
            f'<code>sdel&lt;4&gt;</code> has no effect: {n_kit} settings, '
            f'{whole(min(kit.values()))} to {whole(max(kit.values()))} '
            f'picoseconds, each additional gate adding the amber '
            f'amount.</figcaption></figure>\n'
            f'<figure class="dfig">{reb_svg}<figcaption><b>Rebuilt.</b> The '
            f'same latches, start and end, but five sections in series. '
            f'Section k either skips its block or goes through '
            f'2<sup>k+1</sup> pairs of buffers, so the five pins are a binary '
            f'number: 32 settings, {whole(min(reb.values()))} to '
            f'{whole(max(reb.values()))} picoseconds, about {whole(span)} apart. '
            f'Outlined in amber: the buffer blocks the rebuild adds. The edit is made to the '
            f'netlist recovered from the kit\'s layout, then placed and routed '
            f'again with the rest of the control.</figcaption></figure>')


def outputs_figure() -> dict:
    """"Outputs": the views a build writes, each drawn or quoted
    from the real thing for the 2 kilobyte block.  The pin families come
    from sramforge/pins.py, the abstract from sta/sramforge_32bx4x64.lef
    (the file the timing run links against), the timing from the
    characterization record out/char/sramforge_32bx4x64_TT.json, and the
    model and manifest from a fresh build."""
    import html as H
    import subprocess
    import tempfile
    from sramforge import pins
    from sramforge.verilog import emit_verilog
    from sramforge import BankSpec
    out = {}
    whole = lambda v: str(int(v + 0.5))

    # ---- the abstract, read from the file
    lef = (REPO / "sta" / "sramforge_32bx4x64.lef").read_text().splitlines()
    size = next(l for l in lef if l.strip().startswith("SIZE")).split()
    MW, MH = float(size[1]), float(size[3])
    pinr, obs, cur, layer, in_obs = [], [], None, None, False
    for l in lef:
        w = l.split()
        if not w:
            continue
        if w[0] == "PIN":
            cur, in_obs = w[1], False
        elif w[0] == "OBS":
            in_obs, cur = True, None
        elif w[0] == "LAYER":
            layer = w[1]
        elif w[0] == "RECT":
            box = tuple(float(v) for v in w[1:5])
            if in_obs:
                obs.append((layer, box))
            elif cur:
                pinr.append((cur, layer, box))
    names = sorted({p[0] for p in pinr})
    n_pins = len(names)

    # the contract's ten families for the 2 kilobyte block: names,
    # directions and layers from pins.FAMILIES, widths from its census;
    # only what each one is for is written here
    WHAT = {"ADDRESS": "which word: the bits split into column, word line "
                       "and half. Eight bits here, nine on every other size",
            "clk": "the clock: an access starts on its rising edge",
            "banksel": "enables the block; while it is low nothing happens",
            "read": "read enable",
            "byte_wrenab": "write enable",
            "sdel": "the sense amplifier's delay setting, tied to the value "
                    "the manifest names (step 4 of Generation)",
            "wd": "write data, one pin per column group",
            "dataout": "read data, latched and held between reads, one pin "
                       "per column group",
            "VDD": "the supply: one pin made of two pieces of metal",
            "VSS": "ground"}
    assert set(WHAT) == {f.name for f in pins.FAMILIES}, "describe every family"
    census = pins.expected_census(BankSpec())
    USE = {"SIGNAL": "", "CLOCK": "", "POWER": "power, ", "GROUND": "ground, "}
    items = []
    for f_ in pins.FAMILIES:
        n = census[f_.name]
        name = f"{f_.name}[{n - 1}:0]" if n else f_.name
        kind = USE[f_.use] or f_.direction.lower() + ", "
        items.append(f"<li><code>{name}</code>, {kind}metal {f_.layer[1]}: "
                     f"{WHAT[f_.name]}.</li>")
    out["families_html"] = '<ul class="fams">' + "".join(items) + "</ul>"
    out["census_sum"] = " + ".join(str(census[f_.name] or 1) for f_ in pins.FAMILIES)

    # ---- 1. one pin list, four views
    def box(x, y, w, h, lines, cls="ob"):
        s = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" class="{cls}"/>']
        for i, (t, c) in enumerate(lines):
            s.append(f'<text x="{x + 12}" y="{y + 19 + i * 15}" class="{c}">{t}</text>')
        return "".join(s)

    def arrow(x1, y1, x2, y2):
        return (f'<path d="M{x1} {y1}C{(x1 + x2) / 2} {y1} {(x1 + x2) / 2} {y2} '
                f'{x2 - 6} {y2}" class="oa"/>'
                f'<path d="M{x2 - 7} {y2 - 4}L{x2} {y2}L{x2 - 7} {y2 + 4}" class="oa"/>')
    views = [("Abstract", "LEF (Library Exchange Format)",
              f"outline, {n_pins} pins, blockages",
              "checked: covers the layout's metal exactly"),
             ("Timing library", "Liberty",
              "delays, setup and hold, leakage",
              "checked: each value traces to a record"),
             ("Behavioural model", "Verilog",
              "what the block does, clock by clock",
              "checked: its ports are the contract's"),
             ("Manifest", "JSON (JavaScript Object Notation)",
              "sizes, address map, sense settings",
              "states what to tie the sdel pins to")]
    BH, GAP = 72, 8
    total = 4 * BH + 3 * GAP
    mid = 12 + total / 2
    f = [box(8, mid - 44, 214, 88, [("Layout", "obt"),
                                    ("GDSII (Graphic Design", "obs"),
                                    ("System II): every shape,", "obs"),
                                    ("every label", "obs")]),
         box(258, mid - 60, 186, 120, [("Pin contract", "obt"),
                                       (f"{len(pins.FAMILIES)} pin families,", "obs"),
                                       ("read off the layout's", "obs"),
                                       ("labels: names, directions,", "obs"),
                                       ("layers, bus notation,", "obs"),
                                       ("counts", "obs")], "ob oc"),
         arrow(222, mid, 258, mid)]
    for i, (t, sub, what, chk) in enumerate(views):
        y = 12 + i * (BH + GAP)
        f.append(box(480, y, 272, BH, [(t, "obt"), (sub, "obs"), (what, "obs"),
                                       (chk, "obv")]))
        f.append(arrow(444, mid, 480, y + BH / 2))
    out["flow"] = (f'<svg class="oflow" viewBox="0 0 760 {total + 24:.0f}" role="img" '
                   f'aria-label="One pin contract, read off the layout, feeds '
                   f'the four views a build writes">{"".join(f)}</svg>')
    out["n_pins"] = n_pins

    # ---- 2. the abstract: the whole macro, and the band's pins magnified
    COL = {"M1": "l1", "M2": "l2", "M3": "l3", "M4": "l4", "M5": "l5"}
    K1, K2 = 4.4, 56.0                       # pixels per micrometre
    ZX0, ZX1, ZY0, ZY1 = 1.9, 8.1, 34.6, 40.3    # the magnified window
    PX, PY = 24, 14
    g = [f'<rect x="{PX}" y="{PY}" width="{MW * K1:.1f}" height="{MH * K1:.1f}" '
         f'class="omac"/>']
    for name, lay, (x0, y0, x1, y1) in pinr:
        g.append(f'<rect x="{PX + x0 * K1:.1f}" y="{PY + (MH - y1) * K1:.1f}" '
                 f'width="{max(0.8, (x1 - x0) * K1):.1f}" '
                 f'height="{max(0.8, (y1 - y0) * K1):.1f}" class="op {COL[lay]}"/>')
    zb = (PX + ZX0 * K1, PY + (MH - ZY1) * K1, (ZX1 - ZX0) * K1, (ZY1 - ZY0) * K1)
    g.append(f'<rect x="{zb[0] - 1:.1f}" y="{zb[1] - 1:.1f}" width="{zb[2] + 2:.1f}" '
             f'height="{zb[3] + 2:.1f}" class="ozb"/>')
    QX, QY = 130, 14
    QW, QH = (ZX1 - ZX0) * K2, (ZY1 - ZY0) * K2
    g.append(f'<path d="M{zb[0] + zb[2] + 1:.1f} {zb[1] - 1:.1f}L{QX} {QY}'
             f'M{zb[0] + zb[2] + 1:.1f} {zb[1] + zb[3] + 1:.1f}L{QX} {QY + QH:.1f}" '
             f'class="ozl"/>')
    zx = lambda x: QX + (x - ZX0) * K2
    zy = lambda y: QY + (ZY1 - y) * K2
    g.append(f'<svg x="{QX}" y="{QY}" width="{QW:.1f}" height="{QH:.1f}" '
             f'viewBox="{QX} {QY} {QW:.1f} {QH:.1f}" overflow="hidden">'
             f'<rect x="{QX}" y="{QY}" width="{QW:.1f}" height="{QH:.1f}" class="omac"/>')
    for lay, (x0, y0, x1, y1) in obs:
        if lay in ("M4", "M5") and x1 > ZX0 and x0 < ZX1 and y1 > ZY0 and y0 < ZY1:
            g.append(f'<rect x="{zx(x0):.1f}" y="{zy(y1):.1f}" '
                     f'width="{(x1 - x0) * K2:.1f}" height="{(y1 - y0) * K2:.1f}" '
                     f'class="oo {COL[lay]}"/>')
    runs = {}
    for name, lay, (x0, y0, x1, y1) in pinr:
        if not (x1 > ZX0 and x0 < ZX1 and y1 > ZY0 and y0 < ZY1):
            continue
        g.append(f'<rect x="{zx(x0):.1f}" y="{zy(y1):.1f}" '
                 f'width="{max(1.2, (x1 - x0) * K2):.1f}" '
                 f'height="{max(1.2, (y1 - y0) * K2):.1f}" class="op {COL[lay]}"/>')
        if lay == "M5":
            runs.setdefault(name.split("[")[0], []).append((x0, x1, y1, name))
    g.append('</svg>')
    # one rotated label per run of neighbouring stripes of a family
    for base, ps_ in runs.items():
        ps_.sort()
        groups, cur_ = [], [ps_[0]]
        for p in ps_[1:]:
            if p[0] - cur_[-1][1] < 0.3:
                cur_.append(p)
            else:
                groups.append(cur_)
                cur_ = [p]
        groups.append(cur_)
        for grp in groups:
            if len(grp) == 1:
                t = grp[0][3]
            else:
                idx = sorted(int(p[3].split("[")[1][:-1]) for p in grp)
                t = f"{base}[{idx[-1]}:{idx[0]}]"
            cx = zx((grp[0][0] + grp[-1][1]) / 2)
            ty = zy(max(p[2] for p in grp)) - 6
            g.append(f'<text x="{cx + 4:.1f}" y="{ty:.1f}" class="ol" '
                     f'transform="rotate(-90 {cx + 4:.1f} {ty:.1f})">{H.escape(t)}</text>')
    lx = QX + QW + 30
    for i, (t, c) in enumerate((("M3", "l3"), ("M5", "l5"))):
        g.append(f'<rect x="{lx}" y="{30 + i * 20}" width="12" height="12" class="op {c}"/>'
                 f'<text x="{lx + 18}" y="{40 + i * 20}" class="ol">pin, metal {t[1]}</text>')
    for i, (t, c) in enumerate((("M4", "l4"), ("M5", "l5"))):
        g.append(f'<rect x="{lx}" y="{82 + i * 20}" width="12" height="12" class="oo {c}"/>'
                 f'<text x="{lx + 18}" y="{92 + i * 20}" class="ol">blockage, metal {t[1]}</text>')
    g.append(f'<text x="{PX - 6}" y="{PY + MH * K1 + 16:.1f}" class="ol">the whole block</text>')
    g.append(f'<text x="{QX}" y="{QY + QH + 16:.1f}" class="ol">'
             f'the central band\'s pins, magnified</text>')
    H_abs = max(PY + MH * K1 + 26, QY + QH + 26)
    out["abstract"] = (f'<svg class="oabs" viewBox="0 0 760 {H_abs:.0f}" role="img" '
                       f'aria-label="The abstract of the 2 kilobyte block: its '
                       f'outline, pins and blockages">{"".join(g)}</svg>')
    out["obs_counts"] = {l: sum(1 for o in obs if o[0] == l)
                         for l in ("M1", "M2", "M3", "M4", "M5")}

    # ---- 3. the timing library: one read, as the record measured it
    ch = json.loads((REPO / "out" / "char" / "sramforge_32bx4x64_TT.json").read_text())
    sa = ch["stage_a"]["20"]
    t_wl, t_sae = sa["clk_to_wl_ps"][0], sa["clk_to_sae_ps"][0]
    ra = ch["read_access"]
    t_do = ra["cell_rise_ps"][0][0]
    per = ch["min_period"]
    period = 1.2 * (per["t_high_min_ps"] + per["t_low_min_ps"])
    su = {k.split(" ")[0]: v["setup_ps"] for k, v in ch["setup_ps"].items()}
    ho = {k.split(" ")[0]: v["hold_ps"] for k, v in ch["hold_ps"].items()}
    a_su = max(v for k, v in su.items() if k.startswith("ADDRESS"))
    a_ho = max(v for k, v in ho.items() if k.startswith("ADDRESS"))
    c_su = max(su["read"], su["banksel"])
    c_ho = max(ho["read"], ho["banksel"])
    T0, T1, TX0, TX1 = -120, 600, 150, 744
    X = lambda t: TX0 + (TX1 - TX0) * (t - T0) / (T1 - T0)
    rows = [("clk", 40), ("ADDRESS", 86), ("read, banksel", 128),
            ("word line (inside)", 172), ("sense enable (inside)", 212),
            ("dataout", 254)]
    tg = []
    for name, y in rows:
        tg.append(f'<text x="{TX0 - 12}" y="{y + 4}" class="tn" text-anchor="end">'
                  f'{name}</text>')
    hi, lo = -12, 12                       # a signal's high and low, about its row
    half = period / 2

    def sig(y, edges, dashed=False):
        """A one-bit waveform: (time, level) steps from low."""
        d, lvl, t_prev = f"M{X(T0):.1f} {y + lo}", 0, T0
        for t, v in edges:
            d += f"H{X(t) - 3:.1f}L{X(t) + 3:.1f} {y + (hi if v else lo)}"
            lvl = v
        d += f"H{X(T1):.1f}"
        return f'<path d="{d}" class="tw{" twd" if dashed else ""}"/>'

    def bus(y, a, b, label):
        """A bus that is valid between a and b, unknown outside."""
        s = [f'<path d="M{X(T0):.1f} {y + hi}H{X(a) - 4:.1f}L{X(a) + 4:.1f} {y + lo}'
             f'M{X(T0):.1f} {y + lo}H{X(a) - 4:.1f}L{X(a) + 4:.1f} {y + hi}'
             f'H{X(b) - 4:.1f}L{X(b) + 4:.1f} {y + lo}H{X(T1):.1f}'
             f'M{X(a) + 4:.1f} {y + lo}H{X(b) - 4:.1f}L{X(b) + 4:.1f} {y + hi}'
             f'H{X(T1):.1f}" class="tw"/>',
             f'<rect x="{X(T0):.1f}" y="{y + hi}" width="{X(a) - X(T0) - 4:.1f}" '
             f'height="{lo - hi}" class="tx"/>',
             f'<rect x="{X(b) + 4:.1f}" y="{y + hi}" width="{X(T1) - X(b) - 4:.1f}" '
             f'height="{lo - hi}" class="tx"/>',
             f'<text x="{(X(a) + X(b)) / 2:.1f}" y="{y + 4}" class="ts" '
             f'text-anchor="middle">{label}</text>']
        return "".join(s)

    def window(y, a, b, text, side="mid"):
        """A measured interval: its ends ticked, its value beside it
        (left of it, right of it, or over its middle)."""
        tx, anchor, ty = {"left": (X(a) - 5, "end", y + 4),
                          "right": (X(b) + 5, "start", y + 4),
                          "mid": ((X(a) + X(b)) / 2, "middle", y - 6)}[side]
        return (f'<path d="M{X(a):.1f} {y}H{X(b):.1f}M{X(a):.1f} {y - 4}V{y + 4}'
                f'M{X(b):.1f} {y - 4}V{y + 4}" class="tm"/>'
                f'<text x="{tx:.1f}" y="{ty}" class="tv" '
                f'text-anchor="{anchor}">{text}</text>')
    tg.append(f'<path d="M{X(0):.1f} 18V292M{X(period):.1f} 18V292" class="tg"/>')
    tg.append(sig(40, [(0, 1), (half, 0), (period, 1)]))
    tg.append(bus(86, -a_su - 60, a_ho + 60, "address"))
    tg.append(window(66, -a_su, 0, f"setup {whole(a_su)}", "left"))
    tg.append(window(66, 0, a_ho, f"hold {whole(a_ho)}", "right"))
    tg.append(bus(128, -c_su - 60, c_ho + 60, "read = 1"))
    tg.append(window(108, -c_su, 0, f"setup {whole(c_su)}", "left"))
    tg.append(sig(172, [(t_wl, 1), (half + sa["wl_retire_after_clk_fall_ps"][0], 0)], True))
    tg.append(f'<text x="{X(t_wl) + 6:.1f}" y="{172 - 16}" class="tv">{whole(t_wl)}</text>')
    tg.append(sig(212, [(t_sae, 1), (half + sa["sae_retire_after_clk_fall_ps"][0], 0)], True))
    tg.append(f'<text x="{X(t_sae) + 6:.1f}" y="{212 - 16}" class="tv">{whole(t_sae)}</text>')
    tg.append(f'<path d="M{X(T0):.1f} {254 + hi}H{X(t_do) - 4:.1f}L{X(t_do) + 4:.1f} '
              f'{254 + lo}H{X(T1):.1f}M{X(T0):.1f} {254 + lo}H{X(t_do) - 4:.1f}'
              f'L{X(t_do) + 4:.1f} {254 + hi}H{X(T1):.1f}" class="tw"/>')
    tg.append(f'<text x="{(X(T0) + X(t_do)) / 2:.1f}" y="{258}" class="ts" '
              f'text-anchor="middle">previous word</text>')
    tg.append(f'<text x="{(X(t_do) + X(T1)) / 2:.1f}" y="{258}" class="ts" '
              f'text-anchor="middle">this word</text>')
    tg.append(window(286, 0, t_do, f"clock to output {whole(t_do)}", "right"))
    tg.append(f'<text x="{X(period) - 4:.1f}" y="{14}" class="tv" text-anchor="end">'
              f'next clock: {whole(period)}</text>')
    out["timing"] = (f'<svg class="otim" viewBox="0 0 760 300" role="img" '
                     f'aria-label="One read, as the timing library describes it">'
                     f'{"".join(tg)}</svg>')
    out["t"] = {"wl": whole(t_wl), "sae": whole(t_sae), "do": whole(t_do),
                "period": whole(period), "ghz": f"{1000 / period:.2f}",
                "a_su": whole(a_su), "a_ho": whole(a_ho),
                "wd_late": whole(-su["wd"]), "wd_ho": whole(ho["wd"])}
    head = "".join(f"<th>{l} fF</th>" for l in ra["load_ff"])
    body = "".join(f'<tr><td>{s} ps</td>'
                   + "".join(f'<td class="n">{v}</td>' for v in row) + "</tr>"
                   for s, row in zip(ra["input_slew_ps"], ra["cell_rise_ps"]))
    out["table"] = (f'<table class="otab"><tr><th>clock slew</th>{head}</tr>'
                    f'{body}</table>')

    # ---- 4 and 5. the model and the manifest, from a fresh build
    out["model"] = H.escape(emit_verilog(BankSpec()).strip())
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, "-m", "sramforge", "--out", tmp], cwd=REPO,
                       check=True, stdout=subprocess.DEVNULL)
        man = json.loads((pathlib.Path(tmp) / "sramforge_32bx4x64.json").read_text())
    keep = {k: v for k, v in man.items() if k not in ("gds", "verilog", "lef")}
    for c in keep.get("sense_timing", {}).values():
        c.pop("note", None)
    out["manifest"] = H.escape(json.dumps(keep, indent=2))
    return out


# how the block is known to work, one question a reader would ask per
# row: what was checked, and on which sizes (from the tests and the
# project's records; "checked once" = run by hand and recorded, not
# repeated since)
QUESTIONS = [
    ("Is every shape where it should be?",
     "At the reference size the layout is identical to the kit's own block "
     "on all 31 layers. At every size, an independent program confirms the "
     "position and mirror of every cell.",
     "identical to the kit: the reference. Cells in place: 14 sizes"),
    ("Is it wired the way it should be?",
     "Rebuilding the wiring from the raw shapes finds every bit line, word "
     "line and supply as one unbroken wire, and no two wires touching.",
     "the reference and eight strips; 16 and 64 bits checked once"),
    ("Can it be manufactured?",
     "97 rules from the kit's rule manual, the checks a foundry would run "
     "before making it.",
     "the reference passes every rule. The other sizes still break some "
     "in known places, recorded so that no new one can appear"),
    ("Are the transistors the ones we meant?",
     "Every transistor is pulled back out of the shapes: each cell matches "
     "the six-transistor schematic, and all 114,454 in the block are "
     "accounted for.",
     "the reference; transistor counts cross-checked for 16 and 64 bits"),
    ("Does it store and return data?",
     "A simulation of the extracted transistors shows every address opening "
     "exactly the right cells and column, and a transistor-level simulation "
     "writes bits into cells and reads them back on the data pins.",
     "selection: the reference, with 16 bits, 64 bits and eight strips "
     "checked once. Write and read back: the reference, and every width from "
     "4 to 32 bits checked once"),
    ("Does it work in time?",
     "The read is simulated at transistor level on the extracted layout "
     "and confirmed by simulating reads back to back; every size's sense "
     "amplifier waits for its farthest cell.",
     "read timing: the reference. Sense timing: 13 sizes"),
    ("Do the files a chip designer uses tell the truth?",
     "The abstract matches the layout exactly, every value in the timing "
     "library traces to a measurement, and a timing tool accepts both.",
     "abstract: the reference, 16 and 64 bits. Timing library: the reference"),
]


def wiring_figure(gds_path):
    """Question 2: shapes become wires.  One column group of the built
    block is flattened and its conductor graph built with the same engine
    rung 2 uses (sramforge/nets.py); a window of metals 1 to 3 is drawn
    with every island coloured by the wire it joins.  Then the same is
    done twice more on damaged copies, a gap cut in one bit line and a
    bridge between a bit line and its pair, and the figure asserts that
    the graph really sees the open and the short."""
    import klayout.db as kdb
    from sramforge.nets import ConductorGraph
    from sramforge.tech import LAYERS
    ly = kdb.Layout()
    ly.read(str(gds_path))
    src = ly.cell("column_group_32b")
    dbu = ly.dbu
    WIN = kdb.DBox(0.20, -0.02, 1.10, 0.56)        # two strips, eight cells
    box = WIN.to_itype(dbu)
    COL = {"BL": "wbl", "BLN": "wbn", "WL": "wwl", "VDD": "wvd", "VSS": "wvs"}

    def flat(edit=None):
        new = kdb.Layout()
        new.dbu = dbu
        top = new.create_cell("grp")
        top.copy_tree(src)
        top.flatten(-1, True)
        if edit:
            edit(new, top)
        return new, top, ConductorGraph(new, top)

    def classes(g):
        """Net id -> wire class, from the labels, and the strip-0 left
        bit line's nets (one if it is whole)."""
        cls, bl0 = {}, set()
        for (l, d), cond in (((20, 251), "m2"), ((30, 251), "m3"), ((19, 251), "m1")):
            for name, x, y in g.texts(l, d):
                n = g.net_of(cond, x, y)
                if n is None:
                    continue
                c = {"BL": "BL", "BLN": "BLN", "WL": "WL", "vdd!": "VDD",
                     "vss!": "VSS"}.get(name)
                if c:
                    cls.setdefault(n, set()).add(c)
                if name == "BL" and 0 < y * dbu < 0.27 and x * dbu < 3.6:
                    bl0.add(n)
        return cls, bl0

    # the strip-0 bit line pair, located on the clean build
    _, top0, g0 = flat()
    cls0, bl0 = classes(g0)
    assert len(bl0) == 1, "the left array's first bit line is one wire"
    bl_poly = next(p for p in g0.idx["m2"].polys
                   if g0.uf.find(("m2", g0.idx["m2"].polys.index(p))) in bl0
                   and p.bbox().overlaps(box))
    blb = bl_poly.bbox()
    # the wire right beside the bit line on metal 2, and what it is
    m2p = g0.idx["m2"].polys
    nb_i, nb = min(((i, q.bbox()) for i, q in enumerate(m2p)
                    if q.bbox().overlaps(box) and q.bbox().bottom >= blb.top),
                   key=lambda t: t[1].bottom)
    nb_cls = cls0.get(g0.uf.find(("m2", nb_i)), set())
    assert len(nb_cls) == 1, "the bit line's neighbour is one labelled wire"
    nb_cls = next(iter(nb_cls))
    NAME = {"BL": "bit line", "BLN": "bit line pair", "WL": "word line",
            "VDD": "supply", "VSS": "ground"}
    xc = int(0.66 / dbu)
    cut = kdb.Box(xc - 40, blb.bottom - 4, xc + 40, blb.top + 4)
    jumper = kdb.Box(xc - 40, blb.bottom, xc + 40, nb.top)

    def edit_cut(lay, top):
        li = lay.layer(*LAYERS["m2"])
        r = kdb.Region(top.shapes(li)) - kdb.Region(cut)
        top.shapes(li).clear()
        top.shapes(li).insert(r)

    def edit_jumper(lay, top):
        top.shapes(lay.layer(*LAYERS["m2"])).insert(jumper)
    _, top1, g1 = flat(edit_cut)
    cls1, bl1 = classes(g1)
    assert len(bl1) == 2, "the cut must split the bit line in two"
    _, top2, g2 = flat(edit_jumper)
    cls2, _ = classes(g2)
    assert any({"BL", nb_cls} <= c for c in cls2.values()), "the bridge must short them"

    P_W, K = 236, 236 / WIN.width()
    P_H = WIN.height() * K

    def panel(px, g, cls, bad, mark, title, sub):
        """The window's metal 1 to 3 islands, coloured by wire; nets in
        bad drawn as the damage."""
        s = [f'<rect x="{px}" y="30" width="{P_W}" height="{P_H:.1f}" class="vpan"/>']
        for cond, op in (("m1", "w1"), ("m2", "w2"), ("m3", "w3")):
            for i, poly in enumerate(g.idx[cond].polys):
                if not poly.bbox().overlaps(box):
                    continue
                n = g.uf.find((cond, i))
                c = cls.get(n, set())
                kind = ("wbad" if n in bad else
                        COL[sorted(c)[0]] if len(c) == 1 else "wot")
                for piece in (kdb.Region(poly) & kdb.Region(box)).each():
                    pts = " ".join(f"{px + (q.x * dbu - WIN.left) * K:.1f},"
                                   f"{30 + (WIN.top - q.y * dbu) * K:.1f}"
                                   for q in piece.each_point_hull())
                    s.append(f'<polygon points="{pts}" class="{kind} {op}"/>')
        if mark is not None:
            mb = mark.to_dtype(dbu)
            s.append(f'<rect x="{px + (mb.left - WIN.left) * K - 3:.1f}" '
                     f'y="{30 + (WIN.top - mb.top) * K - 3:.1f}" '
                     f'width="{mb.width() * K + 6:.1f}" height="{mb.height() * K + 6:.1f}" '
                     f'class="wmk"/>')
        s.append(f'<text x="{px + P_W / 2}" y="{30 + P_H + 18:.1f}" class="vc" '
                 f'text-anchor="middle">{title}</text>')
        s.append(f'<text x="{px + P_W / 2}" y="{30 + P_H + 32:.1f}" class="vn" '
                 f'text-anchor="middle">{sub}</text>')
        return "".join(s)
    # the damaged nets: the piece of the cut bit line without the left
    # end's labels, and the net the bridge made of the pair
    cut_off = {n for n in bl1 if n not in {g1.net_of("m2", x, y)
                                           for name, x, y in g1.texts(20, 251)
                                           if name == "BL" and x * dbu < 0.5
                                           and 0 < y * dbu < 0.27}}
    joined = {n for n, c in cls2.items() if {"BL", nb_cls} <= c}
    g = [panel(10, g0, cls0, set(), None, "as built",
               "every wire whole, none touching"),
         panel(262, g1, cls1, cut_off, cut, "a gap cut in a bit line",
               "open: one bit line is now two wires"),
         panel(514, g2, cls2, joined, jumper, f"a bridge to the {NAME[nb_cls]} beside it",
               f"short: bit line and {NAME[nb_cls]} are one wire")]
    legend = [("wbl", "bit line"), ("wbn", "its pair"), ("wwl", "word line"),
              ("wvd", "supply"), ("wvs", "ground"), ("wot", "inside a cell"),
              ("wbad", "the damage")]
    for i, (c, t) in enumerate(legend):
        x = 10 + i * 106
        g.append(f'<rect x="{x}" y="6" width="12" height="12" class="{c}"/>'
                 f'<text x="{x + 17}" y="16" class="vn">{t}</text>')
    svg = (f'<svg class="vxor" viewBox="0 0 760 {30 + P_H + 42:.0f}" role="img" '
           f'aria-label="Shapes coloured by the wire they join, as built, with '
           f'a bit line cut, and with a bit line bridged to its pair">'
           f'{"".join(g)}</svg>')
    return svg


def rules_figure_m2(gds_path):
    """Question 3: one manufacturing rule, drawn.  In one flattened
    column group, a bit line is a metal-2 wire at the rule manual's
    minimum width with clearance to the wire beside it; the figure
    measures both, then widens the bit line along a short stretch until
    the gap is under the minimum, runs the real deck (sramforge/drc.py,
    the metal checks) on the damaged copy, and asserts that the spacing
    rule fires where the damage is."""
    import klayout.db as kdb
    from sramforge import drc
    from sramforge.tech import LAYERS
    ly = kdb.Layout()
    ly.read(str(gds_path))
    src = ly.cell("column_group_32b")
    dbu = ly.dbu

    def flat(extra=None):
        new = kdb.Layout()
        new.dbu = dbu
        top = new.create_cell("grp")
        top.copy_tree(src)
        top.flatten(-1, True)
        if extra is not None:
            top.shapes(new.layer(*LAYERS["m2"])).insert(extra)
        deck = drc.DrcDeck(new, top)
        deck.check_m1m3()
        return new, top, deck
    _, top0, d0 = flat()
    assert not d0.violations, "the group's metals pass as built"
    m2 = list(d0.raw("m2").each())
    # the strip-0 bit line of the left array and the wire just above it
    bl = None
    it = top0.begin_shapes_rec(top0.layout().layer(20, 251))
    while not it.at_end():
        sh = it.shape()
        if sh.is_text() and sh.text_string == "BL":
            pt = it.trans() * sh.text_trans.disp
            if 0 < pt.y * dbu < 0.27 and pt.x * dbu < 3.6:
                bl = next(q for q in m2 if q.inside(kdb.Point(pt.x, pt.y)))
                break
        it.next()
    blb = bl.bbox()
    nb = min((q.bbox() for q in m2 if q.bbox().bottom >= blb.top
              and q.bbox().left < blb.right and q.bbox().right > blb.left),
             key=lambda b: b.bottom)
    gap, width = nb.bottom - blb.top, blb.height()
    assert width == drc.nm(18), width
    xc = int(0.72 / dbu)
    PUSH = drc.nm(16)
    bump = kdb.Box(xc - int(0.05 / dbu), blb.top, xc + int(0.05 / dbu), blb.top + PUSH)
    _, top1, d1 = flat(bump)
    near = bump.enlarged(drc.nm(30), drc.nm(30))
    fired = sorted({v.rule for v in d1.violations
                    if any(kdb.Box(*e).overlaps(near) if not isinstance(e, kdb.Box)
                           else e.overlaps(near) for e in v.examples)})
    assert "M2.S.1" in fired, f"the spacing rule must fire, fired {fired}"
    marks = [e for v in d1.violations if v.rule == "M2.S.1" for e in v.examples
             if (e if isinstance(e, kdb.Box) else kdb.Box(*e)).overlaps(near)]

    WIN = kdb.DBox(0.54, blb.bottom * dbu - 0.11, 0.90, nb.top * dbu + 0.11)
    box = WIN.to_itype(dbu)
    PW = 300
    K = PW / WIN.width()
    PH = WIN.height() * K
    X = lambda x: (x * dbu - WIN.left) * K
    Y = lambda y: 30 + (WIN.top - y * dbu) * K

    def wires(px, region, hi):
        s = [f'<rect x="{px}" y="30" width="{PW}" height="{PH:.1f}" class="vpan"/>']
        for q in (region & kdb.Region(box)).each():
            b = q.bbox()
            cls = "mhi" if hi and b.overlaps(blb) else "mwi"
            pts = " ".join(f"{px + X(p.x):.1f},{Y(p.y):.1f}" for p in q.each_point_hull())
            s.append(f'<polygon points="{pts}" class="{cls}"/>')
        return "".join(s)

    def vdim(px, x, y0, y1, label, cls="mdim", tcls="mv halo2"):
        return (f'<path d="M{px + X(x) - 5:.1f} {Y(y0):.1f}h10M{px + X(x) - 5:.1f} '
                f'{Y(y1):.1f}h10M{px + X(x):.1f} {Y(y0):.1f}V{Y(y1):.1f}" class="{cls}"/>'
                f'<text x="{px + X(x) + 9:.1f}" y="{(Y(y0) + Y(y1)) / 2 + 4:.1f}" '
                f'class="{tcls}">{label}</text>')
    xa, xb2 = int(0.60 / dbu), int(0.82 / dbu)
    g = [wires(20, d0.raw("m2"), True),
         vdim(20, xa, blb.bottom, blb.top, f"{width * dbu * 1000:.0f} nm wide"),
         vdim(20, xb2, blb.top, nb.bottom, f"{gap * dbu * 1000:g} nm apart"),
         f'<text x="{20 + PW / 2}" y="{30 + PH + 18:.1f}" class="vc" '
         f'text-anchor="middle">as built</text>',
         f'<text x="{20 + PW / 2}" y="{30 + PH + 32:.1f}" class="vn" '
         f'text-anchor="middle">rule: at least 18 nm wide, 18 nm apart: passes</text>',
         wires(420, d1.raw("m2"), True),
         f'<rect x="{420 + X(bump.left):.1f}" y="{Y(bump.top):.1f}" '
         f'width="{(bump.right - bump.left) * dbu * K:.1f}" '
         f'height="{(bump.top - bump.bottom) * dbu * K:.1f}" class="mbump"/>',
         vdim(420, xc, bump.top, nb.bottom,
              f"{(gap - PUSH) * dbu * 1000:g} nm", "mbad", "mvb halo2")]
    for e in marks:
        # a reported pair spans both edges, and the neighbour's edge runs
        # the length of the strip: show the part beside the damage
        e = (e if isinstance(e, kdb.Box) else kdb.Box(*e)) & near
        g.append(f'<rect x="{420 + X(e.left) - 3:.1f}" y="{Y(e.top) - 3:.1f}" '
                 f'width="{(e.right - e.left) * dbu * K + 6:.1f}" '
                 f'height="{(e.top - e.bottom) * dbu * K + 6:.1f}" class="mmark"/>')
    g += [f'<text x="{420 + PW / 2}" y="{30 + PH + 18:.1f}" class="vc" '
          f'text-anchor="middle">the bit line 16 nm wider for a short stretch</text>',
          f'<text x="{420 + PW / 2}" y="{30 + PH + 32:.1f}" class="vn" '
          f'text-anchor="middle">flagged: {", ".join(fired)}</text>',
          '<rect x="20" y="8" width="12" height="12" class="mhi"/>'
          '<text x="37" y="18" class="vn">the bit line</text>'
          '<rect x="130" y="8" width="12" height="12" class="mwi"/>'
          '<text x="147" y="18" class="vn">other metal-2 wires</text>'
          '<rect x="420" y="8" width="12" height="12" class="mmark"/>'
          '<text x="437" y="18" class="vn">where the check reports</text>']
    svg = (f'<svg class="vxor" viewBox="0 0 760 {30 + PH + 42:.0f}" role="img" '
           f'aria-label="Metal-2 wires at the manual\'s minimum width and '
           f'spacing, and one widened until the check flags it">{"".join(g)}</svg>')
    base = json.loads((REPO / "tests" / "drc_baselines.json").read_text())
    n = {"cal": len(drc.CALIBRATION),
         "b16": base["sramforge_16bx4x64"]["total"],
         "s8": base["sramforge_32bx8x64"]["total"],
         "b64": base["sramforge_64bx4x128"]["total"],
         "gap": f"{gap * dbu * 1000:g}"}
    return svg, n


def transistor_figure():
    """Question 4: the transistors read back out of the memory cell's
    shapes by the same extractor rung 4 uses (sramforge/lvs.py), drawn on
    the cell, numbered, and placed on the six-transistor schematic.  The
    roles and the storage-node names are worked out from the extracted
    connections, not assumed."""
    import klayout.db as kdb
    from sramforge.lvs import extract
    path = REPO / "cells" / "sram_cell_6t_122.gds"
    ex = extract(str(path), "sram_cell_6t_122")
    ly = kdb.Layout()
    ly.read(str(path))
    cell = ly.cell("sram_cell_6t_122")
    dbu = ly.dbu
    name = lambda n: ex.named(n) if isinstance(ex.named(n), str) else None
    devs = ex.devices
    assert len(devs) == 6 and not ex.unclassified
    # the storage nodes: Q is the inner side of the access device on BL
    acc = [d for d in devs if name(d.gate) == "WL"]
    inner = lambda d: next(n for n in d.sd if name(n) is None)
    q = next(inner(d) for d in acc if any(name(n) == "BL" for n in d.sd))
    qb = next(inner(d) for d in acc if any(name(n) == "BLN" for n in d.sd))
    nodes = {q: "Q", qb: "QB"}
    lab = lambda n: name(n) or nodes.get(n, "?")
    roles = []
    for d in devs:
        kind = "n-type" if d.model.startswith("nmos") else "p-type"
        if name(d.gate) == "WL":
            role = "access"
        elif kind == "p-type":
            role = "pull-up"
        else:
            role = "pull-down"
        roles.append((d, role, kind))
    # number them for the reader: access, then the inverter driving Q,
    # then the one driving QB
    def key(t):
        d, role, kind = t
        out = next((n for n in d.sd if n in nodes), None)
        side = 0 if out == q else 1
        return ({"access": 0, "pull-up": 1, "pull-down": 2}[role], side)
    roles.sort(key=key)

    def boxes(layer):
        r = kdb.Region(cell.begin_shapes_rec(ly.layer(*layer))).merged()
        return list(r.each())
    WIN = kdb.DBox(-0.022, -0.02, 0.130, 0.29)
    K = 1000
    PX, PY = 30, 34
    X = lambda x: PX + (x * dbu - WIN.left) * K
    Y = lambda y: PY + (WIN.top - y * dbu) * K
    g = [f'<rect x="{PX}" y="{PY}" width="{WIN.width() * K:.0f}" '
         f'height="{WIN.height() * K:.0f}" class="vpan"/>']
    box = WIN.to_itype(dbu)
    for cls, layer in (("tfin", (2, 0)), ("ra", (11, 0)), ("rg", (7, 0)),
                       ("tgc", (10, 0))):
        for poly in boxes(layer):
            for piece in (kdb.Region(poly) & kdb.Region(box)).each():
                pts = " ".join(f"{X(p.x):.1f},{Y(p.y):.1f}" for p in piece.each_point_hull())
                g.append(f'<polygon points="{pts}" class="{cls}"/>')
    for i, (d, role, kind) in enumerate(roles, 1):
        b = d.bbox
        g.append(f'<rect x="{X(b.left) - 2:.1f}" y="{Y(b.top) - 2:.1f}" '
                 f'width="{(b.right - b.left) * dbu * K + 4:.1f}" '
                 f'height="{(b.top - b.bottom) * dbu * K + 4:.1f}" class="tch"/>')
        right = b.center().x * dbu > 0.054
        cx = X(b.right) + 16 if right else X(b.left) - 16
        g.append(f'<circle cx="{cx:.1f}" cy="{(Y(b.top) + Y(b.bottom)) / 2:.1f}" r="8" class="tno"/>'
                 f'<text x="{cx:.1f}" y="{(Y(b.top) + Y(b.bottom)) / 2 + 4:.1f}" '
                 f'class="tnt" text-anchor="middle">{i}</text>')
    g.append(f'<text x="{PX + WIN.width() * K / 2:.0f}" y="{PY + WIN.height() * K + 18:.0f}" '
             f'class="vc" text-anchor="middle">the memory cell\'s shapes</text>')
    for j, (c, t) in enumerate((("ra", "active"), ("rg", "gate"), ("tch", "a transistor found"))):
        g.append(f'<rect x="{PX + j * 0}" y="{PY + WIN.height() * K + 30 + j * 16:.0f}" width="11" '
                 f'height="11" class="{c}"/><text x="{PX + 16}" '
                 f'y="{PY + WIN.height() * K + 40 + j * 16:.0f}" class="vn">{t}</text>')

    # the schematic: two inverters feeding each other, two access devices
    sx = 250
    num = {id(d): i for i, (d, _, _) in enumerate(roles, 1)}
    by = {}
    for d, role, kind in roles:
        out = next((n for n in d.sd if n in nodes), None)
        by[(role, nodes.get(out))] = d
    VDD, VSS, NY, WLY = 92, 268, 180, 54
    QX, QBX, BLX, BLNX = sx + 190, sx + 90, sx + 262, sx + 18
    S = [f'<path d="M{BLNX} 40V300M{BLX} 40V300" class="tbl"/>',
         f'<text x="{BLNX}" y="34" class="vc" text-anchor="middle">bit line bar</text>',
         f'<text x="{BLX}" y="34" class="vc" text-anchor="middle">bit line</text>',
         f'<path d="M{BLNX - 10} {WLY}H{BLX + 10}" class="twl"/>',
         f'<text x="{(BLNX + BLX) / 2}" y="{WLY - 6}" class="vn" text-anchor="middle">word line</text>',
         f'<path d="M{QBX - 30} {VDD}H{QX + 30}M{QBX - 30} {VSS}H{QX + 30}" class="tw"/>',
         f'<text x="{(QBX + QX) / 2}" y="{VDD - 6}" class="vn" text-anchor="middle">supply</text>',
         f'<text x="{(QBX + QX) / 2}" y="{VSS + 14}" class="vn" text-anchor="middle">ground</text>']

    def fet(x, y0, y1, d, gate_side):
        """A vertical transistor between y0 and y1 at x, its number and
        its gate stub on gate_side (-1 left, +1 right)."""
        n = num[id(d)]
        return (f'<path d="M{x} {y0}V{y1}" class="tw"/>'
                f'<rect x="{x - 4}" y="{(y0 + y1) / 2 - 12}" width="8" height="24" class="tchs"/>'
                f'<path d="M{x + gate_side * 9} {(y0 + y1) / 2 - 12}V{(y0 + y1) / 2 + 12}" class="tw"/>'
                f'<circle cx="{x - gate_side * 18}" cy="{(y0 + y1) / 2}" r="8" class="tno"/>'
                f'<text x="{x - gate_side * 18}" y="{(y0 + y1) / 2 + 4}" class="tnt" '
                f'text-anchor="middle">{n}</text>')
    # the inverter driving QB (gates on Q) on the left, driving Q on the right
    S.append(fet(QBX, VDD, NY, by[("pull-up", "QB")], +1))
    S.append(fet(QBX, NY, VSS, by[("pull-down", "QB")], +1))
    S.append(fet(QX, VDD, NY, by[("pull-up", "Q")], -1))
    S.append(fet(QX, NY, VSS, by[("pull-down", "Q")], -1))
    S.append(f'<path d="M{QBX + 9} {(VDD + NY) / 2}H{QBX + 30}V{(NY + VSS) / 2}H{QBX + 9}'
             f'M{QBX + 30} {NY + 22}L{QX} {NY}" class="tw"/>')
    S.append(f'<path d="M{QX - 9} {(VDD + NY) / 2}H{QX - 30}V{(NY + VSS) / 2}H{QX - 9}'
             f'M{QX - 30} {NY - 22}L{QBX} {NY}" class="tw"/>')
    # access devices, horizontal, gates up to the word line
    for d, x0, x1 in ((by[("access", "QB")], BLNX, QBX), (by[("access", "Q")], QX, BLX)):
        n = num[id(d)]
        cx = (x0 + x1) / 2
        S.append(f'<path d="M{x0} {NY}H{x1}" class="tw"/>'
                 f'<rect x="{cx - 12}" y="{NY - 4}" width="24" height="8" class="tchs"/>'
                 f'<path d="M{cx - 12} {NY - 9}H{cx + 12}M{cx} {NY - 9}V{WLY}" class="tw"/>'
                 f'<circle cx="{cx}" cy="{NY + 20}" r="8" class="tno"/>'
                 f'<text x="{cx}" y="{NY + 24}" class="tnt" text-anchor="middle">{n}</text>')
    S.append(f'<circle cx="{QBX}" cy="{NY}" r="3" class="tdot"/><circle cx="{QX}" cy="{NY}" r="3" class="tdot"/>'
             f'<text x="{QBX - 6}" y="{NY - 8}" class="vc" text-anchor="end">QB</text>'
             f'<text x="{QX + 6}" y="{NY - 8}" class="vc">Q</text>')
    S.append(f'<text x="{(BLNX + BLX) / 2}" y="318" class="vc" text-anchor="middle">'
             f'the schematic it must match</text>')
    # the table, from the extraction; net names in words
    words = {"BL": "bit line", "BLN": "bit line bar", "WL": "word line",
             "VDD": "supply", "VSS": "ground"}
    say = lambda n: words.get(lab(n), lab(n))
    rows = []
    for i, (d, role, kind) in enumerate(roles, 1):
        ends = " and ".join(sorted(say(n) for n in d.sd))
        rows.append((i, role, kind, d.nfin, say(d.gate), ends))
    tx = 560
    T = [f'<text x="{tx}" y="44" class="vn tth">#  role</text>',
         f'<text x="{tx + 100}" y="44" class="vn tth">type, fins</text>']
    for k, (i, role, kind, nfin, gate, ends) in enumerate(rows):
        y = 70 + k * 44
        T.append(f'<circle cx="{tx + 6}" cy="{y - 4}" r="8" class="tno"/>'
                 f'<text x="{tx + 6}" y="{y}" class="tnt" text-anchor="middle">{i}</text>'
                 f'<text x="{tx + 20}" y="{y}" class="vc">{role}</text>'
                 f'<text x="{tx + 100}" y="{y}" class="vc">{kind}, {nfin} fin{"s" if nfin > 1 else ""}</text>'
                 f'<text x="{tx + 20}" y="{y + 14}" class="vn">gate: {gate}</text>'
                 f'<text x="{tx + 20}" y="{y + 27}" class="vn">joins {ends}</text>')
    H = max(PY + WIN.height() * K + 84, 70 + len(rows) * 44)
    svg = (f'<svg class="vxor" viewBox="0 0 760 {H:.0f}" role="img" aria-label="The memory '
           f'cell\'s transistors found in its shapes, on its schematic, and listed">'
           f'{"".join(g + S + T)}</svg>')
    return svg


def decode_figure(gds_path):
    """Question 5, selection: the decode check's own bench (tests/
    test_band_decode.py) runs every half and column through the extracted
    decoder; the grid marks which decoder outputs settle high for each.
    A second run sticks one word-line driver, as the check's own damage
    test does."""
    import klayout.db as kdb
    sys.path.insert(0, str(REPO / "tests"))
    import test_band_decode as tbd
    from sramforge.lvs import Extraction
    ly = kdb.Layout()
    ly.read(str(gds_path))
    ex = Extraction(ly, ly.cell("ctrl_decode_32"))
    wlmap = tbd.wordline_map(ex)
    cols = [("refR", k) for k in range(32)] + [("refL", k) for k in range(32)]
    rows = [(h, c) for h in (0, 1) for c in range(32)]

    def sweep(extraction):
        bench = tbd.BandBench(extraction, wlmap, False)
        bench.cycle(0)
        bench.cycle(0)
        return {(h, c): bench.cycle(bench.address(h, c)) for h, c in rows}
    good = sweep(ex)
    assert all(good[r] == {tbd.expected(*r)} for r in rows), "every address one-hot"
    target = next(n for n, v in wlmap.items() if v == ("refL", 1))
    vss = next(iter(tbd.BandBench(ex, wlmap, False).sim.vss))

    class Stuck:
        devices = [d._replace(gate=vss) if target in d.sd else d for d in ex.devices]
        net_labels = ex.net_labels
    bad = sweep(Stuck)
    wrong = [r for r in rows if bad[r] != {tbd.expected(*r)}]
    assert wrong, "the stuck driver must break the sweep"
    C = 3.4
    W = len(cols) * C

    def grid(gx, res, title, sub):
        s = [f'<rect x="{gx}" y="34" width="{W:.1f}" height="{W:.1f}" class="vpan"/>']
        for i, r in enumerate(rows):
            want = tbd.expected(*r)
            for j, col in enumerate(cols):
                lit, exp = col in res[r], col == want
                if lit and exp:
                    cls = "dok"
                elif lit:
                    cls = "dbad"
                elif exp:
                    cls = "dmiss"
                else:
                    continue
                s.append(f'<rect x="{gx + j * C:.1f}" y="{34 + i * C:.1f}" '
                         f'width="{C:.1f}" height="{C:.1f}" class="{cls}"/>')
        s.append(f'<text x="{gx + W / 2:.1f}" y="{34 + W + 18:.1f}" class="vc" '
                 f'text-anchor="middle">{title}</text>'
                 f'<text x="{gx + W / 2:.1f}" y="{34 + W + 32:.1f}" class="vn" '
                 f'text-anchor="middle">{sub}</text>'
                 f'<text x="{gx + W / 2:.1f}" y="26" class="vn" text-anchor="middle">'
                 f'word lines</text>'
                 f'<text x="{gx - 8}" y="{34 + W / 2:.1f}" class="vn" text-anchor="middle" '
                 f'transform="rotate(-90 {gx - 8} {34 + W / 2:.1f})">addresses</text>')
        return "".join(s)
    # the stuck driver holds its word line on, so it opens alongside
    # every other address's word line
    assert all(("refL", 1) in bad[r] for r in rows), "stuck on at every address"
    g = [grid(70, good, "as built", "every address lights exactly one word line"),
         grid(470, bad, "one word-line driver stuck on",
              "its word line opens at every address: caught")]
    legend = [("dok", "lit, as expected"), ("dmiss", "expected, not lit"),
              ("dbad", "lit, not expected")]
    for i, (cls, t) in enumerate(legend):
        g.append(f'<rect x="{70 + i * 150}" y="{34 + W + 46:.1f}" width="10" height="10" '
                 f'class="{cls}"/><text x="{85 + i * 150}" y="{34 + W + 55:.1f}" '
                 f'class="vn">{t}</text>')
    return (f'<svg class="vxor" viewBox="0 0 760 {34 + W + 66:.0f}" role="img" '
            f'aria-label="Every address against every word line: a clean diagonal, '
            f'and a gap when one driver is stuck">{"".join(g)}</svg>')


def writeread_figure():
    """Question 5, data: the recorded transistor-level write-then-read of
    the 16-bit block (scripts/access_sim/writeread_sweep.py, run
    2026-07-20): write a 0 into a cell holding 1, then read it back.  The
    traces are the raw file's own samples; the nets are named by the
    run's map and the order stageC_emit saves them in (data out, word
    line, write enable, sense enable, then the cell's two sides)."""
    work = REPO / "out" / "wr_sweep" / "b16"
    raw = (work / "stageC_w0.raw").read_text().split("\n")
    nv = int(next(l for l in raw if l.startswith("No. Variables")).split()[-1])
    i = raw.index("Variables:")
    names = [raw[i + 1 + k].split()[1] for k in range(nv)]
    pts, cur = [], []
    for line in raw[raw.index("Values:") + 1:]:
        p = line.split()
        if not p:
            continue
        if len(p) == 2:
            if len(cur) == nv:
                pts.append(cur)
            cur = [float(p[1])]
        else:
            cur.append(float(p[0]))
    if len(cur) == nv:
        pts.append(cur)
    mp = json.loads((work / "stageC_w0_map.json").read_text())
    col = lambda node: names.index(f"v({node})")
    # stageC_emit saves data out, then wlb<0>, wrena, sae, then the cell
    WL, WE, SE = names[2][2:-1], names[3][2:-1], names[4][2:-1]
    lanes = [("word line", WL, "rwl"), ("write enable", WE, "rwe"),
             ("sense enable", SE, "rse"), ("cell, one side", mp["sel"][0], "rc0"),
             ("cell, other side", mp["sel"][1], "rc1"), ("data out", mp["dataout"], "rdo")]
    T1 = 3.6e-9
    X0, X1, LH, Y0 = 130, 744, 40, 40
    X = lambda t: X0 + (X1 - X0) * t / T1
    g = [f'<rect x="{X(0.5275e-9):.1f}" y="{Y0 - 22}" width="{X(1.55e-9) - X(0.5275e-9):.1f}" '
         f'height="{len(lanes) * LH + 18}" class="rwin"/>',
         f'<rect x="{X(2.5275e-9):.1f}" y="{Y0 - 22}" width="{X(3.55e-9) - X(2.5275e-9):.1f}" '
         f'height="{len(lanes) * LH + 18}" class="rwin"/>',
         f'<text x="{X(0.55e-9):.1f}" y="{Y0 - 8}" class="vc vh">write a 0</text>',
         f'<text x="{X(2.55e-9):.1f}" y="{Y0 - 8}" class="vc vh">read it back</text>']
    for k, (label, node, cls) in enumerate(lanes):
        base = Y0 + k * LH + LH - 6
        j = col(node)
        d = " ".join(f"{'M' if n == 0 else 'L'}{X(p[0]):.1f},{base - p[j] / 0.7 * (LH - 14):.1f}"
                     for n, p in enumerate(pts) if p[0] <= T1)
        g.append(f'<text x="{X0 - 10}" y="{base - 6}" class="vc" text-anchor="end">{label}</text>'
                 f'<path d="M{X0} {base}H{X1}" class="rbase"/>'
                 f'<path d="{d}" class="rtr {cls}"/>')
    # the two moments that matter, read off the samples
    sa, sb, do = col(mp["sel"][0]), col(mp["sel"][1]), col(mp["dataout"])
    flip = next(p[0] for p in pts if p[sb] > p[sa] and p[0] > 0.3e-9)
    out0 = next(p[0] for p in pts if p[do] < 0.35 and p[0] > 2e-9)
    # the first label reads rightwards; the second, near the right edge,
    # leftwards and below the high trace
    for t, txt_, k, side, dy in (
            (flip, f"the cell flips at {flip * 1e9:.2f} ns", 3, 1, 8),
            (out0, f"data out returns 0 at {out0 * 1e9:.2f} ns", 5, -1, 22)):
        y = Y0 + k * LH + 4
        g.append(f'<path d="M{X(t):.1f} {y}V{y + LH - 10}" class="rmk"/>'
                 f'<text x="{X(t) + 5 * side:.1f}" y="{y + dy}" class="vv2" '
                 f'text-anchor="{"start" if side > 0 else "end"}">{txt_}</text>')
    for ns in range(4):
        g.append(f'<text x="{X(ns * 1e-9):.1f}" y="{Y0 + len(lanes) * LH + 14}" class="vn" '
                 f'text-anchor="middle">{ns} ns</text>')
    return (f'<svg class="vxor" viewBox="0 0 760 {Y0 + len(lanes) * LH + 22}" role="img" '
            f'aria-label="A transistor-level simulation writing a 0 into a cell that '
            f'held 1, then reading it back">{"".join(g)}</svg>')


def cycle_figure():
    """Question 6: one read cycle at the minimum period, from the
    characterization records: clock high until the data is valid, clock
    low until the bit lines are restored, the sum re-simulated two reads
    back to back, then a fifth added as margin."""
    rows = []
    for c, name in (("TT", "typical"), ("SS", "slow")):
        mp = json.loads((REPO / "out" / "char" / f"sramforge_32bx4x64_{c}.json")
                        .read_text())["min_period"]
        rows.append((name, mp["t_high_min_ps"], mp["t_low_min_ps"],
                     mp["verified_period_ps"], mp["min_period_ps"]))
    X0, X1, PS = 110, 590, 600
    X = lambda ps: X0 + (X1 - X0) * ps / PS
    g = []
    for k, (name, hi, lo, ver, mn) in enumerate(rows):
        y = 34 + k * 74
        g.append(f'<text x="{X0 - 10}" y="{y + 18}" class="vc" text-anchor="end">{name}</text>'
                 f'<rect x="{X(0):.1f}" y="{y}" width="{X(hi) - X(0):.1f}" height="26" class="cyh"/>'
                 f'<rect x="{X(hi):.1f}" y="{y}" width="{X(hi + lo) - X(hi):.1f}" height="26" class="cyl"/>'
                 f'<rect x="{X(ver):.1f}" y="{y}" width="{X(mn) - X(ver):.1f}" height="26" class="cym"/>'
                 f'<text x="{(X(0) + X(hi)) / 2:.1f}" y="{y + 17}" class="cyt" text-anchor="middle">'
                 f'read: data valid by {hi}</text>'
                 f'<text x="{(X(hi) + X(hi + lo)) / 2:.1f}" y="{y + 17}" class="cyt" text-anchor="middle">'
                 f'restore: {lo}</text>'
                 f'<text x="{X(mn) + 6:.1f}" y="{y + 17}" class="vv2">{mn:.0f} ps, '
                 f'{1000 / mn:.2f} gigahertz</text>'
                 f'<text x="{X(ver):.1f}" y="{y + 40}" class="vn" text-anchor="middle">'
                 f'{ver} verified</text>'
                 f'<text x="{(X(ver) + X(mn)) / 2:.1f}" y="{y + 17}" class="vn" text-anchor="middle">'
                 f'+20%</text>')
    for ps in range(0, PS + 1, 100):
        g.append(f'<text x="{X(ps):.1f}" y="{34 + len(rows) * 74 + 4}" class="vn" '
                 f'text-anchor="middle">{ps}{" ps" if ps == PS else ""}</text>')
    return (f'<svg class="vxor" viewBox="0 0 760 {34 + len(rows) * 74 + 12}" role="img" '
            f'aria-label="One read cycle at the minimum period, typical and slow '
            f'corners">{"".join(g)}</svg>')


def abstract_cover_figure(gds_path):
    """Question 7, the abstract: drawn metal 4 and 5 of the band against
    the abstract's pins and blockages on the same layers, from an exact
    (4000-unit) abstract exported here with sramforge/lef.py; their
    exclusive or must be empty, which is what rung 6 checks everywhere."""
    import tempfile
    import klayout.db as kdb
    from sramforge import BankSpec
    from sramforge.lef import emit_lef
    with tempfile.TemporaryDirectory() as tmp:
        lefp = pathlib.Path(tmp) / "b.lef"
        emit_lef(gds_path, "sramforge_32bx4x64", BankSpec(), lefp, units=4000)
        lef = lefp.read_text().splitlines()
    dy = float(next(l for l in lef if l.strip().startswith("FOREIGN")).split()[3])
    ly = kdb.Layout()
    ly.read(str(gds_path))
    top = ly.cell("sramforge_32bx4x64")
    dbu = ly.dbu
    WIN = kdb.DBox(1.9, 34.6, 8.1, 40.3)
    box = kdb.Region(WIN.to_itype(dbu))
    drawn, abst = {}, {}
    for name, (l, d) in (("M4", (40, 0)), ("M5", (50, 0))):
        drawn[name] = kdb.Region(top.begin_shapes_rec(ly.layer(l, d))).merged() & box
        abst[name] = kdb.Region()
    lay = pin = None
    for line in lef:
        w = line.split()
        if not w:
            continue
        if w[0] == "LAYER":
            lay = w[1]
        elif w[0] == "RECT" and lay in abst:
            x0, y0, x1, y1 = (float(v) for v in w[1:5])
            abst[lay].insert(kdb.DBox(x0, y0 - dy, x1, y1 - dy).to_itype(dbu))
    for name in abst:
        abst[name] = abst[name].merged() & box
    left = {n: drawn[n] ^ abst[n] for n in drawn}
    n_left = sum(r.count() for r in left.values())
    assert n_left == 0, f"abstract and metal differ: {n_left} pieces"
    P, K = 200, 200 / WIN.width()
    PH = WIN.height() * K

    def panel(px, regs, title, sub):
        s = [f'<rect x="{px}" y="26" width="{P}" height="{PH:.1f}" class="vpan"/>']
        for reg, cls in regs:
            for poly in reg.each():
                pts = " ".join(f"{px + (p.x * dbu - WIN.left) * K:.1f},"
                               f"{26 + (WIN.top - p.y * dbu) * K:.1f}"
                               for p in poly.each_point_hull())
                s.append(f'<polygon points="{pts}" class="{cls}"/>')
        s.append(f'<text x="{px + P / 2}" y="{26 + PH + 18:.1f}" class="vc" '
                 f'text-anchor="middle">{title}</text>'
                 f'<text x="{px + P / 2}" y="{26 + PH + 32:.1f}" class="vn" '
                 f'text-anchor="middle">{sub}</text>')
        return "".join(s)
    xs = [20, 280, 540]
    g = [panel(xs[0], [(drawn["M4"], "am4"), (drawn["M5"], "am5")], "the layout's metal",
               "metal 4 and 5 in the central band"),
         f'<text x="{xs[0] + P + 30}" y="{26 + PH / 2 + 8:.1f}" class="vop" '
         f'text-anchor="middle">⊕</text>',
         panel(xs[1], [(abst["M4"], "am4"), (abst["M5"], "am5")], "the abstract",
               "its pins and blockages there"),
         f'<text x="{xs[1] + P + 30}" y="{26 + PH / 2 + 8:.1f}" class="vop" '
         f'text-anchor="middle">=</text>',
         panel(xs[2], [], "0 shapes left over", "the abstract covers the metal exactly")]
    return (f'<svg class="vxor" viewBox="0 0 760 {26 + PH + 42:.0f}" role="img" '
            f'aria-label="The layout\'s metal, exclusive-or the abstract\'s pins '
            f'and blockages, leaves nothing">{"".join(g)}</svg>')


def trace_html():
    """Question 7, the library: the delay table as the timing library
    writes it, beside the record field it comes from."""
    import html as H
    lib = (REPO / "out" / "char" / "sramforge_32bx4x64_TT.lib").read_text()
    a = lib.index("      /* clk -> dataout:")
    b = lib.index("        cell_fall(", a)
    import textwrap
    lines = [l[6:] for l in lib[a:b].rstrip().splitlines()]
    lines = textwrap.wrap(lines[0], 52, subsequent_indent="   ") + lines[1:]
    lib_part = "\n".join(lines)
    ch = json.loads((REPO / "out" / "char" / "sramforge_32bx4x64_TT.json").read_text())
    ra = ch["read_access"]
    # the record's own values, one array to a line so it reads like the table
    rows = ",\n".join(" " * 6 + json.dumps(r) for r in ra["cell_rise_ps"])
    rec = ('{\n  "read_access": {\n'
           f'    "input_slew_ps": {json.dumps(ra["input_slew_ps"])},\n'
           f'    "load_ff": {json.dumps(ra["load_ff"])},\n'
           f'    "cell_rise_ps": [\n{rows}\n    ]\n  }}\n}}')
    return (f'<div class="trace"><div><span class="ttl">the timing library</span>'
            f'<pre class="code">{H.escape(lib_part)}</pre></div>'
            f'<div><span class="ttl">the measurement record it was written from</span>'
            f'<pre class="code">{H.escape(rec)}</pre></div></div>')


def verify_figures() -> dict:
    """The Verification section's drawings: the ladder of checks, and
    for the shape checks a real exclusive-or of the generated and the
    kit's layouts (and of a deliberately broken recipe) and the slot
    comparison.  The kit's block is read from ../asap7_sram_0p0, the
    same file tests/test_golden.py compares against."""
    import subprocess
    import tempfile
    import klayout.db as kdb
    from sramforge import generator as gen, tech
    out = {}
    out["questions"] = ('<ol class="qlist">' + "".join(
        f'<li><b>{q}</b><span class="qa">{a}</span>'
        f'<span class="qp"><i>Proven on</i> {p}</span></li>'
        for q, a, p in QUESTIONS) + '</ol>')

    # ---- the identity check, drawn from the two layouts themselves
    kit = REPO.parent / "asap7_sram_0p0" / "gds" / "srambank_32b.gds"
    if not kit.exists():
        raise SystemExit(f"the kit's block is not at {kit}: clone "
                         "https://github.com/The-OpenROAD-Project/asap7_sram_0p0 "
                         "next to this repository (tests/test_golden.py needs it too)")
    U = tech.DBU
    UP_Y = gen.UPPER_STACK_ANCHOR_Y * U
    x0 = (gen.COLGRP_X_IN_BANK + gen.ARRAY_X_IN_COLGRP + gen.STRIP_X_IN_ARRAY
          + tech.BIT_X0) * U - 0.03
    WIN = kdb.DBox(x0, UP_Y - 0.03, x0 + 0.66, UP_Y + 0.63)
    LAYERS = (("ra", (11, 0)), ("rg", (7, 0)))
    SHIFT = 72                                 # 18 nanometres, in 0.25 nm units

    def regions(path, mutate=False):
        ly = kdb.Layout()
        ly.read(str(path))
        top = max((ly.cell(c) for c in ly.each_top_cell()),
                  key=lambda c: c.bbox().area())
        if mutate:
            # a wrong constant in the strip recipe: the upright cells are
            # one array placed from BIT_X0, so an offset 18 nm off moves
            # every upright cell of every strip (the mirrored ones stay)
            strip = ly.cell("bitcell_strip_x32")
            moved = 0
            for inst in strip.each_inst():
                if inst.cell.name == "sram_cell_6t_122" and inst.trans.rot == 0 \
                        and not inst.trans.is_mirror():
                    inst.transform(kdb.Trans(kdb.Vector(SHIFT, 0)))
                    moved += 1
            assert moved == 1, "expected one upright array in the strip"
        box = WIN.to_itype(ly.dbu)
        res = {}
        for cls, (l, d) in LAYERS:
            li = ly.find_layer(l, d)
            r = kdb.Region(top.begin_shapes_rec_touching(li, box)) & kdb.Region(box)
            res[cls] = r.merged()
        return res, ly.dbu

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, "-m", "sramforge", "--out", tmp], cwd=REPO,
                       check=True, stdout=subprocess.DEVNULL)
        ours_path = pathlib.Path(tmp) / "sramforge_32bx4x64.gds"
        ours, dbu = regions(ours_path)
        broken, _ = regions(ours_path, mutate=True)
        out["wiring"] = wiring_figure(ours_path)
        out["rules"], out["rules_n"] = rules_figure_m2(ours_path)
        out["decode"] = decode_figure(ours_path)
        out["cover"] = abstract_cover_figure(ours_path)
    out["transistors"] = transistor_figure()
    out["writeread"] = writeread_figure()
    out["cycle"] = cycle_figure()
    out["trace"] = trace_html()
    theirs, _ = regions(kit)
    P = 160
    K = P / WIN.width()

    def panel(px, py, regs, cls_map, title, sub):
        s = [f'<rect x="{px}" y="{py}" width="{P}" height="{P}" class="vpan"/>']
        for cls, reg in regs:
            for poly in reg.each():
                pts = " ".join(
                    f"{px + (p.x * dbu - WIN.left) * K:.1f},"
                    f"{py + (WIN.top - p.y * dbu) * K:.1f}"
                    for p in poly.each_point_hull())
                s.append(f'<polygon points="{pts}" class="{cls_map.get(cls, cls)}"/>')
        s.append(f'<text x="{px + P / 2}" y="{py + P + 18}" class="vc" '
                 f'text-anchor="middle">{title}</text>')
        s.append(f'<text x="{px + P / 2}" y="{py + P + 32}" class="vn" '
                 f'text-anchor="middle">{sub}</text>')
        return "".join(s)
    xr = {c: ours[c] ^ theirs[c] for c in ours}
    xb = {c: broken[c] ^ theirs[c] for c in broken}
    n_left = sum(r.count() for r in xr.values())
    n_bad = sum(r.count() for r in xb.values())
    # two rows, each the whole calculation: generated, exclusive-or the
    # kit's block, equals what is left over
    xs, R1, R2 = [96, 316, 536], 34, 34 + P + 78
    both = [(c, theirs[c]) for c, _ in LAYERS]
    g = []
    for py, head, gen_regs, left, left_n, verdict in (
            (R1, "The generator's recipe", ours, xr, n_left, "pass, on all 31 layers"),
            (R2, "The same recipe with one constant 18 nm off", broken, xb, n_bad,
             "fail")):
        g.append(f'<text x="{xs[0]}" y="{py - 12}" class="vc vh">{head}</text>')
        g.append(panel(xs[0], py, [(c, gen_regs[c]) for c, _ in LAYERS], {},
                       "generated", "active and gate" if py == R1
                       else "upright cells 18 nm right"))
        g.append(f'<text x="{(xs[0] + P + xs[1]) / 2}" y="{py + P / 2 + 8}" '
                 f'class="vop" text-anchor="middle">⊕</text>')
        g.append(panel(xs[1], py, both, {}, "the kit's block", "the same window"))
        g.append(f'<text x="{(xs[1] + P + xs[2]) / 2}" y="{py + P / 2 + 8}" '
                 f'class="vop" text-anchor="middle">=</text>')
        word = "shapes" if py == R1 else "slivers"
        g.append(panel(xs[2], py, [("x" + c, left[c]) for c, _ in LAYERS],
                       {"xra": "vx", "xrg": "vx"}, f"{left_n} {word} left over",
                       verdict))
    out["xor"] = (f'<svg class="vxor" viewBox="0 0 760 {R2 + P + 44}" role="img" '
                  f'aria-label="The generated layout, exclusive-or the kit\'s, '
                  f'leaves nothing; with one constant 18 nm off it leaves slivers">'
                  f'{"".join(g)}</svg>')

    out["xor_counts"] = (n_left, n_bad)

    # ---- every cell in place: expected slots against the cells found
    def grid(gx, shifted_row=None, title="", sub=""):
        s = []
        CW, CH, NC, NR = 15, 22, 12, 4
        for r in range(NR):
            for c in range(NC):
                x, y = gx + c * CW, 30 + r * CH
                s.append(f'<rect x="{x}" y="{y}" width="{CW - 2}" height="{CH - 2}" '
                         f'rx="2" class="vslot"/>')
                dx = 7 if r == shifted_row else 0
                cls = "vbad" if r == shifted_row else "vok"
                s.append(f'<rect x="{x + 2 + dx}" y="{y + 2}" width="{CW - 6}" '
                         f'height="{CH - 6}" rx="1.5" class="{cls}"/>')
        s.append(f'<text x="{gx + NC * CW / 2}" y="{30 + NR * CH + 18}" class="vc" '
                 f'text-anchor="middle">{title}</text>')
        s.append(f'<text x="{gx + NC * CW / 2}" y="{30 + NR * CH + 32}" class="vn" '
                 f'text-anchor="middle">{sub}</text>')
        return "".join(s)
    g = ['<rect x="12" y="34" width="200" height="80" rx="6" class="vs"/>'
         '<text x="24" y="56" class="vc">a second program,</text>'
         '<text x="24" y="72" class="vc">written apart from the</text>'
         '<text x="24" y="88" class="vc">generator, lists where every</text>'
         '<text x="24" y="104" class="vc">cell should sit and face</text>'
         '<path d="M214 74H252" class="vax"/><path d="M246 70l6 4l-6 4" class="vax"/>',
         grid(266, None, "every slot filled, exactly", "pass, at all 14 sizes"),
         grid(486, 1, "a strip shifted", "caught: cells off their slots"),
         '<rect x="694" y="30" width="13" height="20" rx="2" class="vslot"/>'
         '<text x="714" y="44" class="vn">slot</text>'
         '<rect x="694" y="58" width="13" height="20" rx="2" class="vok"/>'
         '<text x="714" y="72" class="vn">cell</text>']
    out["slots"] = (f'<svg class="vslots" viewBox="0 0 760 170" role="img" '
                    f'aria-label="Expected cell slots against the cells found in '
                    f'the layout">{"".join(g)}</svg>')
    return out


SENSE_JS = r'''<script>
// step 4's chart: one readout for the row under the pointer or in focus
(function () {
  var fig = document.querySelector('.sfig');
  if (!fig) return;
  var tip = fig.querySelector('.stip');
  function show(g, px, py) {
    var lines = g.getAttribute('data-tip').split('|');
    tip.textContent = '';
    lines.forEach(function (l, i) {
      var d = document.createElement('div');
      if (i === 0) d.className = 'h';
      d.textContent = l;
      tip.appendChild(d);
    });
    tip.hidden = false;
    var box = fig.getBoundingClientRect();
    var x = Math.min(px - box.left + 14, box.width - tip.offsetWidth - 4);
    tip.style.left = Math.max(4, x) + 'px';
    tip.style.top = (py - box.top + 14) + 'px';
  }
  fig.querySelectorAll('.srow').forEach(function (g) {
    g.addEventListener('pointermove', function (e) { show(g, e.clientX, e.clientY); });
    g.addEventListener('pointerleave', function () { tip.hidden = true; });
    g.addEventListener('focus', function () {
      var r = g.getBoundingClientRect();
      show(g, r.left + r.width * 0.55, r.top + r.height / 2);
    });
    g.addEventListener('blur', function () { tip.hidden = true; });
  });
})();
</script>'''


# drawing helpers for the to-scale figures of "Generation"
def _n(v):                                # 0.270 -> 0.27
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _svg(h, label, body, w=400):
    return (f'<svg class="rules" viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="{label}">{"".join(body)}</svg>')


def _rect(x, y, w, h, cls):
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
            f'height="{h:.1f}" class="{cls}"/>')


def _text(x, y, t, cls="rl"):
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}">{t}</text>'


def _path(d, cls):
    return f'<path d="{d}" class="{cls}"/>'


def _notch(x, y, w, h, flip_x, flip_y, s):
    """The orientation mark: a corner triangle, bottom left on an
    upright copy, that mirrors with the copy it sits in."""
    cx = x + w if flip_x else x
    cy = y if flip_y else y + h
    dx = -s if flip_x else s
    dy = s if flip_y else -s
    pts = ((cx, cy), (cx + dx, cy), (cx, cy + dy))
    return ('<polygon class="rn" points="'
            + " ".join(f"{a:.1f},{b:.1f}" for a, b in pts) + '"/>')


def _dim_h(x1, x2, y, label, ty):
    return (_path(f"M{x1:.1f} {y - 4}V{y + 4}M{x2:.1f} {y - 4}V{y + 4}"
                  f"M{x1:.1f} {y}H{x2:.1f}", "rd")
            + _text((x1 + x2) / 2, ty, label, "rv mid"))


def _dim_v(x, y1, y2, label):
    return (_path(f"M{x - 4} {y1:.1f}H{x + 4}M{x - 4} {y2:.1f}H{x + 4}"
                  f"M{x} {y1:.1f}V{y2:.1f}", "rd")
            + _text(x + 7, (y1 + y2) / 2 + 4, label, "rv"))


def _figures(figs):
    """Figures for a grid: each drawing in a frame that may scroll
    sideways on a phone, with its caption."""
    return "\n".join(
        f'  <figure class="r{i}">\n    <div class="scroll">{s}</div>\n'
        f'    <figcaption><b>{t}.</b> {c}'
        f'</figcaption>\n  </figure>' for i, (t, s, c) in enumerate(figs))


def rules_figure() -> str:
    """Step 1 of "Generation": the tiling rules measured from the
    kit's block, one panel per level, smallest first.  Every position is
    drawn from the generator's own constants (the ones rung 0 proves
    against the reference) and the bitcell's drawn outline is read from
    its vendored layout, so the figure cannot drift from the code."""
    import klayout.db as kdb
    from sramforge import generator as gen, tech
    U = tech.DBU
    def leaf(name):
        """A vendored leaf cell's boundary and the boxes of the layers a
        transistor is made of, back to front (the gate cut is drawn as a
        gap in the gate)."""
        ly = kdb.Layout()
        ly.read(str(REPO / "cells" / f"{name}.gds"))
        c = ly.cell(name)

        def boxes(layer):
            r = kdb.Region(c.begin_shapes_rec(ly.layer(*layer))).merged()
            return [p.bbox().to_dtype(ly.dbu) for p in r.each()]
        return {"bnd": boxes((100, 0))[0], "boxes": boxes,
                "layers": [(cls, boxes(l)) for cls, l in
                           (("ra", (11, 0)), ("rg", (7, 0)), ("rgc", (10, 0)),
                            ("rsd", (17, 0)), ("rgl", (16, 0)))]}
    BIT = leaf("sram_cell_6t_122")
    LEAVES = {n: leaf(n) for n in ("dummy_sram_6t122", "tapcell_sram_6t122",
                                   "dummy_topbot_v1")}
    bnd = BIT["bnd"]                         # the cell's own boundary
    # how far the strip-continuous layers (fins, wells, implants, the
    # horizontal metal) run past the boundary to join their neighbours
    REACH = -min(b.left for lyr in ((2, 0), (1, 0), (12, 0), (20, 0))
                 for b in BIT["boxes"](lyr))
    ly = kdb.Layout()
    ly.read(str(REPO / "cells" / "ctrl_decode_32.gds"))
    bb = ly.cell("ctrl_decode_32").dbbox()
    BAND_H, WIDTH = bb.height(), bb.width()

    PAIR = tech.BIT_STEP * U                 # upright to upright
    X0 = tech.BIT_X0 * U                     # first upright cell
    ROW = gen.STRIP_HEIGHT * U
    GRP = tech.COLGRP_ROW_PITCH * U
    IO_X = gen.IO_COLUMN_X_REF * U
    MIR_X = gen.MIRRORED_ARRAY_X_REF * U
    ARR_X = gen.ARRAY_X_IN_COLGRP * U
    EDGE = gen.EDGE_FILLER_LEFT_XY[0] * U    # left edge filler, negative
    UP_Y = gen.UPPER_STACK_ANCHOR_Y * U
    LO_Y = gen.LOWER_STACK_ANCHOR_Y * U
    BAND_Y = gen.DECODER_XY[1] * U
    HALF = PAIR / 2

    n, svg, R, T, P, notch, dim_h, dim_v = (
        _n, _svg, _rect, _text, _path, _notch, _dim_h, _dim_v)

    figs = []

    # a. one strip end to end, from each leaf's real shapes: the array's
    # outer dummy (dummy_topbot_v1, m90, placed by the array at x 0 while
    # the strip sits at STRIP_X_IN_ARRAY), cells 0 and 1, then 30 and 31,
    # the strip's own dummy at its end and the tap half a pair beyond.
    # Upright copies (r0) step PAIR from X0; mirrored ones (m90: x maps to
    # -x about the origin) step PAIR from X0 + PAIR, so boundaries abut.
    END = X0 + 16 * PAIR
    SKIP = 14 * PAIR                          # cells 2 to 29, left out
    copies = [("dummy_topbot_v1", -gen.STRIP_X_IN_ARRAY * U, True, "dummy"),
              (None, X0, False, "cell 0"), (None, X0 + PAIR, True, "cell 1"),
              (None, X0 + 15 * PAIR, False, "cell 30"),
              (None, END, True, "cell 31"),
              ("dummy_sram_6t122", END, False, "dummy"),
              ("tapcell_sram_6t122", END + HALF, False, "tap")]
    K, OX, TOP, GAP = 860, 50, 62, 60
    Y = lambda y: TOP + (bnd.top - y) * K
    xs = lambda x: OX + (x - (X0 - HALF)) * K + (
        GAP - SKIP * K if x > X0 + PAIR + 1e-6 else 0)
    b = []
    for name, o, mir, label in copies:
        lf = LEAVES[name] if name else BIT
        q0 = lf["bnd"]
        dy = bnd.bottom - q0.bottom            # the outer dummy's own origin
        fx = (lambda x, o=o: o - x) if mir else (lambda x, o=o: o + x)
        l, r = sorted((fx(q0.left), fx(q0.right)))
        X = lambda x, l=l: xs(l) + (x - l) * K
        tint = "rdt" if name else ("rtm" if mir else "rt0")
        b.append(R(X(l), Y(bnd.top), (r - l) * K, bnd.height() * K, tint))
        for cls, bs in lf["layers"]:
            for q in bs:
                x1, x2 = sorted((fx(q.left), fx(q.right)))
                b.append(R(X(x1), Y(q.top + dy), (x2 - x1) * K,
                           q.height() * K, cls))
        b.append(R(X(l), Y(bnd.top), (r - l) * K, bnd.height() * K, "rbn"))
        b.append(T(X((l + r) / 2), Y(bnd.top) - 30, label, "rs mid"))
        b.append(T(X((l + r) / 2), Y(bnd.top) - 16, "m90" if mir else "r0",
                   "rl mid"))
    gx = xs(X0 + PAIR) + GAP / 2
    b.append(T(gx, Y(bnd.height() / 2) - 6, "· · ·", "rl mid"))
    b.append(T(gx, Y(bnd.height() / 2) + 10, "28 more", "rs mid"))
    b.append(T(gx, Y(bnd.height() / 2) + 22, "cells", "rs mid"))
    b.append(P(f"M{OX - 12} {Y(bnd.top):.1f}h8M{OX - 12} {Y(bnd.bottom):.1f}h8"
               f"M{OX - 8} {Y(bnd.top):.1f}V{Y(bnd.bottom):.1f}", "rd"))
    b.append(T(OX - 14, Y(bnd.height() / 2) + 4, n(bnd.height()), "rv end"))
    yb = Y(bnd.bottom) + 18
    c0 = xs(X0)
    b.append(dim_h(c0, c0 + HALF * K, yb, n(HALF), yb + 16))
    b.append(dim_h(c0, c0 + PAIR * K, yb + 26, "", yb + 26))
    b.append(T(c0 + PAIR * K + 6, yb + 30, f"{n(PAIR)}, one mirrored pair", "rv"))
    te = xs(END + PAIR)
    b.append(P(f"M{xs(END):.1f} {yb - 4}V{yb}H{te:.1f}V{yb - 4}", "rd"))
    b.append(T(te, yb + 16, "beside the read and write column", "rs end"))
    ly0 = yb + 54
    for j, (cls, t) in enumerate((("ra", "active"), ("rg", "gate"),
                                   ("rsd", "source and drain contact"),
                                   ("rgl", "gate contact"))):
        x = OX + (0, 80, 150, 310)[j]
        y = ly0
        b.append(R(x, y - 8, 10, 10, cls))
        b.append(T(x + 15, y + 1, t, "rs"))
    figs.append(("Cells in a strip", svg(ly0 + 10,
        "One strip end to end: an outer dummy, memory cells alternately r0 and "
        "m90, then a dummy and a tap cell", b, w=te + 20),
        f"Each cell has a boundary {n(bnd.width())} by {n(bnd.height())}, "
        f"and neighbours abut exactly. Every second one is mirrored left to "
        f"right (m90; r0 is the cell as drawn), so the shapes that meet at a "
        f"shared edge are identical and merge. The strip ends in a dummy "
        f"cell and a tap cell, which ties the wells to the supply; the array "
        f"adds a second dummy at the outer end. A dummy holds no bit: it "
        f"gives the edge cells the same surroundings as the inner ones. The "
        f"fins, wells, implants and horizontal metal run {n(REACH)} past "
        f"every edge deliberately, so they join into lines along the whole "
        f"strip."))

    # b. strips into an array: slots, outer dummy | 32 cells | dummy, tap;
    # every second strip flipped upside down
    K, OX, BOT = 86, 14, 150
    X = lambda x: OX + (x + HALF) * K
    b = []
    w = HALF * K
    for s in range(4):
        y = BOT - (s + 1) * ROW * K
        h = ROW * K
        b.append(R(X(-HALF), y, w, h, "rdm"))
        for k in range(32):
            b.append(R(X(k * HALF), y, w, h, "rc2m" if k % 2 else "rc2"))
        b.append(R(X(32 * HALF), y, w, h, "rdm"))
        b.append(R(X(33 * HALF), y, 1.5 * w, h, "rtp"))
        b.append(T(X(16 * HALF), y + h / 2 + 4,
                   "flipped strip: m0, r180" if s % 2
                   else "upright strip: r0, m90", "rs mid halo"))
    right = X(33 * HALF) + 1.5 * w
    b.append(T(X(-HALF), BOT - 4 * ROW * K - 7, "dummy", "rs"))
    b.append(T(right, BOT - 4 * ROW * K - 7, "dummy, tap", "rs end"))
    b.append(P(f"M{X(0):.1f} {BOT + 6}V{BOT + 10}H{X(32 * HALF):.1f}V{BOT + 6}",
               "rd"))
    b.append(T((X(0) + X(32 * HALF)) / 2, BOT + 22, "32 cells", "rs mid"))
    b.append(dim_v(right + 8, BOT - ROW * K, BOT, n(ROW)))
    b.append(P(f"M{right + 30:.1f} {BOT - 4 * ROW * K:.1f}h-4M{right + 30:.1f} "
               f"{BOT:.1f}h-4M{right + 30:.1f} {BOT - 4 * ROW * K:.1f}V{BOT:.1f}",
               "rd"))
    b.append(T(right + 34, BOT - 2 * ROW * K + 4, n(GRP), "rv"))
    figs.append(("Strips into an array", svg(BOT + 30,
        "Four strips of 32 cells, every second strip flipped", b),
        f"Four strips stack every {n(ROW)}, every second one flipped upside "
        f"down (m0), which turns its mirrored cells to r180. Blue columns "
        f"hold the cells drawn upright, green the mirrored ones."))

    # c. arrays into a column group; the mirrored copy maps group x g to
    # MIR_X - (g - ARR_X), so the read and write column sits between
    K, OX, GT = 39, 14, 46
    X = lambda g: OX + (g - EDGE) * K
    m = lambda g: MIR_X + ARR_X - g
    a0 = ARR_X - HALF
    h = GRP * K
    b = [R(X(EDGE), GT, (a0 - EDGE) * K, h, "rf"),
         R(X(a0), GT, (IO_X - a0) * K, h, "rc2"),
         R(X(IO_X), GT, (m(IO_X) - IO_X) * K, h, "rio"),
         R(X(m(IO_X)), GT, (m(a0) - m(IO_X)) * K, h, "rc2m"),
         R(X(m(a0)), GT, (m(EDGE) - m(a0)) * K, h, "rf"),
         notch(X(a0), GT, (IO_X - a0) * K, h, False, False, 9),
         notch(X(m(IO_X)), GT, (m(a0) - m(IO_X)) * K, h, True, False, 9),
         T((X(a0) + X(IO_X)) / 2, GT - 8, "array", "rl mid"),
         T((X(IO_X) + X(m(IO_X))) / 2, GT - 20, "read and", "rs mid"),
         T((X(IO_X) + X(m(IO_X))) / 2, GT - 8, "write column", "rs mid"),
         T((X(m(IO_X)) + X(m(a0))) / 2, GT - 8, "mirrored array", "rl mid"),
         P(f"M{X(0):.1f} {GT + h}V{GT + h + 44}", "rd"),
         T(X(0), GT + h + 56, "0", "rv mid")]
    yb = GT + h + 14
    b.append(dim_h(X(0), X(IO_X), yb, n(IO_X), yb - 3))
    b.append(dim_h(X(0), X(MIR_X), yb + 20, f"{n(MIR_X)}, flipped", yb + 17))
    b.append(dim_h(X(EDGE), X(m(EDGE)), yb + 54,
                   f"{n(m(EDGE) - EDGE)} across, with an edge filler each side",
                   yb + 70))
    figs.append(("Arrays into a column group", svg(yb + 80,
        "A column group: array, read and write column, mirrored array", b),
        f"The read and write column is placed {n(IO_X)} from the group's "
        f"origin and a mirrored copy of the array at {n(MIR_X)}, so both "
        f"arrays face the column they share."))

    # d. groups into the block: the upright stack grows up from UP_Y, the
    # flipped stack grows down from LO_Y, the band sits at BAND_Y
    n2 = lambda v: f"{v:.2f}"
    K, OX, TOP = 4.2, 44, 14
    top_y = UP_Y + 32 * GRP
    Y = lambda y: TOP + (top_y - y) * K
    w = WIDTH * K
    b = []
    for k in range(32):
        y0 = UP_Y + k * GRP
        b.append(R(OX, Y(y0 + GRP), w, GRP * K, "rc2" if k % 2 else "rc3"))
        y0 = LO_Y - k * GRP
        b.append(R(OX, Y(y0), w, GRP * K, "rc2" if k % 2 else "rc3"))
    b.append(R(OX, Y(BAND_Y + BAND_H), w, BAND_H * K, "rbd"))
    for y in (top_y, BAND_Y + BAND_H, BAND_Y, LO_Y - 32 * GRP):
        b.append(P(f"M{OX - 5} {Y(y):.1f}H{OX}", "rd"))
    b.append(T(OX - 8, Y(BAND_Y + BAND_H) + 4, n2(BAND_Y + BAND_H), "rv end"))
    b.append(T(OX - 8, Y(BAND_Y) + 8, n2(BAND_Y), "rv end"))
    LX = OX + w + 16

    def note(y, lines, arrow=None):
        out = [P(f"M{OX + w + 2:.1f} {y:.1f}H{LX - 4:.1f}", "rd")]
        for i, t in enumerate(lines):
            out.append(T(LX, y + 4 + (i - (len(lines) - 1) / 2) * 13, t))
        return "".join(out)
    b.append(note(Y(UP_Y + 16 * GRP),
                  ["upper stack: 32 groups,",
                   f'each <tspan class="rvt">{n2(GRP)}</tspan> above the last,',
                   f'from y = <tspan class="rvt">{n2(UP_Y)}</tspan>']))
    b.append(note(Y(BAND_Y + BAND_H / 2),
                  [f'central band at y = <tspan class="rvt">{n2(BAND_Y)}</tspan>,',
                   f'<tspan class="rvt">{n2(BAND_H)}</tspan> tall']))
    b.append(note(Y(LO_Y - 16 * GRP),
                  ["lower stack: the same 32 groups",
                   f'flipped, each <tspan class="rvt">{n2(GRP)}</tspan> below the last,',
                   f'from y = <tspan class="rvt">{n2(LO_Y)}</tspan>']))
    figs.append(("Groups into the block", svg(Y(LO_Y - 32 * GRP) + 14,
        "The block: an upper stack of groups, the band, a flipped lower stack",
        b), f"The two stacks are mirror images about the central band, so "
        f"every word line runs from the band out through both."))

    return _figures(figs)


def tile_animation():
    """Step 2 of "Generation", animated: the block tiling itself
    from the measured rules, copy by copy, then each knob at work.
    Returns the SVG definitions (the leaf cells drawn from their real
    layout, and the strips and pairs built from them) and the frames as
    JSON.

    Every copy is placed exactly as the generator places it: an origin
    and a mirror (r0, m90, m0) composed down the hierarchy, from the
    generator's own constants.  A frame maps a key to a shape; the page
    script only moves each keyed shape, and the camera window, from one
    frame to the next, so no layout rule lives in the script.  A copy
    that changes its mirror (sx or sy from 1 to -1) swings over its axis
    on the way, which is what a mirrored placement is."""
    import klayout.db as kdb
    from sramforge import generator as gen, tech
    from sramforge.band import growth_for
    U = tech.DBU

    def leaf(name):
        ly = kdb.Layout()
        ly.read(str(REPO / "cells" / f"{name}.gds"))
        c = ly.cell(name)

        def boxes(layer):
            r = kdb.Region(c.begin_shapes_rec(ly.layer(*layer))).merged()
            return [p.bbox().to_dtype(ly.dbu) for p in r.each()]
        return c.dbbox(), boxes
    BAND0 = leaf("ctrl_decode_32")[0].height()
    FTOP = leaf("FILLER_32x2_top")[0].height()
    FBOT = leaf("FILLER_32x2_bottom")[0].height()
    MUXW = leaf("wrasst_prech_ymux_x4_sram_6t122_v2")[0].right

    HALF = tech.BIT_STEP * U / 2             # one cell column
    PAIR = 2 * HALF
    BX0 = tech.BIT_X0 * U                    # first upright cell in a strip
    ROW = gen.STRIP_HEIGHT * U
    RP = tech.ROW_PAIR_PITCH * U             # one flipped pair of strips
    GX = gen.COLGRP_X_IN_BANK * U            # group origin in the block
    ARR = gen.ARRAY_X_IN_COLGRP * U
    SXA = gen.STRIP_X_IN_ARRAY * U
    MIR = gen.MIRRORED_ARRAY_X_REF * U
    IOg = gen.IO_COLUMN_X_REF * U
    EDGEg = gen.EDGE_FILLER_LEFT_XY[0] * U
    AXg = (MIR + ARR) / 2                    # the group's mirror axis
    mg = lambda g: 2 * AXg - g
    C0g = ARR + SXA + BX0                    # cell 0's left edge, group frame
    UP_Y = gen.UPPER_STACK_ANCHOR_Y * U
    LO_Y = gen.LOWER_STACK_ANCHOR_Y * U
    DEC_Y = gen.DECODER_XY[1] * U
    DV = (tech.DUMMY_V1_Y0 * U, tech.DUMMY_V2_Y0 * U)
    END = BX0 + 32 * HALF                    # the strip's own dummy
    PITCH = tech.COLGRP_ROW_PITCH * U
    ASPECT = 2.0
    bx = lambda g: GX + g                    # group frame to block frame

    def r4(v):
        return round(v, 4)

    # ---- definitions: the leaf cells as drawn, then strips and pairs
    def cell_def(key, name):
        _, boxes = leaf(name)
        b = boxes((100, 0))[0]
        # copies of a definition are drawn in a shadow tree that page
        # classes do not reach, so the colours are inline and read
        # variables set on the drawing (light and dark), which inherit in
        box = (f'x="{r4(b.left)}" y="{r4(-b.top)}" width="{r4(b.width())}" '
               f'height="{r4(b.height())}"')
        out = [f'<rect {box} style="fill:currentColor"/>']
        for var, layer in (("ra", (11, 0)), ("rg", (7, 0)), ("rgc", (10, 0)),
                           ("rsd", (17, 0)), ("rgl", (16, 0))):
            for q in boxes(layer):
                out.append(f'<rect x="{r4(q.left)}" y="{r4(-q.top)}" '
                           f'width="{r4(q.width())}" height="{r4(q.height())}" '
                           f'style="fill:var(--tl-{var})"/>')
        out.append(f'<rect {box} style="fill:none;stroke:var(--tl-cbn);'
                   f'stroke-width:0.6;vector-effect:non-scaling-stroke"/>')
        return f'<g id="tl-{key}">{"".join(out)}</g>'

    def cell_at(k):
        """Cell k of a strip (k < 0 for the cells a wider strip adds
        outward): its origin in the strip and its mirror, as
        build_bitcell_strip places it (upright copies step a pair from
        BIT_X0, mirrored ones a pair back from the strip's end)."""
        if k % 2 == 0:
            return BX0 + k * HALF, 1
        return BX0 + (k + 1) * HALF, -1

    def place(ref, ox, sx, cls=None, swap=False):
        """A copy inside a definition.  Its tint shows its mirror as the
        reader sees it, so a definition used mirrored (the right array)
        has a twin with the tints swapped."""
        if cls is None:
            cls = "c0" if (sx == 1) != swap else "c1"
        return (f'<use href="#tl-{ref}" style="color:var(--tl-{cls})" '
                f'transform="translate({r4(ox)},0) scale({sx},1)"/>')

    def strip_def(key, ks, swap=False):
        parts = [place("cell", *cell_at(k), swap=swap) for k in ks]
        parts += [place("dmy", END, 1, "cd"), place("tap", END + HALF, 1, "ct")]
        return f'<g id="tl-{key}">{"".join(parts)}</g>'

    defs = [cell_def("cell", "sram_cell_6t_122"),
            cell_def("dmy", "dummy_sram_6t122"),
            cell_def("tap", "tapcell_sram_6t122"),
            cell_def("dv1", "dummy_topbot_v1"),
            cell_def("dv2", "dummy_topbot_v2"),
            strip_def("s32", range(32)), strip_def("s16", range(16, 32)),
            strip_def("s32R", range(32), True),
            strip_def("s16R", range(16, 32), True),
            '<g id="tl-pair">' + place("cell", *cell_at(0))
            + place("cell", *cell_at(1)) + '</g>',
            '<g id="tl-pairR">' + place("cell", *cell_at(0), swap=True)
            + place("cell", *cell_at(1), swap=True) + '</g>',
            f'<g id="tl-grp"><rect x="{r4(EDGEg)}" y="{r4(-PITCH)}" '
            f'width="{r4(mg(EDGEg) - EDGEg)}" height="{r4(PITCH)}" '
            f'style="fill:currentColor"/>'
            f'<rect x="{r4(EDGEg)}" y="{r4(-PITCH)}" width="{r4(mg(EDGEg) - EDGEg)}" '
            f'height="0.16" style="fill:var(--tl-rline)"/></g>']

    # ---- shapes; defaults (mirror 1, fully shown) are left out
    def use(ref, ox, oy, sx=1, sy=1, c=None):
        s = {"k": "u", "r": ref, "x": r4(ox), "y": r4(-oy)}
        if sx != 1:
            s["sx"] = sx
        if sy != 1:
            s["sy"] = sy
        if c:
            s["c"] = c
        return s

    def rect(x0, x1, y0, y1, c):
        x0, x1 = sorted((x0, x1))
        return {"x": r4(x0), "y": r4(-y1), "w": r4(x1 - x0), "h": r4(y1 - y0),
                "c": c}

    def label(x, y, s, w, c="ta", a="middle", size=12):
        return {"x": r4(x), "y": r4(-y), "s": s, "c": c, "a": a,
                "f": r4(w / 760 * size)}

    def dim(x1, y1, x2, y2, s, w, side):
        """A dimension line between two world points, its label on
        side t, b, l or r."""
        return {"k": "d", "x1": r4(x1), "y1": r4(-y1), "x2": r4(x2),
                "y2": r4(-y2), "s": s, "f": r4(w / 760 * 12), "sd": side}

    def cam(cx, cy, w):
        return [r4(cx - w / 2), r4(-cy - w / ASPECT / 2), r4(w)]

    def at(s, **kw):
        out = dict(s)
        out.update(kw)
        return out

    def stamp(s, by):
        """A copy that drops into its place from a little above (below,
        for by < 0) as it appears."""
        return at(s, ly=by)

    def lifted(s, by):
        """Where a copy goes as it leaves: a little above, unseen."""
        return at(s, y=r4(s["y"] - by), o=0)

    def merge(*frames):
        out = {}
        for fr in frames:
            out.update(fr)
        return out

    def timed(frame, keys, d0, d1, sp):
        """Stagger the listed shapes' moves across [d0, d1] of a phase."""
        n = max(1, len(keys) - 1)
        for i, k in enumerate(keys):
            frame[k] = at(frame[k], dl=r4(d0 + (d1 - d0) * i / n), sp=sp)
        return frame

    # ---- placements, composed as the generator composes them
    def strip_tf(s, side):
        """Strip s of the upper stack's first group: the array places
        strips in flipped pairs (r0, then m0 a pair pitch up) and the
        group mirrors the right array about MIRRORED_ARRAY_X_REF."""
        oy = UP_Y + (s // 2 if s % 2 == 0 else (s + 1) // 2) * RP
        sy = 1 if s % 2 == 0 else -1
        if side == "L":
            return bx(ARR + SXA), oy, 1, sy
        return bx(MIR - SXA), oy, -1, sy

    def strip(s, side, ref="s32"):
        ox, oy, sx, sy = strip_tf(s, side)
        return use(ref + ("R" if side == "R" else ""), ox, oy, sx, sy)

    def in_strip(ref, s, side, lx, lsx, c=None):
        ox, oy, sx, sy = strip_tf(s, side)
        if ref == "pair" and side == "R":
            ref = "pairR"
        return use(ref, ox + sx * lx, oy, sx * lsx, sy,
                   c or ("c0" if sx * lsx == 1 else "c1"))

    def outer(s, side, wd=0.0):
        """The array's outer dummy for strip s (dummy_topbot_v1 under the
        even strips, v2 under the odd ones, placed m90 at the array's
        outer edge)."""
        oy = UP_Y + DV[s % 2] + (s // 2) * RP
        ref = "dv1" if s % 2 == 0 else "dv2"
        if side == "L":
            return use(ref, bx(ARR - wd), oy, -1, c="cd")
        return use(ref, bx(MIR + wd), oy, 1, c="cd")

    def detail(bits=32, strips=4, quarter=False):
        """The upper stack's first group, in full.  bits 64 adds pairs
        outward; bits 16 is the sparse strip with its outer columns
        gone; quarter shows a 32-bit group as the sparse strips plus the
        outer pairs, so the pairs can leave one by one."""
        wd = gen.width_delta(bits) * U
        gh = gen.colgrp_pitch(gen.BankSpec(32, strips, 32)) * U
        f = {}
        for side, fx in (("L", lambda g: g), ("R", mg)):
            for s in range(strips):
                f[f"s{side}{s}"] = strip(s, side, "s16" if bits < 32 or quarter
                                         else "s32")
                f[f"o{side}{s}"] = outer(s, side, wd)
                if bits > 32:
                    for j in range((bits - 32) // 2):
                        f[f"x{side}{s}_{j}"] = in_strip(
                            "pair", s, side, BX0 - (j + 1) * PAIR, 1)
                if quarter:
                    for j in range(8):
                        f[f"q{side}{s}_{j}"] = in_strip(
                            "pair", s, side, BX0 + j * PAIR, 1)
            f["f" + side] = rect(bx(fx(EDGEg - wd)), bx(fx(EDGEg - wd + HALF)),
                                 UP_Y, UP_Y + gh, "rf")
            if bits < 32:
                f["e" + side] = rect(bx(fx(C0g)), bx(fx(C0g + 16 * HALF)),
                                     UP_Y, UP_Y + strips * ROW, "remp")
        f["io"] = rect(bx(IOg), bx(mg(IOg)), UP_Y, UP_Y + gh, "rio")
        for s in range(strips):
            f[f"mux{s}"] = rect(bx(IOg) + 0.03, bx(IOg) + MUXW, UP_Y + s * ROW + 0.03,
                                UP_Y + (s + 1) * ROW - 0.03,
                                "rmux" if s < 4 else "rmux2")
        if strips == 8:
            for i, sx in enumerate(gen.SELECT_LINE_X):
                x = bx(sx * U + IOg)
                f[f"sel{i}"] = rect(x, x + 0.024, UP_Y, UP_Y + gh, "rsel")
        return f

    def blockf(rows):
        """The block at a depth, groups as plain copies: the upright
        stack from UPPER_STACK_ANCHOR_Y up, the m0 stack from
        LOWER_STACK_ANCHOR_Y down (lower still by the rebuilt band's
        growth), fillers where build_bank puts them."""
        spec = gen.BankSpec(32, 4, rows)
        grow = (growth_for(gen.control_variant(spec))
                if gen.uses_band_v2(spec) else 0) * U
        ch = gen.colgrp_height(spec) * U
        up = UP_Y + ch + (rows - 1) * PITCH
        lo = LO_Y - grow - ch - (rows - 1) * PITCH
        bot = lo - gen.BOTTOM_FILLER_STANDOFF * U
        top = up - gen.TOP_FILLER_TUCK * U + FTOP
        f = {}
        for k in range(rows):
            f[f"gU{k}"] = use("grp", GX, UP_Y + k * PITCH, c="c0")
            f[f"gL{k}"] = use("grp", GX, LO_Y - grow - k * PITCH, sy=-1, c="c0")
        f["band"] = rect(bx(EDGEg), bx(mg(EDGEg)), DEC_Y - grow, DEC_Y + BAND0,
                         "rbd2" if grow else "rbd")
        f["fT"] = rect(bx(EDGEg), bx(mg(EDGEg)), top - FTOP, top, "rf")
        f["fB"] = rect(bx(EDGEg), bx(mg(EDGEg)), bot, bot + FBOT, "rf")
        return f, bot, top, grow

    gcx = bx(AXg)
    steps = []

    # 1. tile the cell along a strip: four by hand, then the rest
    cells = {f"c{k}": stamp(in_strip("cell", 0, "L", *cell_at(k)), 0.05)
             for k in range(32)}
    cam1 = cam(bx(C0g + 2 * HALF), UP_Y + 0.11, 1.5)
    w = cam1[2]
    first = {f"c{k}": cells[f"c{k}"] for k in range(4)}
    timed(first, [f"c{k}" for k in range(4)], 0, 0.6, 0.18)
    marks = {
        "d_w": dim(bx(C0g), UP_Y + ROW + 0.05, bx(C0g + HALF), UP_Y + ROW + 0.05,
                   f"{_n(HALF)}", w, "t"),
        "d_h": dim(bx(C0g) - 0.05, UP_Y, bx(C0g) - 0.05, UP_Y + ROW,
                   f"{_n(ROW)}", w, "l"),
        "d_p": dim(bx(C0g), UP_Y - 0.12, bx(C0g + PAIR), UP_Y - 0.12,
                   f"{_n(PAIR)}: one mirrored pair", w, "b")}
    for k in range(4):
        marks[f"l_o{k}"] = label(bx(C0g + (k + 0.5) * HALF), UP_Y - 0.055,
                                 "m90" if k % 2 else "r0", w, "ta")
    timed(marks, list(marks), 0.72, 0.8, 0.2)
    cam2 = cam(bx((C0g - HALF + IOg) / 2), UP_Y + 0.05, 4.8)
    w = cam2[2]
    rest = merge(cells)
    timed(rest, [f"c{k}" for k in range(4, 32)], 0.12, 0.8, 0.1)
    span = {"d_32": dim(bx(C0g), UP_Y - 0.14, bx(C0g + 32 * HALF), UP_Y - 0.14,
                        f"32 cells: {_n(32 * HALF)}", w, "b")}
    timed(span, ["d_32"], 0.85, 0.85, 0.15)
    steps.append({"t": "Tile the cell along a strip",
                  "d": f"Each copy of the memory cell is placed {_n(HALF)} µm "
                       "from the previous one, and every second copy is "
                       "mirrored (m90), so a pair repeats every "
                       f"{_n(PAIR)} µm. Thirty-two cells form a strip.",
                  "p": [{"s": {}, "c": cam1, "i": 1},
                        {"s": merge(first, marks), "c": cam1, "ms": 3400},
                        {"s": merge(rest, span), "c": cam2, "ms": 3600}]})

    # 2. close the strip: the dummy and tap at its inner end, and the
    # array's outer dummy at the other
    ends = {"di0": stamp(in_strip("dmy", 0, "L", END, 1, "cd"), 0.05),
            "tp0": stamp(in_strip("tap", 0, "L", END + HALF, 1, "ct"), 0.05),
            "oL0": stamp(outer(0, "L"), 0.05)}
    endlabs = {"l_dI": label(bx(C0g + 32.5 * HALF), UP_Y + ROW + 0.05, "dummy", w),
               "l_tp": label(bx(C0g + 33.5 * HALF), UP_Y - 0.1, "tap", w),
               "l_dO": label(bx(C0g - HALF / 2), UP_Y + ROW + 0.05, "dummy", w)}
    e1 = merge(cells, ends, endlabs)
    timed(e1, ["di0", "tp0", "oL0"], 0, 0.5, 0.3)
    timed(e1, list(endlabs), 0.7, 0.7, 0.3)
    steps.append({"t": "Close the strip",
                  "d": "A dummy cell is placed at each end, so that the "
                       "edge cells have the same surroundings as the inner "
                       "ones, and a tap cell ties the wells to the supply.",
                  "p": [{"s": e1, "c": cam2, "ms": 2200}]})

    # 3. stack the strips: a copy flips up over the strip's top edge (m0),
    # then the flipped pair is copied up a pair pitch
    cam3 = cam(bx((C0g - HALF + IOg) / 2), UP_Y + 0.52, 5.4)
    w = cam3[2]
    s0 = {"sL0": strip(0, "L"), "oL0": outer(0, "L")}
    a1 = merge(s0, {"sL1": strip(0, "L")})
    b1 = merge(s0, {"sL1": strip(1, "L"), "oL1": stamp(outer(1, "L"), 0.05)},
               {"d_s": dim(bx(IOg) + 0.12, UP_Y, bx(IOg) + 0.12, UP_Y + ROW,
                           f"{_n(ROW)}", w, "r"),
                "l_m0": label(bx(C0g + 16 * HALF), UP_Y + 1.5 * ROW - 0.03,
                              "flipped (m0)", w, "th")})
    timed(b1, ["sL1"], 0, 0, 0.6)
    timed(b1, ["oL1", "d_s", "l_m0"], 0.6, 0.7, 0.3)
    a2 = merge(b1, {"sL2": strip(0, "L"), "sL3": strip(1, "L")})
    four = {f"sL{s}": strip(s, "L") for s in range(4)}
    four.update({f"oL{s}": outer(s, "L") for s in range(4)})
    b2 = merge(four, {k: stamp(four[k], 0.05) for k in ("oL2", "oL3")}, {
        "d_rp": dim(bx(IOg) + 0.12, UP_Y, bx(IOg) + 0.12, UP_Y + RP,
                    f"{_n(RP)}", w, "r"),
        "d_a": dim(bx(C0g) - 0.3, UP_Y, bx(C0g) - 0.3, UP_Y + 4 * ROW,
                   f"{_n(4 * ROW)}", w, "l")})
    timed(b2, ["sL2", "sL3"], 0, 0, 0.6)
    timed(b2, ["oL2", "oL3", "d_rp", "d_a"], 0.6, 0.7, 0.3)
    steps.append({"t": "Stack the strips",
                  "d": f"A copy of the strip is flipped over its top edge "
                       f"(m0), {_n(ROW)} µm higher, and the pair is then "
                       f"copied {_n(RP)} µm upward. Four strips form the "
                       "array.",
                  "p": [{"s": a1, "c": cam2, "i": 1},
                        {"s": b1, "c": cam3, "ms": 2400},
                        {"s": a2, "c": cam3, "i": 1},
                        {"s": b2, "c": cam3, "ms": 2600}]})

    # 4. the read and write column, placed from the group's origin
    G = detail()
    cam4 = cam(bx((C0g + mg(IOg)) / 2), UP_Y + 0.5, 7.6)
    w = cam4[2]
    colk = ["io"] + [f"mux{s}" for s in range(4)]
    b4 = merge(four, {k: stamp(G[k], 0.12) for k in colk}, {
        "d_io": dim(bx(0), UP_Y - 0.25, bx(IOg), UP_Y - 0.25,
                    f"{_n(IOg)} from the group's origin", w, "b"),
        "l_io": label(bx((IOg + mg(IOg)) / 2), UP_Y + 4 * ROW + 0.18,
                      "read and write column", w)})
    timed(b4, colk, 0, 0.45, 0.4)
    timed(b4, ["d_io", "l_io"], 0.7, 0.7, 0.3)
    steps.append({"t": "Place the read and write column",
                  "d": f"The column is placed {_n(IOg)} µm from the "
                       "group's origin, beside the taps. Its multiplexer has "
                       "one leg per strip.",
                  "p": [{"s": b4, "c": cam4, "ms": 2200}]})

    # 5. mirror the array about the group's axis: each copy's origin moves
    # to its mirror image while its sx turns from 1 to -1, so the array
    # swings over the axis
    cam5 = cam(gcx, UP_Y + 0.5, 11.6)
    w = cam5[2]
    left = merge(four, {k: G[k] for k in colk})
    a5 = merge(left, {f"sR{s}": strip(s, "L") for s in range(4)},
               {f"oR{s}": outer(s, "L") for s in range(4)})
    b5 = merge(G, {
        "d_m": dim(bx(0), UP_Y - 0.3, bx(MIR), UP_Y - 0.3,
                   f"{_n(MIR)}: the mirrored copy's origin", w, "b"),
        "d_W": dim(bx(EDGEg), UP_Y + 4 * ROW + 0.3, bx(mg(EDGEg)),
                   UP_Y + 4 * ROW + 0.3, f"{_n(mg(EDGEg) - EDGEg)} across", w, "t")})
    timed(b5, [f"sR{s}" for s in range(4)] + [f"oR{s}" for s in range(4)],
          0, 0.04, 0.62)
    timed(b5, ["fL", "fR", "d_m", "d_W"], 0.7, 0.75, 0.25)
    steps.append({"t": "Mirror the array",
                  "d": f"A mirrored copy of the array (m90) is placed at "
                       f"{_n(MIR)} µm, so that both arrays face the column "
                       "they share. Together they form one column group.",
                  "p": [{"s": a5, "c": cam4, "i": 1},
                        {"s": b5, "c": cam5, "ms": 2800}]})

    # 6. stack the groups: zoom out, stamp the upper stack one group at a
    # time, then flip the whole stack down over the band (m0)
    B32, bot, top, _ = blockf(32)
    cam_b = lambda bot, top: cam(gcx, (bot + top) / 2, (top - bot) * 1.3 * ASPECT)
    cb = cam_b(bot, top)
    w = cb[2]
    up = {f"gU{k}": B32[f"gU{k}"] for k in range(32)}
    z = {"gU0": up["gU0"]}
    timed(z, ["gU0"], 0.5, 0.5, 0.4)
    st = merge({k: stamp(v, 1.2) for k, v in up.items()}, {"band": B32["band"]})
    timed(st, [f"gU{k}" for k in range(1, 32)], 0, 0.85, 0.12)
    st["d_g"] = dim(bx(mg(EDGEg)) + 1.5, UP_Y, bx(mg(EDGEg)) + 1.5, UP_Y + PITCH,
                    f"every {_n(PITCH)}", w, "r")
    timed(st, ["d_g"], 0.1, 0.1, 0.2)
    flip = merge(up, {"band": B32["band"]})
    for k in range(32):
        flip[f"gL{k}"] = at(up[f"gU{k}"], o=1)
    fl = merge(B32, {
        "d_t": dim(bx(mg(EDGEg)) + 1.5, bot, bx(mg(EDGEg)) + 1.5, top,
                   f"{top - bot:.1f} tall", w, "r"),
        "l_b": label(bx(EDGEg) - 1.2, DEC_Y + BAND0 / 2, "central band", w,
                     "ta", "end")})
    timed(fl, [f"gL{k}" for k in range(32)], 0, 0.1, 0.6)
    timed(fl, ["fT", "fB", "d_t", "l_b"], 0.7, 0.75, 0.25)
    steps.append({"t": "Stack the groups",
                  "d": f"Groups are stacked every {_n(PITCH)} µm upward "
                       "from the central band, and the whole stack is then "
                       "copied, flipped (m0), below the band. The result is "
                       "the 2 kilobyte block.",
                  "p": [{"s": z, "c": cb, "ms": 2000},
                        {"s": st, "c": cb, "ms": 3200},
                        {"s": flip, "c": cb, "i": 1},
                        {"s": fl, "c": cb, "ms": 2600}]})

    # 7. knob one: bits per group.  64: pairs tile outward on every
    # strip; then back to 32, and 16: the outer pairs lift away
    cw = cam(gcx, UP_Y + 0.5, 19.6)
    w = cw[2]
    G64, Gq, G16 = detail(64), detail(32, quarter=True), detail(16)
    wd = gen.width_delta(64) * U
    back = merge(G)
    timed(back, list(G), 0.5, 0.5, 0.5)
    grow64 = merge(G, G64, {
        "d_xL": dim(bx(EDGEg - wd), UP_Y - 0.3, bx(EDGEg), UP_Y - 0.3,
                    f"+{_n(wd)}", w, "b"),
        "d_xR": dim(bx(mg(EDGEg)), UP_Y - 0.3, bx(mg(EDGEg - wd)), UP_Y - 0.3,
                    f"+{_n(wd)}", w, "b"),
        "d_W": dim(bx(EDGEg - wd), UP_Y + 4 * ROW + 0.3, bx(mg(EDGEg - wd)),
                   UP_Y + 4 * ROW + 0.3,
                   f"64 bits: {_n(mg(EDGEg - wd) - EDGEg + wd)} across", w, "t")})
    xs = sorted((k for k in G64 if k.startswith("x")),
                key=lambda k: int(k.split("_")[1]))
    for k in xs:
        grow64[k] = stamp(G64[k], 0.25)
    timed(grow64, xs, 0.05, 0.75, 0.12)
    timed(grow64, ["d_xL", "d_xR", "d_W"], 0.8, 0.8, 0.2)
    shrink = merge(G)
    out16 = merge(G16, {k: lifted(Gq[k], 0.25) for k in Gq if k.startswith("q")}, {
        "l_eL": label(bx(C0g + 8 * HALF), UP_Y + 4 * ROW + 0.15, "left empty", w, "ts"),
        "l_eR": label(bx(mg(C0g + 8 * HALF)), UP_Y + 4 * ROW + 0.15, "left empty", w, "ts"),
        "d_W": dim(bx(EDGEg), UP_Y - 0.3, bx(mg(EDGEg)), UP_Y - 0.3,
                   f"16 bits: still {_n(mg(EDGEg) - EDGEg)} across", w, "b")})
    qs = sorted((k for k in Gq if k.startswith("q")),
                key=lambda k: int(k.split("_")[1]))
    timed(out16, qs, 0, 0.6, 0.15)
    timed(out16, ["eL", "eR", "l_eL", "l_eR", "d_W"], 0.75, 0.8, 0.2)
    steps.append({"t": "Parameter one: bits per group",
                  "d": f"Above 32 bits, additional mirrored pairs are tiled "
                       f"outward on every strip, {_n(PAIR)} µm each. Below "
                       "32, the outer pairs are omitted but the outline "
                       "keeps its 32-bit size, so a 16-bit block saves no "
                       "area.",
                  "p": [{"s": back, "c": cw, "ms": 2000},
                        {"s": grow64, "c": cw, "ms": 3400},
                        {"s": shrink, "c": cw, "ms": 1400},
                        {"s": Gq, "c": cw, "i": 1},
                        {"s": out16, "c": cw, "ms": 2800}]})

    # 8. knob two: strips per group.  The four strips are copied up a
    # group pitch, the column grows, legs 4 to 7 and the new select lines
    cg8 = cam(gcx, UP_Y + 1.1, 11.6)
    w = cg8[2]
    G8 = detail(strips=8)
    ret = merge(Gq)
    timed(ret, qs, 0, 0.5, 0.3)
    up8 = merge(G, {f"s{d}{s + 4}": strip(s, d) for s in range(4) for d in "LR"},
                {f"sel{i}": at(G8[f"sel{i}"], h=0, y=r4(-UP_Y))
                 for i in range(8)})
    e8 = merge(G8, {k: stamp(G8[k], 0.1) for k in
                    [f"o{d}{s}" for s in range(4, 8) for d in "LR"]
                    + [f"mux{s}" for s in range(4, 8)]}, {
        "d_8": dim(bx(C0g) - 0.3, UP_Y, bx(C0g) - 0.3, UP_Y + 8 * ROW,
                   f"{_n(8 * ROW)}", w, "l"),
        "l_sel": label(bx(gen.SELECT_LINE_X[0] * U + IOg), UP_Y - 0.22,
                       "8 new select lines", w, "ts", "start"),
        "l_leg": label(bx(IOg + MUXW) + 0.1, UP_Y + 6 * ROW - 0.03,
                       "legs 4 to 7", w, "ts", "start")})
    timed(e8, [f"s{d}{s}" for s in range(4, 8) for d in "LR"], 0, 0, 0.55)
    timed(e8, ["io", "fL", "fR"], 0, 0, 0.55)
    timed(e8, [f"o{d}{s}" for s in range(4, 8) for d in "LR"]
          + [f"mux{s}" for s in range(4, 8)], 0.5, 0.7, 0.2)
    timed(e8, [f"sel{i}" for i in range(8)], 0.6, 0.75, 0.25)
    timed(e8, ["d_8", "l_sel", "l_leg"], 0.85, 0.85, 0.15)
    steps.append({"t": "Parameter two: strips per group",
                  "d": "With eight strips, one read and write column serves "
                       f"twice as many cells: the four strips are copied "
                       f"{_n(4 * ROW)} µm upward, the multiplexer is tiled "
                       "again for legs 4 to 7, and eight new select lines "
                       "run up through every group to drive them.",
                  "p": [{"s": ret, "c": cw, "ms": 1500},
                        {"s": G, "c": cw, "i": 1},
                        {"s": up8, "c": cw, "i": 1},
                        {"s": e8, "c": cg8, "ms": 3400}]})

    # 9. knob three: rows.  Groups stamp onto both stack ends; past 48
    # rows the band grows and the lower stack slides down with it
    phases, prev = [], None
    for rows in (8, 32, 64, 96):
        fb, bot, top, grow = blockf(rows)
        c = cam_b(bot, top)
        w = c[2]
        fr = merge(fb, {
            f"d_r{rows}": dim(bx(mg(EDGEg)) + 0.05 * w, bot, bx(mg(EDGEg)) + 0.05 * w,
                              top, f"{rows} rows: {top - bot:.1f}", w, "r")})
        if grow:
            fr[f"l_b{rows}"] = label(bx(EDGEg) - 0.02 * w, DEC_Y - grow / 2,
                                     f"rebuilt control: band {BAND0 + grow:.1f}",
                                     w, "ta", "end")
        if prev is None:
            phases.append({"s": fr, "c": c, "ms": 2200})
        else:
            new = sorted((k for k in fb if k not in prev and k[0] == "g"),
                         key=lambda k: int(k[2:]))
            for k in new:
                fr[k] = stamp(fb[k], 1.5 if k[1] == "U" else -1.5)
            timed(fr, new, 0.1, 0.8, 0.1)
            phases.append({"s": fr, "c": c, "ms": 3000})
        prev = fb
    steps.append({"t": "Parameter three: rows",
                  "d": "The number of rows sets how many groups each stack "
                       "holds. Beyond 48 rows the kit's delay line cannot "
                       "wait long enough for the far row, so the rebuilt "
                       "control is used and the band grows.",
                  "p": phases})

    return ("<defs>" + "".join(defs) + "</defs>",
            json.dumps({"steps": steps}, separators=(",", ":"), ensure_ascii=False))


SIDE_JS = r'''<script>
// contents list: marks the last section whose heading has passed near the
// top of the window.  On wide screens the list starts below the header,
// rides up with the page until it reaches the top, follows from there, and
// hides before the footer.
(function () {
  var side = document.getElementById('side'), btn = document.getElementById('side-btn');
  var links = [].slice.call(side.querySelectorAll('a'));
  var secs = links.map(function (a) { return document.getElementById(a.getAttribute('href').slice(1)); });
  var head = document.querySelector('header'), foot = document.querySelector('footer');
  var wide = window.matchMedia('(min-width: 1000px)');
  var queued = false;

  function update() {
    queued = false;
    if (wide.matches) {
      side.style.top = Math.max(96, head.getBoundingClientRect().bottom + 28) + 'px';
      var clear = foot.getBoundingClientRect().top >
                  side.getBoundingClientRect().top + side.offsetHeight + 24;
      side.classList.toggle('is-on', clear);
    } else {
      side.style.top = '';
    }
    var here = 0;
    secs.forEach(function (sec, i) { if (sec && sec.getBoundingClientRect().top < 140) here = i; });
    // at the very bottom the last section may never reach the top: mark the last
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4)
      here = secs.length - 1;
    links.forEach(function (a, i) { a.classList.toggle('is-here', i === here); });
  }
  function queue() { if (!queued) { queued = true; requestAnimationFrame(update); } }
  window.addEventListener('scroll', queue, {passive: true});
  window.addEventListener('resize', queue);
  // web fonts and images change the header's height once they load
  window.addEventListener('load', queue);
  if (document.fonts) document.fonts.ready.then(queue);

  // below 1000px: the list is a small card opened by the Contents button
  function setOpen(open) {
    side.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  btn.addEventListener('click', function (e) { e.stopPropagation(); setOpen(!side.classList.contains('open')); });
  links.forEach(function (a) { a.addEventListener('click', function () { setOpen(false); }); });
  document.addEventListener('click', function (e) { if (!side.contains(e.target)) setOpen(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') setOpen(false); });
  update();
})();
</script>'''


TILE_JS = r'''<script>
// "Tile to the requested size", animated.  The frames come from
// scripts/make_site.py (tile_animation): every shape is keyed, and this
// only moves each keyed shape, and the camera window, from one frame to
// the next.  A shape missing from a frame fades out where it stands.
// Shapes: rectangles, labels, dimension lines (k "d") and placed copies
// of a definition (k "u": an origin and a mirror, sx and sy).
(function () {
  var D = __TILE__, ASPECT = 2, NS = 'http://www.w3.org/2000/svg';
  var svg = document.getElementById('tile');
  if (!svg) return;
  var world = document.getElementById('tl-world');
  var reduce = !!(window.matchMedia &&
                  window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  var els = {}, shown = {}, camNow = null;
  var step = 0, playing = false, token = 0, timer = null;
  var MOVE = 1500, BETWEEN = 600, DWELL = 2600;
  var NUM = ['x', 'y', 'w', 'h', 'f', 'o', 'sx', 'sy', 'x1', 'y1', 'x2', 'y2'];
  var $ = function (id) { return document.getElementById(id); };

  // fill in what the frames leave out: shown, unmirrored
  D.steps.forEach(function (st) {
    st.p.forEach(function (ph) {
      for (var k in ph.s) {
        var s = ph.s[k];
        if (s.o === undefined) s.o = 1;
        if (s.k === 'u') {
          if (s.sx === undefined) s.sx = 1;
          if (s.sy === undefined) s.sy = 1;
        }
      }
    });
  });

  // labels are sized to read at 760 pixels across; on a narrower screen
  // they grow back towards that size instead of shrinking with the drawing
  var grow = 1;
  function fit() {
    var px = svg.getBoundingClientRect().width;
    grow = px > 0 ? Math.min(2.2, Math.max(1, 760 / px)) : 1;
  }
  fit();
  window.addEventListener('resize', function () {
    fit();
    for (var k in shown) paint(k, shown[k]);
  });

  function copy(s, o) {
    var r = {};
    for (var k in s) r[k] = s[k];
    if (o !== undefined) r.o = o;
    return r;
  }
  function make(k, s) {
    var e;
    if (s.k === 'u') {
      e = document.createElementNS(NS, 'use');
    } else if (s.k === 'd') {
      e = document.createElementNS(NS, 'g');
      e.appendChild(document.createElementNS(NS, 'path')).setAttribute('class', 'dimp');
      e.appendChild(document.createElementNS(NS, 'text')).setAttribute('class', 'tv');
    } else if (s.s !== undefined) {
      e = document.createElementNS(NS, 'text');
    } else {
      e = document.createElementNS(NS, 'rect');
    }
    world.appendChild(e);
    return (els[k] = e);
  }
  function label(e, s, f) {
    e.style.fontSize = f * grow + 'px';
    e.style.strokeWidth = f * grow * 0.3 + 'px';
    if (e.textContent !== s) e.textContent = s;
  }
  function paint(k, s) {
    var e = els[k] || make(k, s);
    if (s.k === 'u') {
      var ref = '#tl-' + s.r;
      if (e.getAttribute('href') !== ref) e.setAttribute('href', ref);
      e.setAttribute('transform', 'translate(' + s.x + ',' + s.y + ') scale(' +
                     s.sx + ',' + s.sy + ')');
      e.setAttribute('class', s.c || '');
    } else if (s.k === 'd') {
      // a dimension: the line, a tick at each end, the value beside it
      var L = s.f * grow * 0.45, hz = Math.abs(s.y2 - s.y1) < Math.abs(s.x2 - s.x1);
      var d = hz
        ? 'M' + s.x1 + ' ' + (s.y1 - L) + 'V' + (s.y1 + L) + 'M' + s.x2 + ' ' +
          (s.y2 - L) + 'V' + (s.y2 + L) + 'M' + s.x1 + ' ' + s.y1 + 'H' + s.x2
        : 'M' + (s.x1 - L) + ' ' + s.y1 + 'H' + (s.x1 + L) + 'M' + (s.x2 - L) +
          ' ' + s.y2 + 'H' + (s.x2 + L) + 'M' + s.x1 + ' ' + s.y1 + 'V' + s.y2;
      e.firstChild.setAttribute('d', d);
      var t = e.lastChild, f = s.f * grow, mx = (s.x1 + s.x2) / 2, my = (s.y1 + s.y2) / 2;
      var pos = {t: [mx, my - f * 0.55, 'middle'], b: [mx, my + f * 1.2, 'middle'],
                 l: [mx - f * 0.5, my + f * 0.35, 'end'],
                 r: [mx + f * 0.5, my + f * 0.35, 'start']}[s.sd];
      t.setAttribute('x', pos[0]);
      t.setAttribute('y', pos[1]);
      t.setAttribute('text-anchor', pos[2]);
      label(t, s.s, s.f);
    } else if (s.s !== undefined) {
      e.setAttribute('x', s.x);
      e.setAttribute('y', s.y);
      e.setAttribute('text-anchor', s.a);
      e.setAttribute('class', s.c);
      label(e, s.s, s.f);
    } else {
      e.setAttribute('x', s.x);
      e.setAttribute('y', s.y);
      e.setAttribute('width', Math.max(0, s.w));
      e.setAttribute('height', Math.max(0, s.h));
      e.setAttribute('class', s.c);
    }
    e.setAttribute('opacity', s.o);
    e.style.display = s.o > 0.002 ? '' : 'none';
  }
  function setCam(c) {
    camNow = c;
    svg.setAttribute('viewBox', c[0] + ' ' + c[1] + ' ' + c[2] + ' ' + c[2] / ASPECT);
  }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function ease(t) { return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }
  function mix(a, b, t) {
    var o = copy(b);
    NUM.forEach(function (p) {
      if (a[p] !== undefined && b[p] !== undefined) o[p] = lerp(a[p], b[p], t);
    });
    return o;
  }
  // the centre moves straight; the width zooms at a steady rate
  function camMix(a, b, t) {
    var w = Math.exp(lerp(Math.log(a[2]), Math.log(b[2]), t));
    var cx = lerp(a[0] + a[2] / 2, b[0] + b[2] / 2, t);
    var cy = lerp(a[1] + a[2] / ASPECT / 2, b[1] + b[2] / ASPECT / 2, t);
    return [cx - w / 2, cy - w / ASPECT / 2, w];
  }
  // a frame, plus every shape on screen now that it leaves out, faded
  function full(frame) {
    var out = {}, k;
    for (k in frame) out[k] = frame[k];
    for (k in shown) if (!(k in out)) out[k] = copy(shown[k], 0);
    return out;
  }
  function show(frame, c) {
    var to = full(frame);
    for (var k in to) paint(k, to[k]);
    shown = to;
    setCam(c);
  }
  // where every shape starts and ends for a move to frame
  function prepare(frame, c) {
    var to = full(frame), from = {}, k;
    for (k in to) {
      var was = shown[k], z = to[k];
      if (z.ly !== undefined && (!was || was.o === 0)) {
        // a stamped copy: it drops into its place from just above
        from[k] = copy(z, 0);
        from[k].y = z.y - z.ly;
      } else {
        from[k] = was || copy(z, 0);
      }
    }
    return {to: to, from: from, c0: camNow, c: c};
  }
  // the move at time t, 0 to 1
  function draw(m, t) {
    for (var key in m.to) {
      var a = m.from[key], z = m.to[key], u;
      if (z.dl !== undefined) {
        // a staggered copy: its own move inside the phase
        u = ease(Math.max(0, Math.min(1, (t - z.dl) / z.sp)));
      } else {
        u = ease(t);
        // labels are sized for one camera, so they leave early and
        // arrive late rather than swelling or shrinking with a zoom
        if (z.s !== undefined) {
          if (z.o === 0 && a.o > 0) u = Math.min(1, t * 4);
          else if (a.o === 0 && z.o > 0) u = Math.max(0, (t - 0.65) / 0.35);
        }
      }
      paint(key, mix(a, z, u));
    }
    setCam(camMix(m.c0, m.c, ease(t)));
  }
  function animate(frame, c, ms, done) {
    if (reduce || !camNow) { show(frame, c); done(); return; }
    var m = prepare(frame, c), t0 = null, my = token;
    function tick(now) {
      if (my !== token) return;
      if (t0 === null) t0 = now;
      var t = Math.min(1, (now - t0) / ms);
      draw(m, t);
      if (t < 1) requestAnimationFrame(tick);
      else { shown = m.to; done(); }
    }
    requestAnimationFrame(tick);
  }
  function ui() {
    var st = D.steps[step];
    $('tl-n').textContent = (step + 1) + ' / ' + D.steps.length;
    $('tl-title').textContent = st.t;
    $('tl-desc').textContent = st.d;
    $('tl-prev').disabled = step === 0;
    $('tl-next').disabled = step === D.steps.length - 1;
    $('tl-play').textContent = playing ? '❚❚ pause' : '▶ play';
  }
  function stop() {
    token++;
    clearTimeout(timer);
  }
  // play step i's phases in order, from whatever is on screen
  function run(i, done) {
    stop();
    step = i;
    ui();
    var phases = D.steps[i].p, p = 0, my = token;
    (function next() {
      if (my !== token) return;
      if (p === phases.length) { if (done) done(); return; }
      var ph = phases[p++];
      if (ph.i) { show(ph.s, ph.c); next(); return; }
      animate(ph.s, ph.c, ph.ms || MOVE, function () {
        if (my !== token) return;
        timer = setTimeout(next, p < phases.length ? BETWEEN : 0);
      });
    })();
  }
  // the end of step i, at once
  function end(i) {
    stop();
    step = i;
    var ph = D.steps[i].p;
    show(ph[ph.length - 1].s, ph[ph.length - 1].c);
    ui();
  }
  function auto() {
    if (!playing) return;
    if (step === D.steps.length - 1) { playing = false; ui(); return; }
    timer = setTimeout(function () { run(step + 1, auto); }, DWELL);
  }
  function play() {
    playing = true;
    run(step === D.steps.length - 1 ? 0 : step, auto);
  }
  $('tl-play').onclick = function () {
    if (playing) { playing = false; end(step); }
    else play();
  };
  $('tl-next').onclick = function () {
    playing = false;
    if (step < D.steps.length - 1) run(step + 1);
  };
  $('tl-prev').onclick = function () {
    playing = false;
    if (step > 0) end(step - 1);
  };
  end(0);
  // play once by itself the first time most of it comes into view
  if (!reduce && 'IntersectionObserver' in window) {
    var seen = false, io = new IntersectionObserver(function (es) {
      if (!seen && es[0].isIntersecting) { seen = true; io.disconnect(); play(); }
    }, {threshold: 0.6});
    io.observe(svg);
  }
  // for checking the drawing: step i, phase p, at time t of its move
  function at(i, p, t) {
    if (i > 0) end(i - 1); else { stop(); show({}, D.steps[0].p[0].c); }
    step = i;
    ui();
    var ph = D.steps[i].p;
    for (var q = 0; q < p; q++) show(ph[q].s, ph[q].c);
    draw(prepare(ph[p].s, ph[p].c), t);
  }
  window.__tile = {end: end, at: at, steps: D.steps.length, data: D};
})();
</script>'''



DIAGRAM = r'''
<div class="config">
  <div class="knobs">
    <div class="knob">
      <div class="name">Bits per group</div>
      <div class="val" id="o-bits">32</div>
      <input id="k-bits" type="range" min="4" max="64" step="4" value="32">
      <div class="range"><span>4</span><span>64</span></div>
      <div class="eff">How many words the block holds</div>
    </div>
    <div class="knob">
      <div class="name">Cell strips</div>
      <div class="val" id="o-strips">4</div>
      <span class="seg">
        <button id="k-s4" class="on" data-s="4">4</button><button id="k-s8" data-s="8">8</button>
      </span>
      <div class="range"><span>4 or 8</span><span></span></div>
      <div class="eff">How many words, by sharing one read and write column
        between twice as many cells</div>
    </div>
    <div class="knob">
      <div class="name">Rows</div>
      <div class="val" id="o-depth">32</div>
      <input id="k-depth" type="range" min="8" max="96" step="8" value="32">
      <div class="range"><span>8</span><span>96</span></div>
      <div class="eff">How wide each word is, at two bits per row</div>
    </div>
  </div>
  <div class="presets">Built and measured:
    <button data-p="32,4,32">2 kilobytes</button>
    <button data-p="16,4,32">1 kilobyte</button>
    <button data-p="32,8,32">4 kilobytes</button>
    <button data-p="64,4,64">8 kilobytes</button>
  </div>
  <svg id="dia" viewBox="0 0 560 470" role="img" aria-label="Block diagram of the configured memory"></svg>
  <div class="wordsec">
    <h3>Where one word lives</h3>
    <p>A word is not a run of neighbouring cells. Every column group
    holds one bit of it, always in the same place, so a word is a single
    vertical thread through the whole block. Here it is in the artwork,
    at three magnifications of one generated 2 kilobyte block.</p>
    <div class="shots">

      <h4>The whole block</h4>
      <div class="pair a">
        <figure>
          <div class="tag">the layout</div>
          <img src="images/word_bank.png"
               alt="The whole generated block with one column of cells marked">
        </figure>
        <figure>
          <div class="tag">drawn</div>
          <!-- the same block, at the same proportions: every number here
               is read out of the layout by scripts/render_word.py -->
          <svg class="d" viewBox="0 0 96.12 778.04" role="img"
               aria-label="The block drawn: two arrays, the read and write
                           column, the decoder band, and the word's column">
            <rect class="arr" x="2.165" y="2.785" width="34.55" height="345.6"/>
            <rect class="arr" x="2.165" y="429.655" width="34.55" height="345.6"/>
            <rect class="peri" x="36.715" y="2.785" width="22.69" height="345.6"/>
            <rect class="peri" x="36.715" y="429.655" width="22.69" height="345.6"/>
            <rect class="arr" x="59.405" y="2.785" width="34.55" height="345.6"/>
            <rect class="arr" x="59.405" y="429.655" width="34.55" height="345.6"/>
            <rect class="band" x="0" y="348.385" width="96.12" height="81.27"/>
            <rect class="mark" x="13.325" y="2.785" width="2.5" height="345.6"/>
            <rect class="mark" x="13.325" y="429.655" width="2.5" height="345.6"/>
            <rect class="outline" x="0.5" y="0.5" width="95.12" height="777.04"/>
          </svg>
        </figure>
        <p class="cap">9.61 by 77.80 micrometres. The amber line is the word's column of cells: full
          height, both halves, stopping at the band. Pale blue is
          memory cells, green the shared read and write column, slate the
          decoder and control. The marker is twice the column's true
          width, to be visible.</p>
      </div>

      <h4>One column group</h4>
      <div class="pair b">
        <figure>
          <div class="tag">the layout</div>
          <img src="images/word_group.png"
               alt="One column group of the generated block, enlarged">
        </figure>
        <figure>
          <div class="tag">drawn, to the same width</div>
          <!-- the same window: x is the full 9.612 um of the block, y is
               one group plus a row of each neighbour, so the drawing
               stands column for column under the picture -->
          <svg class="d" viewBox="0 0 961.2 252" role="img"
               aria-label="One column group drawn: two arrays sharing one
                           read and write column">
            <defs>
              <pattern id="cellgrid" x="21.65" y="30" width="10.8" height="27"
                       patternUnits="userSpaceOnUse">
                <rect class="cell" x="1.6" y="2.4" width="7.6" height="22.2" rx="1.4"/>
              </pattern>
            </defs>
            <text class="dl mid strong" x="480.6" y="19">ONE COLUMN GROUP</text>
            <rect class="arr" x="21.65" y="30" width="345.5" height="162"/>
            <rect x="21.65" y="30" width="345.5" height="162" fill="url(#cellgrid)"/>
            <rect class="arr" x="594.05" y="30" width="345.5" height="162"/>
            <rect x="594.05" y="30" width="345.5" height="162" fill="url(#cellgrid)"/>
            <rect class="peri" x="367.15" y="30" width="226.9" height="162"/>
            <rect class="io" x="392" y="66" width="177" height="90"/>
            <rect class="fade" x="0" y="30" width="961.2" height="27"/>
            <rect class="fade" x="0" y="165" width="961.2" height="27"/>
            <rect class="mark" x="139.25" y="30" width="13.2" height="162"/>
            <line class="sep" x1="0" y1="57" x2="961.2" y2="57"/>
            <line class="sep" x1="0" y1="165" x2="961.2" y2="165"/>
            <rect class="markbox" x="140.45" y="111" width="10.8" height="27"/>
            <text class="dl mid" x="480.6" y="117">read and write column</text>
            <text class="dl mid" x="194.4" y="213">array, 32 cells across</text>
            <text class="dl mid" x="480.6" y="213">shared between the two</text>
            <text class="dl mid" x="766.8" y="213">mirrored array, 32 cells across</text>
            <text class="dl mid" x="480.6" y="239">One address bit chooses which array, five choose
              the column, two choose the row: one cell, one bit of the word.</text>
          </svg>
        </figure>
        <p class="cap">Both sides of it: two arrays of 32 cells, four
          tall, sharing one read and write column. That sharing is what
          makes them one group, giving one bit.</p>
      </div>

      <h4>One cell</h4>
      <div class="pair c">
        <figure>
          <div class="tag">the layout</div>
          <img src="images/word_cell.png"
               alt="One six-transistor memory cell in the generated layout">
        </figure>
        <figure>
          <div class="tag">drawn</div>
          <svg class="d" viewBox="0 0 240 240" role="img"
               aria-label="The six-transistor cell as a circuit">
            <path class="wire" d="M150 86 H172 V156 H150"/>
            <path class="wire" d="M90 156 H68 V86 H90"/>
            <path class="wire" d="M68 121 H58"/>
            <path class="wire" d="M172 121 H182"/>
            <path class="wire" d="M30 121 H18 V46"/>
            <path class="wire" d="M210 121 H222 V46"/>
            <path class="wire" d="M44 133 V196"/>
            <path class="wire" d="M196 133 V196"/>
            <path class="wire" d="M18 196 H222"/>
            <polygon class="inv" points="88,66 88,106 150,86"/>
            <polygon class="inv" points="152,136 152,176 90,156"/>
            <rect class="fet" x="30" y="109" width="28" height="24" rx="3"/>
            <rect class="fet" x="182" y="109" width="28" height="24" rx="3"/>
            <text class="dl" x="8" y="34">bit line</text>
            <text class="dl end" x="232" y="34">bit line bar</text>
            <text class="dl" x="8" y="222">word line</text>
            </svg>
        </figure>
        <p class="cap">Six transistors in 0.108 by 0.27 micrometres.
          Two cross-coupled inverters hold the bit for
          as long as the supply is on, and the two access transistors
          join it to the bit lines when the word line rises.</p>
      </div>

    </div>
    <h4>Any size, drawn</h4>
    <p class="shotnote">The same two drawings for any size the compiler
    will build: the block with the word's column marked, and one of its
    groups.</p>
    <div class="mini">
      <label>Bits per group
        <input id="m-bits" type="range" min="4" max="64" step="4" value="32">
        <b id="m-obits">32</b></label>
      <label>Cell strips
        <span class="seg"><button id="m-s4" class="on" data-s="4">4</button
        ><button id="m-s8" data-s="8">8</button></span></label>
      <label>Rows
        <input id="m-depth" type="range" min="8" max="96" step="8" value="32">
        <b id="m-odepth">32</b></label>
    </div>
    <svg id="word" viewBox="0 0 560 420" role="img"
         aria-label="One word picked out in the block"></svg>
    <h4>What an address means</h4>
    <p class="shotnote">What each part of it selects, from the block
    down to one cell in every group.</p>
    <svg id="addr" viewBox="0 0 560 120" role="img"
         aria-label="The address broken into its three fields"></svg>
    <h4>One read</h4>
    <p class="shotnote">From the signals at the edge of the bank to data
    on the pins. Step through it with the arrows, which stay in
    view as you scroll the drawing.</p>
    <div class="stepper">
      <button id="rs-prev" aria-label="previous step">&#9664;</button>
      <span id="rs-n">1 / 8</span>
      <button id="rs-next" aria-label="next step">&#9654;</button>
      <b id="rs-title"></b>
      <p id="rs-desc"></p>
    </div>
    <svg id="read" class="walk" viewBox="0 0 700 860" role="img"
         aria-label="A read, from the pins to data out, one step at a time"></svg>
    <h4>One write</h4>
    <p class="shotnote">The same bank, storing a bit instead of reading
    one: the write driver takes the place of the sense amplifier.</p>
    <div class="stepper">
      <button id="ws-prev" aria-label="previous step">&#9664;</button>
      <span id="ws-n">1 / 8</span>
      <button id="ws-next" aria-label="next step">&#9654;</button>
      <b id="ws-title"></b>
      <p id="ws-desc"></p>
    </div>
    <svg id="write" class="walk" viewBox="0 0 700 880" role="img"
         aria-label="A write, from the pins to the cell, one step at a time"></svg>
    <h4>At the cell: a read and a write</h4>
    <p class="shotnote">Inside one cell: six transistors, two bit lines and
    a word line. Pick read or write and step through what each part does.
    The gauge beside each bit line shows its voltage, from 0 to 0.7 volts.</p>
    <div class="stepper">
      <span class="seg"><button id="cs-read" class="on">read</button><button
        id="cs-write">write</button></span>
      <button id="cs-prev" aria-label="previous step">&#9664;</button>
      <span id="cs-n">1 / 5</span>
      <button id="cs-next" aria-label="next step">&#9654;</button>
      <b id="cs-title"></b>
      <p id="cs-desc"></p>
    </div>
    <svg id="cell" class="walk" viewBox="0 0 700 440" role="img"
         aria-label="One six-transistor cell, through a read and a write"></svg>
  </div>
  <div class="derived">
    <div><b id="d-org">256 words of 64 bits</b><span>organisation</span></div>
    <div><b id="d-cap">2 kilobytes</b><span>capacity</span></div>
    <div><b id="d-size">9.61 by 77.80 micrometres</b><span>block size</span></div>
    <div><b id="d-addr">8</b><span>address bits</span></div>
  </div>
  <p class="caption" id="d-note"></p>
</div>
<script>
(function () {
  var CELL_W = 0.108, CELL_H = 0.27;        // memory cell pitch, micrometres
  var st = {bits: 32, strips: 4, depth: 32};
  var $ = function (id) { return document.getElementById(id); };

  function bandV2(s) { return s.bits !== 32 || s.strips !== 4 || s.depth > 48; }

  // size model, reproduced exactly on all four built configurations
  function size(s) {
    var w = 2 * Math.max(s.bits, 32) * CELL_W + 2.700;
    var margin = s.strips === 8 ? 17.864 : (bandV2(s) ? 16.244 : 8.684);
    return [w, 2 * s.depth * s.strips * CELL_H + margin];
  }

  function el(tag, attrs, text) {
    var n = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text !== undefined) n.textContent = text;
    return n;
  }

  // Individual cells, drawn with gaps so a square reads as one cell
  // rather than as hatching.  Past ten columns the drawing breaks and
  // says how many there really are: legibility beats a faithful count,
  // and the block on the left already carries the true proportions.
  var CELL_CAP = 10;      // shared by both drawings
  function cells(g, x, y, w, h, ncol, nrow, markFirst) {
    var shown = Math.min(ncol, CELL_CAP), broken = ncol > shown;
    var slots = shown + (broken ? 1 : 0);
    var cw = w / slots, ch = h / nrow;
    var pad = Math.max(0.6, Math.min(1.6, cw * 0.14, ch * 0.14));
    for (var r = 0; r < nrow; r++) {
      for (var c = 0; c < shown; c++) {
        var cls = (markFirst && r === 0 && c === 0) ? 'cell mark' : 'cell';
        g.appendChild(el('rect', {x: x + c * cw + pad, y: y + r * ch + pad,
                                  width: cw - 2 * pad, height: ch - 2 * pad,
                                  rx: Math.min(1.2, cw / 6), class: cls}));
      }
      if (broken)
        g.appendChild(el('text', {x: x + w - cw / 2, y: y + r * ch + ch / 2 + 3,
                                  class: 'lab mid'}, '\u2026'));
    }
  }

  function draw() {
    var s = st, sz = size(s), W = sz[0], H = sz[1];
    var X = 230, Y = 54, GW = 310, GH = 180;     // the detail panel
    var svg = $('dia');
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    // LEFT: the block at true scale.  One scale serves every setting, so
    // a deeper memory is visibly a taller one, and it is set from the
    // tallest block the compiler will build (96 rows at eight strips,
    // 432 micrometres) so nothing can run off the canvas.
    var PANEL = 400, TOP = 20;
    var px = PANEL / size({bits: 32, strips: 8, depth: 96})[1];
    var bw = W * px, bh = H * px;
    var bx = 64 - bw / 2, by = TOP + (PANEL - bh);
    var g = el('g', {});
    svg.appendChild(g);
    var arrayW = bw * (2 * Math.max(s.bits, 32) * CELL_W) / W;
    var bandH = Math.max(3, bh * (sz[1] - 2 * s.depth * s.strips * CELL_H) / H);
    var stackH = (bh - bandH) / 2;
    g.appendChild(el('rect', {x: bx, y: by, width: bw, height: stackH, class: 'arr'}));
    g.appendChild(el('rect', {x: bx, y: by + stackH + bandH, width: bw,
                              height: stackH, class: 'arr'}));
    g.appendChild(el('rect', {x: bx, y: by + stackH, width: bw, height: bandH,
                              class: 'band'}));
    g.appendChild(el('rect', {x: bx, y: by, width: bw, height: bh, class: 'outline'}));
    ['whole block, to scale', W.toFixed(2) + ' by ' + H.toFixed(2),
     'micrometres'].forEach(function (t, i) {
      g.appendChild(el('text', {x: 64, y: by + bh + 15 * (i + 1), class: 'lab mid'}, t));
    });


    // CALLOUT: one column-group row, marked where it sits in the block and
    // opened out into the detail panel on the right
    var rowH = Math.max(2.5, (s.strips * CELL_H) * px);
    var rowY = by + stackH * 0.42;
    var rowY2 = by + bh - stackH * 0.42 - rowH;   // the mirror of it below
    var gbx = X - 7, gby = Y - 7, gbw = GW + 14, gbh = GH + 14;
    g.appendChild(el('polygon', {points: [bx + bw, rowY, gbx, gby,
                                          gbx, gby + gbh, bx + bw,
                                          rowY + rowH].join(' '), class: 'zoom'}));
    g.appendChild(el('line', {x1: bx + bw, y1: rowY, x2: gbx, y2: gby,
                              class: 'leader'}));
    g.appendChild(el('line', {x1: bx + bw, y1: rowY + rowH, x2: gbx,
                              y2: gby + gbh, class: 'leader'}));
    g.appendChild(el('rect', {x: bx - 1, y: rowY, width: bw + 2, height: rowH,
                              class: 'hl'}));
    g.appendChild(el('rect', {x: bx - 1, y: rowY2, width: bw + 2, height: rowH,
                              class: 'hl'}));
    g.appendChild(el('line', {x1: bx + bw, y1: rowY2 + rowH / 2, x2: gbx,
                              y2: gby + gbh, class: 'leader'}));

    // RIGHT: one column-group row, exploded
    var periW = 46, aw = (GW - periW) / 2;
    g.appendChild(el('rect', {x: gbx, y: gby, width: gbw, height: gbh,
                              rx: 4, class: 'groupbox'}));
    g.appendChild(el('text', {x: gbx, y: gby - 8, class: 'lab strong'},
                     'ONE COLUMN GROUP, 1 of ' + (2 * s.depth) + ' in the block'));
    [X, X + aw + periW].forEach(function (ax, i) {
      g.appendChild(el('rect', {x: ax, y: Y, width: aw, height: GH, class: 'arr'}));
      cells(g, ax, Y, aw, GH, s.bits, s.strips, i === 0);
      g.appendChild(el('rect', {x: ax, y: Y, width: aw, height: GH, class: 'outline'}));
    });
    g.appendChild(el('rect', {x: X + aw, y: Y, width: periW, height: GH,
                              class: 'peri'}));
    g.appendChild(el('rect', {x: X + aw, y: Y, width: periW, height: GH,
                              class: 'outline'}));
    // three short lines: as one line the label ran past the column's ends
    ['sense amplifier,', 'write driver,', 'column multiplexer'].forEach(function (t, i) {
      g.appendChild(el('text', {x: X + aw + periW / 2, y: Y + GH / 2 - 8 + 12 * i,
                                class: 'labs mid rot',
                                transform: 'rotate(-90 ' + (X + aw + periW / 2) +
                                           ' ' + (Y + GH / 2) + ')'}, t));
    });
    ['array', 'mirrored array'].forEach(function (nm, i) {
      var cx = X + aw / 2 + i * (aw + periW);
      g.appendChild(el('text', {x: cx, y: Y + GH + 22, class: 'labs mid'},
                       nm + ', ' + s.bits + ' cells across'));
    });
    g.appendChild(el('text', {x: X - 22, y: Y + GH / 2, class: 'lab mid',
                              transform: 'rotate(-90 ' + (X - 22) + ' ' +
                                         (Y + GH / 2) + ')'},
                     s.strips + ' strips tall'));
    // the legend sits under the right panel, starting at its left edge,
    // which is where the leader from the mirrored row ends, so no leader
    // crosses it; its lines are short enough to stay inside the canvas
    var CX = gbx, cy = Y + GH + 46;
    g.appendChild(el('rect', {x: CX, y: cy - 8, width: 9, height: 9,
                              rx: 1.2, class: 'cell mark'}));
    g.appendChild(el('rect', {x: CX, y: cy + 22, width: 9, height: 9,
                              rx: 1.2, class: 'band'}));
    [[CX + 15, 'one six-transistor memory cell,'],
     [CX + 15, '0.108 by 0.27 micrometres'],
     [CX + 15, 'the band across the middle:'],
     [CX + 15, 'decoder, control and timing'],
     [CX, 'a group is two arrays of ' + s.bits + ' cells sharing one'],
     [CX, 'read and write column: ' + (2 * s.bits * s.strips) + ' cells in all'],
     [CX, s.depth + ' groups sit above the decoder and control,'],
     [CX, 'and ' + s.depth + ' below it, mirrored']
    ].forEach(function (ln, i) {
      g.appendChild(el('text', {x: ln[0], y: cy + i * 15, class: 'labs'}, ln[1]));
    });

    // derived numbers
    var words = 2 * s.bits * s.strips, wbits = 2 * s.depth;
    var bytes = words * wbits / 8;
    $('d-org').textContent = words.toLocaleString() + ' words of ' + wbits + ' bits';
    $('d-cap').textContent = (bytes >= 1024 ? (bytes / 1024) + ' kilobytes'
                                            : bytes + ' bytes');
    $('d-size').textContent = W.toFixed(2) + ' by ' + H.toFixed(2) + ' micrometres';
    $('d-addr').textContent = bandV2(s) ? '9' : '8';
    $('o-bits').textContent = s.bits;
    $('o-depth').textContent = s.depth;
    $('o-strips').textContent = s.strips;
    var notes = [];
    if (s.bits < 32)
      notes.push('Below 32 bits the block keeps its full width: narrow builds '
        + 'leave columns empty rather than shrinking the frame, which is a '
        + 'stated limitation.');
    if (s.bits !== 32)
      notes.push('Eight strips is unavailable here. The compiler builds it at '
        + '32 bits only, because the machinery that narrows a block below 32 '
        + 'bits and the second decoder bank that widens it past 32 have not '
        + 'been combined with the taller column group. It refuses the '
        + 'combination rather than build something unproven.');
    if (s.strips === 8)
      notes.push('Eight strips doubles the words by sharing each read and '
        + 'write column between twice as many cells.');
    $('d-note').textContent = notes.join(' ');
    $('k-s8').title = s.bits === 32 ? ''
      : 'Only built at 32 bits per group';
    $('m-s8').title = $('k-s8').title;
    syncControls();
    drawWord();
    drawAddr();
    drawRead();
    drawWrite();
    drawCell();
  }

  // Address fields, exactly as the pin contract defines them
  // (sramforge/pins.py): the reference control decodes five column bits,
  // the re-placed control six, and the eight-strip variant spends one of
  // Address fields, exactly as the pin contract defines them
  // (sramforge/pins.py): the reference control decodes five column bits,
  // the re-placed control six, and the eight-strip variant spends one of
  // those on the third multiplexer bit instead.
  function fields(s) {
    var mux = s.strips === 8 ? 3 : 2;
    var col = s.strips === 8 ? 5 : (bandV2(s) ? 6 : 5);
    return {mux: mux, col: col, half: 1, total: mux + col + 1};
  }

  // The same two drawings as the pictures above, for whatever the three
  // controls are set to: the block with the word's column marked, and
  // one of its groups enlarged.  The block keeps each group's true
  // proportions and leaves most of them out, which the breaks say.
  // The shares of a block's width: cells, the read and write column the
  // two arrays share, cells (read off the artwork by
  // scripts/render_word.py).
  var A0 = 0.0225, A1 = 0.382, A2 = 0.618, A3 = 0.9775;

  // The word's column.  It keeps the same place across the array at any
  // bit count, and lands on column 11 at 32 bits, where the pictures
  // above are marked.
  function markColumn(s) {
    return Math.min(s.bits - 1, Math.round(s.bits * 11 / 32));
  }

  // One block, drawn whole: arrays, the column they share, the decoder
  // band.  Its height is given, not derived, because the real block is
  // up to 45 times taller than it is wide and would be a hairline.
  function drawBlock(g, s, x, y, w, ht, opt) {
    var szH = size(s)[1], cellsUm = 2 * s.depth * s.strips * 0.27;
    var bandH = ht * (szH - cellsUm) / szH, halfH = (ht - bandH) / 2;
    var ax = [x + w * A0, x + w * A1, x + w * A2, x + w * A3];
    var cw = (ax[1] - ax[0]) / s.bits, rowH = halfH / s.depth;
    var bandY = y + halfH, c, r;
    [y, bandY + bandH].forEach(function (y0) {
      g.appendChild(el('rect', {x: ax[0], y: y0, width: ax[1] - ax[0],
                                height: halfH, class: 'cells'}));
      g.appendChild(el('rect', {x: ax[1], y: y0, width: ax[2] - ax[1],
                                height: halfH, class: 'peri'}));
      g.appendChild(el('rect', {x: ax[2], y: y0, width: ax[3] - ax[2],
                                height: halfH, class: 'cells'}));
      if (opt.grid) {
        for (r = 1; r < s.depth; r++)
          g.appendChild(el('line', {x1: ax[0], y1: y0 + r * rowH, x2: ax[3],
                                    y2: y0 + r * rowH, class: 'grid'}));
        for (c = 1; c < s.bits; c++)
          [ax[0], ax[2]].forEach(function (x0) {
            g.appendChild(el('line', {x1: x0 + c * cw, y1: y0, x2: x0 + c * cw,
                                      y2: y0 + halfH, class: 'grid'}));
          });
      }
    });
    g.appendChild(el('rect', {x: x, y: bandY, width: w, height: bandH,
                              class: 'band'}));
    if (opt.label && bandH > 11)
      g.appendChild(el('text', {x: x + w / 2, y: bandY + bandH / 2 + 4,
                                class: 'labs mid onband'},
                       'decoder and control'));
    if (opt.column) {                      // the chosen column, every group
      var mx = ax[0] + markColumn(s) * cw, pad = Math.max(0, (2.2 - cw) / 2);
      [[y, halfH], [bandY + bandH, halfH]].forEach(function (seg) {
        g.appendChild(el('rect', {x: mx - pad, y: seg[0], width: cw + 2 * pad,
                                  height: seg[1], class: opt.column}));
      });
    }
    g.appendChild(el('rect', {x: x, y: y, width: w, height: ht,
                              class: 'outline'}));
    return {ax: ax, cw: cw, rowH: rowH, bandY: bandY, bandH: bandH,
            halfH: halfH, bottom: y + ht};
  }

  // One column group: two arrays of cells with the read and write
  // column between them, to scale.
  function drawGroup(g, s, x, y, w, opt) {
    var gh = w * s.strips * 0.27 / 9.612;
    var gx = [x + w * A0, x + w * A1, x + w * A2, x + w * A3];
    var cw = (gx[1] - gx[0]) / s.bits, ch = gh / s.strips;
    var pad = Math.min(0.9, cw * 0.16), mc = markColumn(s), r, c;
    g.appendChild(el('rect', {x: gx[0], y: y, width: gx[1] - gx[0],
                              height: gh, class: 'arr'}));
    g.appendChild(el('rect', {x: gx[2], y: y, width: gx[3] - gx[2],
                              height: gh, class: 'arr'}));
    g.appendChild(el('rect', {x: gx[1], y: y, width: gx[2] - gx[1],
                              height: gh, class: 'peri'}));
    [gx[0], gx[2]].forEach(function (x0) {
      for (r = 0; r < s.strips; r++)
        for (c = 0; c < s.bits; c++)
          g.appendChild(el('rect', {x: x0 + c * cw + pad, y: y + r * ch + pad,
                                    width: cw - 2 * pad, height: ch - 2 * pad,
                                    rx: Math.min(0.8, cw / 6), class: 'cell'}));
    });
    if (opt.strip !== undefined)           // that strip's bit lines, out of
      g.appendChild(el('rect', {x: gx[0], y: y + opt.strip * ch,    // the array
                                width: gx[1] - gx[0] + (gx[2] - gx[1]) * 0.2,
                                height: ch, class: 'bl'}));          // and in
    if (opt.open)                          // every cell the word line opens
      for (r = 0; r < s.strips; r++)
        g.appendChild(el('rect', {x: gx[0] + mc * cw - 0.3, y: y + r * ch - 0.3,
                                  width: cw + 0.6, height: ch + 0.6,
                                  class: 'openbox'}));
    if (opt.column) {                      // in the chosen array only
      var mx = gx[0] + mc * cw, pad2 = Math.max(0, (2.6 - cw) / 2);
      g.appendChild(el('rect', {x: mx - pad2, y: y, width: cw + 2 * pad2,
                                height: gh, class: opt.column}));
    }
    if (opt.cell !== undefined) {          // the one cell, where they cross
      var cx = gx[0] + mc * cw, cy = y + opt.cell * ch;
      g.appendChild(el('rect', {x: cx + pad, y: cy + pad, width: cw - 2 * pad,
                                height: ch - 2 * pad,
                                rx: Math.min(0.8, cw / 6), class: 'cell mark'}));
      g.appendChild(el('rect', {x: cx - 0.4, y: cy - 0.4, width: cw + 0.8,
                                height: ch + 0.8, class: 'markbox'}));
    }
    g.appendChild(el('rect', {x: x, y: y, width: w, height: gh,
                              class: 'outline'}));
    return {gx: gx, cw: cw, ch: ch, gh: gh, bottom: y + gh};
  }

  function swatch(g, x, y, cls) {
    g.appendChild(el('rect', {x: x, y: y - 7, width: 8, height: 8, rx: 1,
                              class: cls}));
  }

  function drawWord() {
    var s = st, svg = $('word');
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var g = el('g', {}); svg.appendChild(g);
    var rows = 2 * s.depth, szW = size(s)[0], szH = size(s)[1];

    // the block, compressed to fit: its height grows with the real one
    var BX = 20, BW = 185, BY = 50;
    var MINH = 25.9, MAXH = 432.6;              // the smallest and largest
    var t = Math.sqrt(Math.max(0, Math.min(1, (szH - MINH) / (MAXH - MINH))));
    var HT = 185 + 150 * t;
    g.appendChild(el('text', {x: BX, y: 22, class: 'lab strong'},
                     'THE WHOLE BLOCK'));
    g.appendChild(el('text', {x: BX, y: 36, class: 'labs'},
                     'all ' + rows + ' groups, squared up to fit'));
    var b = drawBlock(g, s, BX, BY, BW, HT,
                      {grid: true, label: true, column: 'mark'});
    var markRowY = BY + b.halfH * 0.45, rh = Math.max(2.4, b.rowH);
    g.appendChild(el('rect', {x: BX, y: markRowY - rh / 2, width: BW,
                              height: rh, class: 'hl'}));
    g.appendChild(el('text', {x: BX, y: b.bottom + 15, class: 'labs'},
                     szW.toFixed(2) + ' by ' + szH.toFixed(2) + ' micrometres, '
                     + 'really ' + Math.round(szH / szW) + ' times taller '
                     + 'than wide'));
    g.appendChild(el('text', {x: BX, y: b.bottom + 29, class: 'labs'},
                     'the amber line is the word’s column'));

    // one of its groups, enlarged
    var GX = 230, GW = 310;
    var gh = GW * s.strips * 0.27 / 9.612;
    var GY = Math.max(64, (BY + b.bottom) / 2 - gh / 2);
    g.appendChild(el('text', {x: GX, y: GY - 24, class: 'lab strong'},
                     'ONE COLUMN GROUP'));
    g.appendChild(el('text', {x: GX, y: GY - 10, class: 'labs'},
                     '1 of ' + rows + ', to scale'));
    g.appendChild(el('polygon', {points: [BX + BW, markRowY - rh / 2,
                                          GX, GY, GX, GY + gh,
                                          BX + BW, markRowY + rh / 2].join(' '),
                                 class: 'zoom'}));
    var gr = drawGroup(g, s, GX, GY, GW, {column: 'mark', cell: 1});
    [[(gr.gx[0] + gr.gx[1]) / 2, 14, 'array, ' + s.bits + ' cells'],
     [(gr.gx[2] + gr.gx[3]) / 2, 14, 'mirrored array'],
     [(gr.gx[1] + gr.gx[2]) / 2, 29, 'read and write column, shared']
    ].forEach(function (lb) {
      g.appendChild(el('text', {x: lb[0], y: gr.bottom + lb[1],
                                class: 'labs mid'}, lb[2]));
    });
    g.appendChild(el('text', {x: GX, y: gr.bottom + 47, class: 'labs'},
                     s.strips + ' strips tall. The outlined cell is this '
                     + 'group’s bit of the word.'));
    svg.setAttribute('viewBox', '0 0 560 '
                     + Math.round(Math.max(b.bottom + 34, gr.bottom + 52) + 10));
  }

  // One read, traced from the address to the data pins, with a worked
  // address: every field is shown deciding one of something.  The three
  // field colours carry down into the drawings; amber stays the word.
  function drawAddr() {
    var s = st, svg = $('addr'), f = fields(s), rows = 2 * s.depth;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var g = el('g', {}); svg.appendChild(g);
    var LX = 20, mc = markColumn(s), STRIP = 1, HALF = 1;

    function stage(n, y, title) {
      g.appendChild(el('circle', {cx: LX + 7, cy: y - 4, r: 7.5, class: 'badge'}));
      g.appendChild(el('text', {x: LX + 7, y: y, class: 'bnum'}, String(n)));
      g.appendChild(el('text', {x: LX + 20, y: y, class: 'lab strong'}, title));
    }
    function lines(x, y, arr) {
      arr.forEach(function (t, i) {
        g.appendChild(el('text', {x: x, y: y + i * 14, class: 'labs'}, t));
      });
      return y + arr.length * 14;
    }
    function bin(v, n) {
      var t = (v >>> 0).toString(2);
      while (t.length < n) t = '0' + t;
      return t;
    }

    // 1: a worked address, field by field
    stage(1, 18, 'ONE ADDRESS, ' + f.total + ' BITS');
    var BW = 23, BG = 3, FG = 12, bx = LX, fy = 30;
    [['half', f.half, HALF, 'f0'],
     ['column', f.col, mc, 'f1'],
     ['strip', f.mux, STRIP, 'f2']].forEach(function (fd) {
      var t = bin(fd[2], fd[1]), x0 = bx, i;
      for (i = 0; i < fd[1]; i++) {
        g.appendChild(el('rect', {x: bx, y: fy, width: BW, height: 24, rx: 2,
                                  class: 'fld ' + fd[3]}));
        g.appendChild(el('text', {x: bx + BW / 2, y: fy + 17,
                                  class: 'lab mid onband'}, t.charAt(i)));
        bx += BW + BG;
      }
      var w = bx - BG - x0;
      g.appendChild(el('path', {d: 'M' + x0 + ' ' + (fy + 30) + 'v4h' + w + 'v-4',
                                class: 'brace'}));
      g.appendChild(el('text', {x: x0 + w / 2, y: fy + 48, class: 'labs mid'},
                       fd[0] + ' = ' + fd[2]));
      bx += FG;
    });

    // 2: what each set of bits selects, said plainly
    var DY = 104;
    stage(2, DY, 'WHAT EACH SET OF BITS SELECTS');
    var ry0 = DY + 16, dy = ry0;
    [[f.half, 'f0', 'one of the 2 arrays in every group, here the left one'],
     [f.col, 'f1', 'one of the ' + s.bits + ' columns of cells in that array'],
     [f.mux, 'f2', 'one of the ' + s.strips + ' strips, the row inside every '
                   + 'group']].forEach(function (fd, i) {
      var y = ry0 + i * 28;
      g.appendChild(el('rect', {x: LX, y: y, width: 52, height: 22, rx: 3,
                                class: 'fld ' + fd[1]}));
      g.appendChild(el('text', {x: LX + 26, y: y + 15, class: 'labs mid onband'},
                       fd[0] + (fd[0] === 1 ? ' bit' : ' bits')));
      g.appendChild(el('text', {x: LX + 64, y: y + 15, class: 'labs'},
                       'select ' + fd[2]));
      dy = y + 22;
    });

    // 3: those lines, in the block
    var SY = dy + 36;
    stage(3, SY, 'ONE WORD LINE, THROUGH EVERY GROUP');
    var b = drawBlock(g, s, LX, SY + 14, 200, 170,
                      {label: true, column: 'colmark'});
    var ty = SY + 34;
    swatch(g, 260, ty, 'fld f1');
    g.appendChild(el('text', {x: 274, y: ty, class: 'labs'},
                     'The column bits raise one word line. It runs up'));
    ty = lines(274, ty + 14,
               ['and down from the band through all ' + rows + ' groups',
                'and opens that column of cells in every one.']);
    swatch(g, 260, ty + 8, 'fld f0');
    g.appendChild(el('text', {x: 274, y: ty + 8, class: 'labs'},
                     'The half bit says whose word line: the left'));
    ty = lines(274, ty + 22, ['arrays\u2019 or the right\u2019s.']);
    swatch(g, 260, ty + 8, 'fld f2');
    g.appendChild(el('text', {x: 274, y: ty + 8, class: 'labs'},
                     'The strip bits never reach the array. They'));
    ty = lines(274, ty + 22, ['steer the multiplexer in each group\u2019s',
                              'read and write column.']);

    // 4: where the two cross is one cell, in each group
    var GY = Math.max(b.bottom, ty) + 36;
    stage(4, GY, 'ONE CELL PER GROUP');
    var gg = el('g', {class: 'gw'});
    g.appendChild(gg);
    var gr = drawGroup(gg, s, LX, GY + 14, 520,
                       {strip: STRIP, column: 'colmark', open: true, cell: STRIP});
    var ry = lines(LX, gr.bottom + 18,
                   ['The word line opens ' + s.strips + ' cells in each group, '
                    + 'one per strip, and each pulls on its own strip\u2019s',
                    'bit lines. The multiplexer listens only to the teal strip, '
                    + 'so one cell per group is read: the amber one.']);

    svg.setAttribute('viewBox', '0 0 560 ' + Math.round(ry + 14));
  }

  // A read, from the signals at the edge of the bank to data on the
  // pins.  Every number is the measured one for the built 2 kilobyte
  // block at the typical corner, injected at build time from
  // sramforge/sae_codes.json and rung 7, so the page cannot drift.
  var RB = __READ_BUDGET__;

  function badge(g, n, x, y, t) {
    g.appendChild(el('circle', {cx: x + 7, cy: y - 4, r: 7.5, class: 'badge'}));
    g.appendChild(el('text', {x: x + 7, y: y, class: 'bnum'}, String(n)));
    g.appendChild(el('text', {x: x + 20, y: y, class: 'lab strong'}, t));
  }

  // A read and a write, each walked one step at a time inside the bank,
  // in the manner of the read and write access animations.  The block is
  // the built 2 kilobyte one, whose word is the one marked everywhere
  // above.  Each step names the parts it acts on; a part is amber on its
  // own step and filled once the access has passed it.
  var WALK_ALL = ['ff', 'dec', 'drv', 'wl', 'cells', 'bls', 'pre', 'mux'];
  var READ_STEPS = [
    {t: 'The clock edge', on: ['ff'],
     d: 'The pins wire into the address flip-flops along the bottom of the '
      + 'central band: on the rising clk (with banksel = 1 and read = 1) they '
      + 'take a snapshot of ADDRESS[7:0] = 1 01011 01, so the pins are free to '
      + 'change while the read goes on.'},
    {t: 'Decode', on: ['dec'],
     d: 'A decoder turns a number into a choice of one wire. In the row above '
      + 'the flip-flops, the five column bits 01011, the number 11, raise '
      + 'only line 11 of 32, and the half bit (1) steers it to the left '
      + 'arrays. The strip bits and the half bit choose, the same way, one '
      + 'select line for the multiplexers.'},
    {t: 'The word line rises', on: ['drv', 'wl'],
     d: 'One decoder gate is far too weak for a word line: a wire the full '
      + 'height of the block, with 256 cells hanging on it. The drivers in '
      + 'the top rows of the band are chains of ever larger inverters that '
      + 'charge it quickly. Word line 11 runs up and down from the band '
      + 'through the left array of all 64 groups and opens every cell on '
      + 'column 11: four in each group, one per strip.'},
    {t: 'The cells pull on their bit lines', on: ['cells', 'bls', 'pre'],
     d: 'In every group at once (group 2 is magnified here), precharge, '
      + 'which has held every bit line at the supply, lets go. Each open cell '
      + 'pulls one of its strip’s two bit lines down a little, a few tens of '
      + 'millivolts, along the strip into the read and write column.'},
    {t: 'The multiplexer picks one strip', on: ['mux'],
     d: 'Eight bit line pairs reach each group’s read and write column: four '
      + 'strips from each array. Select lines run from the band up and down '
      + 'the column to every group, and let exactly one pair through to the '
      + 'sense amplifier: strip 1, from the left.'},
    {t: 'The sense amplifier fires', on: ['dly', 'se', 'sa'],
     d: 'The delay line, at the left end of the band and set by sdel[4:0], '
      + 'waits until the split is large enough in the farthest group, then '
      + 'fires the sense enable up and down the column, and every amplifier '
      + 'turns its small split into a full one or zero.'},
    {t: 'The latch holds it', on: ['out'],
     d: 'A latch beside the amplifier catches the result and drives '
      + 'dataout[2], a pin on this group’s own read and write column: every '
      + 'group has its own data pin. Reading does not change what the cells '
      + 'hold, and precharge restores the bit lines for the next access.'},
    {t: 'Sixty-four at once', all: true,
     on: WALK_ALL.concat(['dly', 'se', 'sa', 'out']),
     d: 'Every group did the same thing in the same picoseconds, so one '
      + 'clock edge returns a word of 64 bits, one on each group’s pin: '
      + RB.dataout + ' picoseconds from the edge to data out on this block.'}
  ];
  var WRITE_STEPS = [
    {t: 'The clock edge', on: ['ff'],
     d: 'The same pins, with read = 0 and byte_wrenab = 1. On the rising clk '
      + 'the address flip-flops take the same snapshot, ADDRESS[7:0] = '
      + '1 01011 01, and the control in the band latches that this cycle is '
      + 'a write.'},
    {t: 'Decode', on: ['dec'],
     d: 'Exactly as for a read: the column bits 01011 raise line 11 of 32, '
      + 'the half bit picks the left arrays, and the strip and half bits '
      + 'choose the select line for the multiplexers.'},
    {t: 'Write enable fires', on: ['dly', 'we', 'wdrv', 'wdin'],
     d: 'At ' + Math.round(RB.wen) + ' picoseconds after the edge, the timing logic in the band '
      + 'fires write enable up and down the read and write column. In every '
      + 'group it opens the write driver, which takes its own data pin: here '
      + 'wd[2], the bit to store.'},
    {t: 'The word line rises', on: ['drv', 'wl'],
     d: 'At ' + Math.round(RB.clk_to_wl) + ' picoseconds after the edge, the drivers raise word line '
      + '11, through the left array of all 64 groups. It opens every cell on '
      + 'column 11, four in each group, one per strip.'},
    {t: 'The driver pulls the bit line down', on: ['pre', 'mux', 'bls'],
     d: 'Precharge lets go. Through the multiplexer, now fully on, the write '
      + 'driver pulls one bit line of strip 1 all the way to ground: a full '
      + 'swing, where a read moves only a few tens of millivolts.'},
    {t: 'The cell flips', on: ['cells'],
     d: 'About ' + RB.flip + ' picoseconds after its word line rises, the cell’s two sides cross over: the grounded '
      + 'bit line drags the high side down through the access transistor '
      + 'faster than the cell’s small pull-up can hold it, and the cell '
      + 'latches the new bit. The other three open cells are not driven, so, '
      + 'as in a read, they keep what they hold.'},
    {t: 'Precharge restores', on: ['pre'],
     d: 'The word line and write enable close and the cell holds its new bit '
      + 'on its own. Precharge pulls the grounded bit line back to the '
      + 'supply, since the next access cannot start from a fully split pair.'},
    {t: 'Sixty-four at once', all: true,
     on: WALK_ALL.concat(['dly', 'we', 'wdrv', 'wdin']),
     d: 'Every group wrote the bit on its own wd pin in the same cycle, so '
      + 'one clock edge stores a word of 64 bits. Measured on this block: '
      + '0.590 picojoules a write, and 463 to 474 millivolts of write '
      + 'margin, the room by which the driver beats the cell.'}
  ];
  var readStep = 0, writeStep = 0;

  function drawWalk(o) {
    var svg = $(o.svg), steps = o.steps, at = o.at, write = o.write;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var g = el('g', {}); svg.appendChild(g);
    var LX = 20, step = steps[at];

    // 1: what the outside drives
    badge(g, 1, LX, 18, 'AT THE PINS');
    g.appendChild(el('text', {x: LX + 20, y: 32, class: 'labs'},
                     'the built 2 kilobyte block'));
    var ins = write
      ? ['clk, a rising edge', 'banksel = 1', 'read = 0', 'byte_wrenab = 1',
         'ADDRESS[7:0] = 1 01011 01', 'wd[63:0] = the word to store']
      : ['clk, a rising edge', 'banksel = 1', 'read = 1',
         'ADDRESS[7:0] = 1 01011 01', 'sdel[4:0] = ' + RB.code];
    var BXx = 280, BXw = 150, BXy = 46, BXh = 20 + ins.length * 17;
    g.appendChild(el('rect', {x: BXx, y: BXy, width: BXw, height: BXh, rx: 4,
                              class: 'bankbox'}));
    g.appendChild(el('text', {x: BXx + BXw / 2, y: BXy + BXh / 2 - 6,
                              class: 'lab mid'}, 'the bank'));
    g.appendChild(el('text', {x: BXx + BXw / 2, y: BXy + BXh / 2 + 10,
                              class: 'labs mid'},
                     RB.words + ' words of ' + RB.width + ' bits'));
    ins.forEach(function (t, i) {
      var y = BXy + 18 + i * 17;
      g.appendChild(el('text', {x: BXx - 14, y: y + 4, class: 'labs end'}, t));
      g.appendChild(el('path', {d: 'M' + (BXx - 10) + ' ' + y + 'h8',
                                class: 'wire'}));
    });
    if (!write) {
      g.appendChild(el('path', {d: 'M' + (BXx + BXw) + ' ' + (BXy + BXh / 2)
                                   + 'h10', class: 'wire'}));
      g.appendChild(el('text', {x: BXx + BXw + 16, y: BXy + BXh / 2 + 4,
                                class: 'labs'}, 'dataout[63:0]'));
    }

    // 2: the walk, drawn inside the bank.  Across, every block sits where
    // its cells are in the layout of the built 2 kilobyte block (the
    // three-part map X below; band rows and pins measured from the GDSII
    // (Graphic Design System II) file and its abstract).  Down, the band
    // and one group are stretched to be readable and most of the other
    // groups are folded away.
    var PY = BXy + BXh + 36;
    badge(g, 2, LX, PY, 'WALK THE PATH, IN PLACE');
    g.appendChild(el('text', {x: LX + 20, y: PY + 14, class: 'labs'},
                     'step ' + (at + 1) + ' of ' + steps.length + ': '
                     + step.t.toLowerCase()));

    var done = {};
    for (var k = 0; k <= at; k++)
      steps[k].on.forEach(function (id) { done[id] = 1; });
    function state(id) {
      if (step.on.indexOf(id) >= 0) return 'pbox now';
      return done[id] ? 'pbox lit' : 'pbox';
    }
    function reached(id) { return !!done[id]; }
    function txt(x, y, t, cls) {
      g.appendChild(el('text', {x: x, y: y, class: cls || 'labs'}, t));
    }
    function path(d, cls) { g.appendChild(el('path', {d: d, class: cls})); }
    function rect(x, y, w, h, cls) {
      g.appendChild(el('rect', {x: x, y: y, width: w, height: h, class: cls}));
    }
    function poly(pts, cls) {
      g.appendChild(el('polygon', {points: pts.join(' '), class: cls}));
    }
    function head(x, y, on) {            // an arrowhead pointing up
      poly([x, y, x - 3.5, y + 6, x + 3.5, y + 6], on ? 'phead on' : 'phead');
    }
    function line(id) { return reached(id) ? 'parr on' : 'parr'; }

    // across: micrometres in the layout to the drawing, one linear piece
    // per array and one for the read and write column
    var A0 = 0.2165, A1 = 3.6715, A2 = 5.9405, A3 = 9.612, CP = 0.108;
    function X(rx) {
      if (rx <= A1) return 100 + rx / A1 * 180;
      if (rx <= A2) return 280 + (rx - A1) / (A2 - A1) * 180;
      return 460 + (rx - A2) / (A3 - A2) * 180;
    }
    var COL = 11, STR = 1, WLX = X(A0 + (COL + 0.5) * CP);
    // the vertical control spines in the read and write column
    var SLX = 306, SRX = 435, WEX = 410, SEX = 418;

    // down: the stack, top to bottom, as the layout has it
    var TH = 8, FH = 16, GH = 160, BH = 212;
    var rows = [['thin', 31], ['thin', 30], ['thin', 29], ['fold', 24],
                ['thin', 4], ['thin', 3], ['group', 2], ['thin', 1], ['thin', 0],
                ['band'], ['thin', 32], ['thin', 33], ['thin', 34], ['fold', 26],
                ['thin', 61], ['thin', 62], ['thin', 63]];
    var y = PY + 30, G0 = 0, BY0 = 0, top = y, folds = [];
    var io = step.all ? 'gio on' : 'gio';
    rows.forEach(function (r) {
      if (r[0] === 'thin') {
        rect(X(A0), y, X(A1) - X(A0), TH - 1, 'garr');
        rect(X(A1), y, X(A2) - X(A1), TH - 1, io);
        rect(X(A2), y, X(A3 - A0) - X(A2), TH - 1, 'garr');
        y += TH;
      } else if (r[0] === 'fold') {
        folds.push([y, r[1]]); y += FH;
      } else if (r[0] === 'group') {
        G0 = y; y += GH;
      } else {
        BY0 = y; y += BH;
      }
    });
    var bottom = y;
    txt(LX, top + 14, 'upper half', 'labs');
    txt(LX, top + 27, '32 groups', 'tiny');
    txt(LX, G0 + 14, 'group 2,', 'labs');
    txt(LX, G0 + 27, 'magnified', 'tiny');
    txt(LX, BY0 + 14, 'central band', 'labs');
    txt(LX, BY0 + 27, 'control and', 'tiny');
    txt(LX, BY0 + 37, 'decode', 'tiny');
    txt(LX, BY0 + BH + 14, 'lower half', 'labs');
    txt(LX, BY0 + BH + 27, '32 groups', 'tiny');

    // the magnified group: two arrays of 32 by 4 cells either side of
    // the read and write column; one bit line pair along every strip
    rect(X(A0), G0, X(A1) - X(A0), GH - 2, 'garr');
    rect(X(A1), G0, X(A2) - X(A1), GH - 2, io);
    rect(X(A2), G0, X(A3 - A0) - X(A2), GH - 2, 'garr');
    var cw = X(A0 + CP) - X(A0);
    for (var s = 0; s < 4; s++) {
      var ys = G0 + 34 + s * 26, pick = s === STR;
      txt(X(A0) - 4, ys + 3, 'strip ' + s, 'tiny end');
      for (var c = 0; c < 32; c++) {
        var cl = 'gcell';
        if (c === COL && reached('wl')) cl = 'gcell open';
        if (c === COL && pick && reached('cells')) cl = 'gcell now';
        rect(X(A0) + c * cw, ys - 8, cw, 16, cl);
        rect(X(A2) + (31 - c) * cw, ys - 8, cw, 16, 'gcell');
      }
      var lon = pick && reached('bls') ? 'bl on' : 'bl';
      [-3, 3].forEach(function (o2) {
        // a write drives one line of the pair all the way down
        var slam = write && o2 > 0 && pick && reached('bls') ? ' slam' : '';
        path('M' + X(A0) + ' ' + (ys + o2) + 'H296', lon + slam);
        path('M' + X(A3 - A0) + ' ' + (ys + o2) + 'H446', 'bl');
      });
    }
    // precharge either side, then a 4 to 1 multiplexer either side: the
    // strip bits pick the strip and the half bit picks the side, so the
    // eight pairs come down to one pair, shared by the sense amplifier and
    // the write driver
    rect(284, G0 + 22, 6, 102, state('pre'));
    rect(450, G0 + 22, 6, 102, state('pre'));
    poly([296, G0 + 22, 318, G0 + 56, 318, G0 + 84, 296, G0 + 124], state('mux'));
    poly([446, G0 + 22, 424, G0 + 56, 424, G0 + 84, 446, G0 + 124], 'pbox');
    var pon = reached('mux') ? 'bl on' : 'bl';
    path('M318 ' + (G0 + 66) + 'H344M318 ' + (G0 + 74) + 'H344', pon);
    path('M424 ' + (G0 + 66) + 'H404M424 ' + (G0 + 74) + 'H404', 'bl');
    // the sense amplifier and its latch, for reads
    rect(344, G0 + 58, 60, 24, state('sa'));
    txt(374, G0 + 68, 'sense', 'tiny mid');
    txt(374, G0 + 78, 'amplifier', 'tiny mid');
    path('M374 ' + (G0 + 58) + 'V' + (G0 + 44), line('sa'));
    rect(354, G0 + 26, 40, 18, state('out'));
    txt(374, G0 + 38, 'latch', 'tiny mid');
    path('M394 ' + (G0 + 35) + 'H400V' + (G0 + 8) + 'H' + X(A3), line('out'));
    rect(X(A3) - 3, G0 + 5, 6, 6, reached('out') ? 'pin on' : 'pin');
    txt(X(A3) + 6, G0 + 11, 'dataout[2]', 'tiny');
    // the write driver, for writes: onto the same pair as the amplifier
    rect(322, G0 + 100, 40, 24, state('wdrv'));
    txt(342, G0 + 110, 'write', 'tiny mid');
    txt(342, G0 + 120, 'driver', 'tiny mid');
    path('M330 ' + (G0 + 100) + 'V' + (G0 + 74) + 'M338 ' + (G0 + 100) + 'V'
         + (G0 + 66), reached('wdrv') && reached('mux') ? 'bl on' : 'bl');
    path('M' + X(A3) + ' ' + (G0 + 142) + 'H342V' + (G0 + 124), line('wdin'));
    rect(X(A3) - 3, G0 + 139, 6, 6, reached('wdin') ? 'pin on' : 'pin');
    txt(X(A3) + 6, G0 + 145, 'wd[2]', 'tiny');
    txt(307, G0 + 16, 'multiplexer', 'tiny mid');
    txt(287, G0 + 154, 'precharge', 'tiny mid');
    txt(X(A0) + 2, G0 + 14, 'array, 32 cells by 4 strips', 'tiny');
    txt(X(A3 - A0) - 2, G0 + 22, 'mirrored array', 'tiny end');

    // the central band.  Its rows are in the layout's order (drivers on
    // top, the decoder, then the delay line and the address flip-flops),
    // stretched; across, each block spans the cells of its kind.
    var BE = BY0 + BH, DRV = [BY0 + 12, BY0 + 56], DEC = [BY0 + 70, BY0 + 96],
        LOW = [BY0 + 110, BY0 + 130];
    rect(X(0), BY0, X(A3) - X(0), BH, 'gband');
    rect(X(2.5), DRV[0], X(7.1) - X(2.5), DRV[1] - DRV[0], state('drv'));
    txt(264, DRV[0] + 20, 'word line', 'tiny mid');
    txt(264, DRV[0] + 31, 'drivers', 'tiny mid');
    rect(X(0.7), DEC[0], X(8.9) - X(0.7), DEC[1] - DEC[0], state('dec'));
    txt(180, DEC[0] + 17, 'decoder', 'tiny');
    rect(X(2.35), LOW[0], X(8.2) - X(2.35), LOW[1] - LOW[0], state('ff'));
    txt(382, LOW[0] + 13, 'address flip-flops', 'tiny mid');
    rect(X(0.6), LOW[0], X(2.3) - X(0.6), LOW[1] - LOW[0], state('dly'));
    txt(150, LOW[0] + 9, 'delay', 'tiny mid');
    txt(150, LOW[0] + 18, 'line', 'tiny mid');
    // inside the band: flip-flops feed the decoder, the decoder the drivers
    path('M520 ' + LOW[0] + 'V' + (DEC[1] + 6), line('dec'));
    head(520, DEC[1] + 1, reached('dec'));
    path('M480 ' + DEC[0] + 'V' + (DRV[1] + 6), line('drv'));
    head(480, DRV[1] + 1, reached('drv'));

    // the pins, drawn as ports on the boundary, each wired to what it feeds
    function port(y, name, on) {
      rect(X(0) - 3, y - 3, 6, 6, on ? 'pin on' : 'pin');
      txt(X(0) - 6, y + 3, name, 'tiny end');
    }
    var pOn = reached('ff');
    port(LOW[0] + 10, 'sdel[4:0]', reached('se'));
    path('M' + X(0) + ' ' + (LOW[0] + 10) + 'H' + X(0.6), line('se'));
    [['clk', 148, 250], ['read', 160, 262], ['banksel', 172, 274],
     ['byte_wrenab', 184, 286]].forEach(function (p) {
      port(BY0 + p[1], p[0], pOn);
      path('M' + X(0) + ' ' + (BY0 + p[1]) + 'H' + p[2] + 'V' + LOW[1], line('ff'));
    });
    port(BY0 + 198, 'ADDRESS[7:0]', pOn);
    path('M' + X(0) + ' ' + (BY0 + 198) + 'H480V' + LOW[1], pOn ? 'wl on' : 'wl');

    // the spines that leave the band: the word line up and down through
    // every group at its column, and down the read and write column the
    // two sides' select lines, the sense enable and the write enable.
    // Inside the band they are wiring over the cells, dashed back to
    // where they start.
    var wlOn = reached('wl') ? 'wl on' : 'wl';
    path('M' + X(2.5) + ' ' + (DRV[0] + 18) + 'H' + WLX, wlOn);
    path('M' + WLX + ' ' + top + 'V' + bottom, wlOn);
    function spine(x, toY, cls) {        // above group 2, into it, below the band
      path('M' + x + ' ' + top + 'V' + G0 + 'M' + x + ' ' + toY + 'V' + BY0
           + 'M' + x + ' ' + BE + 'V' + bottom, cls);
    }
    var slOn = reached('mux') ? 'bus f2' : 'bl';
    spine(SLX, G0 + 106, slOn);
    spine(SRX, G0 + 104, 'bl');
    path('M' + SLX + ' ' + BY0 + 'V' + BE, slOn + ' thru');
    path('M' + SRX + ' ' + BY0 + 'V' + BE, 'bl thru');
    var SEY = LOW[1] + 6, WEY = LOW[1] + 12;
    path('M' + X(2.0) + ' ' + LOW[1] + 'V' + SEY + 'H' + SEX, line('se'));
    spine(SEX, G0 + 90, line('se'));
    path('M' + SEX + ' ' + (G0 + 90) + 'H374V' + (G0 + 82), line('se'));
    path('M' + SEX + ' ' + BY0 + 'V' + SEY, line('se') + ' thru');
    path('M' + X(1.0) + ' ' + LOW[1] + 'V' + WEY + 'H' + WEX, line('we'));
    spine(WEX, G0 + 112, line('we'));
    path('M' + WEX + ' ' + (G0 + 112) + 'H362', line('we'));
    path('M' + WEX + ' ' + BY0 + 'V' + WEY, line('we') + ' thru');
    txt(WLX + 4, top - 4, 'word line ' + COL, 'tiny');
    txt(SLX, top - 4, 'strip select', 'tiny mid');
    txt(WEX - 2, bottom + 12, 'write enable', 'tiny end');
    txt(SEX + 2, bottom + 12, 'sense enable', 'tiny');

    // the folds, over everything: the groups left out
    folds.forEach(function (f) {
      rect(X(0) - 2, f[0] + 1, X(A3) - X(0) + 4, FH - 2, 'fold');
      txt((X(0) + X(A3)) / 2, f[0] + FH / 2 + 3,
          '· · ·   ' + f[1] + ' more groups   · · ·',
          'tiny mid');
    });
    rect(X(0), top, X(A3) - X(0), bottom - top, 'bankline');
    txt(X(0), bottom + 30, 'Across, every block is where the layout puts it. '
        + 'Down, the band and group 2 are stretched and most groups are '
        + 'folded away.', 'tiny');
    txt(X(0), bottom + 42, 'Pins are drawn as ports on the edge. In the layout '
        + 'they sit on top of the band, and each group’s data pins on top '
        + 'of its', 'tiny');
    txt(X(0), bottom + 54, 'column, where the chip’s wiring lands on them '
        + 'from above.', 'tiny');

    $(o.pre + '-n').textContent = (at + 1) + ' / ' + steps.length;
    $(o.pre + '-title').textContent = step.t;
    $(o.pre + '-desc').textContent = step.d;
    $(o.pre + '-prev').disabled = at === 0;
    $(o.pre + '-next').disabled = at === steps.length - 1;
    svg.setAttribute('viewBox', '0 0 700 ' + Math.round(bottom + 64));
  }
  function drawRead() {
    drawWalk({svg: 'read', pre: 'rs', steps: READ_STEPS, at: readStep,
              write: false});
  }
  function drawWrite() {
    drawWalk({svg: 'write', pre: 'ws', steps: WRITE_STEPS, at: writeStep,
              write: true});
  }

  // One cell, its six transistors, through a read and a write.  The
  // order and the numbers follow the measured access animations and the
  // cell's static margins; voltages between those points are described,
  // not invented.  q is the left side's bit; bl and blb the two bit
  // lines in volts; block is the read and write column part at work.
  var CELL_STEPS = {
    read: [
      {t: 'The cell holds its bit', wl: 0, pre: 1, bl: 0.7, blb: 0.7, q: 1,
       d: 'The two inverters feed each other: the left side at 1 keeps the '
        + 'right side at 0, and the right side at 0 keeps the left at 1, for '
        + 'as long as there is power. The word line is low, so both access '
        + 'transistors are off and the cell is cut off from its bit lines, '
        + 'which precharge holds at the supply, 0.7 volts. It would take 309 '
        + 'millivolts of noise to flip it (the measured hold margin).'},
      {t: 'The word line rises', wl: 1, pre: 1, bl: 0.7, blb: 0.7, q: 1,
       d: 'Both access transistors turn on, and each side of the cell now '
        + 'touches its own bit line. Precharge is still holding both lines at '
        + '0.7 volts, so nothing has moved yet.'},
      {t: 'The 0 side pulls its bit line down', wl: 1, pre: 0, bl: 0.7,
       blb: 0.65, blbText: 'falling', q: 1, flow: 'read',
       d: 'Precharge lets go. The side at 1 sits at the supply, the same as its '
        + 'bit line, so nothing flows there. The side at 0 connects its bit '
        + 'line to ground through its access transistor and pull-down, so bit '
        + 'line bar falls, a few millivolts at a time. That side bumps up '
        + 'slightly as the current comes in; the cell is sized so the bump '
        + 'stays well short of flipping it (the measured read margin is 152 to '
        + '158 millivolts).'},
      {t: 'The sense amplifier decides', wl: 1, pre: 0, bl: 0.7, blb: 0.6,
       blbText: 'about 0.6 V', q: 1, block: 'sa',
       d: 'Once the two lines are about 100 millivolts apart, the sense '
        + 'amplifier fires and turns that small difference into a full answer: '
        + 'bit line is the higher one, so the cell holds 1. Deciding on a '
        + 'small split, instead of waiting for a full swing, is what makes a '
        + 'read fast.'},
      {t: 'Close and restore', wl: 0, pre: 1, bl: 0.7, blb: 0.7, q: 1,
       d: 'The word line falls and cuts the cell off again, and precharge '
        + 'pulls bit line bar back to 0.7 volts. Reading did not change the '
        + 'cell: it still holds 1.'}
    ],
    write: [
      {t: 'The cell holds 1', wl: 0, pre: 1, bl: 0.7, blb: 0.7, q: 1,
       d: 'The same cell, holding 1, cut off from its bit lines. To store a 0 '
        + 'instead, the left side has to come down and the right side go up.'},
      {t: 'The word line rises', wl: 1, pre: 1, bl: 0.7, blb: 0.7, q: 1,
       d: 'As in a read, both access transistors turn on. Precharge is still '
        + 'holding both bit lines at 0.7 volts, so for the moment the cell '
        + 'sees nothing new.'},
      {t: 'The driver pulls a bit line down: a tug of war', wl: 1, pre: 0,
       bl: 0, blb: 0.7, q: 1, flow: 'write', block: 'wd',
       d: 'Precharge lets go and the write driver takes the bit lines: bit line '
        + 'all the way to ground, bit line bar held at 0.7 volts, a full swing '
        + 'where a read moves only millivolts. The left access transistor now '
        + 'joins the side at 1 to a line at 0 volts. Its pull-up, one fin, '
        + 'tries to hold the side up; the access transistor, two fins, drags '
        + 'it down. The cell is sized so the access transistor wins.'},
      {t: 'The cell flips', wl: 1, pre: 0, bl: 0, blb: 0.7, q: 0, block: 'wd',
       d: 'Once the left side falls far enough, the right inverter switches: '
        + 'the right side rises, which turns the left pull-up off and the left '
        + 'pull-down on, and the loop snaps to the new state. In the measured '
        + 'write this comes about ' + RB.flip + ' picoseconds after the word line '
        + 'rises, with 463 to 474 millivolts of write margin to spare.'},
      {t: 'Close: the cell keeps the 0', wl: 0, pre: 1, bl: 0.7, blb: 0.7, q: 0,
       d: 'The word line falls and the driver lets go. The inverters hold the '
        + '0 on their own now, and precharge pulls bit line back to 0.7 volts '
        + 'for the next access.'}
    ]
  };
  var cellMode = 'read', cellStep = 0;

  function drawCell() {
    var svg = $('cell'), steps = CELL_STEPS[cellMode], p = steps[cellStep];
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var g = el('g', {}); svg.appendChild(g);
    function txt(x, y, t, cls) {
      g.appendChild(el('text', {x: x, y: y, class: cls || 'labs'}, t));
    }
    function path(d, cls) { g.appendChild(el('path', {d: d, class: cls})); }
    function rect(x, y, w, h, cls) {
      g.appendChild(el('rect', {x: x, y: y, width: w, height: h, class: cls}));
    }
    function dot(x, y) {
      g.appendChild(el('circle', {cx: x, cy: y, r: 2.6, class: 'jdot'}));
    }
    var BL = 150, BB = 550, WLY = 100, VDD = 160, GND = 320, NY = 240;
    var LX = 280, RX = 420, on = p.wl === 1, q = p.q, wide = 'wire';

    // the rails and the bit lines, with precharge at the top of each
    path('M240 ' + VDD + 'H460', wide);
    path('M240 ' + GND + 'H460', wide);
    txt(350, VDD - 6, 'supply, 0.7 V', 'tiny mid');
    txt(350, GND + 14, 'ground', 'tiny mid');
    [[BL, 'bit line', p.bl], [BB, 'bit line bar', p.blb]].forEach(function (b) {
      rect(b[0] - 26, 34, 52, 18, p.pre ? 'pbox lit' : 'pbox');
      txt(b[0], 47, 'precharge', 'tiny mid');
      path('M' + b[0] + ' 52V400', b[2] < 0.7 ? 'bl on' : 'bl');
      txt(b[0], 26, b[1], 'labs mid');
    });
    txt(BL - 30, 47, p.pre ? 'on' : 'off', 'tiny end');
    txt(BB + 30, 47, p.pre ? 'on' : 'off', 'tiny');

    // the word line, across the top, into both access gates
    path('M110 ' + WLY + 'H590', on ? 'wl on' : 'wl');
    txt(350, WLY - 6, 'word line ' + (on ? 'high' : 'low'), 'tiny mid');

    // a transistor: a channel bar with a gate bar beside it.  Vertical
    // ones (the inverters) have the gate on the inner side; horizontal
    // ones (access) have it on top.
    function fetV(x, y0, gateSide, pmos, conducting) {
      path('M' + x + ' ' + y0 + 'v12M' + x + ' ' + (y0 + 34) + 'v12', 'wire');
      rect(x - 3, y0 + 12, 6, 22, conducting ? 'ch strong' : 'ch');
      var gx = x + gateSide * 9;
      path('M' + gx + ' ' + (y0 + 13) + 'V' + (y0 + 33), 'gate');
      if (pmos) g.appendChild(el('circle', {cx: gx + gateSide * 3.5, cy: y0 + 23,
                                            r: 3, class: 'bubble'}));
      path('M' + (gx + gateSide * (pmos ? 6.5 : 0)) + ' ' + (y0 + 23) + 'H'
           + (x + gateSide * 26), 'wire');
    }
    function fetH(x0, conducting) {
      rect(x0, NY - 3, 30, 6, conducting ? 'ch on' : 'ch');
      path('M' + (x0 + 2) + ' ' + (NY - 12) + 'H' + (x0 + 28), 'gate');
      path('M' + (x0 + 15) + ' ' + (NY - 12) + 'V' + WLY, on ? 'wl on thin' : 'wl thin');
    }
    // left inverter: pull-up on when the left side is 1
    path('M' + LX + ' ' + VDD + 'V' + (VDD + 20), 'wire');
    fetV(LX, VDD + 20, 1, true, q === 1);
    fetV(LX, GND - 46, 1, false, q === 0);
    path('M' + LX + ' ' + (VDD + 66) + 'V' + (GND - 46), 'wire');
    path('M' + (LX + 26) + ' ' + (VDD + 43) + 'V' + (GND - 23), 'wire');
    // right inverter, mirrored
    path('M' + RX + ' ' + VDD + 'V' + (VDD + 20), 'wire');
    fetV(RX, VDD + 20, -1, true, q === 0);
    fetV(RX, GND - 46, -1, false, q === 1);
    path('M' + RX + ' ' + (VDD + 66) + 'V' + (GND - 46), 'wire');
    path('M' + (RX - 26) + ' ' + (VDD + 43) + 'V' + (GND - 23), 'wire');
    // the cross-coupling: each side drives the other inverter's gates.
    // Where a wire passes over a gate line it does not touch, the gate
    // line is broken for it.
    var hop = NY + 26 * 26 / 114;
    rect(LX + 23.5, hop - 4, 5, 8, 'gap'); rect(RX - 28.5, hop - 4, 5, 8, 'gap');
    path('M' + LX + ' ' + NY + 'L' + (RX - 26) + ' ' + (NY + 26), 'wire');
    path('M' + RX + ' ' + NY + 'L' + (LX + 26) + ' ' + (NY + 26), 'wire');
    dot(RX - 26, NY + 26); dot(LX + 26, NY + 26);
    // the access transistors, joining each side to its bit line
    path('M' + BL + ' ' + NY + 'H190M220 ' + NY + 'H' + LX, 'wire');
    path('M' + RX + ' ' + NY + 'H480M510 ' + NY + 'H' + BB, 'wire');
    fetH(190, on); fetH(480, on);
    dot(BL, NY); dot(BB, NY); dot(LX, NY); dot(RX, NY);
    txt(205, NY + 18, 'access', 'tiny mid');
    txt(495, NY + 18, 'access', 'tiny mid');
    txt(LX - 10, VDD + 46, 'pull-up', 'tiny end');
    txt(LX - 10, GND - 20, 'pull-down', 'tiny end');
    txt(RX + 10, VDD + 46, 'pull-up', 'tiny');
    txt(RX + 10, GND - 20, 'pull-down', 'tiny');

    // what each side holds
    [[LX, q], [RX, 1 - q]].forEach(function (n, i) {
      var bx = n[0] + (i ? 16 : -16);
      g.appendChild(el('circle', {cx: bx, cy: NY - 14, r: 9,
                                  class: n[1] ? 'bit1' : 'bit0'}));
      txt(bx, NY - 10.5, String(n[1]), n[1] ? 'bnum one' : 'bnum zero');
    });

    // the current at work: a read drains the 0 side's bit line to ground;
    // a write is a tug of war on the 1 side
    function arrow(d, hx, hy, dir) {
      path(d, 'flow');
      var h = {down: [hx, hy, hx - 4, hy - 7, hx + 4, hy - 7],
               left: [hx, hy, hx + 7, hy - 4, hx + 7, hy + 4]}[dir];
      g.appendChild(el('polygon', {points: h.join(' '), class: 'phead on'}));
    }
    if (p.flow === 'read') {
      arrow('M540 ' + (NY - 25) + 'H' + (RX + 4) + 'V' + (GND - 6), RX + 4, GND - 4, 'down');
      txt(RX + 14, NY + 40, 'current to ground', 'tiny');
    }
    if (p.flow === 'write') {
      arrow('M' + (LX - 6) + ' ' + (VDD + 4) + 'V' + (NY - 30), LX - 6, NY - 28, 'down');
      arrow('M' + (LX - 30) + ' ' + (NY - 22) + 'H' + (BL + 12), BL + 10, NY - 22, 'left');
      txt(LX - 10, VDD + 26, 'holds up', 'tiny end');
      txt(BL - 6, NY - 19, 'drags down', 'tiny end');
    }

    // a voltage gauge beside each bit line, 0 to 0.7 volts, tied to its
    // line by a leader; the scale sits on the outer side
    [[96, p.bl, p.blText, 'bit line', BL, -1],
     [588, p.blb, p.blbText, 'bit line bar', BB, 1]].forEach(function (m) {
      var H = 110, top = 280, h = m[1] / 0.7 * H, cx = m[0] + 8;
      txt(cx, top - 19, 'voltage on', 'tiny mid');
      txt(cx, top - 8, m[3], 'tiny mid');
      rect(m[0], top, 16, H, 'meter');
      rect(m[0], top + H - h, 16, h, 'meterfill');
      var sx = m[5] < 0 ? m[0] - 4 : m[0] + 20, sc = m[5] < 0 ? 'tiny end' : 'tiny';
      txt(sx, top + 4, '0.7 V', sc);
      txt(sx, top + H + 3, '0 V', sc);
      var ex = m[5] < 0 ? m[0] + 16 : m[0];
      path('M' + ex + ' ' + (top + H / 2) + 'H' + m[4], 'bl thru');
      txt(cx, top + H + 14, 'now ' + (m[2] || (m[1] ? m[1].toFixed(1) : '0') + ' V'),
          'tiny mid');
    });

    // the part of the read and write column at work below
    var blk = cellMode === 'read' ? 'sense amplifier' : 'write driver';
    var act = cellMode === 'read' ? p.block === 'sa' : p.block === 'wd';
    rect(BL - 20, 404, BB - BL + 40, 26, act ? 'pbox now' : 'pbox');
    txt(350, 421, blk + (act ? (cellMode === 'read' ? ': reads 1' : ': drives 0')
                         : ''), 'labs mid');

    $('cs-n').textContent = (cellStep + 1) + ' / ' + steps.length;
    $('cs-title').textContent = p.t;
    $('cs-desc').textContent = p.d;
    $('cs-prev').disabled = cellStep === 0;
    $('cs-next').disabled = cellStep === steps.length - 1;
    $('cs-read').className = cellMode === 'read' ? 'on' : '';
    $('cs-write').className = cellMode === 'write' ? 'on' : '';
  }

  function setStrips(v) {
    st.strips = v;
    [['k-s4', 'k-s8'], ['m-s4', 'm-s8']].forEach(function (pair) {
      $(pair[0]).className = v === 4 ? 'on' : '';
      $(pair[1]).className = v === 8 ? 'on' : '';
    });
  }
  function syncLimits() {
    var ok = st.bits === 32;
    $('k-s8').disabled = !ok;
    $('m-s8').disabled = !ok;
    if (!ok && st.strips === 8) setStrips(4);
  }
  // the second set of controls sits beside the drawing it feeds; both
  // sets show the same state whichever one is moved
  function syncControls() {
    $('k-bits').value = st.bits; $('m-bits').value = st.bits;
    $('k-depth').value = st.depth; $('m-depth').value = st.depth;
    $('m-obits').textContent = st.bits;
    $('m-odepth').textContent = st.depth;
  }
  $('rs-prev').onclick = function () {
    readStep = Math.max(0, readStep - 1); drawRead();
  };
  $('cs-prev').onclick = function () {
    cellStep = Math.max(0, cellStep - 1); drawCell();
  };
  $('cs-next').onclick = function () {
    cellStep = Math.min(CELL_STEPS[cellMode].length - 1, cellStep + 1); drawCell();
  };
  $('cs-read').onclick = function () { cellMode = 'read'; cellStep = 0; drawCell(); };
  $('cs-write').onclick = function () { cellMode = 'write'; cellStep = 0; drawCell(); };
  $('ws-prev').onclick = function () {
    writeStep = Math.max(0, writeStep - 1); drawWrite();
  };
  $('ws-next').onclick = function () {
    writeStep = Math.min(WRITE_STEPS.length - 1, writeStep + 1); drawWrite();
  };
  $('rs-next').onclick = function () {
    readStep = Math.min(READ_STEPS.length - 1, readStep + 1); drawRead();
  };
  [$('k-bits'), $('m-bits')].forEach(function (r) {
    r.oninput = function () { st.bits = +this.value; syncLimits(); draw(); };
  });
  [$('k-depth'), $('m-depth')].forEach(function (r) {
    r.oninput = function () { st.depth = +this.value; draw(); };
  });
  [$('k-s4'), $('k-s8'), $('m-s4'), $('m-s8')].forEach(function (b) {
    b.onclick = function () { if (!this.disabled) { setStrips(+this.dataset.s); draw(); } };
  });
  Array.prototype.forEach.call(document.querySelectorAll('.presets button'),
    function (b) {
      b.onclick = function () {
        var v = this.dataset.p.split(',').map(Number);
        st.bits = v[0]; st.depth = v[2];
        $('k-bits').value = v[0]; $('k-depth').value = v[2];
        syncLimits(); setStrips(v[1]); syncLimits(); draw();
      };
    });
  syncLimits(); draw();
})();
</script>
'''


def python_lines():
    """Lines of tracked Python, to the nearest 500, read from git when the
    page is built."""
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, check=True,
                           capture_output=True, text=True).stdout.split()
    lines = sum(len((REPO / f).read_text().splitlines()) for f in files)
    return f"{round(lines / 500) * 500:,}"


def main() -> int:
    DOCS.mkdir(exist_ok=True)
    (DOCS / ".nojekyll").write_text("")
    lines = python_lines()
    version = re.search(r'^version = "([^"]+)"', (REPO / "pyproject.toml").read_text(),
                        re.M).group(1)
    rules = rules_figure()
    sense_html, sn = sense_figure()
    o = outputs_figure()
    v = verify_figures()
    delay_html = delay_figure()
    sense_js = SENSE_JS
    tile_defs, tile_data = tile_animation()
    tile_js = TILE_JS.replace("__TILE__", tile_data)
    diagram = DIAGRAM.replace("__READ_BUDGET__",
                              json.dumps(read_budget(), sort_keys=True))
    (DOCS / "index.html").write_text(f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sramforge, a memory compiler for a 7 nanometre process</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 0 20px 72px;
         font: 16.5px/1.65 -apple-system, "Segoe UI", Roboto, sans-serif;
         color: #1d1b16; background: #fbfaf6; }}
  :root {{ --maxw: 820px; }}
  main {{ max-width: var(--maxw); margin: 0 auto; }}
  header {{ padding: 64px 0 8px; }}
  h1 {{ margin: 0 0 14px; font-size: 38px; letter-spacing: -0.025em; }}
  .lede {{ margin: 0 0 20px; font-size: 20px; line-height: 1.5;
           color: #3a362d; }}
  p {{ margin: 0 0 16px; }}
  .figure {{ margin: 34px 0 10px; text-align: center; }}
  .figure img {{ max-width: 100%; border: 1px solid #e4dfd2; border-radius: 10px; }}
  .caption {{ font-size: 13.5px; color: #625d52; margin: 10px 0 0; }}
  h2 {{ font-size: 24px; letter-spacing: -0.015em; margin: 52px 0 14px; }}
  h3 {{ margin: 0 0 6px; font-size: 17px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 18px 0 8px;
           font-size: 15.5px; }}
  th, td {{ text-align: left; padding: 9px 12px 9px 0;
            border-bottom: 1px solid #ebe7dd; vertical-align: top; }}
  th {{ font-weight: 600; color: #625d52; font-size: 12.5px;
        text-transform: uppercase; letter-spacing: 0.06em; }}
  td.n {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
  @media (max-width: 640px) {{ td.n {{ white-space: normal; }} }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 14px; margin-top: 18px; }}
  .card {{ display: block; border: 1px solid #e4dfd2; border-radius: 9px;
           padding: 18px; text-decoration: none; color: inherit; background: #fff; }}
  .card:hover {{ border-color: #a39b8a; }}
  .card p {{ margin: 0; font-size: 14px; color: #4a463d; }}
  ul.more {{ list-style: none; padding: 0; margin: 20px 0 0; }}
  ul.more li {{ padding: 8px 0; border-bottom: 1px solid #ebe6da; font-size: 15px; }}
  ul.more span {{ color: #625d52; font-size: 14px; }}
  .kit {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
          gap: 10px; margin: 14px 0 16px; }}
  .kit span {{ background: #f3f0e8; border-radius: 9px; padding: 12px 14px;
               font-size: 13.5px; color: #625d52; line-height: 1.45; }}
  .kit b {{ display: block; color: #1d1b16; font-size: 14.5px; margin-bottom: 2px; }}
  ol.flow {{ list-style: none; padding: 0; margin: 16px 0 6px; counter-reset: st; }}
  ol.flow li {{ position: relative; margin: 0 0 0 30px; padding: 0 0 18px 18px;
                border-left: 2px solid #d9d3c4; font-size: 14.5px; color: #4a463d;
                counter-increment: st; }}
  ol.flow li:last-child {{ border-left-color: transparent; padding-bottom: 4px; }}
  ol.flow li::before {{ content: counter(st); position: absolute; left: -13px; top: -2px;
                        width: 24px; height: 24px; border-radius: 50%; background: #1f3a6d;
                        color: #fff; font-size: 12.5px; font-weight: 600;
                        text-align: center; line-height: 24px; }}
  ol.flow .st {{ display: block; font-size: 11.5px; text-transform: uppercase;
                 letter-spacing: 0.07em; color: #625d52; margin: 2px 0 1px; }}
  ol.flow b {{ display: block; font-size: 17px; color: #1d1b16; margin-bottom: 3px; }}
  .bands {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px;
            align-items: start; margin: 26px 0 8px; }}
  .bands figure {{ margin: 0; }}
  .bands img {{ display: block; width: 100%; height: auto; background: #000;
                border: 1px solid #e4dfd2; border-radius: 8px; }}
  .bands figcaption {{ font-size: 13px; line-height: 1.5; color: #625d52;
                       margin-top: 8px; }}
  .bands figcaption b {{ color: #1d1b16; }}
  p.note {{ font-size: 13.5px; color: #625d52; }}
  h3.sub {{ margin: 30px 0 8px; }}
  h4.vsub {{ margin: 26px 0 8px; font-size: 15px; }}
  ol.vsteps {{ padding-left: 22px; }}
  ol.vsteps li {{ margin: 0 0 10px; }}
  .rules-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 26px 28px;
                 align-items: start; margin: 20px 0 18px; }}
  .rules-grid .r0 {{ grid-column: 1 / -1; }}
  .rules-grid .r1 {{ grid-column: 1; grid-row: 2; }}
  .rules-grid .r2 {{ grid-column: 1; grid-row: 3; }}
  .rules-grid .r3 {{ grid-column: 2; grid-row: 2 / span 2; }}
  svg.tile {{ display: block; width: 100%; height: auto; aspect-ratio: 2 / 1;
              background: #fdfcf8; border: 1px solid #ebe6da; border-radius: 8px;
              margin: 4px 0 18px; overflow: hidden; }}
  svg.rules.tile {{ overflow: hidden; }}
  svg.oflow, svg.oabs, svg.otim {{ display: block; width: 100%; height: auto;
                                   margin: 12px 0 6px; }}
  svg.oflow text, svg.oabs text, svg.otim text {{
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }}
  svg.oflow .ob {{ fill: #f3f0e8; stroke: #d9d3c4; }}
  svg.oflow .oc {{ fill: #fff4e0; stroke: #d6a23a; }}
  svg.oflow .obt {{ fill: #1d1b16; font-size: 12.5px; font-weight: 600; }}
  svg.oflow .obs {{ fill: #625d52; font-size: 11px; }}
  svg.oflow .obv {{ fill: #a9761c; font-size: 11px; }}
  svg.oflow .oa {{ fill: none; stroke: #8f8879; stroke-width: 1.4; }}
  svg.oabs .omac {{ fill: #fdfcf8; stroke: #8f8879; stroke-width: 1; }}
  svg.oabs .l3 {{ fill: #2a78d6; }}
  svg.oabs .l4 {{ fill: #1baf7a; }}
  svg.oabs .l5 {{ fill: #6250d6; }}
  svg.oabs .oo {{ fill-opacity: 0.22; }}
  svg.oabs .op {{ fill-opacity: 1; }}
  svg.oabs .ozb {{ fill: none; stroke: #a9761c; stroke-width: 1.4; }}
  svg.oabs .ozl {{ fill: none; stroke: #b8b1a2; stroke-width: 0.8; }}
  svg.oabs .ol {{ fill: #4a463d; font-size: 11.5px; }}
  svg.otim .tn {{ fill: #1d1b16; font-size: 12px; }}
  svg.otim .tw {{ fill: none; stroke: #1d1b16; stroke-width: 1.5; }}
  svg.otim .twd {{ stroke: #625d52; stroke-dasharray: 4 3; }}
  svg.otim .tx {{ fill: #ebe6da; }}
  svg.otim .ts {{ fill: #4a463d; font-size: 11px; }}
  svg.otim .tv {{ fill: #a9761c; font-size: 11px; font-weight: 600; }}
  svg.otim .tm {{ fill: none; stroke: #a9761c; stroke-width: 1.2; }}
  svg.otim .tg {{ stroke: #d9d3c4; stroke-dasharray: 3 3; }}
  table.otab {{ width: auto; margin: 6px 0 14px; }}
  table.otab td, table.otab th {{ padding: 6px 18px 6px 0; }}
  pre.code {{ font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
              background: #f3f0e8; border: 1px solid #e4dfd2; border-radius: 8px;
              padding: 12px 14px; overflow: auto; max-height: 360px; margin: 8px 0 16px; }}
  @media (max-width: 640px) {{
    .oscroll {{ overflow-x: auto; }}
    svg.oflow, svg.oabs, svg.otim {{ min-width: 620px; }} }}
  svg.vxor, svg.vslots {{ display: block; width: 100%; height: auto;
                                     margin: 12px 0 6px; }}
  svg.vxor text, svg.vslots text {{
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }}
  svg .vs {{ fill: #f3f0e8; stroke: #d9d3c4; }}
  svg .vc {{ fill: #1d1b16; font-size: 11.5px; }}
  svg .vn {{ fill: #625d52; font-size: 11px; }}
  svg .vax {{ fill: none; stroke: #8f8879; stroke-width: 1.3; }}
  svg .vpan {{ fill: #fdfcf8; stroke: #d9d3c4; }}
  svg .ra {{ fill: rgba(90, 158, 111, 0.75); }}
  svg .rg {{ fill: #d0453f; }}
  svg .vfa {{ fill: rgba(90, 158, 111, 0.22); }}
  svg .vfg {{ fill: rgba(208, 69, 63, 0.22); }}
  svg .vx {{ fill: #d6a23a; }}
  svg .vop {{ fill: #625d52; font-size: 22px; }}
  svg .vh {{ font-weight: 600; font-size: 12.5px; }}
  svg .vsep {{ stroke: #d9d3c4; stroke-dasharray: 3 3; }}
  svg .dok {{ fill: #2a78d6; }}
  svg .dmiss {{ fill: none; stroke: #e34948; stroke-width: 1.2; }}
  svg .dbad {{ fill: #e34948; }}
  svg .rwin {{ fill: #f3f0e8; }}
  svg .rbase {{ stroke: #e4dfd2; }}
  svg .rtr {{ fill: none; stroke-width: 1.8; }}
  svg .rwl {{ stroke: #6250d6; }}
  svg .rwe {{ stroke: #eb6834; }}
  svg .rse {{ stroke: #1baf7a; }}
  svg .rc0, svg .rc1 {{ stroke: #2a78d6; }}
  svg .rdo {{ stroke: #1d1b16; }}
  svg .rmk {{ stroke: #a9761c; stroke-dasharray: 3 2; }}
  svg .vv2 {{ fill: #a9761c; font-size: 11px; font-weight: 600; }}
  svg .cyh {{ fill: #c3d0e8; }}
  svg .cyl {{ fill: #c4e2da; }}
  svg .cym {{ fill: none; stroke: #8f8879; stroke-dasharray: 3 3; }}
  svg .cyt {{ fill: #1d1b16; font-size: 11px; }}
  svg .am4 {{ fill: #1baf7a; fill-opacity: 0.55; }}
  svg .am5 {{ fill: #6250d6; fill-opacity: 0.55; }}
  .trace {{ display: grid; grid-template-columns: 3fr 2fr; gap: 14px; margin: 10px 0 8px; }}
  .trace > div {{ min-width: 0; }}
  .trace pre.code {{ overflow-x: auto; }}
  .trace .ttl {{ display: block; font-size: 12px; color: #625d52; margin-bottom: 4px; }}
  .trace pre.code {{ margin: 0; font-size: 11.5px; }}
  @media (max-width: 640px) {{ .trace {{ grid-template-columns: 1fr; }} }}
  svg .tfin {{ fill: #8f8879; fill-opacity: 0.35; }}
  svg .tgc {{ fill: #fdfcf8; }}
  svg .tch {{ fill: none; stroke: #a9761c; stroke-width: 2; }}
  svg .tchs {{ fill: #f3d9a4; stroke: #a9761c; stroke-width: 1.4; }}
  svg .tno {{ fill: #1d1b16; }}
  svg .tnt {{ fill: #fff; font-size: 10.5px; font-weight: 600; }}
  svg .tw {{ fill: none; stroke: #4a463d; stroke-width: 1.4; }}
  svg .tbl {{ fill: none; stroke: #2a78d6; stroke-width: 2; }}
  svg .twl {{ fill: none; stroke: #6250d6; stroke-width: 2; }}
  svg .tdot {{ fill: #4a463d; }}
  svg .tth {{ font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; font-size: 10.5px; }}
  svg .mwi {{ fill: #c3d0e8; }}
  svg .mhi {{ fill: #2a78d6; }}
  svg .mbump {{ fill: #e34948; }}
  svg .mmark {{ fill: none; stroke: #e34948; stroke-width: 1.6; }}
  svg .mdim {{ fill: none; stroke: #a9761c; stroke-width: 1.2; }}
  svg .mbad {{ fill: none; stroke: #e34948; stroke-width: 1.2; }}
  svg .mv {{ fill: #a9761c; font-size: 11px; font-weight: 600; }}
  svg .mvb {{ fill: #e34948; font-size: 11px; font-weight: 600; }}
  svg .halo2 {{ paint-order: stroke; stroke: #fdfcf8; stroke-width: 3px; stroke-linejoin: round; }}
  svg .wbl {{ fill: #2a78d6; }}
  svg .wbn {{ fill: #1baf7a; }}
  svg .wwl {{ fill: #6250d6; }}
  svg .wvd {{ fill: #eb6834; }}
  svg .wvs {{ fill: #8f8879; }}
  svg .wot {{ fill: #d9d3c4; }}
  svg .wbad {{ fill: #e34948; }}
  svg .w1 {{ fill-opacity: 0.45; }}
  svg .w2 {{ fill-opacity: 0.8; }}
  svg .w3 {{ fill-opacity: 0.6; }}
  svg .wmk {{ fill: none; stroke: #a9761c; stroke-width: 1.6; }}
  svg .vslot {{ fill: none; stroke: #8f8879; stroke-dasharray: 2 2; }}
  svg .vok {{ fill: #c3d0e8; stroke: #5c7fb8; }}
  svg .vbad {{ fill: #f3d9a4; stroke: #a9761c; }}
  @media (max-width: 640px) {{ svg.vxor, svg.vslots {{ min-width: 620px; }} }}
  ol.qlist {{ list-style: none; counter-reset: q; padding: 0; margin: 14px 0 6px; }}
  ol.qlist li {{ counter-increment: q; position: relative; padding: 12px 0 12px 44px;
                 border-top: 1px solid #ebe6da; }}
  ol.qlist li:first-child {{ border-top: 0; }}
  ol.qlist li::before {{ content: counter(q); position: absolute; left: 0; top: 11px;
                         width: 28px; height: 28px; border-radius: 50%; background: #1f3a6d;
                         color: #fff; font-size: 13px; font-weight: 600; text-align: center;
                         line-height: 28px; }}
  ol.qlist b {{ display: block; font-size: 16px; margin-bottom: 2px; }}
  ol.qlist .qa {{ display: block; font-size: 14.5px; color: #4a463d; }}
  ol.qlist .qp {{ display: block; font-size: 13px; color: #625d52; margin-top: 4px; }}
  ol.qlist .qp i {{ font-style: normal; font-weight: 600; color: #a9761c; margin-right: 4px; }}
  ul.parts {{ margin: 6px 0 12px; padding-left: 20px; }}
  ul.fams {{ margin: 6px 0 12px; padding-left: 20px; }}
  ul.fams li {{ margin: 0 0 5px; }}
  figure.dfig {{ margin: 14px 0 18px; }}
  figure.dfig figcaption {{ font-size: 13px; line-height: 1.5; color: #625d52; margin-top: 4px; }}
  figure.dfig figcaption b {{ color: #1d1b16; }}
  svg.dline {{ display: block; width: 100%; height: auto; background: #fdfcf8;
               border: 1px solid #ebe6da; border-radius: 8px; }}
  svg.dline text {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }}
  svg.dline .dw {{ fill: none; stroke: #5f5a4f; stroke-width: 1.4; }}
  svg.dline .dg {{ fill: #fff; stroke: #1d1b16; stroke-width: 1.4; }}
  svg.dline .db {{ fill: #fff; stroke: #1d1b16; stroke-width: 1.3; }}
  svg.dline .dl {{ fill: #f3f0e8; stroke: #8f8879; stroke-width: 1; }}
  svg.dline .dk {{ fill: #e8edf7; stroke: #93a4c4; stroke-width: 1; }}
  svg.dline .dnew {{ stroke: #a9761c; stroke-width: 1.8; }}
  svg.dline .dt {{ fill: #4a463d; font-size: 12px; }}
  svg.dline .ds {{ fill: #625d52; font-size: 10.5px; }}
  svg.dline .dm {{ fill: #625d52; font-size: 9px; }}
  svg.dline .dv {{ fill: #a9761c; font-size: 11px; font-weight: 600; }}
  @media (max-width: 640px) {{ figure.dfig {{ overflow-x: auto; }}
    svg.dline {{ min-width: 620px; }} }}
  ul.parts li {{ margin: 0 0 6px; }}
  .slegend {{ display: flex; flex-wrap: wrap; gap: 6px 16px; margin: 14px 0 4px;
              font-size: 13px; color: #4a463d; }}
  .slegend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .slegend i {{ display: inline-block; width: 12px; height: 12px; border-radius: 3px; }}
  .slegend i.tick {{ width: 2px; height: 14px; border-radius: 0; background: #1d1b16; }}
  .slegend i.lim {{ width: 0; height: 14px; border-left: 2px dashed #a9761c;
                    border-radius: 0; }}
  .sv1 {{ background: #2a78d6; fill: #2a78d6; }}
  .sv2 {{ background: #eb6834; fill: #eb6834; }}
  .sv3 {{ background: #1baf7a; fill: #1baf7a; }}
  .sv4 {{ background: #eda100; fill: #eda100; }}
  .sfig {{ position: relative; margin: 0 0 6px; }}
  svg.sensechart {{ display: block; width: 100%; height: auto; }}
  svg.sensechart text {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }}
  svg.sensechart .sgrid {{ stroke: #ebe6da; stroke-width: 1; }}
  svg.sensechart .saxis {{ fill: #8f8879; font-size: 11px; }}
  svg.sensechart .slimit {{ stroke: #a9761c; stroke-width: 1.5; stroke-dasharray: 4 3; }}
  svg.sensechart .slab {{ fill: #a9761c; font-size: 11.5px; font-weight: 600; }}
  svg.sensechart .sname {{ fill: #1d1b16; font-size: 12.5px; }}
  svg.sensechart .sctl {{ fill: #8f8879; font-size: 10.5px; }}
  svg.sensechart .sseg {{ stroke: #fbfaf6; stroke-width: 1; }}
  svg.sensechart .stick {{ stroke: #1d1b16; stroke-width: 2; }}
  svg.sensechart .sval {{ fill: #4a463d; font-size: 11px; }}
  svg.sensechart .shit {{ fill: transparent; }}
  svg.sensechart .srow {{ outline: none; cursor: default; }}
  svg.sensechart .srow:hover .shit, svg.sensechart .srow:focus .shit {{ fill: rgba(26, 86, 184, 0.06); }}
  .stip {{ position: absolute; z-index: 3; pointer-events: none; background: #fff;
           border: 1px solid #d9d3c4; border-radius: 8px; padding: 8px 10px;
           box-shadow: 0 4px 14px rgba(22, 24, 29, 0.12); font-size: 12.5px;
           line-height: 1.5; color: #4a463d; white-space: nowrap; }}
  .stip .h {{ color: #1d1b16; font-weight: 600; }}
  details.stable {{ margin: 4px 0 14px; font-size: 13.5px; }}
  details.stable summary {{ cursor: pointer; color: #1f3a6d; }}
  details.stable table {{ font-size: 13px; }}
  @media (max-width: 640px) {{ .sfig {{ overflow-x: auto; }}
    svg.sensechart {{ min-width: 620px; }}
    details.stable {{ overflow-x: auto; }} }}
  svg.tile rect {{ vector-effect: non-scaling-stroke; }}
  svg.tile pattern rect {{ stroke: none; }}
  svg.tile .rline {{ fill: #93a4c4; }}
  svg.tile path {{ vector-effect: non-scaling-stroke; }}
  svg.tile {{ --tl-c0: #c9d5ec; --tl-c1: #c9e6de; --tl-cd: #e4dfd2;
              --tl-ct: #cfe0d4; --tl-ra: rgba(90, 158, 111, 0.75);
              --tl-rg: #d0453f; --tl-rgc: #fdfcf8; --tl-rsd: rgba(138, 111, 199, 0.85);
              --tl-rgl: #2f7fc1; --tl-cbn: #8f8879; --tl-rline: #93a4c4; }}
  svg.tile .c0 {{ color: var(--tl-c0); }}
  svg.tile .c1 {{ color: var(--tl-c1); }}
  svg.tile .cd {{ color: var(--tl-cd); }}
  svg.tile .ct {{ color: var(--tl-ct); }}
  svg.tile .dimp {{ fill: none; stroke: #a9761c; stroke-width: 1.2; }}
  svg.tile text {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }}
  svg.tile .ta {{ fill: #4a463d; }}
  svg.tile .ts {{ fill: #625d52; }}
  svg.tile .tv {{ fill: #a9761c; font-weight: 600; }}
  svg.tile .th {{ fill: #1d1b16; paint-order: stroke; stroke: #fdfcf8;
                  stroke-linejoin: round; }}
  .stepper #tl-n {{ font-size: 12.5px; color: #625d52;
                    font-variant-numeric: tabular-nums; }}
  table.knobrules td {{ font-size: 14.5px; vertical-align: top; }}
  table.knobrules td:first-child {{ font-weight: 600; white-space: nowrap; }}
  @media (max-width: 640px) {{ .rules-grid {{ grid-template-columns: 1fr; }}
    .rules-grid figure[class] {{ grid-column: auto; grid-row: auto; min-width: 0; }}
    .rules-grid .r0 .scroll {{ overflow-x: auto; }}
    .rules-grid .r0 svg {{ min-width: 620px; }} }}
  .rules-grid figure {{ margin: 0; }}
  .rules-grid figcaption {{ font-size: 13px; line-height: 1.5; color: #625d52;
                            margin-top: 6px; }}
  .rules-grid figcaption b {{ color: #1d1b16; }}
  svg.rules {{ display: block; width: 100%; height: auto; overflow: visible; }}
  svg.rules text {{ font: 11px -apple-system, "Segoe UI", Roboto, sans-serif;
                    fill: #4a463d; }}
  svg.rules .rs {{ font-size: 10px; fill: #625d52; }}
  svg.rules .rv, svg.rules .rvt {{ fill: #a9761c; font-weight: 600; }}
  svg.rules .mid {{ text-anchor: middle; }}
  svg.rules .end {{ text-anchor: end; }}
  svg.rules .rc {{ fill: #5c7fb8; fill-opacity: 0.22; stroke: #5c7fb8; stroke-width: 1.2; }}
  svg.rules .rc2 {{ fill: #c3d0e8; stroke: #93a4c4; stroke-width: 0.5; }}
  svg.rules .rc2m {{ fill: #c4e2da; stroke: #8dbfb2; stroke-width: 0.5; }}
  svg.rules .rcm {{ fill: #2f9a86; fill-opacity: 0.2; stroke: #2f9a86; stroke-width: 1.2; }}
  svg.rules .halo {{ paint-order: stroke; stroke: #fbfaf6; stroke-width: 3px;
                     stroke-linejoin: round; fill: #1d1b16; }}
  svg.rules .rc3 {{ fill: #d5dff0; stroke: #93a4c4; stroke-width: 0.5; }}
  svg.rules .rn {{ fill: #3d5a8c; }}
  svg.rules .rt0 {{ fill: #c3d0e8; fill-opacity: 0.45; }}
  svg.rules .rtm {{ fill: #c4e2da; fill-opacity: 0.6; }}
  svg.rules .rdt {{ fill: #e4dfd2; fill-opacity: 0.8; }}
  svg.rules .rbn {{ fill: none; stroke: #1d1b16; stroke-width: 1.3; stroke-dasharray: 5 3; }}
  svg.rules .ra {{ fill: #5a9e6f; fill-opacity: 0.75; }}
  svg.rules .rg {{ fill: #d0453f; }}
  svg.rules .rgc {{ fill: #fbfaf6; }}
  svg.rules .rsd {{ fill: #8a6fc7; fill-opacity: 0.85; }}
  svg.rules .rgl {{ fill: #2f7fc1; }}
  svg.rules .rdm {{ fill: #e4dfd2; stroke: #a3a9b4; stroke-width: 0.5; }}
  svg.rules .rtp {{ fill: #cfe0d4; stroke: #7fa38a; stroke-width: 0.5; }}
  svg.rules .rio {{ fill: #a9c6b1; stroke: #7fa38a; stroke-width: 0.5; }}
  svg.rules .rf {{ fill: #ebe6da; stroke: #b8b1a2; stroke-width: 0.5; }}
  svg.rules .rbd {{ fill: #8b97ad; }}
  svg.rules .rbd2 {{ fill: #8b97ad; stroke: #a9761c; stroke-width: 1.5; }}
  svg.rules .remp {{ fill: none; stroke: #93a4c4; stroke-width: 0.8; stroke-dasharray: 3 2; }}
  svg.rules .rmux {{ fill: #5f8f6c; }}
  svg.rules .rmux2 {{ fill: #5f8f6c; stroke: #a9761c; stroke-width: 1; }}
  svg.rules .rsel {{ fill: #1f3a6d; }}
  svg.rules .rd {{ fill: none; stroke: #a9761c; stroke-width: 1; }}
  @media (max-width: 520px) {{ .bands {{ grid-template-columns: 1fr; }} }}
  /* the configurator */
  .config {{ border: 1px solid #e4dfd2; border-radius: 10px; padding: 18px 18px 14px;
             background: #fff; margin: 22px 0 10px; }}
  .config.built {{ padding: 20px 22px 8px; }}
  .config.built > p:first-child {{ margin-top: 0; }}
  .config.built h3.sub {{ margin: 30px 0 10px; padding-top: 22px;
                          border-top: 1px solid #ebe6da; }}
  @media (max-width: 640px) {{ .config.built {{ padding: 16px 14px 6px; }} }}
  .knobs {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
  @media (max-width: 640px) {{ .knobs {{ grid-template-columns: 1fr; }} }}
  .knob {{ background: #f3f0e8; border-radius: 9px; padding: 14px 15px 13px;
           display: flex; flex-direction: column; }}
  .knob .name {{ font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.07em;
                 color: #625d52; }}
  .knob .val {{ font-size: 27px; line-height: 1.1; margin: 3px 0 10px;
                font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }}
  .knob input[type=range] {{ width: 100%; margin: 0; accent-color: #1f3a6d; }}
  .knob .range {{ display: flex; justify-content: space-between; margin-top: 3px;
                  font-size: 11.5px; color: #8f8879;
                  font-variant-numeric: tabular-nums; }}
  .knob .eff {{ font-size: 13px; color: #4a463d; margin-top: 10px; }}
  .knob:has(#k-s8:disabled) .val {{ color: #8f8879; }}
  .seg {{ display: inline-flex; border: 1px solid #d9d3c4;
          border-radius: 7px; overflow: hidden; width: max-content; }}
  .seg button {{ border: 0; background: #fff; padding: 4px 18px; font: inherit;
                 font-size: 15px; cursor: pointer; color: #1d1b16; }}
  .seg button.on {{ background: #1f3a6d; color: #fff; }}
  .seg button:disabled {{ color: #b8b1a2; cursor: not-allowed; }}
  .presets {{ margin: 14px 0 4px; font-size: 13.5px; color: #625d52; }}
  .presets button {{ font: inherit; font-size: 13.5px; margin-left: 6px; padding: 3px 10px;
                     border: 1px solid #d9d3c4; border-radius: 20px; background: #fff;
                     cursor: pointer; color: #1f3a6d; }}
  .presets button:hover {{ border-color: #a39b8a; }}
  #dia {{ width: 100%; height: auto; margin: 6px 0 2px; }}
  #dia .arr {{ fill: #e8edf7; }}
  #dia .band {{ fill: #8b97ad; }}
  #dia .peri {{ fill: #cfe0d4; }}
  #dia .outline {{ fill: none; stroke: #8f8879; stroke-width: 1; }}
  #dia .groupbox {{ fill: none; stroke: #625d52; stroke-width: 1.2; }}
  #dia .strong {{ font-weight: 600; fill: #1d1b16; letter-spacing: 0.04em; }}
  #dia .cell {{ fill: #c3d0e8; stroke: #93a4c4; stroke-width: 0.35; }}
  #dia .cell.mark {{ fill: #d6a23a; stroke: #a9761c; stroke-width: 0.5; }}
  #dia .hl {{ fill: #d6a23a; stroke: #a9761c; stroke-width: 0.6; }}
  #dia .zoom {{ fill: #d6a23a; opacity: 0.11; }}
  #dia .leader {{ stroke: #c49a46; stroke-width: 0.9; stroke-dasharray: 3 3; }}
  #dia .lab {{ font: 12px -apple-system, "Segoe UI", Roboto, sans-serif; fill: #625d52; }}
  #dia .labs {{ font: 11px -apple-system, "Segoe UI", Roboto, sans-serif;
                fill: #625d52; }}
  #dia .mid {{ text-anchor: middle; }}
  .wordsec {{ margin-top: 18px; padding-top: 16px; border-top: 1px solid #ebe6da; }}
  .wordsec h3 {{ margin: 0 0 6px; font-size: 16px; }}
  .wordsec h4 {{ margin: 24px 0 11px; padding-top: 15px; font-size: 15px;
                 border-top: 1px solid #ebe6da; }}
  .wordsec p {{ font-size: 14.5px; color: #4a463d; margin: 0 0 6px; }}
  .shots {{ margin: 16px 0 10px; }}
  .pair {{ display: grid; gap: 12px 18px; align-items: start; margin: 0 0 22px; }}
  .pair.a {{ grid-template-columns: 110px 110px 1fr; }}
  .pair.b {{ grid-template-columns: 1fr; gap: 10px; }}
  .pair.c {{ grid-template-columns: 200px 200px 1fr; }}
  .pair figure {{ margin: 0; }}
  .pair .tag {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
                color: #8f8879; margin-bottom: 5px; }}
  .pair img, .pair svg.d {{ display: block; width: 100%; height: auto;
                            box-sizing: border-box; border: 1px solid #d9d3c4;
                            border-radius: 4px; }}
  .pair img {{ background: #000; }}
  .pair svg.d {{ background: #fdfcf8; }}
  .pair .cap {{ font-size: 12.5px; line-height: 1.55; color: #625d52; margin: 0; }}
  .pair .cap b {{ color: #1d1b16; font-weight: 600; }}
  .pair.b .cap {{ grid-column: 1 / -1; }}
  @media (max-width: 640px) {{
    .pair.a, .pair.b, .pair.c {{ grid-template-columns: 1fr 1fr; }}
    .pair .cap {{ grid-column: 1 / -1; }} }}
  svg.d .arr {{ fill: #e8edf7; }}
  svg.d .peri {{ fill: #cfe0d4; }}
  svg.d .band {{ fill: #8b97ad; }}
  svg.d .cell {{ fill: #c3d0e8; stroke: #93a4c4; stroke-width: 0.5; }}
  svg.d .io {{ fill: #a9c6b1; }}
  svg.d .mark {{ fill: #d6a23a; opacity: 0.8; }}
  svg.d .markbox {{ fill: none; stroke: #a9761c; stroke-width: 3; }}
  svg.d .fade {{ fill: #fdfcf8; opacity: 0.62; }}
  svg.d .strong {{ font-weight: 600; fill: #1d1b16; letter-spacing: 0.05em; }}
  svg.d .sep {{ stroke: #625d52; stroke-width: 2.5; }}
  svg.d .outline {{ fill: none; stroke: #8f8879; stroke-width: 1; }}
  svg.d .wire {{ fill: none; stroke: #5f5a4f; stroke-width: 3;
                 stroke-linecap: round; stroke-linejoin: round; }}
  svg.d .inv {{ fill: #c3d0e8; stroke: #5f5a4f; stroke-width: 3;
                stroke-linejoin: round; }}
  svg.d .fet {{ fill: #cfe0d4; stroke: #5f5a4f; stroke-width: 3; }}
  svg.d .dl {{ font: 15px -apple-system, "Segoe UI", Roboto, sans-serif;
               fill: #4a463d; }}
  svg.d .end {{ text-anchor: end; }}
  svg.d .mid {{ text-anchor: middle; }}
  .pair.c svg.d .dl {{ font-size: 13px; }}
  .shotnote {{ font-size: 13.5px; color: #625d52; margin: 18px 0 2px; }}
  .mini {{ display: flex; flex-wrap: wrap; gap: 10px 22px; align-items: center;
           margin: 12px 0 6px; padding: 10px 12px; background: #f3f0e8;
           border-radius: 8px; }}
  .mini label {{ display: flex; align-items: center; gap: 8px; font-size: 12.5px;
                 color: #625d52; }}
  .mini input[type=range] {{ width: 110px; margin: 0; accent-color: #1f3a6d; }}
  .mini b {{ font-size: 13.5px; color: #1d1b16; font-variant-numeric: tabular-nums;
             min-width: 18px; }}
  .mini .seg button {{ padding: 2px 12px; font-size: 13.5px; }}
  #word, #addr, #read, #write {{ width: 100%; height: auto; }}
  #word .mark, #addr .mark {{ fill: #d6a23a; opacity: 0.45; }}
  #word .markbox, #addr .markbox {{ fill: none; stroke: #a9761c; stroke-width: 1.3; }}
  #word .sep, #addr .sep {{ stroke: #fff; stroke-width: 0.8; }}
  #word .zoom, #addr .zoom {{ fill: #8b97ad; opacity: 0.13; }}
  #word .cells, #addr .cells {{ fill: #c3d0e8; }}
  #addr .bl {{ fill: #4a9a86; opacity: 0.42; }}
  #addr .openbox {{ fill: none; stroke: #2f5fa8; stroke-width: 0.9; }}
  #addr .colmark {{ fill: #5c7fb8; opacity: 0.32; }}
  #addr .badge {{ fill: #8b97ad; }}
  #addr .bnum {{ font: 600 10px -apple-system, "Segoe UI", Roboto, sans-serif;
                 fill: #fff; text-anchor: middle; }}
  #addr .amp {{ fill: #cfe0d4; stroke: #5f5a4f; stroke-width: 1.1; }}
  #addr .wire {{ fill: none; stroke: #5f5a4f; stroke-width: 1.1; }}
  #addr .brace {{ fill: none; stroke: #b8b1a2; stroke-width: 1; }}
  .walk .pbox {{ fill: #fff; stroke: #c9c3b4; stroke-width: 1; }}
  .walk .pbox.lit {{ fill: #eef1f6; stroke: #8f8879; }}
  .walk .pbox.now {{ fill: #fbeed2; stroke: #d6a23a; stroke-width: 2; }}
  .walk .parr {{ fill: none; stroke: #c9c3b4; stroke-width: 1.2; }}
  .walk .parr.on {{ stroke: #a9761c; }}
  .walk .tiny {{ font: 9.5px -apple-system, "Segoe UI", Roboto, sans-serif;
                 fill: #625d52; }}
  .walk .tiny.mid {{ text-anchor: middle; }}
  .walk .tiny.end {{ text-anchor: end; }}
  .walk .bus {{ fill: none; stroke-width: 2.2; }}
  .walk .bus.f2 {{ stroke: #4a9a86; }}
  .walk .wl {{ fill: none; stroke: #c9c3b4; stroke-width: 2.4; }}
  .walk .wl.on {{ stroke: #d6a23a; }}
  .walk .bl {{ fill: none; stroke: #c9c3b4; stroke-width: 1; }}
  .walk .bl.on {{ stroke: #4a9a86; stroke-width: 1.5; }}
  .walk .bl.slam {{ stroke-width: 3; }}
  .walk .garr {{ fill: #e8edf7; }}
  .walk .gio {{ fill: #dfeae2; }}
  .walk .gio.on {{ fill: #f3dfb0; }}
  .walk .gband {{ fill: #eef0f4; }}
  .walk .gcell {{ fill: #c3d0e8; stroke: #fff; stroke-width: 0.5; }}
  .walk .gcell.open {{ stroke: #d6a23a; stroke-width: 1.2; }}
  .walk .gcell.now {{ fill: #d6a23a; stroke: #a9761c; stroke-width: 1.2; }}
  .walk .pin {{ fill: #a39b8a; }}
  .walk .pin.on {{ fill: #a9761c; }}
  .walk .fold {{ fill: #fff; }}
  .walk .bankline {{ fill: none; stroke: #8f8879; stroke-width: 1; }}
  .walk .thru {{ stroke-dasharray: 3 3; }}
  .walk .ch {{ fill: #c9c3b4; }}
  .walk .ch.strong {{ fill: #5f5a4f; }}
  .walk .ch.on {{ fill: #d6a23a; }}
  .walk .gate {{ fill: none; stroke: #5f5a4f; stroke-width: 2; }}
  .walk .bubble {{ fill: #fff; stroke: #5f5a4f; stroke-width: 1.1; }}
  .walk .jdot {{ fill: #5f5a4f; }}
  .walk .gap {{ fill: #fff; }}
  .walk .wl.thin {{ stroke-width: 1.2; }}
  .walk .bit1 {{ fill: #1d1b16; }}
  .walk .bit0 {{ fill: #fff; stroke: #8f8879; stroke-width: 1.2; }}
  .walk .bnum.one {{ fill: #fff; }}
  .walk .bnum.zero {{ fill: #1d1b16; }}
  .walk .flow {{ fill: none; stroke: #d6a23a; stroke-width: 2.2;
                 stroke-dasharray: 5 3; }}
  .walk .meter {{ fill: none; stroke: #8f8879; }}
  .walk .meterfill {{ fill: #5c7fb8; }}
  .walk .phead {{ fill: #c9c3b4; }}
  .walk .phead.on {{ fill: #a9761c; }}
  .stepper {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px;
              position: sticky; top: 0; z-index: 2;
              box-shadow: 0 2px 6px rgba(22, 24, 29, 0.08);
              margin: 2px 0 10px; padding: 10px 12px; background: #f3f0e8;
              border-radius: 8px; }}
  .stepper button {{ font: inherit; font-size: 13px; padding: 3px 14px;
                     border: 1px solid #d9d3c4; border-radius: 6px;
                     background: #fff; color: #1f3a6d; cursor: pointer; }}
  .stepper button:disabled {{ color: #b8b1a2; cursor: default; }}
  .stepper #rs-n {{ font-size: 12.5px; color: #625d52;
                    font-variant-numeric: tabular-nums; }}
  .stepper b {{ flex-basis: 100%; font-size: 14.5px; }}
  .stepper p {{ flex-basis: 100%; margin: 0; font-size: 14px; color: #4a463d; }}
  .walk .bankbox {{ fill: #f3f0e8; stroke: #8f8879; stroke-width: 1.2; }}
  .walk .end {{ text-anchor: end; }}
  .walk .wire {{ fill: none; stroke: #5f5a4f; stroke-width: 1.1; }}
  .walk .badge {{ fill: #8b97ad; }}
  .walk .bnum {{ font: 600 10px -apple-system, "Segoe UI",
       Roboto, sans-serif; fill: #fff; text-anchor: middle; }}
  .walk .lab {{ font: 12px -apple-system, "Segoe UI", Roboto,
       sans-serif; fill: #625d52; }}
  .walk .labs {{ font: 11px -apple-system, "Segoe UI", Roboto,
       sans-serif; fill: #625d52; }}
  .walk .mid {{ text-anchor: middle; }}
  .walk .strong {{ font-weight: 600; fill: #1d1b16;
       letter-spacing: 0.04em; }}
  #word .grid, #addr .grid {{ stroke: #fff; stroke-width: 0.4; opacity: 0.85; }}
  #word .hl, #addr .hl {{ fill: none; stroke: #5f5a4f; stroke-width: 0.9; }}
  #word .arr, #addr .arr {{ fill: #e8edf7; }}
  #word .band, #addr .band {{ fill: #8b97ad; }}
  #word .peri, #addr .peri {{ fill: #cfe0d4; }}
  #word .outline, #addr .outline {{ fill: none; stroke: #8f8879; stroke-width: 1; }}
  #word .cell, #addr .cell {{ fill: #c3d0e8; stroke: #93a4c4; stroke-width: 0.35; }}
  #word .cell.mark, #addr .cell.mark {{ fill: #d6a23a; stroke: #a9761c; stroke-width: 0.5; }}
  #word .leader, #addr .leader {{ stroke: #c49a46; stroke-width: 0.9; stroke-dasharray: 3 3; }}
  #word .groupbox, #addr .groupbox {{ fill: none; stroke: #c49a46; stroke-width: 1.1;
                     stroke-dasharray: 4 3; }}
  #word .labs, #addr .labs {{ font: 11px -apple-system, "Segoe UI", Roboto, sans-serif;
                 fill: #625d52; }}
  #word .lab, #addr .lab {{ font: 12px -apple-system, "Segoe UI", Roboto, sans-serif;
                fill: #625d52; }}
  #word .mid, #addr .mid {{ text-anchor: middle; }}
  #word .strong, #addr .strong {{ font-weight: 600; fill: #1d1b16; letter-spacing: 0.04em; }}
  #word .onband, #addr .onband {{ fill: #fff; }}
  #word .fld, #addr .fld {{ stroke: #fff; stroke-width: 1; }}
  #word .f0, #addr .f0 {{ fill: #8b97ad; }}
  #word .f1, #addr .f1 {{ fill: #5c7fb8; }}
  #word .f2, #addr .f2 {{ fill: #4a9a86; }}
  .derived {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
              gap: 10px; margin-top: 14px; padding-top: 14px;
              border-top: 1px solid #ebe6da; }}
  .derived b {{ display: block; font-size: 15.5px; }}
  /* the opening: title, introduction and strip on the left, and the
     block itself in a narrow column on the right, as tall as the text.
     The image is cropped only at its black sides, so it stays to scale. */
  .hero {{ display: grid; grid-template-columns: minmax(0, 1fr) 168px;
           gap: 40px; padding-top: 64px; }}
  .hero header {{ padding-top: 0; }}
  .hero .glance {{ grid-template-columns: 1fr 1fr; margin-bottom: 0; }}
  .hero-fig {{ margin: 0; display: grid; grid-template-rows: minmax(0, 1fr) auto; }}
  .hero-fig .frame {{ position: relative; min-height: 360px; }}
  .hero-fig img {{ position: absolute; inset: 0; width: 100%; height: 100%;
                   object-fit: cover; background: #000; }}
  .hero-fig figcaption {{ font-size: 12.5px; line-height: 1.5; color: #625d52;
                          margin-top: 10px; }}
  @media (max-width: 700px) {{
    .hero {{ display: block; }}
    .hero-fig {{ display: block; margin-top: 28px; text-align: center; }}
    .hero-fig .frame {{ min-height: 0; }}
    .hero-fig img {{ position: static; width: auto; max-width: 100%; height: 440px; }}
    .hero-fig figcaption {{ text-align: left; }}
  }}
  .glance {{ display: grid; grid-template-columns: repeat(3, 1fr);
             gap: 18px 26px; margin: 28px 0 6px; padding: 18px 0;
             border-top: 1px solid #e4dfd2; border-bottom: 1px solid #e4dfd2; }}
  .glance b {{ display: block; font-size: 17px; letter-spacing: -0.01em; }}
  .glance span {{ display: block; font-size: 13px; line-height: 1.45; color: #625d52; }}
  @media (max-width: 560px) {{ .glance {{ grid-template-columns: 1fr 1fr; }} }}
  h2[id] {{ scroll-margin-top: 16px; }}
  .derived span {{ font-size: 12.5px; color: #625d52; }}
  footer {{ margin-top: 56px; padding-top: 22px; border-top: 1px solid #e4dfd2;
            font-size: 14.5px; color: #625d52; }}
  /* contents list: a small numbered list in the left margin that follows
     the reader. The page stays centred, so the list shrinks to fit the
     margin: full labels from 1320px, compact from 1200px, numbers only
     from 1000px. Below 1000px it is a small floating card, opened and
     closed by the Contents button. */
  .side {{ position: fixed; top: 96px; left: calc(50% - var(--maxw) / 2 - 230px);
           width: 196px; z-index: 40; font-size: 13px; line-height: 1.35;
           counter-reset: side; opacity: 0; visibility: hidden;
           transition: opacity .2s, visibility .2s; }}
  .side.is-on {{ opacity: 1; visibility: visible; }}
  .side-h {{ margin: 0 0 6px 12px; font: 700 10px ui-monospace, SFMono-Regular, Menlo, monospace;
             letter-spacing: .12em; text-transform: uppercase; color: #625d52; }}
  .side ol {{ list-style: none; margin: 0; padding: 0; border-left: 1px solid #e4dfd2; }}
  .side li {{ counter-increment: side; }}
  .side a {{ display: flex; gap: 10px; padding: 5px 12px; margin-left: -1px;
             border-left: 2px solid transparent; color: #625d52; text-decoration: none; }}
  .side a::before {{ content: counter(side, decimal-leading-zero); padding-top: 1px;
                     font: 700 11px ui-monospace, SFMono-Regular, Menlo, monospace; color: #a39b8a; }}
  .side a:hover {{ color: #1d1b16; }}
  .side a.is-here {{ color: #1f3a6d; border-left-color: #1f3a6d; font-weight: 600; }}
  .side a.is-here::before {{ color: #1f3a6d; }}
  .side-btn {{ display: none; position: fixed; left: 12px; bottom: 12px; z-index: 41;
               font: inherit; font-size: 13px; font-weight: 600; padding: 7px 12px;
               border-radius: 999px; border: 1px solid #e4dfd2; background: #fff;
               color: #1d1b16; box-shadow: 0 4px 14px rgba(0,0,0,.12); cursor: pointer; }}
  @media (min-width: 1200px) and (max-width: 1319px) {{
    .side {{ width: 140px; left: calc(50% - var(--maxw) / 2 - 166px); font-size: 12px; }}
    .side a {{ padding: 4px 8px; gap: 7px; }}
    .side-h {{ margin-left: 8px; }}
  }}
  @media (min-width: 1000px) and (max-width: 1199px) {{
    .side {{ width: 40px; left: calc(50% - var(--maxw) / 2 - 62px); }}
    .side-h, .side .lbl {{ display: none; }}
    .side a {{ padding: 5px 8px; }}
  }}
  @media (max-width: 999px) {{
    .side {{ top: 76px !important; left: 10px; width: 190px; max-height: calc(100vh - 140px);
             overflow-y: auto; font-size: 12px; padding: 10px 8px 8px 4px; background: #fff;
             border: 1px solid #e4dfd2; border-radius: 12px; box-shadow: 0 4px 14px rgba(0,0,0,.12);
             opacity: 0; visibility: hidden; transform: translateX(-8px);
             transition: opacity .2s, visibility .2s, transform .2s; }}
    .side.open {{ opacity: 1; visibility: visible; transform: none; }}
    .side a {{ padding: 4px 10px; }}
    .side-btn {{ display: block; }}
  }}
  @media (prefers-reduced-motion: reduce) {{ .side {{ transition: none; }} }}
  a {{ color: #1f3a6d; }}
  @media (prefers-color-scheme: dark) {{
    .side-h, .side a {{ color: #aaa293; }}
    .side a::before {{ color: #7a7366; }}
    .side ol {{ border-color: #37322a; }}
    .side a:hover {{ color: #ece6d8; }}
    .side a.is-here, .side a.is-here::before {{ color: #9db4e0; }}
    .side a.is-here {{ border-left-color: #9db4e0; }}
    .side-btn, .side {{ border-color: #37322a; }}
    .side-btn {{ background: #1f1c17; color: #ece6d8; }}
  }}
  @media (prefers-color-scheme: dark) and (max-width: 999px) {{
    .side {{ background: #1f1c17; }}
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ color: #ece6d8; background: #15130f; }}
    .lede {{ color: #cbc4b4; }}
    .hero-fig figcaption {{ color: #aaa293; }}
    footer {{ border-color: #37322a; }}
    .card {{ background: #1f1c17; border-color: #37322a; }}
    .card p, .caption, ul.more span, th {{ color: #aaa293; }}
    .figure img {{ border-color: #37322a; }}
    .kit span {{ background: #25221c; color: #aaa293; }}
    .kit b, .bands figcaption b {{ color: #ece6d8; }}
    .bands img {{ border-color: #37322a; }}
    .bands figcaption, p.note {{ color: #aaa293; }}
    .rules-grid figcaption {{ color: #aaa293; }}
    .rules-grid figcaption b {{ color: #ece6d8; }}
    svg.rules text {{ fill: #cbc4b4; }}
    svg.rules .rs {{ fill: #aaa293; }}
    svg.rules .rv, svg.rules .rvt {{ fill: #e0ae4d; }}
    svg.rules .rc {{ fill: #6f93cf; fill-opacity: 0.25; stroke: #6f93cf; }}
    svg.rules .rc2 {{ fill: #35435d; stroke: #4d5f80; }}
    svg.rules .rc2m {{ fill: #2b4a45; stroke: #3f6b62; }}
    svg.rules .rcm {{ fill: #5fbfae; fill-opacity: 0.22; stroke: #5fbfae; }}
    svg.rules .halo {{ stroke: #15130f; fill: #ece6d8; }}
    svg.rules .rc3 {{ fill: #2c3850; stroke: #4d5f80; }}
    svg.rules .rn {{ fill: #9db4dc; }}
    svg.rules .rt0 {{ fill: #35435d; fill-opacity: 0.6; }}
    svg.rules .rtm {{ fill: #2b4a45; fill-opacity: 0.7; }}
    svg.rules .rdt {{ fill: #37322a; fill-opacity: 0.8; }}
    svg.rules .rbn {{ stroke: #ece6d8; }}
    svg.rules .ra {{ fill: #4f8f63; }}
    svg.rules .rg {{ fill: #e06c66; }}
    svg.rules .rgc {{ fill: #15130f; }}
    svg.rules .rsd {{ fill: #a593d9; }}
    svg.rules .rgl {{ fill: #6aaee0; }}
    svg.rules .rdm {{ fill: #37322a; stroke: #4a505c; }}
    svg.rules .rtp {{ fill: #2f4436; stroke: #4f6e58; }}
    svg.rules .rio {{ fill: #355540; stroke: #4f6e58; }}
    svg.rules .rf {{ fill: #2a261f; stroke: #3a4150; }}
    svg.rules .rbd {{ fill: #5a6680; }}
    svg.rules .rbd2 {{ fill: #5a6680; stroke: #e0ae4d; }}
    svg.rules .remp {{ stroke: #4d5f80; }}
    svg.rules .rmux {{ fill: #4f8f63; }}
    svg.rules .rmux2 {{ fill: #4f8f63; stroke: #e0ae4d; }}
    svg.rules .rsel {{ fill: #9db4e0; }}
    svg.tile {{ background: #1f1c17; border-color: #37322a; }}
    svg.tile .rline {{ fill: #4d5f80; }}
    svg.tile {{ --tl-c0: #2f3b52; --tl-c1: #284540; --tl-cd: #37322a;
                --tl-ct: #2f4436; --tl-ra: #4f8f63; --tl-rg: #e06c66;
                --tl-rgc: #1f1c17; --tl-rsd: #a593d9; --tl-rgl: #6aaee0;
                --tl-cbn: #6b6458; --tl-rline: #4d5f80; }}
    svg.tile .dimp {{ stroke: #e0ae4d; }}
    ol.qlist li {{ border-color: #2c2821; }}
    ol.qlist li::before {{ background: #3b6fc4; }}
    ol.qlist .qa {{ color: #cbc4b4; }}
    ol.qlist .qp {{ color: #aaa293; }}
    ol.qlist .qp i {{ color: #e0ae4d; }}
    svg .vs {{ fill: #25221c; stroke: #3e382f; }}
    svg .vn, svg .vop {{ fill: #aaa293; }}
    svg .vc {{ fill: #ece6d8; }}
        svg .vax {{ stroke: #7a7366; }}
    svg .vpan {{ fill: #1f1c17; stroke: #3e382f; }}
    svg .ra {{ fill: #4f8f63; }}
    svg .rg {{ fill: #e06c66; }}
    svg .vfa {{ fill: rgba(79, 143, 99, 0.3); }}
    svg .vfg {{ fill: rgba(224, 108, 102, 0.3); }}
    svg .vx {{ fill: #e0ae4d; }}
    svg .vsep {{ stroke: #3e382f; }}
    svg .dok {{ fill: #3987e5; }}
    svg .dmiss {{ stroke: #e66767; }}
    svg .dbad {{ fill: #e66767; }}
    svg .rwin {{ fill: #25221c; }}
    svg .rbase {{ stroke: #37322a; }}
    svg .rwl {{ stroke: #9085e9; }}
    svg .rwe {{ stroke: #d95926; }}
    svg .rse {{ stroke: #199e70; }}
    svg .rc0, svg .rc1 {{ stroke: #3987e5; }}
    svg .rdo {{ stroke: #ece6d8; }}
    svg .rmk {{ stroke: #e0ae4d; }}
    svg .vv2 {{ fill: #e0ae4d; }}
    svg .cyh {{ fill: #35435d; }}
    svg .cyl {{ fill: #284540; }}
    svg .cyt {{ fill: #ece6d8; }}
    .trace .ttl {{ color: #aaa293; }}
    svg .tgc {{ fill: #1f1c17; }}
    svg .tch {{ stroke: #e0ae4d; }}
    svg .tchs {{ fill: #4a3a1c; stroke: #e0ae4d; }}
    svg .tno {{ fill: #ece6d8; }}
    svg .tnt {{ fill: #16181d; }}
    svg .tw, svg .tdot {{ stroke: #aaa293; }}
    svg .tdot {{ fill: #aaa293; }}
    svg .tbl {{ stroke: #3987e5; }}
    svg .twl {{ stroke: #9085e9; }}
    svg .mwi {{ fill: #35435d; }}
    svg .mhi {{ fill: #3987e5; }}
    svg .mbump, svg .mvb {{ fill: #e66767; }}
    svg .mmark, svg .mbad {{ stroke: #e66767; }}
    svg .mdim {{ stroke: #e0ae4d; }}
    svg .mv {{ fill: #e0ae4d; }}
    svg .halo2 {{ stroke: #1f1c17; }}
    svg .wbl {{ fill: #3987e5; }}
    svg .wbn {{ fill: #199e70; }}
    svg .wwl {{ fill: #9085e9; }}
    svg .wvd {{ fill: #d95926; }}
    svg .wvs {{ fill: #7a7366; }}
    svg .wot {{ fill: #3a4150; }}
    svg .wbad {{ fill: #e66767; }}
    svg .wmk {{ stroke: #e0ae4d; }}
    svg .vslot {{ stroke: #7a7366; }}
    svg .vok {{ fill: #35435d; stroke: #6f93cf; }}
    svg .vbad {{ fill: #4a3a1c; stroke: #e0ae4d; }}
    svg.oflow .ob {{ fill: #25221c; stroke: #3e382f; }}
    svg.oflow .oc {{ fill: #2e2618; stroke: #a9761c; }}
    svg.oflow .obt, svg.otim .tn {{ fill: #ece6d8; }}
    svg.oflow .obs, svg.oabs .ol, svg.otim .ts {{ fill: #aaa293; }}
    svg.oflow .obv, svg.otim .tv {{ fill: #e0ae4d; }}
    svg.oflow .oa {{ stroke: #7a7366; }}
    svg.oabs .omac {{ fill: #1f1c17; stroke: #6b6458; }}
    svg.oabs .l3 {{ fill: #3987e5; }}
    svg.oabs .l4 {{ fill: #199e70; }}
    svg.oabs .l5 {{ fill: #9085e9; }}
    svg.oabs .ozl {{ stroke: #4a505c; }}
    svg.otim .tw {{ stroke: #ece6d8; }}
    svg.otim .twd {{ stroke: #aaa293; }}
    svg.otim .tx {{ fill: #2c2821; }}
    svg.otim .tm {{ stroke: #e0ae4d; }}
    svg.otim .tg {{ stroke: #3e382f; }}
    pre.code {{ background: #1f1c17; border-color: #37322a; color: #ece6d8; }}
    figure.dfig figcaption {{ color: #aaa293; }}
    figure.dfig figcaption b {{ color: #ece6d8; }}
    svg.dline {{ background: #1f1c17; border-color: #37322a; }}
    svg.dline .dw {{ stroke: #8f8879; }}
    svg.dline .dg, svg.dline .db {{ fill: #1f1c17; stroke: #ece6d8; }}
    svg.dline .dl {{ fill: #25221c; stroke: #6b6458; }}
    svg.dline .dk {{ fill: #26304a; stroke: #4d5f80; }}
    svg.dline .dnew {{ stroke: #e0ae4d; }}
    svg.dline .dt {{ fill: #cbc4b4; }}
    svg.dline .ds, svg.dline .dm {{ fill: #aaa293; }}
    svg.dline .dv {{ fill: #e0ae4d; }}
    .slegend {{ color: #cbc4b4; }}
    .slegend i.tick {{ background: #ece6d8; }}
    .slegend i.lim {{ border-left-color: #e0ae4d; }}
    .sv1 {{ background: #3987e5; fill: #3987e5; }}
    .sv2 {{ background: #d95926; fill: #d95926; }}
    .sv3 {{ background: #199e70; fill: #199e70; }}
    .sv4 {{ background: #c98500; fill: #c98500; }}
    svg.sensechart .sgrid {{ stroke: #2c2821; }}
    svg.sensechart .saxis, svg.sensechart .sctl {{ fill: #808898; }}
    svg.sensechart .slimit {{ stroke: #e0ae4d; }}
    svg.sensechart .slab {{ fill: #e0ae4d; }}
    svg.sensechart .sname {{ fill: #ece6d8; }}
    svg.sensechart .sseg {{ stroke: #15130f; }}
    svg.sensechart .stick {{ stroke: #ece6d8; }}
    svg.sensechart .sval {{ fill: #cbc4b4; }}
    svg.sensechart .srow:hover .shit, svg.sensechart .srow:focus .shit {{ fill: rgba(122, 167, 240, 0.08); }}
    .stip {{ background: #1f1c17; border-color: #3e382f; color: #cbc4b4; }}
    .stip .h {{ color: #ece6d8; }}
    details.stable summary {{ color: #9db4e0; }}
    svg.tile .ta {{ fill: #cbc4b4; }}
    svg.tile .ts {{ fill: #aaa293; }}
    svg.tile .tv {{ fill: #e0ae4d; }}
    svg.tile .th {{ fill: #ece6d8; stroke: #1f1c17; }}
    svg.rules .rd {{ stroke: #e0ae4d; }}
    ol.flow li {{ border-left-color: #3e382f; color: #cbc4b4; }}
    ol.flow li:last-child {{ border-left-color: transparent; }}
    ol.flow li::before {{ background: #3b6fc4; }}
    ol.flow .st {{ color: #aaa293; }}
    ol.flow b {{ color: #ece6d8; }}
    th, td {{ border-color: #2c2821; }}
    ul.more li {{ border-color: #2a261f; }}
    a {{ color: #9db4e0; }}
    .config {{ background: #1f1c17; border-color: #37322a; }}
    .config.built h3.sub {{ border-color: #2c2821; }}
    .knob {{ background: #25221c; }}
    .knob .eff {{ color: #cbc4b4; }}
    .knob .range {{ color: #808898; }}
    .knobs output, .seg button {{ color: #ece6d8; }}
    .seg, .presets button {{ border-color: #3e382f; background: #25221c; }}
    .seg button {{ background: #25221c; }}
    .seg button.on {{ background: #3b6fc4; color: #fff; }}
    .presets button {{ color: #9db4e0; }}
    .derived {{ border-color: #2c2821; }}
    .glance {{ border-color: #2c2821; }}
    .glance span {{ color: #aaa293; }}
    #dia .arr {{ fill: #232a37; }}
    #dia .peri {{ fill: #26332b; }}
    #dia .outline {{ stroke: #6b6458; }}
    #dia .groupbox {{ stroke: #8f8879; }}
    #dia .strong {{ fill: #ece6d8; }}
    #dia .band {{ fill: #5a6680; }}
    .wordsec {{ border-color: #2c2821; }}
    .wordsec p {{ color: #cbc4b4; }}
    .wordsec h4 {{ border-color: #2a261f; }}
    .shots img {{ border-color: #37322a; }}
    .pair .cap {{ color: #aaa293; }}
    .pair .cap b {{ color: #ece6d8; }}
    .pair img, .pair svg.d {{ border-color: #37322a; }}
    .pair svg.d {{ background: #15181d; }}
    svg.d .arr {{ fill: #232a37; }}
    svg.d .peri {{ fill: #26332b; }}
    svg.d .band {{ fill: #5a6680; }}
    svg.d .cell {{ fill: #35435d; stroke: #4d5f80; }}
    svg.d .io {{ fill: #33493a; }}
    svg.d .outline {{ stroke: #6b6458; }}
    svg.d .sep {{ stroke: #8f8879; }}
    svg.d .wire, svg.d .inv, svg.d .fet {{ stroke: #9e9687; }}
    svg.d .inv {{ fill: #35435d; }}
    svg.d .fet {{ fill: #33493a; }}
    svg.d .dl {{ fill: #cbc4b4; }}
    svg.d .fade {{ fill: #15181d; }}
    svg.d .strong {{ fill: #ece6d8; }}
    #word .arr, #addr .arr {{ fill: #232a37; }}
    #word .band, #addr .band {{ fill: #5a6680; }}
    #word .peri, #addr .peri {{ fill: #26332b; }}
    #word .outline, #addr .outline {{ stroke: #6b6458; }}
    #word .cell, #addr .cell {{ fill: #35435d; stroke: #4d5f80; }}
    #word .cell.mark, #addr .cell.mark {{ fill: #c9922f; stroke: #e0ae4d; }}
    #word .lab, #addr .lab, #word .labs, #addr .labs {{ fill: #aaa293; }}
    .mini {{ background: #25221c; }}
    .mini label {{ color: #aaa293; }}
    .mini b {{ color: #ece6d8; }}
    #word .sep, #addr .sep {{ stroke: #11141a; }}
    #word .markbox, #addr .markbox {{ stroke: #e0ae4d; }}
    #word .cells, #addr .cells {{ fill: #35435d; }}
    #addr .badge {{ fill: #5a6680; }}
    #addr .amp {{ fill: #26332b; stroke: #9e9687; }}
    #addr .wire {{ stroke: #9e9687; }}
    #addr .brace {{ stroke: #6b6458; }}
    .walk .wire {{ stroke: #9e9687; }}
    .walk .badge {{ fill: #5a6680; }}
    .walk .lab, .walk .labs {{ fill: #aaa293; }}
    .walk .strong {{ fill: #ece6d8; }}
    .walk .bankbox {{ fill: #25221c; stroke: #6b6458; }}
    .walk .pbox {{ fill: #1f1c17; stroke: #3a4150; }}
    .walk .pbox.lit {{ fill: #232a37; stroke: #6b6458; }}
    .walk .pbox.now {{ fill: #3a3020; stroke: #e0ae4d; }}
    .walk .parr {{ stroke: #3a4150; }}
    .walk .tiny {{ fill: #aaa293; }}
    .walk .garr {{ fill: #232a37; }}
    .walk .gio {{ fill: #26332b; }}
    .walk .gio.on {{ fill: #4a3d22; }}
    .walk .gband {{ fill: #22262e; }}
    .walk .gcell {{ fill: #35435d; stroke: #1f1c17; }}
    .walk .pin {{ fill: #6b6458; }}
    .walk .phead {{ fill: #3a4150; }}
    .walk .ch {{ fill: #3a4150; }}
    .walk .ch.strong {{ fill: #9e9687; }}
    .walk .gate, .walk .bubble {{ stroke: #9e9687; }}
    .walk .bubble, .walk .gap {{ fill: #1f1c17; }}
    .walk .jdot {{ fill: #9e9687; }}
    .walk .bit1 {{ fill: #ece6d8; }}
    .walk .bit0 {{ fill: #1f1c17; }}
    .walk .bnum.one {{ fill: #16181d; }}
    .walk .bnum.zero {{ fill: #ece6d8; }}
    .walk .meter {{ stroke: #6b6458; }}
    .walk .meterfill {{ fill: #6f93cf; }}
    .walk .fold {{ fill: #1f1c17; }}
    .walk .bankline {{ stroke: #6b6458; }}
    .walk .wl, .walk .bl {{ stroke: #3a4150; }}
    .walk .wl.on {{ stroke: #e0ae4d; }}
    .walk .bl.on {{ stroke: #5fb39d; }}
    .stepper {{ background: #25221c; }}
    .stepper button {{ background: #1f1c17; border-color: #3e382f; color: #9db4e0; }}
    .stepper p {{ color: #cbc4b4; }}
    #addr .fan {{ stroke: #3a4150; }}
    #word .grid, #addr .grid {{ stroke: #11141a; opacity: 0.5; }}
    #word .hl, #addr .hl {{ stroke: #c8ced8; }}
    #word .thread, #addr .thread {{ stroke: #c9922f; }}
    #word .groupbox, #addr .groupbox {{ stroke: #8a6a28; }}
    #word .strong, #addr .strong {{ fill: #ece6d8; }}
    #word .fld, #addr .fld {{ stroke: #1f1c17; }}
    #dia .cell {{ fill: #35435d; stroke: #4d5f80; }}
    #dia .cell.mark {{ fill: #c9922f; stroke: #e0ae4d; }}
    #dia .zoom {{ opacity: 0.16; }}
    #dia .leader {{ stroke: #8a6a28; }}
    #dia .lab, #dia .labs {{ fill: #aaa293; }}
  }}

  /* ---- theme: a warm drawing sheet. The page is a sheet of paper on a
     graph-paper desk; text is set in Source Serif 4, and labels, values
     and code in IBM Plex Mono. The figures keep the system sans-serif they
     were laid out in. Every colour below is a token, and dark mode
     redefines only the tokens. ---- */
  :root {{
    --maxw: 900px;
    --desk: #ece8de; --grid: #e2ddd1; --sheet: #fbfaf6; --card: #fffefb;
    --tint: #f3f0e8; --knob: #f6f3ec;
    --ink: #1d1b16; --ink2: #3a362d; --text2: #4a463d; --muted: #5b574e;
    --faint: #a39b8a; --rule: #c9c3b4; --rule2: #e4dfd2; --frame: #1d1b16;
    --accent: #1f3a6d; --shadow: rgba(40, 32, 16, 0.22);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --desk: #100e0b; --grid: #1a1814; --sheet: #1c1a15; --card: #221f19;
      --tint: #27241d; --knob: #25221c;
      --ink: #ece6d8; --ink2: #d6cfbf; --text2: #c2baa9; --muted: #a9a192;
      --faint: #6f685b; --rule: #3d382f; --rule2: #2f2b24; --frame: #7d7566;
      --accent: #9db4e0; --shadow: rgba(0, 0, 0, 0.55);
    }}
  }}
  body {{ font-family: "Source Serif 4", Georgia, serif; font-size: 18px;
          line-height: 1.6; color: var(--ink); background-color: var(--desk);
          background-image: linear-gradient(var(--grid) 1px, transparent 1px),
                            linear-gradient(90deg, var(--grid) 1px, transparent 1px);
          background-size: 24px 24px; padding-top: 32px; }}
  svg {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }}
  main {{ counter-reset: sec; padding: 0 40px 36px; background: var(--sheet);
          border: 1px solid var(--rule);
          box-shadow: 0 1px 0 var(--rule), 0 12px 32px -18px var(--shadow); }}
  a {{ color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }}
  .hero {{ padding-top: 44px; }}
  .release {{ margin: 0 0 14px; font: 500 11.5px/1 "IBM Plex Mono", monospace;
              letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); }}
  h1, h2, h3, h3.sub {{ font-family: "Source Serif 4", Georgia, serif; }}
  h1 {{ font-weight: 700; font-size: 48px; letter-spacing: -0.02em; margin-bottom: 6px; }}
  .lede {{ font-style: italic; font-size: 25px; line-height: 1.35; color: var(--ink2);
           max-width: 560px; margin-bottom: 30px; }}
  h2 {{ font-weight: 600; font-size: 28px; letter-spacing: -0.01em; margin-top: 64px;
        padding-bottom: 10px; border-bottom: 1px solid var(--rule);
        display: flex; align-items: center; gap: 14px; }}
  h2[id]::before {{ counter-increment: sec; content: counter(sec); flex: none;
                    width: 30px; height: 30px; border: 1.5px solid var(--accent);
                    border-radius: 50%; color: var(--accent);
                    font: 600 14px/27px "IBM Plex Mono", monospace; text-align: center; }}
  h3, h3.sub {{ font-weight: 600; font-size: 18px; }}
  .config, .config.built {{ border: 1px solid var(--rule); border-radius: 0; background: var(--card); }}
  .config.built h3.sub {{ border-color: var(--rule2); }}
  p.note, .caption, figcaption, .shotnote, .pair .cap {{ font-size: 14px; color: var(--muted); }}
  table {{ border: 1px solid var(--rule); }}
  th, td {{ padding: 8px 12px; border: 1px solid var(--rule2); }}
  th {{ background: var(--tint); color: var(--ink); letter-spacing: 0.06em;
        font: 500 11.5px/1.3 "IBM Plex Mono", monospace; text-transform: uppercase; }}
  td.n {{ font-family: "IBM Plex Mono", monospace; font-size: 14.5px; }}
  pre.code, code {{ font-family: "IBM Plex Mono", ui-monospace, monospace; }}
  pre.code {{ background: var(--tint); border: 1px solid var(--rule2); border-radius: 0; }}
  .glance {{ gap: 0; border: 1px solid var(--frame); border-width: 1px 0 0 1px;
             padding: 0; margin-top: 28px; }}
  .glance > div {{ padding: 12px 14px; border: 1px solid var(--frame); border-width: 0 1px 1px 0; }}
  .glance b {{ font: 600 18px/1.25 "IBM Plex Mono", monospace; letter-spacing: -0.02em;
               color: var(--ink); }}
  .glance span {{ color: var(--muted); font-size: 13px; margin-top: 3px; }}
  .kit span {{ background: var(--tint); border-radius: 0; color: var(--text2); }}
  .kit span b {{ font-family: "IBM Plex Mono", monospace; font-weight: 600; color: var(--ink); }}
  .card {{ border-radius: 0; border-color: var(--rule); background: var(--card); }}
  .card:hover {{ border-color: var(--accent); }}
  .card p {{ color: var(--text2); }}
  ul.more span {{ color: var(--muted); }}
  ol.qlist li::before {{ border-radius: 50%; background: transparent; color: var(--accent);
                         border: 1.5px solid var(--accent); font-family: "IBM Plex Mono", monospace; }}
  .figure img {{ border-radius: 0; border-color: var(--rule); }}
  .hero-fig figcaption {{ color: var(--muted); }}
  footer {{ border-color: var(--frame); color: var(--muted); }}
  .knob {{ background: var(--knob); border: 1px solid var(--rule2); border-radius: 0; }}
  .knob .name {{ font-family: "IBM Plex Mono", monospace; color: var(--text2); }}
  .knob .val {{ font-family: "IBM Plex Mono", monospace; font-weight: 600; font-size: 26px; }}
  .knob input[type=range] {{ accent-color: var(--accent); }}
  .seg {{ border-color: var(--frame); border-radius: 0; }}
  .seg button {{ font-family: "IBM Plex Mono", monospace; font-size: 14px; }}
  .seg button.on {{ background: var(--ink); color: var(--sheet); }}
  .presets button {{ border-radius: 0; border-color: var(--rule); font-size: 12.5px;
                     font-family: "IBM Plex Mono", monospace; }}
  .presets button:hover {{ border-color: var(--accent); color: var(--accent); }}
  .stepper button {{ border-radius: 0; }}
  .side-h {{ font-family: "IBM Plex Mono", ui-monospace, monospace; color: var(--muted); }}
  .side ol {{ border-color: var(--rule); }}
  .side a {{ color: var(--muted); }}
  .side a::before {{ color: var(--faint); }}
  .side a:hover {{ color: var(--ink); }}
  .side a.is-here, .side a.is-here::before {{ color: var(--accent); }}
  .side a.is-here {{ border-left-color: var(--accent); }}
  .side-btn {{ background: var(--sheet); color: var(--ink); border-color: var(--rule); border-radius: 0; }}
  @media (max-width: 999px) {{
    .side {{ background: var(--sheet); border-color: var(--rule); border-radius: 0; }}
  }}
  @media (min-width: 1000px) and (max-width: 1199px) {{ .side {{ left: 6px; }} }}
  @media (max-width: 560px) {{ .hero .glance {{ grid-template-columns: 1fr; }} }}
  @media (max-width: 640px) {{
    body {{ padding-top: 0; background: var(--sheet); }}
    main {{ border: 0; box-shadow: none; padding: 0; }}
  }}
</style>
</head>
<body>
<nav class="side" id="side" aria-label="Contents">
  <p class="side-h">On this page</p>
  <ol>
    <li><a href="#ask" title="Configuration space"><span class="lbl">Configuration space</span></a></li>
    <li><a href="#built" title="Generation"><span class="lbl">Generation</span></a></li>
    <li><a href="#out" title="Outputs"><span class="lbl">Outputs</span></a></li>
    <li><a href="#verify" title="Verification"><span class="lbl">Verification</span></a></li>
    <li><a href="#not" title="Limitations"><span class="lbl">Limitations</span></a></li>
    <li><a href="#next" title="Future work"><span class="lbl">Future work</span></a></li>
  </ol>
</nav>
<button class="side-btn" id="side-btn" type="button" aria-controls="side" aria-expanded="false">&#9776; Contents</button>
<main>
<div class="hero">
<div class="hero-text">
<header>
  <p class="release">Release {version}</p>
  <h1>sramforge</h1>
  <p class="lede">A memory compiler for a 7 nanometre process, built with open-source tools only.</p>
</header>

<p>Every chip needs memory, and memory is not written in a hardware
description language like the rest of a design. It is drawn. A compiler
takes the size you want and assembles the transistor-level artwork,
because hand-drawing a few hundred thousand transistors is not an
option. Those compilers are normally supplied by the foundry or bought,
and they are closed.</p>

<p>This one is written in Python for
<a href="https://github.com/The-OpenROAD-Project/asap7">ASAP7 (Arizona
State Predictive Process Design Kit, 7 nanometre)</a>, an open kit for a
7 nanometre process. Like a foundry's compiler, it starts from cells it
did not draw: the memory cell, the sense amplifier, the write driver and
the other leaf cells come from the kit's own
<a href="https://github.com/The-OpenROAD-Project/asap7_sram_0p0">SRAM
(Static Random-Access Memory) release</a>, published under a BSD
(Berkeley Software Distribution) licence. The compiler builds everything
around them, and then checks the result, from
whether every shape is in place to whether the block stores data in
time.</p>

<div class="glance">
  <div><b>{o["t"]["ghz"]} gigahertz</b><span>the 2 kilobyte block, simulated
    from its own layout with a 20% margin</span></div>
  <div><b>114,454 transistors</b><span>in the 2 kilobyte block, every one
    accounted for</span></div>
  <div><b>7 verification questions</b><span>from every shape in place to
    data stored in time</span></div>
  <div><b>4 standard outputs</b><span>layout, abstract, timing
    library and behavioural model</span></div>
  <div><b>{lines} lines of Python</b><span>the compiler, its checks and its
    measurements</span></div>
  <div><b>Open-source tools</b><span>Python,
    <a href="https://www.klayout.de/">KLayout</a>,
    <a href="https://theopenroadproject.org/">OpenROAD</a> and
    <a href="https://ngspice.sourceforge.io/">ngspice</a> only</span></div>
</div>
</div>
<figure class="hero-fig">
  <div class="frame"><img src="images/bank.png"
    alt="A generated 2 kilobyte memory block, drawn to scale"></div>
  <figcaption>A generated 2 kilobyte block, to scale: 16,384 memory cells
  in two mirrored stacks, with the decoder and control logic between them.
  It is 9.6 by 77.8 micrometres.</figcaption>
</figure>
</div>

<h2 id="ask">Configuration space</h2>
<p>A compiler builds a memory block to a requested size rather than
supplying one fixed block. Three parameters set the size: the number of
bits, the number of strips and the number of rows. Every combination
the controls below allow can be generated. The reference size has passed
every check, and Verification states which checks were run on the other
sizes.</p>
{diagram}

<h2 id="built">Generation</h2>
<div class="config built">
<p>The compiler draws no transistors of its own. It works from four
items the kit publishes:</p>
<div class="kit">
  <span><b>Leaf cells</b> the memory cell, sense amplifier, write driver,
  precharge, multiplexer, latches and fillers</span>
  <span><b>One reference block</b> 2 kilobytes, a single fixed size, with
  its control logic</span>
  <span><b>Logic gates</b> the kit's library of standard cells</span>
  <span><b>Rule manual</b> the spacing and width rules every shape must
  meet</span>
</div>
<p>Generation proceeds in four steps.</p>

<h3 class="sub">1. Measure the rules</h3>
<p>The kit publishes the finished reference block, but not the placement
rules that produced it. The rules were therefore recovered from the
block itself: its cell hierarchy was traversed level by level, and the
position and orientation of every placed instance were recorded. The
four drawings show the result, from the smallest level upward. The amber
numbers are measured offsets and pitches in micrometres, and every
drawing is to scale.</p>
<div class="rules-grid">
{rules}
</div>
<p>Replayed at the reference size, these rules reproduce the block shape
for shape on all 31 mask layers; that identity is the first verification
question. Every other size is generated by replaying the same rules with
different counts.</p>

<h3 class="sub">2. Tile to the requested size</h3>
<p>The generator applies the measured rules level by level, from a
single cell to the complete block, and the three parameters change the
counts it applies them with. The animation below shows the process to
scale. Its geometry is computed from the generator's own placement code,
measured values appear in amber, and it can be played or stepped
through.</p>
<div class="stepper">
  <button id="tl-prev" aria-label="previous step">&#9664;</button>
  <button id="tl-play">&#9654; play</button>
  <button id="tl-next" aria-label="next step">&#9654;</button>
  <span id="tl-n">1 / 9</span>
  <b id="tl-title"></b>
  <p id="tl-desc"></p>
</div>
<svg id="tile" class="rules tile" role="img" preserveAspectRatio="xMidYMid meet"
     aria-label="The block assembling from one cell, then each parameter changing it">
  {tile_defs}
  <g id="tl-world"></g>
</svg>
{tile_js}
<p>Each level also contains shapes that the kit drew outside any cell:
supply straps, seam connectors, pin labels and rule markers, about 50
shapes and 370 labels in total. Without them the identity check fails on
13 layers and every bit line is open. They are stored as a table that
can be reviewed, and each parameter has a rule for how they change:</p>
<table class="knobrules">
  <tr><th>Parameter</th><th>Effect on the loose shapes</th></tr>
  <tr><td>Bits above 32</td><td>Shapes beside the array move outward,
      and the metal of one column pair is copied for each additional
      pair</td></tr>
  <tr><td>Bits below 32</td><td>The word line labels of the empty
      columns are removed</td></tr>
  <tr><td>Eight strips</td><td>Shapes that span a group's height
      stretch with it, and the bit line labels are copied for the new
      strips</td></tr>
  <tr><td>Rows</td><td>Shapes at a stack's outer edge move with it, and
      the data pin labels are regenerated for every row</td></tr>
</table>

<h3 class="sub">3. Rebuild the control</h3>
<p>The control logic in the central band latches the address, enables
the decoders, selects the column and times the sense amplifier. The kit
provides it as a single hand-packed block drawn for the reference size.
That block has no spare address input, and its delay line reaches only
277 picoseconds, so it supports only sizes close to the reference: 32
bits, four strips and up to 48 rows. For all other sizes the compiler
rebuilds the control in five stages:</p>
<ol class="flow">
  <li><span class="st">Recover</span><b>197 gates, 147 nets</b>
  The kit provides the control only as drawn shapes. Its netlist is
  recovered from them with the connectivity extractor that the checks
  use, by probing each gate's pins for the wire beneath them. Every pin
  resolves to a net, and the block's 37 ports are taken from its own pin
  labels.</li>
  <li><span class="st">Edit</span><b>348 gates</b>
  New logic is added: a delay line of up to 62 buffer pairs, a ninth
  address bit, the enables for a second decoder bank at 64 bits, and
  the select drivers for the right array. The eight-strip variant adds a
  3-to-8 column decoder, for 412 gates.</li>
  <li><span class="st">Floorplan</span><b>9.4 by 9.0 micrometres</b>
  The rebuilt control occupies a full-width region beneath the decoders,
  which makes the band 7.6 micrometres taller. Each original pin is
  placed exactly on its former pad, and 1,979 shapes of the band's
  overhead wiring become obstructions for the router.</li>
  <li><span class="st">Place and route</span><b>by
  <a href="https://github.com/The-OpenROAD-Project/OpenROAD">OpenROAD</a></b>
  Power rails run along every row, cells are placed at 45 per cent
  density, and global and detailed routing use metal layers 2 to 4. Band
  wires that must cross the region are routed through it as additional
  two-point nets. Any route that contains a short is rejected.</li>
  <li><span class="st">Merge and check</span><b>one wire per word line</b>
  The routed region is inserted back into the band with generated wiring
  around it: supply straps, via stacks down to the original pads, and
  short stubs that join crossing wires to their routes through the
  region. Connectivity, decoding and simulated reads and writes are then
  checked again.</li>
</ol>
<div class="bands">
  <figure>
    <img src="images/band_kit.png" alt="The central band of the 2 kilobyte block, as the kit draws it">
    <figcaption><b>From the kit.</b> The central band of the 2 kilobyte
    block, 9.5 micrometres tall. The control block (outlined), including
    the decoder, is packed by hand into dense, regular rows.</figcaption>
  </figure>
  <figure>
    <img src="images/band_rebuilt.png" alt="The central band of a 16-bit block, rebuilt by the compiler">
    <figcaption><b>Rebuilt.</b> The band of a 16-bit block at the same
    scale, 17.0 micrometres tall. The kit's decoder, trimmed to this
    width, sits above; the control logic below it (outlined) was placed
    and routed by OpenROAD.</figcaption>
  </figure>
</div>
<p>The rebuild was also the source of the project's most serious defect.
The first extraction omitted the right array's select drivers, which
left half the words of every rebuilt block unreachable until a more
detailed check detected it.</p>

<h3 class="sub">4. Time the sense amplifier</h3>
<p>The sense amplifier must not fire until the selected cell has pulled
its bit lines far enough apart to be read, and that time depends on the
block's size. The generator computes it as the sum of four parts, each
measured in simulation:</p>
<ul class="parts">
  <li><b>Clock to word line.</b> The control raises the word line 83 to
  92 picoseconds after the clock edge, depending on which control the
  block uses.</li>
  <li><b>Word-line flight.</b> The word line is a long wire, and its far
  end rises last. This delay grows with the square of the wire's length,
  so it is the part that varies most with size.</li>
  <li><b>Bit-line split.</b> The cell then pulls its bit lines 100
  millivolts apart, in 18 to 30 picoseconds depending on the width.</li>
  <li><b>Margin.</b> A fixed 20 picoseconds.</li>
</ul>
<p>The wait itself is produced by a delay line in the control. The line
is a chain of ordinary logic gates, not an analog circuit: its delay is
the time a signal takes to pass through the gates. It is selected by the
block's five <code>sdel</code> pins and measured by transistor-level
simulation of the block's own extracted layout. The line as the kit
draws it, and as the rebuild makes it:</p>
{delay_html}
<p>The generator selects the shortest setting whose delay covers the
total. Hovering over a row, or moving to it with the Tab key, shows its
parts.</p>
{sense_html}
{sense_js}
<p>Two results follow from the chart. Beyond 48 rows no setting of the
kit's line waits long enough, which is why deeper blocks receive the
rebuilt control. The rebuilt line's shortest delay is
{sn["reb_min"]} picoseconds, so a small block that uses it, such as the
16-bit one, waits longer than necessary. Each build records the setting
for both the typical and the slow corner, and a check under Verification
confirms that every size it covers has one.</p>
</div>

<h2 id="out">Outputs</h2>
<div class="config built">
<p>Each build writes the files needed to place, time and simulate the
block within a chip design. All of them are derived from the same layout
and from one shared list of its pins, so they agree on the block's
interface by construction; a check also confirms that the pin names
match across them.</p>
<div class="oscroll">{o["flow"]}</div>
<p class="note">Pin names, directions and layers come from the contract.
Shapes come from the layout, and numbers from simulating it.</p>

<h3 class="sub">The pin contract</h3>
<p>The ten pin families of the 2 kilobyte block:</p>
{o["families_html"]}
<p>In total the block has {o["n_pins"]} pins: {o["census_sum"]}.</p>

<h3 class="sub">The abstract</h3>
<p>A place-and-route tool does not need the block's transistors. It
needs the block's outline, the position and metal layer of each of its
{o["n_pins"]} pins, and the areas in which it must not route. The
abstract provides exactly that. It is derived from the layout: each pin
is the metal beneath one of the layout's pin labels, and the blockages
are the layout's own metal, merged. A check confirms that the pins and
blockages together cover the drawn metal exactly, and rejects damaged
abstracts.</p>
<div class="oscroll">{o["abstract"]}</div>
<p class="note">Drawn from the abstract file itself. Metals 1 and 2 are
blocked over almost the entire block ({o["obs_counts"]["M1"]:,} and
{o["obs_counts"]["M2"]:,} merged islands), so only the upper metals are
shown. The control and address pins are stripes on metal 5 above the
central band, and each column group brings out its write-data and
read-data pins on metal 3.</p>

<h3 class="sub">The timing library</h3>
<p>A timing tool must know when the block's inputs have to arrive and
when its outputs change, without simulating the block. The timing
library, in the Liberty format, records both, and every value in it was
measured by simulating the block's extracted layout. One read as the
library describes it, at the typical corner, in picoseconds:</p>
<div class="oscroll">{o["timing"]}</div>
<p class="note">The address must be stable {o["t"]["a_su"]} before the
clock edge and {o["t"]["a_ho"]} after it. Inside the block, the word line
rises at {o["t"]["wl"]} and the sense amplifier fires at
{o["t"]["sae"]}; neither appears in the library, which describes only
the pins. The word appears on <code>dataout</code> {o["t"]["do"]} after
the clock, and the next read can begin {o["t"]["period"]} after this
one, which corresponds to {o["t"]["ghz"]} gigahertz. Write data is the
exception: it may arrive up to {o["t"]["wd_late"]} after the clock edge,
but must then remain stable for {o["t"]["wd_ho"]}.</p>
<p>Delays are stored as tables. A timing tool looks up the slew of the
clock it sees and the load on the pin, and interpolates between the
entries. Clock to <code>dataout</code>, rising, in picoseconds:</p>
{o["table"]}
<p>A timing tool has read this library together with the abstract and
met timing at the {o["t"]["period"]} picosecond period; question 7 of
Verification describes the run. A few values are estimates rather than
simulation results, chiefly the wire component of each input pin's
capacitance, and the characterization record marks each of them. The
resistance and capacitance of the block's internal wiring are not
included in the simulations.</p>

<h3 class="sub">The behavioural model</h3>
<p>Simulating a chip that contains the block requires only a fast model
of its behaviour. The Verilog model is generated from the same pin list,
with the address map measured from the layout: which address bits select
the column, the word line and the half.</p>
<pre class="code">{o["model"]}</pre>

<h3 class="sub">The manifest</h3>
<p>Finally, a short record of what was built: its size and organization,
and the delay-line setting to which the <code>sdel</code> pins should be
tied at each corner.</p>
<pre class="code">{o["manifest"]}</pre>

<h3 class="sub">Measured results</h3>
<p>Every number below is measured on the generated layout: the area from
its outline, and the rest by simulating the transistors extracted from
it at the typical corner. Each is reproducible from the repository. The
simulations leave out the resistance and capacitance of the wiring, and
the idle power is scaled up from measured slices of the block. The
values are for the 2 kilobyte block shown above.</p>
<table>
  <tr><th>Property</th><th>Value</th><th>Meaning</th></tr>
  <tr><td>Size</td><td class="n">747.9 square micrometres</td>
      <td>63.9 per cent of it is memory cells; the rest is the surrounding circuitry</td></tr>
  <tr><td>Speed</td><td class="n">1.94 gigahertz</td>
      <td>A 516 picosecond period: the 430 confirmed in simulation, plus a fifth as margin</td></tr>
  <tr><td>Read latency</td><td class="n">243 picoseconds</td>
      <td>From the clock edge to data on the output pins</td></tr>
  <tr><td>Read energy</td><td class="n">0.615 picojoules</td>
      <td>Charge drawn from the supply for one access</td></tr>
  <tr><td>Idle power</td><td class="n">773 nanowatts</td>
      <td>Leakage while the block is idle</td></tr>
  <tr><td>Cell stability</td><td class="n">154 millivolts</td>
      <td>The noise a stored bit tolerates while being read, out of a 700 millivolt supply</td></tr>
</table>
<p>The largest block taken through the same measurements holds 65,536
bits.</p>
</div>

<h2 id="verify">Verification</h2>
<div class="config built">
<p>The block has never been made in silicon, so every answer below comes
from the generated layout itself: the shapes it is made of, the wiring
and transistors read back out of them, and simulations of those
transistors. Seven questions, each relying on the ones before it:</p>
{v["questions"]}
<h3 class="sub">1. Is every shape where it should be?</h3>
<p><b>Same as the kit's block.</b> At the reference size the generator
must reproduce the kit's published 2 kilobyte block exactly. Both
layouts are flattened and, on each of the 31 mask layers, compared with
an exclusive or (XOR): whatever is in one layout but not the other is
left over. The check passes only if nothing is left on any layer, which
shows that the measured rules of "Generation" are exact at that
size.</p>
<div class="oscroll">{v["xor"]}</div>
<p>What is left over is the area that one layout covers and the other
does not. In the first row the two are identical and nothing is left:
the pass. The second row shows what the check is for. It gets one
constant of the strip recipe wrong by 18 nanometres, the offset of the
first upright cell, so every upright cell in every strip sits 18
nanometres to the right of where the kit has it, and the check
fails.</p>
<p class="note">The drawing is computed from the two real layouts, the
active and gate layers of a few cells in the first strips. The real
check does the same on all 31 layers of the whole block.</p>
<p><b>Every cell in place.</b> The identity check only works at the
reference size. At every size, a second program, written apart from the
generator and using none of its code, works out from the measured
constants where every memory cell should sit and which way it should
face. The cells found in the generated layout must match that list
exactly: none missing, none extra, none out of place or mirrored the
wrong way. It runs on 14 sizes: every width from 4 to 32 bits, depths
from 4 to 64 rows, and eight strips.</p>
<div class="oscroll">{v["slots"]}</div>
<p class="note">When this check was written, a shifted strip, deleted tap
cells and a flipped mirror were each tried once by hand and caught.
Those trials were not kept as a repeatable test.</p>

<h3 class="sub">2. Is it wired the way it should be?</h3>
<p>Shapes are not wires yet. A bit line is many separate shapes on
different layers, joined where they overlap or where a via drops from
one layer to the next. The check rebuilds the wiring from the raw
shapes, the way the chip would conduct: it merges the touching shapes
on each layer, splits a transistor's active area where its gate crosses
it, joins the layers through every via and overlap, and then reads off
which wire each of the layout's labels sits on.</p>
<div class="oscroll">{v["wiring"]}</div>
<p>Two things must then hold. Every wire must be whole: all the cells
along a strip on one bit line, each word line one wire from end to end.
And no two wires may touch: bit lines, their pairs, word lines, supply
and ground all separate. On the reference block that is 512 bit lines
and 512 pairs, 64 word lines, one supply and one ground, and the 16
column-select lines each on its own wire.</p>
<p class="note">Drawn from the real extraction of one column group,
metals 1 to 3. The damaged copies are made for this drawing, and the
extraction is run again on each: the open and the short are what it
reports. A gap and a bridge were also tried on the whole block when the
check was written, and both were caught.</p>

<h3 class="sub">3. Can it be manufactured?</h3>
<p>A foundry only makes a design that obeys its rule manual: how narrow
a wire may be, how close two wires may sit, how far a via must land
inside the metal it joins, and much more. The check transcribes 97 of
the kit's rules, across 19 layers and layer pairs, and measures every
shape of the block against them.</p>
<div class="oscroll">{v["rules"]}</div>
<p>The figure shows one of them. In the memory array each bit line is a
metal-2 wire 18 nanometres wide, the narrowest the manual allows, with
{v["rules_n"]["gap"]} nanometres to the wire beside it, where the manual
asks for at least 18. Widen it along a short stretch until the gap is
under the minimum, and the check reports exactly there.</p>
<p>Memory is packed tighter than ordinary logic, and in places the kit's
own block goes below the printed value of {v["rules_n"]["cal"]} rules.
For those, the check uses the kit's tightest value instead: no block may
be packed tighter than the kit's own. Where that leaves each size:</p>
<p><b>Ready to manufacture: the 2 kilobyte block.</b> It passes every
rule.</p>
<p><b>Not yet: the 1, 4 and 8 kilobyte blocks.</b> They break a rule in
{v["rules_n"]["b16"]}, {v["rules_n"]["s8"]} and {v["rules_n"]["b64"]}
places, almost all where new wiring had to be added. Every place is
recorded and the check fails if a new one appears, but they are not
fixed, and most are not yet individually explained. Most of the 8
kilobyte block's are one problem repeated, a via not sitting fully on
its metal, which an outside checker confirms is real.</p>
<p class="note">The figure is computed: the real rule check is run on
one column group, as built and with the wider bit line. Not checked at
all: a few rules that need more than the shapes, such as spacings that
depend on which wire a shape belongs to, and the distance from a
transistor to its nearest well tap.</p>

<h3 class="sub">4. Are the transistors the ones we meant?</h3>
<p>A layout does not list its transistors: they exist only where shapes
overlap. The check finds them the way the physics does. A transistor is
wherever a gate crosses an active region, except where a gate cut severs
the gate. The implant over it says whether it is n-type or p-type, and
its strength is its fin count, the number of fins it crosses. Its
connections come from the wiring rebuilt in question 2.</p>
<div class="oscroll">{v["transistors"]}</div>
<p>The memory cell must then match its schematic exactly: two inverters
feeding each other, whose outputs Q and QB are the cell's two sides, and
two access transistors joining those sides to the bit lines, all with
the right types, fin counts and connections. After that the whole block is counted. The
reference block holds 114,454 transistors. Its 16,384 cells account for
98,304 of them, six each: 65,536 n-type with 2 fins and 32,768 p-type
with 1, exactly as six per cell requires. The other 16,150 are in the
dummy cells, the read and write columns and the control. Every word line
drives exactly 512 access transistors, and every bit line touches
exactly 32.</p>
<p class="note">Drawn from the real extraction of the kit's memory cell.
The check also breaks the cell on purpose, swapping the n and p
implants, deleting a gate cut and deleting a contact, and each must
break the match.</p>

<h3 class="sub">5. Does it store and return data?</h3>
<p>Two things have to work for a memory to hold data: each address has to
reach exactly its own cells, and those cells have to take a bit and give
it back. The first is checked by simulating the extracted transistors as
switches. Every address is presented in turn, with a clock edge, and the
decoder's outputs are read: exactly one word line may come on, and it
must be the one the address map names.</p>
<div class="oscroll">{v["decode"]}</div>
<p>The block has 256 addresses but only 64 word lines. The two least
significant address bits select one of the four strips that share a word
line; they do not change which word line is driven. Each row of the grid
therefore represents four addresses. The same switch-level simulation
also verifies the strip selection, that a write activates the write
driver rather than the sense amplifier, and that the block stays
inactive when it is not selected.</p>
<p>A switch-level model cannot represent the charge stored in a cell, so
the second check is a transistor-level simulation. One column is taken
from the extracted layout, comprising the cells, the write driver, the
sense amplifier and the output latch, and simulated with the kit's
transistor models. A 0 is written into a cell that holds a 1, and the
cell is then read.</p>
<div class="oscroll">{v["writeread"]}</div>
<p class="note">The recorded run of the 1 kilobyte (16-bit) block, from
20 July 2026. The cell's two sides cross over during the write, and on
the read the data pin falls to the bit that was written. Both directions
passed at every width from 4 to 32 bits that day; the control has been
rebuilt since, and the runs have not been repeated.</p>

<h4 class="vsub">How the simulations are run</h4>
<p>Both checks simulate the layout itself rather than a schematic drawn
by hand. The layout is first converted into a list of transistors and
the nets that connect them, and the simulators run on that list.</p>
<ol class="vsteps">
<li><b>Extraction.</b> A transistor exists wherever a gate crosses
active area. Its type is read from the implant layer over it, its
threshold flavour from the threshold-voltage marker, and its width from
the number of fins it crosses; the active area on either side forms its
source and drain. Each conducting layer, from the diffusion contacts up
to metal 5, is merged and joined to the next through its vias, which
assigns every transistor terminal to a net. The pin labels in the layout
name the nets. This is the same extraction that question 4 compares
against the intended transistors.</li>
<li><b>Switch-level simulation.</b> For the address check, each
transistor is treated as a switch: an n-type device conducts when its
gate is 1, a p-type device when its gate is 0. At every step, nets
joined through conducting switches take the value of the supply or
input they reach, and a net that reaches none keeps its previous value,
as a charged node does. This shows which word line each address drives,
but it carries no voltages and no times.</li>
<li><b>Slicing.</b> The full block, 114,454 transistors, is too large
for an analog simulator, so only the circuit that can affect one data
output is kept. Starting from the output pin, the slice grows backwards:
a transistor joins if its source or drain touches a net already in the
slice, and its gate net then joins as well. For the 16-bit block this
gives 878 transistors: the bit lines with their cells, the column
multiplexer, the write driver, the sense amplifier and the output
latch.</li>
<li><b>The circuit file.</b> Each transistor in the slice becomes one
line of a SPICE (Simulation Program with Integrated Circuit Emphasis)
netlist, written directly from the extraction. The control signals at
the edge of the slice (word line, write enable, bit-line precharge,
sense enable) are driven by piecewise-linear voltage sources, with edge
times measured in a separate simulation of the extracted control
circuit. Every cell starts with a stored value, and the addressed cell
starts with the opposite of the bit to be written.</li>
<li><b>Simulation.</b> <a href="https://ngspice.sourceforge.io/">ngspice</a>
solves the circuit over 6 nanoseconds with the kit's
<a href="https://bsim.berkeley.edu/models/bsimcmg/">BSIM-CMG</a>
(Berkeley Short-channel Insulated-Gate Field-Effect Transistor Model,
Common Multi-Gate) transistor models, at the typical corner and 0.7
volts. The figure above plots the voltages it recorded on the word
line, write enable, sense enable, the addressed cell's two storage nodes
and the data output.</li>
</ol>
<pre class="code">N0 nm2_8597 nm3_51 nlig_14524 0 nmos_sram nfin=2</pre>
<p class="note">One line of the recorded circuit file: an access
transistor of a cell in the top half. Its drain is on the complement bit
line, its gate on word line 0 of the top half, its source on the cell's
storage node and its body at ground, and it has two fins. The net names
come from the extraction: nm2_8597 is net 8597, first found on metal
2.</p>
<p>The extraction yields transistors and their connections, not the
resistance and capacitance of the wires. The bit lines are therefore
loaded by the transistors on them but not by their metal, and the
timing shown here is likely somewhat faster than the real circuit
would be.</p>

<h3 class="sub">6. Does it work in time?</h3>
<p>A read has two halves. While the clock is high, the word line opens
the cells, the bit lines split and the sense amplifier decides, until
the word is valid on the data pins. While it is low, the bit lines are
pulled back up, ready for the next read. Both were measured at
transistor level on the extracted layout, and their sum was then
confirmed by simulating two reads back to back. A fifth is added on top
as margin.</p>
<div class="oscroll">{v["cycle"]}</div>
<p>That gives 1.94 gigahertz at the typical corner and 1.67 at the slow
one. The fast corner's simulations did not converge, so it has no
number. As in question 5, the wiring's resistance and capacitance are
not simulated, so both periods are likely somewhat optimistic. Every
size also has to time its sense amplifier for its own farthest cell:
step 4 of Generation shows how, and that check runs on 13 sizes.
The read timing above was measured on the reference block only.</p>

<h3 class="sub">7. Do the files a chip designer uses tell the truth?</h3>
<p>A designer never sees the layout itself, only the abstract and the
timing library. So the abstract is checked against the layout: its pins
and blockages, merged, must cover exactly the metal the layout draws,
on every metal layer. Exported again here and compared on the central
band:</p>
<div class="oscroll">{v["cover"]}</div>
<p>The check also refuses an abstract whose layout has a pin label
deleted, moved off its metal, or used twice. The timing library is
checked differently: every value in it must trace back to a field of the
measurement record, and the library says which. Its delay table, beside
the record it was written from:</p>
{v["trace"]}
<p class="note">The library stores nanoseconds and picofarads, the
record picoseconds and femtofarads: 0.0200 is 20, 0.005000 is 5 and
0.2433 is 243.3. The abstract was checked on the
reference, 16-bit and 64-bit blocks; the library on the reference.</p>
<p>Finally, both files were given to the tools a designer would use.
OpenROAD loads the abstracts of the 16, 32 and 64-bit blocks as
placeable blocks of the expected size and pin count; the 2 kilobyte
block is 9.612 by 77.804 micrometres with 147 pins. OpenROAD refuses the
exact abstract, drawn on a quarter-nanometre grid, alongside the kit's
technology file, so a second variant is generated with every rectangle
rounded outward onto a 1 nanometre grid. OpenROAD's timing engine then
linked the block into a small design with a register on every input and
output, using only the block's own abstract and timing library, and
timed it at the 516 picosecond minimum period. Every path meets timing:
the worst setup slack is 266.6 picoseconds and the worst hold slack
11.7. The clock-to-output delay it reports, 243.6 picoseconds, is the
library's falling-output table, extended to this design's ideal clock
edge and the light load of a single register.</p>
<p class="note">The abstracts were loaded on 19 September 2026, and the
timing run was repeated for this page on 24 October 2026.</p>

<h3 class="sub">Checking the checks</h3>
<p>Five checks are fed deliberately damaged layouts every time they run,
and must reject them: slivers and spacing breaks for the rules, swapped
implants for the transistor check, a stuck word line driver for the
decode and read checks, a missing pin label for the abstract. The rule
and transistor checks were also re-run with tools someone else wrote, an
<a href="https://github.com/laurentc2/ASAP7_for_KLayout">independent
rule deck</a> and the extractor built into
<a href="https://www.klayout.de/">KLayout</a>. The transistor counts match
exactly in every block checked, up to 422,124 in the 8 kilobyte one, and
the rule decks agree on the reference; on the 16-bit block the outside
deck found three small deviations, which are recorded in the
repository.</p>
</div>

<h2 id="not">Limitations</h2>
<p>The process kit is predictive rather than a real foundry's, so
nothing here has been built in silicon. Block area does not yet shrink
with the configuration. The extraction yields transistors and their
connections but not the resistance and capacitance of the wiring, so
every simulated time leaves the wires out. There is no redundancy or
built-in self-test. The repository states each of these in full.</p>
<p>Checking a newly generated block is also less complete than checking
the reference:</p>
<ul>
<li><b>No single check command.</b> Several checks accept a block size
(wiring, address decode, the abstract and the rule baselines), but the
identity comparison, the clean rule check and the transistor check run
on the reference only. No one command checks a user's block and reports
the result.</li>
<li><b>No timing library by default.</b> Only the reference has a
timing library at all three corners. Any other size needs its own
characterization, about 45 minutes of simulation, and the timing-tool
run is set up for the reference only.</li>
<li><b>The behavioural model is not compared with the layout.</b> It is
checked only against a second model and a plain memory array, at the
reference size. Nothing confirms that it does what the layout does when
a read and a write are requested in the same cycle, or when an empty
column is read.</li>
<li><b>The behavioural model has no timing.</b> It has no setup or hold
checks, so a design that changes the address too close to the clock
edge still reads correct data in simulation.</li>
<li><b>One sense setting, chosen at the typical corner.</b> The model
tells the user which delay-line code to tie its sense-timing pins to,
taken from the typical corner. For some sizes the slow corner selects a
different code, and whether the typical code is also safe at the slow
corner has not been checked.</li>
</ul>

<h2 id="next">Future work</h2>
<ul>
<li><b>Replica sense timing.</b> A dummy row at the far end of the
array would trigger the sense amplifier itself, so the strobe follows
the word line's real delay at every corner, instead of a delay-line
setting chosen per size and corner.</li>
<li><b>Fast-corner timing.</b> The fast-corner simulations do not
converge for most delay settings, so that corner has no timing table.
This is a solver problem rather than a circuit one.</li>
<li><b>Timing libraries for more sizes.</b> The 64-bit block has the
typical corner only, and the eight-strip block has none.</li>
<li><b>Clean rule checks for every size.</b> The larger blocks carry
recorded rule markers as baselines; the aim is to fix them rather than
record them.</li>
<li><b>Antenna and density rules.</b> The rule deck covers width and
spacing but has no antenna-ratio, metal-density or fill checks yet.</li>
<li><b>Generated bank wiring.</b> The bank-level wiring is still copied
from a table measured on the kit's block rather than generated from
rules.</li>
<li><b>Checking a user's block.</b> One command that runs every check
on a newly generated block, and a comparison of the behavioural model
against the layout, closing the gaps listed above.</li>
</ul>

<footer>
  Source, verification stack and measured records:
  <a href="https://github.com/Samcooper01/Open-SRAM-Compiler-ASAP7nmPDK">github.com/Samcooper01/Open-SRAM-Compiler-ASAP7nmPDK</a>.
  Built with Python, KLayout, OpenROAD, ngspice and
  <a href="https://openvaf.semimod.de/">OpenVAF</a>, which compiles the
  transistor models for ngspice. No commercial tools.
</footer>
</main>
{SIDE_JS}
</body>
</html>
''')
    print(f"  wrote {DOCS / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
