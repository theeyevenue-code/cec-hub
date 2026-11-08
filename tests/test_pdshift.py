"""PD shift to fit a stock blank (Mark, 9 Nov 2026): SV only, stock only, under 0.7 prism, 3 mm cap."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import lensoptions as LO  # noqa: E402
from hub import pdshift as P  # noqa: E402

# A 54 / DBL 18 / ED 58, mono PD 31: need = 58 + (54+18-62=10) + 2 = 70 per eye
FRAME = {"a": 54, "dbl": 18, "ed": 58}


def test_horizontal_power_uses_the_180_meridian():
    assert P.horizontal_power(-5, 0) == -5
    assert P.horizontal_power(-2, -2, 180) == -2            # cyl axis 180: no power horizontally
    assert abs(P.horizontal_power(-2, -2, 90) - -4) < 1e-9  # axis 90: full cyl horizontally
    assert abs(P.horizontal_power(-2, -2, 45) - -3) < 1e-9
    assert P.horizontal_power(-2, -2, None) == -4           # no axis: the worse meridian


def test_marks_example_minus_five_fits_with_a_small_shift():
    # stock blank 68: each eye needs 1 mm wider PD (blank need drops 2 mm per mm) -> 2 mm, 1.0 prism: refused
    assert P.find(FRAME, {"R": 31, "L": 31}, {"R": -5, "L": -5}, {"R": 68, "L": 68}) is None
    # stock blank 69: 0.5 mm each eye -> 1 mm total, 0.5 prism: offered
    v = P.find(FRAME, {"R": 31, "L": 31}, {"R": -5, "L": -5}, {"R": 69, "L": 69})
    assert v["shift"] == {"R": 0.5, "L": 0.5} and v["total_mm"] == 1.0 and v["prism"] == 0.5
    assert v["base"] == "base-in" and "31 → 31.5" in v["line"]


def test_already_fits_is_not_a_shift():
    assert P.find(FRAME, {"R": 31, "L": 31}, {"R": -5, "L": -5}, {"R": 70, "L": 70}) is None


def test_three_mm_cap_even_at_low_power():
    # -1.00: 4 mm total would be 0.4 prism, but over the 3 mm cap
    assert P.find(FRAME, {"R": 31, "L": 31}, {"R": -1, "L": -1}, {"R": 66, "L": 66}) is None
    v = P.find(FRAME, {"R": 31, "L": 31}, {"R": -1, "L": -1}, {"R": 67, "L": 67})
    assert v["total_mm"] == 3.0 and v["prism"] == 0.3


def test_prism_must_be_under_point_seven():
    # +7.00, 1 mm total = exactly 0.7: Mark said "less than", so refused
    assert P.find(FRAME, {"R": 31, "L": 31}, {"R": 7, "L": 7}, {"R": 69, "L": 69}) is None
    v = P.find(FRAME, {"R": 31, "L": 31}, {"R": 6.5, "L": 6.5}, {"R": 69, "L": 69})
    assert v["base"] == "base-out" and v["prism"] == 0.65


def test_one_eye_only_when_only_one_eye_is_short():
    v = P.find(FRAME, {"R": 31, "L": 32}, {"R": -4, "L": -4}, {"R": 69, "L": 69})
    assert v["shift"] == {"R": 0.5, "L": 0.0} and v["prism"] == 0.2


def test_no_shift_once_decentration_is_gone():
    # frame PD 72 = patient PD 72: widening can't shrink the blank below ED + 2
    assert P.find(FRAME, {"R": 36, "L": 36}, {"R": -1, "L": -1}, {"R": 59, "L": 59}) is None


def _q(kind="Single vision", exact=True):
    return {"kind": kind, "pd_exact": exact, "right": {"sph": -5.0, "cyl": 0.0}, "left": {"sph": -5.0, "cyl": 0.0},
            "axes": {"R": None, "L": None}}


def _est():
    return {"frame": dict(FRAME), "pds": {"R": 31.0, "L": 31.0}}


def test_offered_for_single_vision_only():
    assert LO._shift_ctx(_q(), _est())["powers"] == {"R": -5.0, "L": -5.0}
    assert LO._shift_ctx(_q("Progressive"), _est()) is None
    assert LO._shift_ctx(_q("Occupational"), _est()) is None


def test_not_offered_from_a_halved_or_borrowed_pd():
    assert LO._shift_ctx(_q(exact=False), _est()) is None


def test_summarise_puts_the_variation_on_a_short_stock_lens_only():
    res = {"options": [
        {"supplier": "ZEISS", "name": "ClearView", "raw_name": "ClearView", "index": "1.6", "type": "stock",
         "coating": "DV", "price_now": 100},
        {"supplier": "ZEISS", "name": "SV Grind", "raw_name": "SV Grind", "index": "1.6", "type": "grind",
         "coating": "DV", "price_now": 200},
    ]}
    key = LO._pkey(res["options"][0])
    avail = {"R": {key: [65.0, 69.0]}, "L": {key: [65.0, 69.0]}}
    need = {"R": 70, "L": 70}
    out = LO.summarise(res, {}, avail, need, LO._shift_ctx(_q(), _est()))
    stock = next(o for o in out["options"] if o.get("type") == "Stock")
    grind = next(o for o in out["options"] if o.get("type") != "Stock")
    assert stock["fits"] is False and stock["pd_shift"]["total_mm"] == 1.0
    assert stock["blank_mm"] == 69.0
    assert "pd_shift" not in grind
    # no context (e.g. progressive): no variation
    out2 = LO.summarise(res, {}, avail, need, None)
    assert all("pd_shift" not in o for o in out2["options"])
