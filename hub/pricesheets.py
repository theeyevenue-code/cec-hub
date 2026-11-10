"""Patient price sheets on screen (Mark, 10 Nov 2026: "the one that does sheet like zeiss patient facing
and i click into it").

The approved, printed patient sheets (second-brain price-lists/patient/CEC-<Type>-YYYY-MM.html, built and
signed off by the Front Desk lane) are served AS THEY ARE: the newest file per lens type, unchanged words
and prices. The only additions are for the screen: the top menu becomes tappable, the page scales to the
screen, and a tap on a lens card shows it large. Nothing is retyped here, so the screen can never
disagree with the printed sheet.

No patient data: these routes need no staff code (the page is shown to patients).
"""
from __future__ import annotations

import re
from pathlib import Path

# slug, file stem in CEC-<stem>-YYYY-MM.html, menu label (the sheets' own words)
SHEETS = (
    ("single-vision", "SingleVision", "Single vision lenses"),
    ("multifocal", "Multifocal", "Multifocal lenses"),
    ("anti-fatigue", "AntiFatigue", "Anti-Fatigue lenses"),
    ("bifocal", "Bifocal", "Bifocal lenses"),
    ("sunglasses", "Sunglasses", "Sunglasses"),
)
SLUGS = tuple(s[0] for s in SHEETS)
_DATED = re.compile(r"^CEC-([A-Za-z]+)-(\d{4})-(\d{2})\.html$")

SCREEN_CSS = """
<style id="hub-screen">
/* Hub screen additions (not on the printed sheet): tappable menu, tap a card to see it large. */
html, body { overflow-x:hidden; }
.navlinks { gap:7mm !important; font-size:15px !important; }
.navlinks a { color:inherit; text-decoration:none; padding:4mm 0; }
.navlinks a.on { font-weight:600; color:#0E0F11; border-bottom:2px solid #0F2DB3; }
.hub-home { font-size:15px; color:#606A76; text-decoration:none; margin-right:4mm; }
.card { cursor:pointer; transition:transform .15s ease; }
.card:active { transform:scale(.99); }
#hub-shade { position:fixed; inset:0; background:rgba(14,15,17,.55); display:none; align-items:center;
             justify-content:center; z-index:50; }
#hub-shade.show { display:flex; }
#hub-shade .card { transform:scale(1.6); transform-origin:center; cursor:default; box-shadow:0 8px 40px rgba(0,0,0,.25);
                   max-width:80mm; }
#hub-shade .close { position:absolute; top:6mm; right:8mm; color:#fff; font:600 14px Hind,Arial,sans-serif; }
@media print { #hub-shade, .hub-home { display:none !important; } }
</style>
"""

SCREEN_JS = """
<script id="hub-screen-js">
(function () {
  function fit() {
    var w = document.documentElement.clientWidth, b = document.body;
    b.style.zoom = ""; var full = b.scrollWidth || w;
    if (full > w) b.style.zoom = (w / full).toFixed(3);
  }
  window.addEventListener("resize", fit); window.addEventListener("load", fit); fit();
  var shade = document.createElement("div"); shade.id = "hub-shade";
  document.body.appendChild(shade);
  shade.addEventListener("click", function () { shade.className = ""; shade.innerHTML = ""; });
  document.querySelectorAll("main .card").forEach(function (c) {
    c.addEventListener("click", function () {
      shade.innerHTML = '<span class="close">Close</span>';
      shade.appendChild(c.cloneNode(true)); shade.className = "show";
    });
  });
})();
</script>
"""


def sheets_dir(cfg: dict) -> Path:
    """config\\integrations.json price_sheets.dir, else the second-brain clone on this PC."""
    d = ((cfg or {}).get("price_sheets") or {}).get("dir")
    if d:
        return Path(d)
    return Path.home() / "Claude" / "cec-second-brain" / "price-lists" / "patient"


def latest(folder: Path, stem: str) -> Path | None:
    """The newest dated sheet for one lens type (CEC-<stem>-YYYY-MM.html)."""
    best = None
    if not folder.is_dir():
        return None
    for p in folder.iterdir():
        m = _DATED.match(p.name)
        if m and m.group(1) == stem:
            key = (m.group(2), m.group(3))
            if best is None or key > best[0]:
                best = (key, p)
    return best[1] if best else None


def available(cfg: dict) -> list[tuple[str, str, Path]]:
    folder = sheets_dir(cfg)
    out = []
    for slug, stem, label in SHEETS:
        p = latest(folder, stem)
        if p:
            out.append((slug, label, p))
    return out


def _menu(sheets, current: str) -> str:
    on = ' class="on"'
    links = "".join(
        f'<a href="/pricesheets/{slug}"{on if slug == current else ""}>{label}</a>'
        for slug, label, _p in sheets)
    return f'<div class="navlinks">{links}</div>'


def render(cfg: dict, slug: str) -> str | None:
    """The sheet's own HTML with the screen additions, or None if there is no such sheet."""
    sheets = available(cfg)
    match = [s for s in sheets if s[0] == slug]
    if not match:
        return None
    html = match[0][2].read_text(encoding="utf-8")
    html = re.sub(r'<div class="navlinks">.*?</div>', lambda _m: _menu(sheets, slug), html, count=1, flags=re.S)
    html = html.replace("../assets/", "/pricesheets/assets/")
    html = html.replace("../../raw-sources/Assets/", "/pricesheets/brand/")
    html = html.replace('<div class="navr">', '<div class="navr"><a class="hub-home" href="/">Hub</a>', 1)
    head_add = '<meta name="viewport" content="width=device-width, initial-scale=1">' + SCREEN_CSS
    html = html.replace("</head>", head_add + "</head>", 1)
    html = html.replace("</body>", SCREEN_JS + "</body>", 1)
    return html


def asset_dirs(cfg: dict) -> dict[str, Path]:
    folder = sheets_dir(cfg)
    return {"assets": folder.parent / "assets",
            "brand": folder.parent.parent / "raw-sources" / "Assets"}
