#!/usr/bin/env python3
"""Independent DRC (Design Rule Check) of a generated bank with an EXTERNAL
open ASAP7 (Arizona State University 7 nm predictive Process Design Kit)
deck, run by the KLayout standalone binary.

Why an external run at all: the in-house rung 3 deck (sramforge/drc.py)
was transcribed from the DRM (Design Rule Manual) by the same people who
wrote the generator, and its floors were calibrated to the golden
reference bank.  A deck written by somebody else, executed by a different
rule engine (the KLayout DRC (Design Rule Check) Ruby DSL (Domain Specific
Language) instead of our Python Region calls), is the cheapest independent
corroboration available while the official Calibre decks stay encrypted.

The deck: drc/drc_ASAP7.lydrc from laurentc2/ASAP7_for_KLayout
(https://github.com/laurentc2/ASAP7_for_KLayout, BSD-2-Clause).  The
OpenROAD-flow-scripts platform directory ships a re-licensed copy of the
same file (flow/platforms/asap7/drc/asap7.lydrc, see
https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts); the two
differ only in variable names, the thread count and one category label
typo, so the upstream original is used and the OpenROAD-flow-scripts copy
is accepted as a fallback.

Why the KLayout BINARY and not the pip module: the deck is a KLayout DRC
(Design Rule Check) macro written in the Ruby DSL (Domain Specific
Language).  The Ruby interpreter and the DRC (Design Rule Check) engine
live only in the full application; the pip `klayout` module exposes the
geometry database (klayout.db) and the report database (klayout.rdb) but
not the macro interpreter.  So the deck runs as
`klayout -b -r deck.lydrc -rd input=... -rd topcell=... -rd output=...`
and this script reads the resulting .lyrdb marker database with the pip
module to produce the per-rule count report.

Why the layout is FLATTENED first: the deck runs in `deep` (hierarchical)
mode and reports each violation once per CELL, in cell-local coordinates.
Our rung 3 deck runs flat and counts every INSTANCE.  Flattening the top
cell into a single-cell GDS (Graphic Design System II) before the run
makes the external counts and coordinates directly comparable with ours
(and also guarantees both decks see the same merged geometry).

Usage:
  python3 scripts/external_drc.py <bank.gds> <topcell> [--out-dir DIR]
  python3 scripts/external_drc.py --spec 32,4,32 [--out-dir DIR]
  python3 scripts/external_drc.py --golden [--out-dir DIR]

Outputs, in --out-dir (default out/external_signoff):
  <top>.flat.oas              flattened single-cell copy used for the run
                              (OASIS, Open Artwork System Interchange Standard)
  <top>.external.lyrdb        raw KLayout marker database (open in the KLayout
                              marker browser)
  <top>.external.log          KLayout stdout/stderr (verbose rule timing)
  <top>.external_drc.json     per-rule counts + example boxes (the report)
  deck_patched.lydrc          the deck copy actually executed (see PATCHES)
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

import klayout.db as kdb
import klayout.rdb as rdb

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

DEFAULT_OUT = REPO / "out" / "external_signoff"


def _sibling(*parts) -> pathlib.Path:
    """First existing REPO-ancestor/<parts> (the sibling-checkout
    convention this project uses for ../asap7_sram_0p0; walking every
    ancestor also covers a checkout nested inside another)."""
    for anc in [REPO] + list(REPO.parents):
        p = anc.joinpath(*parts)
        if p.exists():
            return p
    return REPO.parent.joinpath(*parts)


GOLDEN_GDS = _sibling("asap7_sram_0p0", "gds", "srambank_32b.gds")
GOLDEN_TOP = "srambank_32b"

# Where to look for the external deck, in order.  The first entry lets a
# user point anywhere; the rest are the sibling-checkout convention this
# project already uses for the golden bank (../asap7_sram_0p0), a clone
# under tools/, and the OpenROAD-flow-scripts platform copy.
DECK_CANDIDATES = [
    os.environ.get("SRAMFORGE_EXTERNAL_DRC_DECK", ""),
    _sibling("ASAP7_for_KLayout", "drc", "drc_ASAP7.lydrc"),
    REPO / "tools" / "ASAP7_for_KLayout" / "drc" / "drc_ASAP7.lydrc",
    pathlib.Path(os.environ.get("ORFS_ROOT", "/nonexistent"))
    / "flow" / "platforms" / "asap7" / "drc" / "asap7.lydrc",
]
DECK_CLONE_URL = "https://github.com/laurentc2/ASAP7_for_KLayout"

# Category-LABEL-only fixes applied to a COPY of the deck before running.
# None of these changes a rule's geometry or value; they only make the
# report categories unambiguous so per-rule counts can be reconciled.
# Each entry: (exact text to find, replacement, why).
PATCHES = [
    # LIG.S.2 is written into the LISD.S.2 category, so the two rules'
    # counts merge.  Give LIG.S.2 its own category.
    ('.output("LISD.S.2", "LIG.S.2 :',
     '.output("LIG.S.2", "LIG.S.2 :',
     "LIG.S.2 mislabeled as LISD.S.2 (category collision)"),
]

# Rules that cannot run on a FLATTENED bank in reasonable time.  ACTIVE.LUP.1
# (latch-up distance) sizes every device ACTIVE by 30 um and merges: on one
# flat 1.1M-shape bank the merged blob has millions of vertices and the
# not_interacting step ran for more than 15 minutes on a 4-thread run
# before being stopped (the same rule takes 20 s hierarchically).  The
# flat run comments these lines out and the companion HIERARCHICAL run
# (which runs every rule) supplies the count for them.
FLAT_DISABLED = {
    "ACTIVE.LUP.1": ['.output("ACTIVE.LUP.1"'],
}

# Several output() calls in the deck lack the comma between category and
# description, so Ruby concatenates the two strings and the category name
# becomes "V1.AUX.1V1.AUX.1 : V1 must ...".  The rule id is still the
# leading token; normalize on read.


def normalize_rule(category_name: str) -> str:
    """'V1.AUX.1V1.AUX.1 : V1 must be inside M1 and M2' -> 'V1.AUX.1'."""
    name = category_name.strip()
    # split off a duplicated id: the concatenated form repeats the id
    for i in range(1, len(name) // 2 + 1):
        head = name[:i]
        if name[i:].startswith(head) and "." in head:
            return head
    return name.split(" ")[0].split(":")[0]


def find_deck(explicit=None) -> pathlib.Path:
    cands = [explicit] if explicit else DECK_CANDIDATES
    for c in cands:
        if c and pathlib.Path(c).is_file():
            return pathlib.Path(c).resolve()
    sys.exit("external deck not found; clone it with\n"
             f"  git clone {DECK_CLONE_URL} {REPO / 'tools' / 'ASAP7_for_KLayout'}\n"
             "or set SRAMFORGE_EXTERNAL_DRC_DECK=/path/to/drc_ASAP7.lydrc")


def find_klayout() -> str:
    exe = os.environ.get("KLAYOUT_BIN") or shutil.which("klayout")
    if not exe:
        sys.exit("klayout binary not found on PATH (set KLAYOUT_BIN)")
    return exe


def klayout_version(exe: str) -> str:
    out = subprocess.run([exe, "-v"], capture_output=True, text=True)
    return (out.stdout or out.stderr).strip().splitlines()[0]


def patch_deck(src: pathlib.Path, dst: pathlib.Path, threads: int,
               disable: dict) -> list:
    """Write the deck copy that is actually executed.  Returns the list
    of applied label fixes; `disable` maps rule id -> line markers whose
    lines get commented out (flat run only, see FLAT_DISABLED)."""
    text = src.read_text()
    applied = []
    for old, new, why in PATCHES:
        if old in text:
            text = text.replace(old, new)
            applied.append(why)
    if threads:
        text = re.sub(r"threads\(\d+\)", f"threads({threads})", text)
    if disable:
        lines = text.split("\n")
        for rule, markers in disable.items():
            for i, line in enumerate(lines):
                if any(m in line for m in markers) and not line.startswith("#"):
                    lines[i] = "# [sramforge flat run: disabled] " + line
                    applied.append(f"{rule} disabled (flat run only)")
        text = "\n".join(lines)
    dst.write_text(text)
    return applied


def flatten(gds: pathlib.Path, top: str, dst: pathlib.Path) -> dict:
    """Write a single-cell copy of `top` (all hierarchy flattened, texts
    kept).  Returns a few stats for the report."""
    ly = kdb.Layout()
    ly.read(str(gds))
    cell = ly.cell(top)
    if cell is None:
        sys.exit(f"top cell {top} not found in {gds}")
    n_cells = ly.cells()
    cell.flatten(True)          # prune=True drops the orphaned children
    others = [c.cell_index() for c in ly.each_cell()
              if c.cell_index() != cell.cell_index()]
    for ci in others:           # anything not reachable from top
        ly.delete_cell(ci)
    n_shapes = sum(cell.shapes(li).size() for li in ly.layer_indexes())
    ly.write(str(dst))
    bb = cell.bbox()
    return {"cells_before_flatten": n_cells, "flat_shapes": n_shapes,
            "dbu": ly.dbu,
            "bbox_um": [bb.left * ly.dbu, bb.bottom * ly.dbu,
                        bb.right * ly.dbu, bb.top * ly.dbu]}


def read_report(lyrdb: pathlib.Path, dbu: float, max_examples: int) -> dict:
    """Per-rule counts from the marker database.  Example markers are
    given as dbu boxes [l, b, r, t] so they line up with our own deck's
    Violation.examples (which are kdb.Box in dbu)."""
    db = rdb.ReportDatabase("external")
    db.load(str(lyrdb))
    cats = {c.rdb_id(): c for c in db.each_category()}
    rules = {}
    for item in db.each_item():
        cat = cats[item.category_id()]
        rule = normalize_rule(cat.name())
        rec = rules.setdefault(rule, {
            "count": 0, "categories": [], "description": cat.description,
            "examples": []})
        rec["count"] += 1
        if cat.name() not in rec["categories"]:
            rec["categories"].append(cat.name())
        if len(rec["examples"]) < max_examples:
            for v in item.each_value():
                box = None
                if v.is_polygon():
                    box = v.polygon().bbox()
                elif v.is_box():
                    box = v.box()
                elif v.is_edge():
                    box = v.edge().bbox()
                elif v.is_edge_pair():
                    box = v.edge_pair().bbox()
                if box is not None:
                    rec["examples"].append([
                        int(round(box.left / dbu)), int(round(box.bottom / dbu)),
                        int(round(box.right / dbu)), int(round(box.top / dbu))])
                    break
    # rules that were CHECKED but fired nothing are still informative:
    # they are the deck's coverage (a rule it does not implement is not
    # evidence of cleanliness, a rule it implements and finds clean is)
    implemented = sorted({normalize_rule(c.name()) for c in cats.values()})
    return {"rules": dict(sorted(rules.items())),
            "implemented_rules": implemented,
            "total_violations": sum(r["count"] for r in rules.values())}


def run_klayout(exe, deck, gds, top, lyrdb, log):
    cmd = [exe, "-b", "-r", str(deck),
           "-rd", f"input={pathlib.Path(gds).resolve()}",
           "-rd", f"topcell={top}",
           "-rd", f"output={pathlib.Path(lyrdb).resolve()}"]
    print("running:", " ".join(cmd), flush=True)
    t0 = time.time()
    with open(log, "w") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT)
    runtime = round(time.time() - t0, 1)
    if proc.returncode != 0 or not pathlib.Path(lyrdb).is_file():
        sys.exit(f"klayout failed (exit {proc.returncode}); see {log}")
    return runtime


def run(gds: pathlib.Path, top: str, out_dir: pathlib.Path, deck: pathlib.Path,
        no_flatten: bool, no_hier: bool, max_examples: int, threads: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = find_klayout()
    version = klayout_version(exe)

    if no_flatten:
        # one hierarchical run of the complete deck; counts are per CELL
        patched = out_dir / "deck_patched_hier.lydrc"
        applied = patch_deck(deck, patched, threads, {})
        ly = kdb.Layout()
        ly.read(str(gds))
        stats = {"dbu": ly.dbu, "flattened": False}
        lyrdb = out_dir / f"{top}.external_hier.lyrdb"
        log = out_dir / f"{top}.external_hier.log"
        runtime = run_klayout(exe, patched, gds, top, lyrdb, log)
        report = read_report(lyrdb, stats["dbu"], max_examples)
        report["flat_disabled_rules"] = []
        report["hier_rules"] = None
    else:
        # main run: flat, every rule except FLAT_DISABLED
        patched = out_dir / "deck_patched_flat.lydrc"
        applied = patch_deck(deck, patched, threads, FLAT_DISABLED)
        # OASIS (Open Artwork System Interchange Standard) instead of GDS
        # (Graphic Design System II) for the flat copy: same geometry,
        # roughly a tenth of the file size for a multi-million-shape bank
        run_gds = out_dir / f"{top}.flat.oas"
        t0 = time.time()
        stats = flatten(gds, top, run_gds)
        stats["flattened"] = True
        stats["flatten_s"] = round(time.time() - t0, 1)
        lyrdb = out_dir / f"{top}.external.lyrdb"
        log = out_dir / f"{top}.external.log"
        runtime = run_klayout(exe, patched, run_gds, top, lyrdb, log)
        report = read_report(lyrdb, stats["dbu"], max_examples)
        report["flat_disabled_rules"] = sorted(FLAT_DISABLED)
        report["hier_rules"] = None
        if not no_hier:
            # companion run: hierarchical, complete deck, on the ORIGINAL
            # hierarchy.  Per-cell counts (one marker per cell, not per
            # instance), used only for the rules disabled in the flat run
            # and as a cross-check that flattening did not change the
            # fired rule set.
            hpatched = out_dir / "deck_patched_hier.lydrc"
            patch_deck(deck, hpatched, threads, {})
            hlyrdb = out_dir / f"{top}.external_hier.lyrdb"
            hlog = out_dir / f"{top}.external_hier.log"
            hruntime = run_klayout(exe, hpatched, gds, top, hlyrdb, hlog)
            hrep = read_report(hlyrdb, stats["dbu"], max_examples)
            report["hier_rules"] = hrep["rules"]
            report["hier_runtime_s"] = hruntime
            report["hier_lyrdb"] = str(hlyrdb)

    report.update({
        "gds": str(gds), "top": top,
        "deck": str(deck), "deck_patches_applied": applied,
        "klayout": version, "runtime_s": runtime, "layout": stats,
        "lyrdb": str(lyrdb), "log": str(log),
    })
    out_json = out_dir / f"{top}.external_drc.json"
    out_json.write_text(json.dumps(report, indent=1))
    return report


def print_report(rep: dict):
    mode = "flat" if rep["layout"].get("flattened") else "hierarchical (per cell)"
    print(f"\n{rep['top']}: {rep['total_violations']} external violations in "
          f"{len(rep['rules'])} rules, {mode} ({rep['klayout']}, "
          f"{rep['runtime_s']} s, {len(rep['implemented_rules'])} rule "
          f"categories in the deck)")
    if rep.get("flat_disabled_rules"):
        print(f"disabled in the flat run: {rep['flat_disabled_rules']}"
              + (" (see hier_rules in the JSON)" if rep.get("hier_rules") else ""))
    dbu = rep["layout"]["dbu"]
    print(f"{'rule':28s} {'count':>7s}  example (um)")
    for rule, r in sorted(rep["rules"].items(), key=lambda kv: -kv[1]["count"]):
        ex = ""
        if r["examples"]:
            l, b, rr, t = r["examples"][0]
            ex = f"({l*dbu:.4g},{b*dbu:.4g};{rr*dbu:.4g},{t*dbu:.4g})"
        print(f"{rule:28s} {r['count']:7d}  {ex}")
    if rep.get("hier_rules"):
        for rule in rep["flat_disabled_rules"]:
            h = rep["hier_rules"].get(rule)
            print(f"{rule:28s} {h['count'] if h else 0:7d}  (hierarchical run, per cell)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gds", nargs="?")
    ap.add_argument("top", nargs="?")
    ap.add_argument("--spec", help="generate bits,strips,depth with sramforge first")
    ap.add_argument("--golden", action="store_true",
                    help=f"run on the golden reference bank ({GOLDEN_GDS})")
    ap.add_argument("--out-dir", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--deck", help="path to drc_ASAP7.lydrc (else searched)")
    ap.add_argument("--no-flatten", action="store_true",
                    help="run hierarchically only (per-cell counts, NOT comparable)")
    ap.add_argument("--no-hier", action="store_true",
                    help="skip the companion hierarchical run")
    ap.add_argument("--max-examples", type=int, default=5)
    ap.add_argument("--threads", type=int, default=0,
                    help="override the deck's threads(4)")
    a = ap.parse_args()

    a.out_dir.mkdir(parents=True, exist_ok=True)
    if a.golden:
        if not GOLDEN_GDS.is_file():
            sys.exit(f"golden bank not found at {GOLDEN_GDS}")
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

    deck = find_deck(a.deck)
    rep = run(gds, top, a.out_dir, deck, a.no_flatten, a.no_hier,
              a.max_examples, a.threads)
    print_report(rep)
    print(f"\nreport: {a.out_dir / (top + '.external_drc.json')}")


if __name__ == "__main__":
    main()
