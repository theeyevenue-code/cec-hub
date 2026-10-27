"""Power-range audit (Mark, 23 Oct 2026): a lens is called STOCK only when the
script fits one specific band of the real ZEISS/synchrony catalogue.

These run against the shipped lenses/zeiss.csv + synchrony.csv (supplier
price data, no patient data). Every script here is made up.

Sources: ZEISS L20 price book p3 (stock bands) and the ZEISS Product
Portfolio pp29/31 (rendered pages). Stock ranges are in minus-cyl form; the
starred number is the maximum minus combined power (sph + cyl).
"""

from pathlib import Path

import pytest

from hub import lenses

LENS_DIR = Path(__file__).resolve().parents[1] / "lenses"


@pytest.fixture(scope="module")
def rows():
    cat = lenses.load_catalog(LENS_DIR)
    return [l for l in cat["lenses"] if l["supplier"] in ("ZEISS", "Synchrony")]


def _find(rows, sph, cyl=0.0, **kw):
    return lenses.find_options(rows, sph, cyl, kind="Single vision", **kw)


def _stock(result, name=None, index=None):
    """{(name, index): sorted blanks} of the stock options."""
    out = {}
    for o in result["options"]:
        if o["type"] != "stock":
            continue
        if name and o["name"] != name:
            continue
        if index is not None and abs((o["index"] or 0) - index) > 0.001:
            continue
        out.setdefault((o["name"], o["index"]), set()).add(o["blank_mm"])
    return {k: sorted(v) for k, v in out.items()}


def _cv(result, idx):
    return _stock(result, f"ClearView FSV {idx:.2f}", idx).get((f"ClearView FSV {idx:.2f}", idx), [])


def test_plus2_minus3_is_grind_at_every_index(rows):
    # Mark's case. Sphere +2.00 sits in the plus bands, and every plus band
    # (1.50 / 1.60 / 1.67, ZEISS and synchrony) is cyl to -2.00.
    r = _find(rows, 2.0, -3.0)
    assert _stock(r) == {}
    assert r["headline"] == "GRIND: outside stock range (plus sphere: cyl limit -2.00)"
    assert r["verdict"].startswith("GRIND")
    best = next(o for o in r["options"] if o.get("best"))
    assert best["type"] == "grind"
    # 1.60 / 1.67 75mm minus bands are explicitly refused on the sphere
    why = [m["reasons"][0] for m in r["misses"]
           if m["name"] == "ClearView FSV 1.60" and m["blank_mm"] == 75]
    assert why and why[0].startswith("sphere +2.00 is outside this band (75 mm · 0.00 to -6.00")


def test_plus2_minus2_is_stock_on_the_65mm_band(rows):
    r = _find(rows, 2.0, -2.0)
    assert 65 in _cv(r, 1.60)
    assert 65 in _cv(r, 1.50)
    band = next(o for o in r["options"]
                if o["name"] == "ClearView FSV 1.60" and o["blank_mm"] == 65)
    assert band["band_text"] == "65 mm · +2.00 to +6.00, cyl to -2.00"


def test_minus2_minus3(rows):
    r = _find(rows, -2.0, -3.0)
    assert _cv(r, 1.60) == [75]
    assert _cv(r, 1.50) == []
    m150 = [m for m in r["misses"] if m["name"] == "ClearView FSV 1.50"]
    assert m150 and all("cyl -3.00 is beyond this band limit (-2.00)" in m["reasons"]
                        for m in m150 if m["blank_mm"] == 70)
    assert _cv(r, 1.67) == [75]
    b = next(o for o in r["options"] if o["name"] == "ClearView FSV 1.60")
    assert b["band_text"] == "75 mm · 0.00 to -6.00, cyl to -3.00"


def test_minus650_minus2_follows_the_combined_rule_and_is_flagged(rows):
    # -8.50 in the strongest meridian. 1.60: past the 70mm band's -8.00* on
    # both readings -> grind. 1.67: 70mm band (-6.25 to -10.00*) on both.
    # 1.74: only ZEISS's combined reading puts it on the 70mm band -> possible,
    # flagged unproven, never stock.
    r = _find(rows, -6.5, -2.0)
    assert _cv(r, 1.60) == []
    assert _cv(r, 1.67) == [70]
    assert _cv(r, 1.74) == []
    poss = [p for p in r["possible"] if p["name"] == "ClearView FSV" and p["index"] == 1.74]
    assert [p["blank_mm"] for p in poss] == [70]
    assert "only if ZEISS counts sphere + cyl" in poss[0]["why"]
    assert "NOT confirmed" in r["verdict"] or r["possible"]


def test_minus4_minus4(rows):
    # 1.60: every band is cyl -3.00 or less -> grind.
    # 1.67: the 75mm band takes the -4.00 cyl but its starred max combined is
    # -6.00 (book p3 AND portfolio p31) and this lens is -8.00; the 70mm band
    # starts at sphere -6.25. Only the unproven combined reading puts it on
    # 70mm, so it is "possible — confirm", not stock.
    r = _find(rows, -4.0, -4.0)
    assert _cv(r, 1.60) == []
    assert _cv(r, 1.67) == []
    poss = [p for p in r["possible"] if p["index"] == 1.67 and p["name"] == "ClearView FSV"]
    assert [p["blank_mm"] for p in poss] == [70]
    assert r["headline"].startswith("GRIND: outside stock range (sphere + cyl -8.00")
    # Once VISUSTORE confirms the combined reading it becomes stock, 70mm.
    ok = _find(rows, -4.0, -4.0, pricing={"stock_combined_confirmed": True})
    assert _cv(ok, 1.67) == [70]


def test_plus625_is_grind_and_the_disputed_strip_says_confirm(rows):
    # Book p3: 1.60 65mm to +6.00. Portfolio p31: +6.25. Not stock; confirm.
    r = _find(rows, 6.25)
    assert _stock(r) == {}
    assert r["headline"].startswith("GRIND")
    poss = [p for p in r["possible"] if p["index"] == 1.60 and p["blank_mm"] == 65]
    assert poss and "disagree" in poss[0]["why"]


def test_174_has_no_plus_stock(rows):
    for sph in (0.25, 1.0, 2.0, 4.0, 6.0):
        assert _cv(_find(rows, sph), 1.74) == []


def test_both_eyes_must_fit_the_same_lens(rows):
    # R -2.00/-3.00 (1.60/1.67 75mm) with L +2.00/-3.00 (no stock anywhere).
    r = _find(rows, -2.0, -3.0, left={"sph": 2.0, "cyl": -3.0})
    assert _stock(r) == {}
    # R -2.00/-3.00 with L +2.00/-2.00: 1.60 ClearView fits both, each on its band.
    r = _find(rows, -2.0, -3.0, left={"sph": 2.0, "cyl": -2.0})
    o = next(o for o in r["options"] if o["name"] == "ClearView FSV 1.60"
             and o["coating"] == "DuraVision Platinum UV")
    assert o["band_text"].startswith("75 mm")
    assert o["band_text_l"].startswith(("65 mm", "70 mm"))
    # plus-cyl scripts from the recent-Rx tiles transpose on both eyes
    r2 = _find(rows, -5.0, 3.0, left={"sph": "0.00", "cyl": "+2.00"})
    assert r2["rx"]["display"] == "-2.00 / -3.00"


def test_every_stock_row_band_text_matches_its_numbers(rows):
    # The answer card and the table both print band_text: check it is the
    # row's own numbers for every stock band in the catalogue.
    for l in rows:
        if l["type"] != "stock":
            continue
        t = lenses.band_text(l)
        assert t.startswith(f"{l['blank_mm']:g} mm · ")
        assert f"cyl to -{l['cyl_max']:.2f}" in t
        for v in (l["sph_min"], l["sph_max"]):
            assert ("0.00" if abs(v) < 0.005 else f"{v:+.2f}") in t


def test_disputed_bands_hold_the_narrower_range(rows):
    by = {(l["name"], l["coating"], l["blank_mm"], l["sph_min"]): l for l in rows}
    cv160 = by[("ClearView FSV 1.60", "DuraVision Platinum UV", 65, 2.0)]
    assert cv160["sph_max"] == 6.0 and cv160["check"]["sph_max"] == 6.25
    assert "CHECK" in cv160["notes"]
    sph75 = [l for l in rows if l["name"] == "Sph FSV 1.60" and l["blank_mm"] == 75][0]
    assert sph75["sph_min"] == -6.0 and sph75["check"]["sph_min"] == -8.0


def test_api_headline_and_table_share_one_string(hub_client_lenses):
    # the endpoint passes left-eye params through; old single-eye calls unchanged
    client, _ = hub_client_lenses
    res = client.get("/api/lenses/find?sph=-3.00&cyl=-1.00&lsph=-2.75")
    assert res.status_code == 200
    data = res.get_json()
    assert "headline" in data and "possible" in data
    best = next((o for o in data["options"] if o.get("best")), None)
    if best and best["type"] == "stock":
        assert best["band_text"] in data["headline"]


def test_same_lens_same_price_takes_the_larger_blank(rows):
    # synchrony book p3: FSV Clear 1.67 HMC+ is +2.25 to +6.00 on 65 mm AND 0.00 to +6.00 on
    # 75 mm, one price. Before 27 Oct 2026 the finder led with the 65; now the 75 (Mark).
    for sph in (4.25, 5.0, 6.0):
        r = _find(rows, sph, -1.5)
        best = next(o for o in r["options"] if o.get("best"))
        assert (best["supplier"], best["index"], best["blank_mm"]) == ("Synchrony", 1.67, 75), sph
    # ClearView 1.60 Platinum: +2.00 to +4.00 on 65 AND 70 mm at the deal price -> 70 first
    r = _find(rows, 3.0)
    cv = [o["blank_mm"] for o in r["options"]
          if o["name"] == "ClearView FSV 1.60" and o["coating"] == "DuraVision Platinum UV"]
    assert cv[:2] == [70, 65]
