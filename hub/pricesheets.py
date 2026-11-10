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

# The screen additions live in static/pricesheets-screen.js (served fresh: no restart to tweak them).
SCREEN_JS_FILE = Path(__file__).resolve().parent.parent / "static" / "pricesheets-screen.js"


def screen_tag() -> str:
    """The script tag, versioned by the file's change time so an edit reaches the iPad without a restart."""
    try:
        v = int(SCREEN_JS_FILE.stat().st_mtime)
    except OSError:
        v = 0
    return f'<script id="hub-screen-js" src="/pricesheets-screen.js?v={v}"></script>'


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
    head_add = '<meta name="viewport" content="width=device-width, initial-scale=1">'
    html = html.replace("</head>", head_add + "</head>", 1)
    html = html.replace("</body>", screen_tag() + "</body>", 1)
    return html


def asset_dirs(cfg: dict) -> dict[str, Path]:
    folder = sheets_dir(cfg)
    return {"assets": folder.parent / "assets",
            "brand": folder.parent.parent / "raw-sources" / "Assets"}
