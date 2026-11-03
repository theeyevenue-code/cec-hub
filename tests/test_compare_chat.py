"""Compare engine + the Lens Finder chat's local guard - synthetic data only.
The chat itself (now the lens_compare tool of the Hub assistant) is tested in
tests/test_assistant.py with a fake model; nothing touches Optomate or Anthropic."""

import json

import pytest

from hub import compare, lenschat, lenses
from hub import lens_bridge as lb

CSV = (
    "supplier,brand,lens,category,index,material,type,blank_mm,sph_min,sph_max,cyl_max,"
    "combined_max,range_basis,price,price_promo,price_basis,coating,orderable,notes\n"
    # one stock product on two bands with DIFFERENT costs, to prove pair totals use each eye's row
    "ZEISS,ZEISS,TestView FSV 1.60,Single vision,1.60,Clear,stock,65,+0.25,+4.00,-2.00,,combined,10.10,,L25,DuraVision Platinum UV,,ZEISS book p3; book $10.77 L20\n"
    "ZEISS,ZEISS,TestView FSV 1.60,Single vision,1.60,Clear,stock,70,-6.00,+0.00,-2.00,6.00,combined,12.35,,L25,DuraVision Platinum UV,,ZEISS book p3; book $13.17 L20\n"
    # photochromic grind, colours listed in notes
    "Synchrony,ZEISS Synchrony,SV Grind 1.50,Single vision,1.50,Photochromic,grind,,-10.00,+7.00,-5.50,10.00,sphere,44.12,,L25,HMC+,,synchrony book p4; Photo Grey/Brown/Pioneer; book $47.06 L20\n"
    # stock photochromic that names only grey
    "ZEISS,ZEISS,ClearView FSV PFX Extra Grey 1.50,Single vision,1.50,PhotoFusionX,stock,65,+0.25,+4.00,-2.00,,combined,20.25,,L25,DuraVision Platinum UV,,ZEISS book p3\n"
    # photochromic grind with $0 cost - cheaper-looking but unknown
    "ZEISS,ZEISS,FreeLens SV 1.50,Single vision,1.50,PhotoFusionX,grind,,-10.00,+8.00,-6.00,,sphere,0.00,,L25,DuraVision Platinum UV,,ZEISS book p8; PhotoFusionX Grey/Brown; book $0.00 L20\n"
    # photochromic grind with a blank cost cell
    "ZEISS,ZEISS,BlankCost SV 1.50,Single vision,1.50,PhotoFusionX,grind,,-10.00,+8.00,-6.00,,sphere,,,L25,DuraVision Platinum UV,,ZEISS book p8; PhotoFusionX Brown\n"
    # Sensity, no range in the file, no colour named, not ordered
    "Hoya,Hoya,Hilux Sensity 1.50,Single vision,1.50,,grind,,,,,,,31.30,,,Super Hard,no,Sensity photochromic\n"
    # a clear grind for range-edge tests
    "ZEISS,ZEISS,Edge SV 1.50,Single vision,1.50,Clear,grind,,-6.00,+6.00,-2.00,6.00,sphere,30.00,,L25,DuraVision Platinum UV,,ZEISS book p8\n"
)


@pytest.fixture
def rows():
    parsed, errors = lenses.parse_csv_text(CSV, "test.csv")
    assert not errors
    return parsed


def req(r, l=None, **kw):
    q, why = compare.clean_request({"right": r, "left": l if l is not None else r, "kind": "sv", **kw})
    assert q, why
    return q


def by_product(res, name):
    return next(x for x in res["_all"] if x["product"] == name)


# --- engine -------------------------------------------------------------------------

def test_unequal_eyes_pair_total_from_each_eye(rows):
    res = compare.compare(rows, req({"sph": "+2.00", "cyl": "-0.50"}, {"sph": "-2.00", "cyl": "-0.50"},
                                    feature="clear"), {"promo_until": "2000-01-01"})
    tv = by_product(res, "TestView FSV")
    assert tv["cost"] == {"R": "$10.10", "L": "$12.35"}
    assert tv["pair_total"] == "$22.45"          # Decimal: 10.10 + 12.35
    assert tv["verified"]
    assert res["lowest_id"] == tv["id"]
    edge = by_product(res, "Edge SV")
    assert edge["pair_total"] == "$60.00" and edge["vs_lowest"] == "+$37.55"


def test_zero_and_blank_costs_are_unavailable_and_never_cheapest(rows):
    res = compare.compare(rows, req({"sph": "+3.25", "cyl": "-1.25"}, feature="photochromic",
                                    colour="brown"))
    free = by_product(res, "FreeLens SV")
    blank = by_product(res, "BlankCost SV")
    for x in (free, blank):
        assert x["cost_status"] == "unavailable"
        assert x["pair_total"] == compare.COST_MISSING
        assert not x["verified"] and x["vs_lowest"] == ""
    assert res["lowest"]["label"].startswith("1.50 · Single vision · SV Grind")
    assert res["lowest"]["pair_total"] == "$88.24"
    assert res["counts"]["cost_unavailable"] == 2


def test_colour_named_other_is_left_out_unstated_is_not_verified(rows):
    res = compare.compare(rows, req({"sph": "+3.25", "cyl": "-1.25"}, feature="photochromic",
                                    colour="brown"))
    names = [x["product"] for x in res["_all"]]
    assert not any("Extra Grey" in n for n in names)
    assert res["counts"]["other_colour"] >= 1
    hoya = by_product(res, "Hilux Sensity")
    assert hoya["colour"] == "not stated" and not hoya["verified"]
    assert "Not ordered any more (reference only)" in hoya["missing"]
    assert hoya["fit"]["R"] == "not verified"      # no range in the file


def test_wording_never_says_best_for_patient(rows):
    res = compare.compare(rows, req({"sph": "+1.00"}))
    blob = json.dumps(compare.public(res)).lower()
    assert "best for" not in blob
    assert res["lowest_words"] == "Lowest verified cost among priced matching options"


@pytest.mark.parametrize("rx,fits", [
    ({"sph": "+4.00", "cyl": "-2.00"}, True),     # top edge of the 65 band, cyl at limit
    ({"sph": "+4.25", "cyl": "0"}, False),         # past the top
    ({"sph": "+2.00", "cyl": "-2.25"}, False),     # cyl past -2.00
    ({"sph": "-6.00", "cyl": "0"}, True),          # bottom of the 70 band
    ({"sph": "-5.00", "cyl": "-1.50"}, False),     # sph+cyl -6.50 past the starred -6.00
    ({"sph": "+1.00", "cyl": "+1.00"}, True),      # plus cyl transposed -> +2.00 / -1.00
    ({"sph": "+3.50", "cyl": "+1.00"}, False),     # transposed +4.50 / -1.00 -> past +4.00
])
def test_range_edges_and_transposition(rows, rx, fits):
    res = compare.compare(rows, req(rx, feature="clear"))
    names = [x["product"] for x in res["_all"]]
    assert ("TestView FSV" in names) is fits


def test_blank_need_too_small_is_not_verified(rows):
    res = compare.compare(rows, req({"sph": "+2.00"}, feature="clear", min_blank="68"))
    tv = by_product(res, "TestView FSV")
    assert tv["blank_ok"] is False and not tv["verified"]
    assert res["lowest"]["label"].startswith("1.50 · Single vision · Edge SV")


def test_row_ids_stable(rows):
    a = compare.compare(rows, req({"sph": "+1.00"}))
    b = compare.compare(lenses.parse_csv_text(CSV, "test.csv")[0], req({"sph": "+1.00"}))
    assert a["all_ids"] == b["all_ids"]


# --- local pre-send guard -----------------------------------------------------------

NAMES = frozenset({"smith", "nguyen", "brown", "grace", "will", "zztest", "alex"})


@pytest.mark.parametrize("text,why", [
    ("brown photochromic for Smith please", "name"),
    ("what does nguyen need", "name"),
    ("Alex wants grey", "patient_name"),
    ("born 12/05/1987 any stock", "number"),
    ("seen on 3 Nov, stock?", "date"),
    ("12 05 87", "date"),
    ("call 0412 345 678", "number"),
    ("medicare 2123456701", "number"),
    ("x" * 301, "too_long"),
    ("email me a@b.com", "contact"),
])
def test_guard_blocks(text, why):
    clean, reason = lenschat.guard_text(text, NAMES, given="Alex", surname="ZZTEST")
    assert clean == "" and reason == why


@pytest.mark.parametrize("text", [
    "brown photochromic, stock if it can be",
    "+3.25/-1.25x82 both eyes, cheapest 1.67?",
    "is there a 65mm blank or 70 mm",
    "compare zeiss and synchrony polarised grey",
])
def test_guard_allows_lens_words_and_rx_numbers(text):
    clean, reason = lenschat.guard_text(text, NAMES, given="Alex", surname="ZZTEST")
    assert reason == "" and clean


def test_guard_fails_closed_without_name_list():
    assert lenschat.guard_text("brown", None)[1] == "names_unavailable"


def test_bridge_name_words():
    assert lb.name_words(["Mary-Jane", "O'Brien", "Van Der Berg", "X"]) == \
        ["berg", "der", "jane", "mary", "obrien", "van"]


