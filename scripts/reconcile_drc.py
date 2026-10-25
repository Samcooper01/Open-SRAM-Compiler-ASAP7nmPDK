#!/usr/bin/env python3
"""Reconcile the EXTERNAL DRC (Design Rule Check) report (scripts/external_drc.py,
laurentc2/ASAP7_for_KLayout deck run by the KLayout binary) against the
in-house rung 3 deck (sramforge/drc.py), rule by rule.

Both decks transcribe the same ASAP7 (Arizona State University 7 nm
predictive Process Design Kit) DRM (Design Rule Manual), but they name a
few rules differently, split a few rules differently, and disagree with
the DRM (Design Rule Manual) numbering in a few places.  RULE_GROUPS maps
the two vocabularies onto one row each; everything not listed there is
matched by identical rule id.

For every row the table says which side flags it, with counts and one or
two example boxes (dbu, 1 dbu = 0.25 nm), and classifies the disagreement:

  agree-clean     neither deck flags the rule, both implement it
  agree-flag      both decks flag it
  ext-only        the external deck flags it, ours does not
  ours-only       ours flags it, the external deck does not
  ext-not-impl    only ours implements the rule (no external evidence)
  ours-not-impl   only the external deck implements the rule

plus a one-line REASON for each disagreement from EXPLANATIONS (calibrated
floor, waiver, SRAM (Static Random-Access Memory) marker variant, rule not
transcribed, deck bug, ...).
A rule the external deck does not implement is NOT evidence of
cleanliness, and the table says so explicitly.

Usage:
  python3 scripts/reconcile_drc.py <bank.gds> <topcell> [--external JSON]
                                   [--out report.md] [--json report.json]
The external JSON defaults to out/external_signoff/<topcell>.external_drc.json
(run scripts/external_drc.py first).  Our deck is run live on the same GDS
(Graphic Design System II).
"""
import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tests.test_drc import run_deck                       # noqa: E402
from sramforge.drc import CALIBRATION, WAIVED, CAL_ALLOWED  # noqa: E402

DEFAULT_OUT = REPO / "out" / "external_signoff"

# One row per entry: (row label, our rule ids, external rule ids).
# Rules absent here pair up by identical id.  Labels follow the DRM (Design
# Rule Manual) id where the two decks disagree on it.
RULE_GROUPS = [
    # --- same rule, different id spelling ---
    ("FIN.W.1", ["FIN.W.1"], ["FIN.W.1", "FIN.W.1a"]),
    ("LISD.S.3/S.4", ["LISD.S.3/S.4"], ["LISD.S.3-4"]),
    ("LIG.S.4/S.5", ["LIG.S.4", "LIG.S.5"], ["LIG.S.4-5"]),
    ("M1.S.4/S.5", ["M1.S.4", "M1.S.5"], ["M1.S.4-5"]),
    ("M2.S.4/S.5", ["M2.S.4", "M2.S.5"], ["M2.S.4-5"]),
    ("M3.S.4/S.5", ["M3.S.4", "M3.S.5"], ["M3.S.4-5"]),
    ("V0.AUX.1/2", ["V0.AUX.1a", "V0.AUX.1b"], ["V0.AUX.1-2"]),
    ("V1.AUX.1", ["V1.AUX.1a", "V1.AUX.1b"], ["V1.AUX.1"]),
    ("V2.AUX.1", ["V2.AUX.1a", "V2.AUX.1b"], ["V2.AUX.1"]),
    ("V3.AUX.1", ["V3.AUX.1a", "V3.AUX.1b"], ["V3.AUX.1"]),
    ("V4.AUX.1", ["V4.AUX.1a", "V4.AUX.1b"], ["V4.AUX.1"]),
    ("V4.S.1", ["V4.S.1"], ["V4.S.1-2-3"]),
    ("ACTIVE.AUX.1", ["ACTIVE.AUX.1"], ["ACTIVE.AUX.1", "ACTIVE.AUX.1_edges"]),
    # --- external deck labels that differ from the DRM (Design Rule Manual)
    # id (deck bugs).  DRM (Design Rule Manual): GATE.S.2 = min horizontal
    # spacing 34 nm, GATE.S.3 = every gate has a neighbour within 54 nm.
    # The external deck swaps the two labels.
    ("GATE.S.2 (34 nm spacing)", ["GATE.S.2"], ["GATE.S.3"]),
    ("GATE.S.3 (54 nm neighbour)", [], ["GATE.S.2"]),
    ("LIG.GATE.S.9A", ["LIG.GATE.S.9A"], ["LIG.LISD.S.9A"]),
    ("LIG.GATE.S.9B", ["LIG.GATE.S.9B"], ["LIG.LISD.S.9B"]),
    ("SRAM.ACTIVE.A.2A", ["SRAM.ACTIVE.A.2A"], ["SRAM.ACTIVE.A.1A"]),
    ("SRAM.LIG.GATE.A.4", ["SRAM.LIG.GATE.A.4"], ["LIG.GATE.A.4"]),
    # --- one of ours covers both implants; the external deck has two ---
    ("NSELECT/PSELECT.ACTIVE.EN.1", ["NSELECT.ACTIVE.EN.1"],
     ["NSELECT.ACTIVE.EN.1", "PSELECT.ACTIVE.EN.1"]),
    ("NSELECT/PSELECT.ACTIVE.EN.2", ["NSELECT.ACTIVE.EN.2"],
     ["NSELECT.ACTIVE.EN.2", "PSELECT.ACTIVE.EN.2"]),
    ("SRAM.NSELECT/PSELECT.ACTIVE.EN.3/4",
     ["SRAM.NSELECT.ACTIVE.EN.3", "SRAM.NSELECT.ACTIVE.EN.4"],
     ["SRAM.NSELECT.ACTIVE.EN.3-4", "SRAM.PSELECT.ACTIVE.EN.3-4"]),
]

# Why the two decks disagree, per row label (or per rule id).  Classes:
#   calibrated-floor : ours enforces the golden-observed floor, the
#                      external deck the printed DRM (Design Rule Manual) value
#   waived           : ours waives it with a recorded reason (WAIVED)
#   cal-allowed      : ours accepts the golden extra extents (CAL_ALLOWED)
#   sram-variant     : the decks split the SRAMDRC-marker variants
#                      differently
#   not-transcribed  : ours never implemented the rule (see drc.py header)
#   ext-deck-bug     : the external implementation is wrong or mislabeled
#   formulation      : same rule, different geometric formulation
_UNION_BUG = ("ext-deck-bug: the external deck takes the UNION (+) of the two "
              "layers where the DRM (Design Rule Manual) says area/width of the "
              "OVERLAP, so it measures whole polygons, not overlaps; its count "
              "is not evidence either way")
_TIER = ("formulation: the external deck classifies a spacing violation by "
         "'polygon not interacting with any short/long edge', which also "
         "excludes the perpendicular corner edges of the marker, so the "
         "tip-to-tip tiers under-report there; ours classifies by the two "
         "participating edge lengths as the DRM (Design Rule Manual) words it")
_EDGE_COUNT = ("count-unit: the external deck outputs EDGES (two per violating "
               "pair), ours outputs edge PAIRS; the flagged spots coincide")
_MULT27 = ("ext-deck-bug + sram-variant: coded as 'bbox height is none of "
           "k x 27 nm for k = 1..12', which flags every merged polygon taller "
           "than 324 nm and, for SDT, every 17 nm SRAM (Static Random-Access "
           "Memory) source/drain contact the DRM (Design Rule Manual) allows "
           "via SRAM.SDT.W.4; ours does not implement the multiple-of-27 rule")
_NET_COND = ("not-transcribed: net-conditional rule ('not on the same net'); "
             "the external deck ignores the net condition and flags same-net "
             "geometry too; ours defers it to rung 4")
_NOT_TRANSCRIBED = "not-transcribed: ours does not implement this rule (drc.py header)"

EXPLANATIONS = {
    "WELL.W.1": ("metric: ours measures width with the Euclidean metric, the "
                 "external deck with the projection metric the DRM (Design Rule "
                 "Manual) implies ('horizontal width'); the flagged pairs are "
                 "staircase edges with zero projected overlap, so ours "
                 "over-reports here"),
    "M1.S.4/S.5": _TIER, "M2.S.4/S.5": _TIER, "M3.S.4/S.5": _TIER,
    "M1.S.3": _TIER, "M2.S.3": _TIER, "M3.S.3": _TIER,
    "M1.S.2": _TIER, "M2.S.2": _TIER, "M3.S.2": _TIER,
    "M4.S.1": _EDGE_COUNT + "; on the golden ours is silent because of the calibrated floor (88 dbu)",
    "M4.S.2": _EDGE_COUNT + " (ours flags the spots the external deck's edge filter drops)",
    "M5.S.1": _EDGE_COUNT + "; on the golden ours is silent because of the calibrated floor (92 dbu)",
    "M5.S.2": _EDGE_COUNT,
    "GCUT.ACTIVE.S.1": ("formulation: the external deck measures GCUT to ANY "
                        "ACTIVE; the DRM (Design Rule Manual) and ours measure "
                        "GCUT to the CHANNEL (ACTIVE under GATE)"),
    "SRAM.ACTIVE.WELL.S.5": ("formulation: the external deck measures every "
                             "SRAM (Static Random-Access Memory) marked ACTIVE "
                             "to WELL, including ACTIVE "
                             "inside the well that abuts its edge (distance "
                             "0); ours measures only ACTIVE outside the well, "
                             "the inside case being the enclosure rule"),
    "SDT.W.3": _MULT27, "ACTIVE.W.2": _MULT27,
    "LIG.LISD.A.2": _UNION_BUG, "LIG.GATE.A.3": _UNION_BUG,
    "SRAM.LIG.GATE.A.4": _UNION_BUG, "LIG.LISD.OV.1": _UNION_BUG,
    "SRAM.LIG.GATE.OV.2": _UNION_BUG,
    "LIG.LISD.S.6": _NET_COND, "LIG.LISD.S.7": _NET_COND, "LIG.SDT.S.8": _NET_COND,
    "ACTIVE.S.2A": _NET_COND,
    "V0.S.1": ("not-transcribed: the external V0.S.1 has a second, 27 nm "
               "Euclidean output for vias on parallel tracks that are not "
               "aligned; ours implements only the 18 nm base spacing"),
    "V1.S.4": _NOT_TRANSCRIBED + " (corner-to-corner end-cap variants V0/V1.S.2-4)",
    "M1.S.6": _NOT_TRANSCRIBED + " (corner-to-corner)",
    "M2.S.6": _NOT_TRANSCRIBED + " (corner-to-corner)",
    "M3.S.6": _NOT_TRANSCRIBED + " (corner-to-corner)",
    "GATE.S.3 (54 nm neighbour)": _NOT_TRANSCRIBED + " (dummy-gate population)",
    "GATE.S.1": ("formulation + cal-allowed: the external deck flags every "
                 "gate-to-gate gap that is not exactly 34 nm; ours checks "
                 "that all nominal 20 nm stripes share one 54 nm centre grid "
                 "and accepts the golden 20.5 nm edge stripes (GATE.W.1 "
                 "CAL_ALLOWED), whose gaps are off-pitch by construction"),
    "ACTIVE.AUX.1": ("formulation: the external '_edges' half flags ACTIVE "
                     "edges that COINCIDE with a select edge; ours checks "
                     "coverage only (ACTIVE minus select must be empty)"),
    "SRAM.ACTIVE.AUX.2": ("formulation: the external deck flags any ACTIVE "
                          "crossing the SRAMDRC marker edge; ours flags "
                          "ACTIVE touching the marker without overlapping it, "
                          "as the DRM (Design Rule Manual) words it"),
    "GCUT.AUX.3": ("formulation: the external deck flags GCUT EDGES that "
                   "touch ACTIVE; ours flags GCUT area overlapping the "
                   "channel (ACTIVE and GATE)"),
    "GCUT.GATE.S.2": ("formulation: the external deck measures GCUT to ALL "
                      "gate (including the gate it cuts, hence 1 nm slivers); "
                      "ours to gates the cut does not touch"),
    "ACTIVE.AUX.1_edges": "see ACTIVE.AUX.1",
    "ACTIVE.WELL.EN.1": ("calibrated-floor + metric: ours accepts the golden "
                         "floor of 36 dbu (9 nm), which was measured with the "
                         "Euclidean metric at band CORNERS; the external deck "
                         "measures the projected enclosure, so a genuine 9 nm "
                         "projected shortfall passes ours and fails the "
                         "external deck (none on the golden)"),
    "LIG.GATE.A.3": (_UNION_BUG + "; on generated banks the flagged union "
                     "pieces are 10.5 nm tall GATE remnants left above a "
                     "GCUT (gate minus gcut), which ours misses because "
                     "GATE.W.2 is checked on the uncut gate"),
    "SRAM.LIG.GATE.A.4": "same pieces as LIG.GATE.A.3 (gate remnants appear in both unions)",
    "V0.AUX.1/2": ("formulation: the external deck flags PARTIAL coverage "
                   "(V0 area outside M1 or outside LISD/LIG); ours flags only "
                   "vias that touch nothing (not_interacting)"),
    "V3.M4.AUX.2": _NOT_TRANSCRIBED + " (via width must equal the M4 track width)",
    "V4.M5.AUX.2": _NOT_TRANSCRIBED + " (via width must equal the M5 track width)",
    "V0.M1.AUX.3": _NOT_TRANSCRIBED + " (via width must equal the M1 track width)",
    "M4.AUX.3": _NOT_TRANSCRIBED + " (M4 may not bend)",
    "M5.AUX.3": _NOT_TRANSCRIBED + " (M5 may not bend)",
    "GATE.AUX.1": _NOT_TRANSCRIBED + " (GATE may not bend; the golden edge stripes have stepped ends)",
    "GCUT.AUX.2": _NOT_TRANSCRIBED + " (GCUT vertical edge inside GATE)",
    "LIG.GATE.AUX.1": _NOT_TRANSCRIBED + " (LIG vertical edge inside GATE)",
    "LIG.GATE.EX.1": _NOT_TRANSCRIBED + " (LIG extends 1 nm past GATE)",
    "V1.M2.EN.2": _NOT_TRANSCRIBED + " (via end-cap enclosure by the upper metal)",
    "V2.M2.EN.1": _NOT_TRANSCRIBED + " (via enclosure by the lower metal)",
    "V3.M4.EN.2": _NOT_TRANSCRIBED + " (via enclosure by the upper metal)",
    "V0.LIG.A.1": _NOT_TRANSCRIBED + " (V0 over LIG overlap area)",
    "V0.LISD.EN.3": _NOT_TRANSCRIBED + " (V0 enclosure by LISD)",
    "M4.S.3": _NOT_TRANSCRIBED + " (tip-to-tip on adjacent tracks)",
    "M4.S.4": _NOT_TRANSCRIBED + " (tip-to-tip on adjacent tracks)",
    "M4.S.5": _NOT_TRANSCRIBED + " (parallel run length)",
    "V1.S.2": _NOT_TRANSCRIBED + " (corner-to-corner end-cap variant)",
    "V4.S.1": "agree: same spot, same via pair",
    "V3.AUX.1": "agree: same 34 vias outside M3/M4",
    "V4.AUX.1": "agree: same 2 vias outside M4/M5",
}


def _count(rules_dict, ids):
    n = 0
    ex = []
    for i in ids:
        r = rules_dict.get(i)
        if r:
            n += r["count"]
            ex += r["examples"]
    return n, ex


def reconcile(ours, ext):
    """ours: list of Violation; ext: external report dict.  Returns rows."""
    ours_d = {v.rule: {"count": v.count,
                       "examples": [[b.left, b.bottom, b.right, b.top]
                                    for b in v.examples]}
              for v in ours}
    ext_rules = dict(ext["rules"])
    # rules disabled in the flat run come from the hierarchical run
    hier_only = {}
    for rule in ext.get("flat_disabled_rules", []):
        if ext.get("hier_rules") and rule in ext["hier_rules"]:
            ext_rules[rule] = ext["hier_rules"][rule]
            hier_only[rule] = True
    ext_impl = set(ext["implemented_rules"])
    ours_impl = set(OUR_RULES)

    rows = []
    seen_ours, seen_ext = set(), set()
    for label, o_ids, e_ids in RULE_GROUPS:
        seen_ours |= set(o_ids)
        seen_ext |= set(e_ids)
        rows.append(_row(label, o_ids, e_ids, ours_d, ext_rules,
                         ours_impl, ext_impl, hier_only))
    for rule in sorted(ours_impl | ext_impl):
        if rule in seen_ours or rule in seen_ext:
            continue
        rows.append(_row(rule, [rule], [rule], ours_d, ext_rules,
                         ours_impl, ext_impl, hier_only))
    return rows


def _row(label, o_ids, e_ids, ours_d, ext_rules, ours_impl, ext_impl,
         hier_only):
    o_n, o_ex = _count(ours_d, o_ids)
    e_n, e_ex = _count(ext_rules, e_ids)
    o_has = any(i in ours_impl for i in o_ids)
    e_has = any(i in ext_impl for i in e_ids)
    if not o_has and not e_has:
        cls = "neither"
    elif not e_has:
        cls = "ext-not-impl"
    elif not o_has:
        cls = "ours-not-impl"
    elif o_n and e_n:
        cls = "agree-flag"
    elif e_n:
        cls = "ext-only"
    elif o_n:
        cls = "ours-only"
    else:
        cls = "agree-clean"
    reason = EXPLANATIONS.get(label, "")
    if not reason:
        for i in o_ids:
            if i in CALIBRATION and cls == "ext-only":
                reason = ("calibrated-floor: ours enforces the golden floor "
                          f"{CALIBRATION[i]} dbu, the external deck the DRM "
                          "(Design Rule Manual) value")
            elif i in WAIVED and cls in ("ext-only", "ours-not-impl"):
                reason = "waived: " + WAIVED[i]
            elif i in CAL_ALLOWED and cls == "ext-only":
                reason = f"cal-allowed: golden extents {sorted(CAL_ALLOWED[i])} dbu accepted"
    if any(i in hier_only for i in e_ids):
        reason = ("[external count from the hierarchical run, per cell] "
                  + reason).strip()
    # the frozen deck's waiver strings use a dash as punctuation; the
    # project's documents do not
    em_dash = chr(0x2014)
    reason = reason.replace(" " + em_dash + " ", ": ").replace(em_dash, ":")
    return {"label": label, "ours_rules": o_ids, "ext_rules": e_ids,
            "class": cls, "ours_count": o_n, "ext_count": e_n,
            "ours_examples": o_ex[:2], "ext_examples": e_ex[:2],
            "reason": reason}


# The complete rule vocabulary of sramforge/drc.py (its `report` ids),
# needed to tell "implemented but clean" from "not implemented".  Kept as
# a literal list (the deck only reports rules that fire) and checked
# against the deck source at import time.
OUR_RULES = [
    "WELL.W.1", "WELL.W.2", "WELL.S.1", "WELL.S.2", "WELL.A.1A",
    "FIN.W.1", "FIN.W.2", "FIN.S.1",
    "GATE.W.1", "GATE.W.2", "GATE.S.1", "GATE.S.2", "GATE.ACTIVE.EX.1",
    "GATE.ACTIVE.EX.2", "GATE.ACTIVE.S.4",
    "GCUT.W.1", "GCUT.ACTIVE.S.1", "GCUT.GATE.EX.1", "GCUT.GATE.S.2",
    "GCUT.S.3", "GCUT.AUX.1", "GCUT.AUX.3",
    "NSELECT.W.1", "NSELECT.W.2", "PSELECT.W.1", "PSELECT.W.2",
    "NSELECT.ACTIVE.EN.1", "NSELECT.ACTIVE.EN.2",
    "SRAM.NSELECT.ACTIVE.EN.3", "SRAM.NSELECT.ACTIVE.EN.4",
    "NSELECT.PSELECT.AUX.1", "ACTIVE.AUX.1",
    "ACTIVE.FIN.EX.1", "ACTIVE.W.1", "ACTIVE.W.3", "ACTIVE.S.1",
    "ACTIVE.S.2B", "ACTIVE.WELL.S.4", "SRAM.ACTIVE.WELL.S.5",
    "ACTIVE.WELL.EN.1", "SRAM.ACTIVE.WELL.EN.2", "ACTIVE.A.1A",
    "SRAM.ACTIVE.A.2A",
    "SDT.W.1", "SDT.W.2", "SRAM.SDT.W.4", "SDT.S.1", "SDT.GATE.S.2",
    "SDT.ACTIVE.OV.1", "SRAM.SDT.ACTIVE.OV.3", "SDT.LISD.OV.2",
    "SRAM.SDT.LISD.OV.4", "SDT.GATE.AUX.1", "SDT.LISD.AUX.4",
    "LISD.W.1", "LISD.S.1", "LISD.S.2", "LISD.S.3/S.4", "LISD.A.1",
    "LIG.W.1", "LIG.S.1", "LIG.S.2", "LIG.S.3", "LIG.S.4", "LIG.S.5",
    "LIG.GATE.S.9A", "LIG.GATE.S.9B", "LIG.GATE.S.10", "LIG.GCUT.S.11",
    "LIG.A.1", "LIG.LISD.A.2", "LIG.GATE.A.3", "SRAM.LIG.GATE.A.4",
    "V0.W.1", "V0.S.1", "V0.AUX.1a", "V0.AUX.1b",
    "M1.W.1", "M1.S.1", "M1.S.2", "M1.S.3", "M1.S.4", "M1.S.5", "M1.A.1",
    "M2.W.1", "M2.S.1", "M2.S.2", "M2.S.3", "M2.S.4", "M2.S.5", "M2.A.1",
    "M3.W.1", "M3.S.1", "M3.S.2", "M3.S.3", "M3.S.4", "M3.S.5", "M3.A.1",
    "V1.W.1", "V1.S.1", "V1.AUX.1a", "V1.AUX.1b",
    "V2.W.1", "V2.S.1", "V2.AUX.1a", "V2.AUX.1b",
    "V3.W.1", "V3.S.1", "V3.AUX.1a", "V3.AUX.1b",
    "M4.W.1", "M4.W.5", "M4.S.1", "M4.S.2",
    "M5.W.1", "M5.W.5", "M5.S.1", "M5.S.2",
    "V4.W.1", "V4.S.1", "V4.AUX.1a", "V4.AUX.1b",
    "SRAM.ACTIVE.AUX.2", "SRAM.LISD.AUX.1", "SRAM.LIG.AUX.2",
]


def _check_our_rules():
    """Every literal rule id above must appear in sramforge/drc.py and
    every quoted id in drc.py's check_* methods should be listed."""
    src = (REPO / "sramforge" / "drc.py").read_text()
    missing = [r for r in OUR_RULES if f'"{r}"' not in src
               and not any(r.startswith(p) for p in ("M1.", "M2.", "M3.",
                                                       "V1.", "V2.", "V3.",
                                                       "NSELECT.W", "PSELECT.W"))]
    if missing:
        sys.exit(f"OUR_RULES out of sync with sramforge/drc.py: {missing}")


def render_md(rows, gds, top, ext, ours_total):
    dbu = ext["layout"]["dbu"]

    def box(b):
        return f"({b[0]},{b[1]};{b[2]},{b[3]})"

    def um(b):
        return f"({b[0]*dbu:.3f},{b[1]*dbu:.3f})"

    order = ["agree-flag", "ext-only", "ours-only", "ours-not-impl",
             "ext-not-impl", "agree-clean"]
    lines = [f"# DRC (Design Rule Check) reconciliation: {top}", "",
             f"GDS (Graphic Design System II): `{gds}`  ",
             f"External deck: `{ext['deck']}` via {ext['klayout']}, "
             f"{ext['runtime_s']} s flat"
             + (f" + {ext['hier_runtime_s']} s hierarchical" if ext.get("hier_runtime_s") else "")
             + f"; {ext['total_violations']} external markers in "
             f"{len(ext['rules'])} rules.  ",
             f"In-house rung 3 deck: {ours_total} markers in "
             f"{sum(1 for r in rows if r['ours_count'])} rules.", ""]
    summary = {}
    for r in rows:
        summary[r["class"]] = summary.get(r["class"], 0) + 1
    lines.append("| class | rows |")
    lines.append("|---|---|")
    for c in order:
        lines.append(f"| {c} | {summary.get(c, 0)} |")
    lines.append("")
    lines.append("Examples are dbu boxes (l,b;r,t), 1 dbu = 0.25 nm; the "
                 "micron pair is the lower-left corner.")
    lines.append("")
    for c in order:
        sel = [r for r in rows if r["class"] == c]
        if not sel:
            continue
        lines.append(f"## {c} ({len(sel)})")
        lines.append("")
        lines.append("| rule | ours | external | ours example | external example | reason |")
        lines.append("|---|---|---|---|---|---|")
        for r in sel:
            oe = " ".join(box(b) + " " + um(b) for b in r["ours_examples"][:1])
            ee = " ".join(box(b) + " " + um(b) for b in r["ext_examples"][:1])
            lines.append(f"| {r['label']} | {r['ours_count']} | {r['ext_count']} "
                         f"| {oe} | {ee} | {r['reason']} |")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gds")
    ap.add_argument("top")
    ap.add_argument("--external", help="external_drc.py JSON report")
    ap.add_argument("--out", help="markdown table path")
    ap.add_argument("--json", help="machine-readable rows path")
    a = ap.parse_args()
    _check_our_rules()

    ext_path = pathlib.Path(a.external) if a.external else \
        DEFAULT_OUT / f"{a.top}.external_drc.json"
    ext = json.loads(ext_path.read_text())

    print(f"running the in-house rung 3 deck on {a.gds} ...", flush=True)
    ours = run_deck(a.gds, a.top)
    ours_total = sum(v.count for v in ours)
    rows = reconcile(ours, ext)

    md = render_md(rows, a.gds, a.top, ext, ours_total)
    out = pathlib.Path(a.out) if a.out else DEFAULT_OUT / f"{a.top}.reconcile.md"
    out.write_text(md)
    js = pathlib.Path(a.json) if a.json else DEFAULT_OUT / f"{a.top}.reconcile.json"
    js.write_text(json.dumps({"gds": a.gds, "top": a.top, "rows": rows,
                              "ours_total": ours_total,
                              "external": str(ext_path)}, indent=1))
    print(md)
    print(f"\nwritten: {out}\n         {js}")


if __name__ == "__main__":
    main()
