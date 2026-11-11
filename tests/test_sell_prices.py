"""Sell prices per material (Mark, 8 Nov 2026: 'use the qr price sheet on google drive as the
reference'). Every material of a lens (Clear, BluePro, PhotoFusion X, Polarised) carries its own
sheet price; the Lens options page, the compare view and the Lens Finder look it up by material.
The figures are the October 2026 QR sheet's, read from the shipped config/cec_prices.example.json."""
import json
from pathlib import Path

from hub import lenses, lensoptions

ROOT = Path(__file__).resolve().parents[1]
# one price source (11 Nov 2026): the shipped file + the Order Pad's prices.json, as the Hub serves them
from hub import sheetprice  # noqa: E402
CFG = sheetprice.finder_prices(json.loads((ROOT / "config" / "cec_prices.example.json").read_text(encoding="utf-8")))

HEAD = "supplier,brand,lens,category,index,material,type,blank_mm,sph_min,sph_max,cyl_max,add_min,add_max,price,coating\n"
ROWS = HEAD + (
    "ZEISS,ZEISS,SmartLife Progressive Superb 1.50,Progressive,1.50,Clear,grind,,-10,+6,-4,0.75,3.50,111.52,DuraVision Plus Platinum UV\n"
    "ZEISS,ZEISS,SmartLife Progressive Superb 1.50,Progressive,1.50,Clear BluePro,grind,,-10,+6,-4,0.75,3.50,116.11,DuraVision Plus Platinum UV\n"
    "ZEISS,ZEISS,SmartLife Progressive Superb 1.50,Progressive,1.50,PhotoFusionX,grind,,-7,+5,-4,0.75,3.50,144.75,DuraVision Plus Platinum UV\n"
    "ZEISS,ZEISS,SmartLife Progressive Superb 1.50,Progressive,1.50,Polarised,grind,,-8,+5,-4,0.75,3.50,145.59,DuraVision Plus Platinum UV\n"
    "ZEISS,ZEISS,SmartLife Progressive Superb 1.50,Progressive,1.50,AdaptiveSun Polarised,grind,,-8,+5,-4,0.75,3.50,173.86,DuraVision Plus Platinum UV\n"
    "ZEISS,ZEISS,SmartLife Progressive Superb 1.60,Progressive,1.60,Clear,grind,,-10,+6,-4,0.75,3.50,120.00,DuraVision Plus Platinum UV\n"
    "ZEISS,ZEISS,ClearView FSV 1.67,Single vision,1.67,Clear,stock,75,-6,0,-2,,,40.00,DuraVision Platinum UV\n"
    "ZEISS,ZEISS,ClearView Single Vision 1.60,Single vision,1.60,Clear BluePro,grind,,-10,+8,-4,,,60.00,DuraVision Plus Platinum UV\n"
    "ZEISS,ZEISS,ClearView Single Vision 1.60,Single vision,1.60,Polarised,grind,,-8,+6,-4,,,70.00,DuraVision Plus Platinum UV\n"
    "Synchrony,ZEISS Synchrony,FSV Clear 1.60,Single vision,1.60,Clear,stock,70,-6,+4,-2,,,20.00,HMC+\n"
    "Synchrony,ZEISS Synchrony,FSV Photo Grey 1.50,Single vision,1.50,Photochromic,stock,70,-6,+4,-2,,,30.00,HMC+\n"
    "Synchrony,ZEISS Synchrony,FSV Photo Grey 1.50,Single vision,1.50,Photochromic,stock,70,-6,+4,-2,,,25.00,DSHC hard coat\n"
    "Synchrony,ZEISS Synchrony,Single Vision Grind 1.50,Single vision,1.50,Polarised,grind,,-6,+6,-2,,,40.00,HC hard coat\n"
    "Synchrony,ZEISS Synchrony,Single Vision Grind 1.50,Single vision,1.50,Polarised,grind,,-6,+6,-2,,,50.00,HMC+\n"
)


def _priced():
    rows, _ = lenses.parse_csv_text(ROWS, "t.csv")
    prods = lenses.attach_cec_price(lenses.group_products(rows), CFG)
    return rows, prods, lensoptions.sell_index(prods)


def _row(rows, name, material, coating=None):
    return next(r for r in rows if r["name"] == name and r["material"] == material
                and (coating is None or r["coating"] == coating))


def test_each_material_is_its_own_product():
    _, prods, _ = _priced()
    superb = [p["material"] for p in prods if p["name"] == "SmartLife Progressive Superb 1.50"]
    assert sorted(superb) == ["AdaptiveSun Polarised", "Clear", "Clear BluePro", "PhotoFusionX", "Polarised"]


def test_superb_150_photofusion_sells_at_the_sheet_710():
    rows, _, sells = _priced()
    assert lensoptions.sell_for(sells, _row(rows, "SmartLife Progressive Superb 1.50", "PhotoFusionX")) == 710


def test_clear_lens_unchanged_and_superb_160_matches_the_sheet():
    rows, _, sells = _priced()
    assert lensoptions.sell_for(sells, _row(rows, "SmartLife Progressive Superb 1.50", "Clear")) == 560
    assert lensoptions.sell_for(sells, _row(rows, "SmartLife Progressive Superb 1.60", "Clear")) == 690


def test_bluepro_adds_50():
    rows, _, sells = _priced()
    assert lensoptions.sell_for(sells, _row(rows, "SmartLife Progressive Superb 1.50", "Clear BluePro")) == 610
    # single-vision grind: 1.60 $250 + $100 grind + $50 blue = the sheet's $400
    assert lensoptions.sell_for(sells, _row(rows, "ClearView Single Vision 1.60", "Clear BluePro")) == 400


def test_polarised_priced_only_where_the_sheet_prints_it():
    rows, _, sells = _priced()
    assert lensoptions.sell_for(sells, _row(rows, "SmartLife Progressive Superb 1.50", "Polarised")) == 710
    assert lensoptions.sell_for(sells, _row(rows, "SmartLife Progressive Superb 1.50", "AdaptiveSun Polarised")) is None
    assert lensoptions.sell_for(sells, _row(rows, "ClearView Single Vision 1.60", "Polarised")) is None
    sun = "Single Vision Grind 1.50"
    assert lensoptions.sell_for(sells, _row(rows, sun, "Polarised", "HC hard coat")) == 250
    assert lensoptions.sell_for(sells, _row(rows, sun, "Polarised", "HMC+")) == 340


def test_per_coating_price_and_unlisted_coating_has_none():
    rows, _, sells = _priced()
    assert lensoptions.sell_for(sells, _row(rows, "FSV Photo Grey 1.50", "Photochromic", "HMC+")) == 240
    # the hard-coat Photo Grey is not on the sheet: no price, and no borrowing the HMC+ one
    assert lensoptions.sell_for(sells, _row(rows, "FSV Photo Grey 1.50", "Photochromic", "DSHC hard coat")) is None


def test_single_material_lens_keeps_the_old_key_fallback():
    rows, _, sells = _priced()
    # one material only: the material-less key still answers (old callers, rows with no material)
    assert sells[("ZEISS", "ClearView FSV 1.67", "1.67", "stock")]["DuraVision Platinum UV"] == 350
    row = dict(_row(rows, "ClearView FSV 1.67", "Clear"), material="")
    assert lensoptions.sell_for(sells, row) == 350
    # several materials at different prices: no material-less key, so nothing can borrow
    assert ("ZEISS", "SmartLife Progressive Superb 1.50", "1.50", "grind") not in sells


def test_lens_options_line_shows_the_material_price():
    rows, _, sells = _priced()
    o = dict(_row(rows, "SmartLife Progressive Superb 1.50", "PhotoFusionX"), price_now=144.75)
    assert lensoptions._lens_out(o, sells, None)["sell"] == 710


def test_one_price_source_no_figure_in_two_places():
    raw = json.loads((ROOT / "config" / "cec_prices.example.json").read_text(encoding="utf-8"))
    book = sheetprice.load()
    for spec in book["map"]["lens_finder"]["tiers"]:
        t = next(x for x in raw["tiers"] if x["name"] == spec["name"] and not x.get("materials") and not x.get("by_coating"))
        assert not set(t["by_index"]) & set(sheetprice._sheet_by_index(book, spec["from"])), spec["name"]
    assert raw["addons"] == {}
    # a stale per-machine file (the 15 Sep one) is overruled by the sheet
    old = {"tiers": [{"name": "SV Premium", "category": "Single vision", "match": ["ClearView FSV"],
                      "by_index": {"1.67": 300, "1.74": 460}}],
           "addons": {"PhotoFusion|Photochromic|Transitions|Sensity": 130}}
    new = sheetprice.finder_prices(old)
    sv = next(x for x in new["tiers"] if x["name"] == "SV Premium")
    assert sv["by_index"]["1.67"] == 350 and sv["by_index"]["1.74"] == 500
    assert new["addons"]["PhotoFusion|Photochromic|Transitions|Sensity"] == 150
    assert any(x["name"] == "Office" for x in new["tiers"])        # added from the sheet when the file lacks it
