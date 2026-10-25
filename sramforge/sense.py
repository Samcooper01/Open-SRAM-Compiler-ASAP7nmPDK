"""Sense-enable timing rule: which delay-line code (the sdel<4:0> pins) a
bank needs so that its sense amplifier strobes after the far row's
wordline is up and the bitlines have split.

Everything here is a lookup into measurements (sramforge/sae_codes.json,
written by scripts/sae_margin.py --record) plus one fit:

  need(spec, corner) = clk_to_wl[variant][corner]
                     + flight(rows_equiv, corner)
                     + split[bits][corner]
                     + margin

  clk_to_wl   clock to the near wordline of the control that the bank
              places (the reference band, the standard band-v2 control,
              or the eight-strip control), stage A of rung 7
  flight      far-row wordline 50 % rise time from the rung 5 wordline
              bench, a quadratic in the wire length: rows_equiv = depth
              x row pitch / the four-strip pitch (eight strips at depth
              32 are the wire of four strips at depth 64)
  split       bitline 100 mV split after the wordline, rung 5 column
              bench, by bits

The code is the smallest-delay setting of the placed control whose
measured clk-to-sae meets the need.  The reference band has the
family's five thermometer taps (11111 minimum ... 00001, 73 ps of
reach); every band-v2 control carries the binary-weighted line
(scripts/patch_control_delayline.py): bit k of the code is sdel<k>,
1 = short, 11111 the minimum, 00000 the maximum.

A configuration outside the table's reach gets code None: the bank is
built, its manifest says so, and rung 7 refuses to characterize it as
if it were timed.
"""
import json
import pathlib

from .generator import BankSpec, uses_band_v2, control_variant, colgrp_pitch
from .tech import COLGRP_ROW_PITCH

SAE_CODES_JSON = pathlib.Path(__file__).resolve().parent / "sae_codes.json"
DEFAULT_MARGIN_PS = 20.0
_TABLE = None


def table() -> dict:
    global _TABLE
    if _TABLE is None:
        _TABLE = (json.loads(SAE_CODES_JSON.read_text())
                  if SAE_CODES_JSON.exists() else {})
    return _TABLE


def rows_equiv(spec: BankSpec) -> float:
    """Wordline wire length in four-strip row pitches."""
    return spec.depth_groups * colgrp_pitch(spec) / COLGRP_ROW_PITCH


def flight_ps(spec: BankSpec, corner: str):
    fit = table().get("flight_fit", {}).get(corner)
    if not fit:
        return None
    n = rows_equiv(spec)
    return fit["c"] + fit["a"] * n + fit["b"] * n * n


def variant_key(spec: BankSpec) -> str:
    """Which measured control the bank places."""
    return control_variant(spec) if uses_band_v2(spec) else "reference"


def sdel_code(spec: BankSpec, corner: str = "TT",
              margin_ps: float = DEFAULT_MARGIN_PS) -> dict:
    """{'code', 'need_ps', 'sae_ps', 'slack_ps', 'note'}; code None when
    the placed control cannot strobe late enough (or nothing measured)."""
    t = table()
    key = variant_key(spec)
    ctl = t.get("controls", {}).get(key, {}).get(corner)
    split = t.get("split_ps", {}).get(corner, {}).get(str(spec.bits_per_group))
    flight = flight_ps(spec, corner)
    if not ctl or split is None or flight is None:
        return {"code": None, "need_ps": None, "sae_ps": None,
                "slack_ps": None,
                "note": f"no sense-timing measurement for control "
                        f"'{key}' at {corner} (scripts/sae_margin.py --record)"}
    need = ctl["clk_to_wl_ps"] + flight + split + margin_ps
    # codes whose stage A did not converge are MISSING measurements, not
    # fast codes: they are dropped, never sorted against a number
    measured = [(c, v) for c, v in ctl["codes"].items() if v is not None]
    best = None
    for code, sae in sorted(measured, key=lambda kv: kv[1]):
        if sae >= need:
            best = (code, sae)
            break
    if best is None:
        reach = max((v for _, v in measured), default=None)
        return {"code": None, "need_ps": round(need, 1), "sae_ps": None,
                "slack_ps": None,
                "note": f"the {key or 'standard'} control's delay line "
                        f"reaches {reach} ps, "
                        f"the far row needs {need:.0f} ps at {corner}"}
    return {"code": best[0], "need_ps": round(need, 1), "sae_ps": best[1],
            "slack_ps": round(best[1] - need, 1),
            "note": (f"clk-to-WL {ctl['clk_to_wl_ps']} + flight {flight:.0f} "
                     f"+ split {split} + margin {margin_ps:.0f} ps at {corner}")}


def code_bits(code: str) -> list:
    """sdel<0>..sdel<4> as integers for a code string (sdel<0> first)."""
    return [int(c) for c in code]
