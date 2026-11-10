"""Patient price sheets on screen (Mark, 10 Nov 2026): the approved sheets served unchanged."""
from pathlib import Path

import pytest

from hub import pricesheets

SHEET = """<!DOCTYPE html><html><head><title>Single Vision Lenses</title><style>
@font-face { src:url('../assets/fonts/Hind-Light.ttf'); }</style></head><body><section class="front">
<div class="nav"><div class="navl"><img class="zlogo" src="../assets/zeiss-logo.svg" alt="ZEISS">
<div class="navlinks"><b>Single vision lenses</b><span>Multifocal lenses</span></div></div>
<div class="navr"><img class="ceclogo" src="../../raw-sources/Assets/logo.jpg" alt="Concord Eyecare"></div></div>
<main><div class="card"><div class="idx">1.50</div><div class="price">$200</div></div></main>
</section></body></html>"""


@pytest.fixture
def cfg(tmp_path):
    d = tmp_path / "price-lists" / "patient"
    d.mkdir(parents=True)
    (d / "CEC-SingleVision-2026-09.html").write_text(SHEET.replace("$200", "$180"), encoding="utf-8")
    (d / "CEC-SingleVision-2026-11.html").write_text(SHEET, encoding="utf-8")
    (d / "CEC-Multifocal-2026-11.html").write_text(SHEET, encoding="utf-8")
    (d / "CEC-SingleVision-PriceList-Patient.html").write_text("old undated", encoding="utf-8")
    return {"price_sheets": {"dir": str(d)}}


def test_newest_dated_sheet_wins(cfg):
    p = pricesheets.latest(Path(cfg["price_sheets"]["dir"]), "SingleVision")
    assert p.name == "CEC-SingleVision-2026-11.html"


def test_prices_and_words_are_untouched(cfg):
    html = pricesheets.render(cfg, "single-vision")
    assert "$200" in html and "$180" not in html
    assert '<div class="idx">1.50</div>' in html


def test_menu_links_only_the_sheets_present(cfg):
    html = pricesheets.render(cfg, "multifocal")
    assert '<a href="/pricesheets/single-vision">Single vision lenses</a>' in html
    assert '<a href="/pricesheets/multifocal" class="on">Multifocal lenses</a>' in html
    assert "/pricesheets/sunglasses" not in html


def test_assets_point_at_the_hub(cfg):
    html = pricesheets.render(cfg, "single-vision")
    assert "../assets/" not in html and "/pricesheets/assets/zeiss-logo.svg" in html
    assert "/pricesheets/brand/logo.jpg" in html
    assert 'name="viewport"' in html and "hub-screen-js" in html


def test_missing_sheet_is_none(cfg):
    assert pricesheets.render(cfg, "sunglasses") is None


def test_routes(cfg, monkeypatch):
    import app as A
    monkeypatch.setattr(A, "_integrations", lambda: cfg)
    c = A.app.test_client()
    r = c.get("/pricesheets/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/pricesheets/single-vision")
    r = c.get("/pricesheets/single-vision")
    assert r.status_code == 200 and b"$200" in r.data
    assert c.get("/pricesheets/nonsense").status_code == 404
    assert c.get("/pricesheets/assets/../../secret.txt").status_code == 404
