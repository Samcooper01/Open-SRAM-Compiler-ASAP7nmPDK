#!/usr/bin/env python3
"""Independent device extraction of a bank with KLayout's netlist
extractor, and a census comparison against the in-house rung 4 LVS
(Layout Versus Schematic) extraction (sramforge/lvs.py).

What is independent here and what is not, stated plainly:

  * The in-house rung 4 extraction (sramforge/nets.py + sramforge/lvs.py)
    is a first-principles union-find over merged islands, with its own
    device recognition (channel = ACTIVE and (GATE minus GCUT), fins
    counted under the channel).
  * This script uses KLayout's LayoutToNetlist engine
    (https://www.klayout.de/doc/manual/lvs_overview.html): its
    hierarchical connectivity extractor and its MOS (Metal-Oxide-Semiconductor)
    device extractor
    (DeviceExtractorMOS3Transistor).  That is a different algorithm by
    different authors.  It is the same engine the KLayout LVS (Layout
    Versus Schematic) DSL (Domain Specific Language) drives, called
    through the pip module so the result can be compared in Python.
  * What is NOT independent: the layer derivation.  ASAP7 (Arizona State
    University 7 nm predictive Process Design Kit) has no public KLayout
    LVS (Layout Versus Schematic) deck (laurentc2/ASAP7_for_KLayout,
    https://github.com/laurentc2/ASAP7_for_KLayout, ships DRC (Design Rule
    Check) only, and its asap7.lyt connectivity is a metal-only stub), so
    the recognition layers below (channel, source/drain, implant split,
    connectivity list) are written here from the DRM (Design Rule Manual)
    layer semantics.  They mirror the in-house CONNECTIONS list, which is
    the physically correct stack, not a copy of our code.  Magic/Netgen
    were not used: there is no ASAP7 (Arizona State University 7 nm predictive
    Process Design Kit) Magic technology file to extract with.

Device width is KLayout's geometric width W (the channel's extent along
the gate), which for a FinFET drawn with 27 nm fin pitch and 7 nm fins is
an integer multiple of 27 nm; nfin = round(W / 27 nm).  The four models
match the in-house ones: {nmos,pmos}_{sram,rvt}, the flavor being the
SRAMVT marker (GDS (Graphic Design System II) 110/0).

Usage:
  python3 scripts/external_lvs.py <bank.gds> <topcell> [--out-dir DIR]
  python3 scripts/external_lvs.py --spec 32,4,32
  python3 scripts/external_lvs.py --golden
  python3 scripts/external_lvs.py cells/sram_cell_6t_122.gds sram_cell_6t_122

Outputs in --out-dir (default out/external_signoff):
  <top>.external.spice       the KLayout-extracted SPICE (Simulation Program
                             with Integrated Circuit Emphasis) netlist
  <top>.external_lvs.json    census + per-net degrees, both extractions
"""
import argparse
import json
import pathlib
import sys
import time
from collections import Counter

import klayout.db as kdb

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from sramforge.lvs import extract as inhouse_extract     # noqa: E402
from sramforge.tech import LAYERS                       # noqa: E402

DEFAULT_OUT = REPO / "out" / "external_signoff"
FIN_PITCH_UM = 0.027
PIN_TEXT_LAYERS = {"m1": (19, 251), "m2": (20, 251), "m3": (30, 251),
                   "m4": (40, 251), "m5": (50, 251)}


def _sibling(*parts) -> pathlib.Path:
    for anc in [REPO] + list(REPO.parents):
        p = anc.joinpath(*parts)
        if p.exists():
            return p
    return REPO.parent.joinpath(*parts)


GOLDEN_GDS = _sibling("asap7_sram_0p0", "gds", "srambank_32b.gds")
GOLDEN_TOP = "srambank_32b"


def klayout_extract(gds: pathlib.Path, top: str, spice_out: pathlib.Path):
    """KLayout LayoutToNetlist extraction.  Returns (netlist, dbu)."""
    ly = kdb.Layout()
    ly.read(str(gds))
    cell = ly.cell(top)
    if cell is None:
        sys.exit(f"top cell {top} not found in {gds}")
    l2n = kdb.LayoutToNetlist(kdb.RecursiveShapeIterator(ly, cell, []))
    l2n.threads = 4

    def L(name):
        return l2n.make_layer(ly.layer(*LAYERS[name]), name)

    gate, gcut, active = L("gate"), L("gcut"), L("active")
    nsel, psel, sramvt = L("nselect"), L("pselect"), L("sramvt")
    metals = {n: L(n) for n in ("sdt", "lisd", "lig", "v0", "m1", "v1",
                                "m2", "v2", "m3", "v3", "m4", "v4", "m5")}
    labels = {n: l2n.make_text_layer(ly.layer(*ld), n + "_lbl")
              for n, ld in PIN_TEXT_LAYERS.items()}

    # physics: cuts sever the gate, the surviving gate splits the active
    # into channel and source/drain
    gate_c = gate - gcut
    chan = active & gate_c
    sd = active - gate_c
    channels = {}      # model -> channel region
    sd_by_type = {}    # nmos/pmos -> source/drain region
    for base, implant in (("nmos", nsel), ("pmos", psel)):
        ch_i = chan & implant
        sd_by_type[base] = sd & implant
        channels[f"{base}_sram"] = ch_i & sramvt
        channels[f"{base}_rvt"] = ch_i - sramvt
    l2n.register(gate_c, "gate_c")
    for base, r in sd_by_type.items():
        l2n.register(r, base + "_sd")
    for model, r in channels.items():
        l2n.register(r, model + "_chan")

    # device recognition: one MOS (Metal-Oxide-Semiconductor) device per
    # channel piece; the extractor
    # writes its terminal shapes onto the SD (source/drain) and G (gate)
    # layers, which therefore must be part of the connectivity below
    for model, ch in channels.items():
        base = model.split("_")[0]
        ex = kdb.DeviceExtractorMOS3Transistor(model)
        l2n.extract_devices(ex, {"SD": sd_by_type[base], "G": ch, "P": gate_c})

    # connectivity: the same physical stack rung 2 documents
    # (source/drain -> SDT -> LISD -> {LIG, V0}; gate -> LIG -> V0; V0 -> M1
    # -> V1 -> M2 ... -> M5), plus pin texts naming the metal nets
    for r in [gate_c] + list(sd_by_type.values()) + list(channels.values()) \
            + list(metals.values()):
        l2n.connect(r)
    for ch in channels.values():
        l2n.connect(ch, gate_c)
    for r in sd_by_type.values():
        l2n.connect(r, metals["sdt"])
    pairs = [("sdt", "lisd"), ("lisd", "lig"), ("lisd", "v0"), ("lig", "v0"),
             ("v0", "m1"), ("m1", "v1"), ("v1", "m2"), ("m2", "v2"),
             ("v2", "m3"), ("m3", "v3"), ("v3", "m4"), ("m4", "v4"),
             ("v4", "m5")]
    for a, b in pairs:
        l2n.connect(metals[a], metals[b])
    l2n.connect(gate_c, metals["lig"])
    for n, lbl in labels.items():
        l2n.connect(metals[n], lbl)

    l2n.extract_netlist()
    nl = l2n.netlist()
    nl.flatten()          # one circuit, every device instance visible
    writer = kdb.NetlistSpiceWriter()
    nl.write(str(spice_out), writer, "sramforge external extraction")
    # the netlist is owned by the extractor: hand both back so it stays alive
    return nl, l2n, ly.dbu


def klayout_census(nl: kdb.Netlist):
    """Counter((model, nfin)), per-net access-transistor degree, labels."""
    top = nl.top_circuit()
    census = Counter()
    devices = []
    for d in top.each_device():
        model = d.device_class().name
        w = d.parameter("W")
        nfin = int(round(w / FIN_PITCH_UM))
        g = d.net_for_terminal("G")
        s = d.net_for_terminal("S")
        dr = d.net_for_terminal("D")
        devices.append((model, nfin, g.expanded_name() if g else None,
                        frozenset(n.expanded_name() for n in (s, dr) if n)))
        census[(model, nfin)] += 1
    return census, devices, top


def net_degrees(devices):
    """Wordline and bitline degrees, found STRUCTURALLY (the in-house
    extraction finds them by pin label; KLayout's flattened netlist keeps
    only a few hundred top-level names, so labels are not usable here):

      storage net  = gate net of at least one pmos_sram (a 6T pull-up is
                     gated by the opposite storage node)
      access dev   = nmos_sram whose gate is NOT a storage net
      WL net       = gate net of an access device
      BL/BLN net   = source/drain net of an access device that is not a
                     storage net

    Returns (access transistors per WL net, access drains per BL net)."""
    storage = {g for m, nfin, g, sd in devices if m == "pmos_sram" and g}
    per_wl = Counter()
    per_bl = Counter()
    for m, nfin, g, sd in devices:
        if m != "nmos_sram" or not g or g in storage:
            continue
        per_wl[g] += 1
        for n in sd:
            if n not in storage:
                per_bl[n] += 1
    return per_wl, per_bl


def inhouse_census(gds, top):
    ex = inhouse_extract(str(gds), top)
    census = Counter((d.model, d.nfin) for d in ex.devices)
    wl_nets = ex.nets_labeled("WL")
    bl_nets = ex.nets_labeled("BL") | ex.nets_labeled("BLN")
    per_wl = Counter()
    per_bl = Counter()
    for d in ex.devices:
        if d.model == "nmos_sram" and d.gate in wl_nets:
            per_wl[d.gate] += 1
            for n in d.sd:
                if n in bl_nets:
                    per_bl[n] += 1
    return census, per_wl, per_bl, len(ex.devices), len(ex.unclassified)


def summarize_degrees(cnt: Counter):
    return {"nets": len(cnt), "total": sum(cnt.values()),
            "degree_histogram": dict(sorted(Counter(cnt.values()).items()))}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gds", nargs="?")
    ap.add_argument("top", nargs="?")
    ap.add_argument("--spec")
    ap.add_argument("--golden", action="store_true")
    ap.add_argument("--out-dir", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--no-inhouse", action="store_true",
                    help="skip the in-house extraction (external only)")
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)

    if a.golden:
        gds, top = GOLDEN_GDS, GOLDEN_TOP
    elif a.spec:
        from sramforge import BankSpec, generate
        b, s, d = (int(x) for x in a.spec.split(","))
        spec = BankSpec(bits_per_group=b, strips_per_group=s, depth_groups=d)
        gds = a.out_dir / f"{spec.name}.gds"
        generate(spec, str(gds))
        top = spec.name
    elif a.gds and a.top:
        gds, top = pathlib.Path(a.gds), a.top
    else:
        ap.error("give <gds> <top>, or --spec b,s,d, or --golden")

    spice = a.out_dir / f"{top}.external.spice"
    t0 = time.time()
    nl, l2n, dbu = klayout_extract(gds, top, spice)
    ext_t = round(time.time() - t0, 1)
    census, devices, circuit = klayout_census(nl)
    per_wl, per_bl = net_degrees(devices)
    n_nets = sum(1 for _ in circuit.each_net())
    report = {
        "gds": str(gds), "top": top, "spice": str(spice),
        "klayout_module": kdb.__version__ if hasattr(kdb, "__version__") else "",
        "external": {
            "runtime_s": ext_t, "devices": len(devices), "nets": n_nets,
            "census": {f"{m} nfin={f}": n for (m, f), n in sorted(census.items())},
            "access_per_WL": summarize_degrees(per_wl),
            "access_drains_per_BL": summarize_degrees(per_bl),
        },
    }
    print(f"external (KLayout LayoutToNetlist): {len(devices)} devices, "
          f"{n_nets} nets, {ext_t} s")
    for k, v in report["external"]["census"].items():
        print(f"    {k:22s} {v}")
    print(f"    access per WL net: {report['external']['access_per_WL']}")
    print(f"    access drains per BL/BLN net: {report['external']['access_drains_per_BL']}")

    if not a.no_inhouse:
        t0 = time.time()
        icensus, iwl, ibl, ndev, nuncl = inhouse_census(gds, top)
        in_t = round(time.time() - t0, 1)
        report["inhouse"] = {
            "runtime_s": in_t, "devices": ndev, "unclassified": nuncl,
            "census": {f"{m} nfin={f}": n for (m, f), n in sorted(icensus.items())},
            "access_per_WL": summarize_degrees(iwl),
            "access_drains_per_BL": summarize_degrees(ibl),
        }
        same = (report["inhouse"]["census"] == report["external"]["census"]
                and report["inhouse"]["access_per_WL"] == report["external"]["access_per_WL"]
                and report["inhouse"]["access_drains_per_BL"]
                == report["external"]["access_drains_per_BL"])
        report["census_match"] = same
        print(f"in-house (sramforge.lvs): {ndev} devices, {in_t} s")
        for k, v in report["inhouse"]["census"].items():
            print(f"    {k:22s} {v}")
        print(f"    access per WL net: {report['inhouse']['access_per_WL']}")
        print(f"    access drains per BL/BLN net: {report['inhouse']['access_drains_per_BL']}")
        print(f"\nCENSUS {'MATCH' if same else 'MISMATCH'}")

    out = a.out_dir / f"{top}.external_lvs.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"written: {out}\n         {spice}")
    return 0 if report.get("census_match", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
