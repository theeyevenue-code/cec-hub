"""The iPad price sheet: price_sheet/template.html + price_sheet/prices.json -> static/price-sheet.html.

Step 1 (Mark 10 Nov 2026, "that's the one"): the approved 9 Nov demo as the patient price sheet.
Steps 2-3 (10 Nov 2026): the staff side and Make job live in the template itself; the page carries no patient
data (names and the Rx arrive only through the locked /api/sheet calls). The prices come from ONE file,
prices.json, inlined here with its version (a hash of the file); the Hub server reads the same file
(hub/sheetprice.py) and refuses a page built from other prices. Fonts and logos load from the Hub
(/pricesheets/assets, /pricesheets/brand). Edit the template or prices.json, never the built file."""
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "template.html"
PRICES = HERE / "prices.json"
SB = pathlib.Path(r"C:\Users\ConcordeyecareServer\Claude\cec-second-brain")
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "static" / "price-sheet.html"


def build() -> str:
    s = SRC.read_text(encoding="utf-8")

    def rep(old, new, n=1):
        nonlocal s
        assert s.count(old) == n, (old[:70], s.count(old))
        s = s.replace(old, new)

    for w, name in ((300, "Light"), (400, "Regular"), (500, "Medium"), (600, "SemiBold"), (700, "Bold")):
        rep(f"url(data:font/ttf;base64,{{{{F{w}}}}})", f"url(/pricesheets/assets/fonts/Hind-{name}.ttf)")
    z = (SB / "price-lists/assets/zeiss-logo.svg").read_text(encoding="utf-8").replace("<svg ", '<svg class="zlogo" ', 1)
    s = s.replace("{{ZEISS}}", z)
    s = s.replace("{{CEC}}", "/pricesheets/brand/Concord%20Eyecare%20LOGO%20HORIZ.jpg")
    raw = PRICES.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    data["version"] = hashlib.sha256(raw).hexdigest()[:12]     # the server refuses a page built from other prices
    rep("{{PRICES}}", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    assert "{{" not in s, "placeholder left"
    for banned in ("ZZTEST", "?staff=demo", "PATS", "FRAMES = ["):
        assert banned not in s, f"demo data left in the page: {banned}"
    return s


if __name__ == "__main__":
    out = build()
    OUT.write_text(out, encoding="utf-8")
    print("built", OUT, len(out))
