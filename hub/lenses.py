"""Lens catalogue + best-option finder.

The catalogue is plain CSV files in the Hub's own lenses\\ folder — one file
per supplier (zeiss.csv, synchrony.csv, hoya.csv). Files are read fresh on
every request, so dropping in a new CSV (or uploading one from the Lens
Finder page) takes effect on the next page load. Files whose name starts
with an underscore (like _template.csv) are ignored.

Column contract lives in lenses\\README.md. Headers are matched loosely
(case, spaces, a handful of aliases) and numbers shrug off "$", "mm" and
stray "+" signs, because these files get hand-edited from supplier PDFs.

find_options() answers the real question at the bench: for this Rx (and
this frame), which lenses can make the job, which one the practice's own
rules say to reach for, and what the alternatives cost.

Since the ZEISS switch (Sep 2026) every row also knows WHO supplies it,
WHICH price applies (the negotiated deal price, the six-month promo level,
or the book) and whether the practice still orders it at all.
"""

import csv
import datetime as _dt
import io
import math
import re
from pathlib import Path

MAX_ROWS_PER_FILE = 5000
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

# Loose header matching: lowercase, spaces/dashes -> underscore, then alias.
HEADER_ALIASES = {
    "brand": "brand", "manufacturer": "brand",
    "supplier": "supplier", "lab": "supplier", "ordered_from": "supplier",
    "lens": "name", "name": "name", "lens_name": "name", "product": "name",
    "code": "code", "product_code": "code", "lens_type": "code",
    "lenstype": "code", "barcode": "code", "order_code": "code",
    "index": "index", "material_index": "index", "refractive_index": "index",
    "material": "material", "colour": "material", "color": "material",
    "variant": "material", "treatment": "material",
    "type": "type", "stock_or_grind": "type", "stock_grind": "type",
    "category": "category", "lens_category": "category", "vision": "category",
    "form": "form", "spherical_aspherical": "form", "spheric_aspheric": "form",
    "add_range": "add_range", "add": "add_range", "add_power": "add_range",
    "addition": "add_range", "adds": "add_range",
    "add_min": "add_min", "min_add": "add_min",
    "add_max": "add_max", "max_add": "add_max",
    "design": "design", "vision_type": "design",
    "blank_mm": "blank_mm", "blank": "blank_mm", "blank_size": "blank_mm",
    "diameter": "blank_mm", "dia": "blank_mm", "size": "blank_mm",
    "sph_min": "sph_min", "sphere_min": "sph_min", "min_sph": "sph_min",
    "sph_max": "sph_max", "sphere_max": "sph_max", "max_sph": "sph_max",
    "sph_range": "sph_range", "sphere_range": "sph_range", "range": "sph_range",
    "power_range": "sph_range",
    "cyl_max": "cyl_max", "cyl": "cyl_max", "max_cyl": "cyl_max",
    "cyl_to": "cyl_max", "cyl_range": "cyl_max",
    "combined_max": "combined_max", "max_combined": "combined_max",
    "total_power_max": "combined_max", "sph_plus_cyl_max": "combined_max",
    "price": "price", "cost": "price", "price_per_lens": "price",
    "cost_per_lens": "price",
    "price_promo": "price_promo", "promo_price": "price_promo", "promo": "price_promo",
    "price_basis": "price_basis", "basis": "price_basis", "pricing": "price_basis",
    "orderable": "orderable", "ordered": "orderable", "active": "orderable",
    "min_fh_mm": "min_fh_mm", "min_fh": "min_fh_mm",
    "min_fitting_height": "min_fh_mm", "fitting_height_min": "min_fh_mm",
    "coating": "coating", "coat": "coating",
    "range_basis": "range_basis", "basis_of_range": "range_basis",
    "notes": "notes", "note": "notes", "comments": "notes",
}

STOCK_WORDS = {"stock", "finished", "uncut", "fsv"}
GRIND_WORDS = {"grind", "grinding", "surfaced", "rx", "lab", "freeform",
               "made_to_order", "made to order", "mto"}
NO_WORDS = {"no", "n", "false", "0", "off", "retired", "discontinued"}

# Canonical categories. Anything else is kept verbatim (browse-only).
CATEGORY_WORDS = {
    "": "Single vision", "sv": "Single vision", "single vision": "Single vision",
    "single": "Single vision", "fsv": "Single vision",
    "progressive": "Progressive", "progressives": "Progressive",
    "multifocal": "Progressive", "prog": "Progressive", "pal": "Progressive",
    "occupational": "Occupational", "office": "Occupational",
    "workplace": "Occupational", "desk": "Occupational",
    "bifocal": "Bifocal", "bifocals": "Bifocal", "trifocal": "Bifocal",
    "anti-fatigue": "Anti-fatigue", "antifatigue": "Anti-fatigue",
    "anti fatigue": "Anti-fatigue", "digital": "Anti-fatigue",
    "screen": "Anti-fatigue", "sync": "Anti-fatigue",
}
# Categories that are chosen by an add power as well as the distance Rx.
ADD_CATEGORIES = {"Progressive", "Occupational", "Bifocal", "Anti-fatigue"}

NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _num(value):
    """'$18.50' / '65mm' / '+4.00' -> float. Anything unreadable -> None."""
    if value is None:
        return None
    s = str(value).strip().lower().replace("$", "").replace(",", "")
    s = s.replace("mm", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


parse_number = _num  # for the routes: user-typed powers/sizes, same shrugs


def _range(value):
    """'+4.00 to -4.00' (any separator, any order) -> (min, max) or None."""
    nums = [float(n) for n in NUM_RE.findall(str(value or ""))]
    if len(nums) < 2:
        return None
    return min(nums), max(nums)


def _blank(value):
    """'70', '75mm' or a supplier list like '65/70/75' -> the largest
    diameter offered (that's what decides whether a frame can be cut)."""
    nums = [float(n) for n in NUM_RE.findall(str(value or ""))]
    return max(nums) if nums else None


def _fmt_power(v):
    return f"{v:+.2f}"


def _fmt_mm(v):
    return f"{v:g}mm"


def _lens_type(raw, blank_mm):
    s = str(raw or "").strip().lower().replace("-", "_")
    if s in STOCK_WORDS:
        return "stock"
    if s in GRIND_WORDS:
        return "grind"
    # No usable type column: a blank diameter suggests a stock lens,
    # no diameter suggests it's ground/surfaced to size.
    return "stock" if blank_mm is not None else "grind"


def normalise_category(raw) -> str:
    s = str(raw or "").strip().lower()
    return CATEGORY_WORDS.get(s, str(raw or "").strip())


def parse_date(value):
    """'2027-03-11' -> date, else None."""
    try:
        return _dt.date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def parse_csv_text(text: str, source: str):
    """CSV text -> (lenses, errors). Never raises.

    A row only NEEDS a lens name. Sphere range (sph_min+sph_max, or one
    sph_range column like '+4.00 to -4.00'), cyl, blank size and price are
    all optional — missing limits surface as warnings when matching, never
    as guesses. cyl_max / combined_max are stored as magnitudes.
    """
    lenses, errors = [], []
    try:
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
    except csv.Error as e:
        return [], [f"{source}: the file couldn't be read as a CSV "
                    f"({e.__class__.__name__})."]
    if not rows:
        return [], [f"{source}: the file is empty."]

    header = []
    for h in rows[0]:
        key = re.sub(r"[\s\-]+", "_", str(h).strip().lower().lstrip("﻿"))
        header.append(HEADER_ALIASES.get(key, ""))
    if "name" not in header:
        return [], [f"{source}: no 'lens' (name) column found — check the "
                    "headers against lenses\\README.md."]

    for rownum, row in enumerate(rows[1:MAX_ROWS_PER_FILE + 1], start=2):
        if not any(str(c).strip() for c in row):
            continue
        cells = {}
        for i, col in enumerate(header):
            if col and i < len(row) and str(row[i]).strip():
                cells[col] = str(row[i]).strip()

        name = cells.get("name")
        if not name:
            errors.append(f"{source} row {rownum}: no lens name.")
            continue

        # Sphere range is optional — supplier PRICE lists usually don't
        # carry it (the availability guide does). A rangeless lens still
        # matches, with a "check the guide" warning.
        sph_min, sph_max = _num(cells.get("sph_min")), _num(cells.get("sph_max"))
        if sph_min is None and sph_max is None:
            rng = _range(cells.get("sph_range"))
            if rng:
                sph_min, sph_max = rng
        if (sph_min is None) != (sph_max is None):
            errors.append(f"{source} row {rownum} ({name}): only half a "
                          "sphere range — give both sph_min and sph_max, "
                          "or neither.")
            continue
        if sph_min is not None and sph_min > sph_max:
            sph_min, sph_max = sph_max, sph_min

        # Add range: explicit add_min/add_max, else read off the free text
        # ('Add +0.75 to +3.50', '0.75 to 4.00').
        add_min, add_max = _num(cells.get("add_min")), _num(cells.get("add_max"))
        if add_min is None and add_max is None:
            rng = _range(cells.get("add_range"))
            if rng:
                add_min, add_max = rng
        if add_min is not None and add_max is not None and add_min > add_max:
            add_min, add_max = add_max, add_min

        blank_mm = _blank(cells.get("blank_mm"))
        cyl_max = _num(cells.get("cyl_max"))
        combined_max = _num(cells.get("combined_max"))
        brand = cells.get("brand", "")
        orderable_raw = str(cells.get("orderable", "")).strip().lower()
        lenses.append({
            "brand": brand,
            "supplier": cells.get("supplier", "") or brand,
            "name": name,
            "code": cells.get("code", ""),
            "category": normalise_category(cells.get("category", "")),
            "index": _num(cells.get("index")),
            "material": cells.get("material", ""),
            "type": _lens_type(cells.get("type"), blank_mm),
            "form": cells.get("form", ""),
            "add_range": cells.get("add_range", ""),
            "add_min": add_min,
            "add_max": add_max,
            "design": cells.get("design", ""),
            "blank_mm": blank_mm,
            "sph_min": sph_min,
            "sph_max": sph_max,
            "cyl_max": abs(cyl_max) if cyl_max is not None else None,
            "combined_max": abs(combined_max) if combined_max is not None else None,
            "min_fh_mm": _num(cells.get("min_fh_mm")),
            "price": _num(cells.get("price")),
            "price_promo": _num(cells.get("price_promo")),
            "price_basis": cells.get("price_basis", ""),
            "orderable": orderable_raw not in NO_WORDS,
            "range_basis": ("combined" if str(cells.get("range_basis", "")).strip().lower()
                            in ("combined", "meridian", "power") else "sphere"),
            "coating": cells.get("coating", ""),
            "notes": cells.get("notes", ""),
            "source": source,
        })
    if len(rows) - 1 > MAX_ROWS_PER_FILE:
        errors.append(f"{source}: only the first {MAX_ROWS_PER_FILE} rows "
                      "were read.")
    return lenses, errors


def load_catalog(lenses_dir: Path) -> dict:
    """Every non-underscore CSV in the lenses folder, parsed and merged."""
    lenses_dir = Path(lenses_dir)
    if not lenses_dir.is_dir():
        return {"lenses": [], "files": [], "message":
                "No lens files loaded yet. Upload a supplier price CSV "
                "below, or ask Mark to set one up."}

    lenses, files = [], []
    for path in sorted(lenses_dir.glob("*.csv")):
        if path.name.startswith("_"):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            files.append({"filename": path.name, "count": 0,
                          "errors": ["This file couldn't be opened."]})
            continue
        parsed, errors = parse_csv_text(text, path.name)
        lenses.extend(parsed)
        files.append({"filename": path.name, "count": len(parsed),
                      "errors": errors})

    lenses.sort(key=lambda l: (l["brand"].lower(), l["index"] or 0,
                               l["name"].lower()))
    message = "" if files else (
        "No lens files loaded yet. Upload a supplier price CSV below, "
        "or ask Mark to set one up.")
    return {"lenses": lenses, "files": files, "message": message}


# --- Per-machine config: what's dispensed, what's still ordered --------------

def apply_lens_filter(catalog: dict, cfg: dict | None) -> dict:
    """Narrow the catalogue to what THIS machine actually dispenses, and mark
    what it still orders.

    cfg["keep_only"] maps a lens category (e.g. "Progressive") to the ranges
    that machine uses; within that category only lenses whose brand+name
    contains one of those snippets (case-insensitive) survive. Categories not
    named are left whole, so an empty/absent config changes nothing.

    cfg["orderable_suppliers"] (list) + cfg["orderable_extra"] (name snippets):
    when the supplier list is non-empty, a lens is "orderable" only if its
    supplier is listed or its name matches a snippet (MiyoSmart stays on Hoya).
    Retired lenses stay in the catalogue — greyed, never the pick — so a
    Hoya code on an old job can still be looked up. A CSV row that says
    orderable=no is retired regardless.

    This runs after load_catalog, so the shared price files stay complete —
    a git pull that refreshes the price list never fights a machine's own
    choices, which live in the git-ignored config/lens_filter.json.
    """
    cfg = cfg or {}
    keep_only = cfg.get("keep_only") or {}
    rules = {str(cat).strip().lower():
             [str(p).strip().lower() for p in pats if str(p).strip()]
             for cat, pats in keep_only.items() if pats}
    suppliers = [str(s).strip().lower() for s in cfg.get("orderable_suppliers") or []
                 if str(s).strip()]
    extra = [str(s).strip().lower() for s in cfg.get("orderable_extra") or []
             if str(s).strip()]

    kept = []
    for l in catalog.get("lenses", []):
        pats = rules.get(str(l.get("category", "")).strip().lower())
        label = f"{l.get('brand', '')} {l.get('name', '')}".lower()
        if pats is not None and not any(p in label for p in pats):
            continue
        if suppliers and l.get("orderable", True):
            sup = str(l.get("supplier") or l.get("brand") or "").lower()
            l = {**l, "orderable": sup in suppliers or any(x in label for x in extra)}
        kept.append(l)

    # Re-count each file from what survived, so the library's "x lenses"
    # chip matches what's actually shown.
    counts = {}
    for l in kept:
        src = l.get("source", "")
        counts[src] = counts.get(src, 0) + 1
    files = [{**f, "count": counts.get(f.get("filename", ""), 0)}
             for f in catalog.get("files", [])]
    return {**catalog, "lenses": kept, "files": files}


# --- Pricing: which price applies today --------------------------------------

def price_now(lens: dict, today=None, pricing: dict | None = None):
    """(price, basis) that applies on `today`.

    A row can carry two prices: `price` (the ongoing one) and `price_promo`
    (the launch-level one). The promo applies while today < promo_until
    (config/lens_pricing.json). Deal-price rows have no promo — they are
    fixed for the term. Returns (None, basis) when nothing is priced.
    """
    basis = str(lens.get("price_basis") or "").strip()
    promo = lens.get("price_promo")
    if promo is not None:
        until = parse_date((pricing or {}).get("promo_until"))
        today = today or _dt.date.today()
        if until is None or today < until:
            return promo, "promo"
    return lens.get("price"), basis


def group_products(lenses: list, today=None, pricing: dict | None = None) -> list:
    """Collapse the flat rows into one product per supplier + lens + index +
    type, for BROWSING. In the price files a lens repeats once per coating and
    once per power band (each band carries the blank it needs), so a single
    lens can be 30-40 rows. Price only ever moves with coating — it's
    identical across the blank/power bands of a coating — so folding the
    coatings into a list and the blanks into a range is lossless for price
    and far easier to skim.

    The finder still matches on the flat rows (which keep the per-band power
    and blank limits); this view is only for the library.

    Each product: supplier, brand, name, index, type, category, code, notes,
    source, add_range, orderable, min_fh_mm, sph_min/sph_max (widest across
    bands), cyl_max (largest), blanks (sorted list), price_from (cheapest
    price that applies today), and coatings — cheapest first, each
    {coating, price, price_promo, price_now, basis, bands}. A band is
    {sph_min, sph_max, cyl_max, blank}: which blank a given power comes on.
    """
    groups, order = {}, []
    for l in lenses:
        key = (l.get("supplier") or l.get("brand", ""), l.get("name", ""),
               l.get("index"), l.get("type", ""))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "supplier": l.get("supplier") or l.get("brand", ""),
                "brand": l.get("brand", ""), "name": l.get("name", ""),
                "index": l.get("index"), "type": l.get("type", ""),
                "category": l.get("category", ""), "code": l.get("code", ""),
                "material": l.get("material", ""),
                "notes": l.get("notes", ""), "source": l.get("source", ""),
                "add_range": l.get("add_range", ""),
                "add_min": l.get("add_min"), "add_max": l.get("add_max"),
                "min_fh_mm": l.get("min_fh_mm"),
                "orderable": bool(l.get("orderable", True)),
                "sph_min": None, "sph_max": None, "cyl_max": None,
                "blanks": set(), "_coats": {},
            }
            order.append(key)
        if not g["add_range"] and l.get("add_range"):
            g["add_range"] = l["add_range"]
        if l.get("sph_min") is not None:
            g["sph_min"] = (l["sph_min"] if g["sph_min"] is None
                            else min(g["sph_min"], l["sph_min"]))
        if l.get("sph_max") is not None:
            g["sph_max"] = (l["sph_max"] if g["sph_max"] is None
                            else max(g["sph_max"], l["sph_max"]))
        if l.get("cyl_max") is not None:
            g["cyl_max"] = (l["cyl_max"] if g["cyl_max"] is None
                            else max(g["cyl_max"], l["cyl_max"]))
        if l.get("blank_mm") is not None:
            g["blanks"].add(l["blank_mm"])
        coat = l.get("coating", "") or ""
        price = l.get("price")
        entry = g["_coats"].setdefault(coat, {
            "price": None, "price_promo": l.get("price_promo"),
            "basis": l.get("price_basis", ""), "code": l.get("code", ""),
            "bands": []})
        # Price is constant per coating; keep the lowest just in case a file
        # ever disagrees, and don't let a rowless None wipe a real price.
        if price is not None and (entry["price"] is None or price < entry["price"]):
            entry["price"] = price
        entry["bands"].append({
            "sph_min": l.get("sph_min"), "sph_max": l.get("sph_max"),
            "cyl_max": l.get("cyl_max"), "blank": l.get("blank_mm"),
        })

    products = []
    for key in order:
        g = groups[key]
        coatings = []
        for c, entry in g.pop("_coats").items():
            bands = entry["bands"]
            bands.sort(key=lambda b: (b["sph_min"] is None,
                                      b["sph_min"] if b["sph_min"] is not None else 0))
            now, basis = price_now({"price": entry["price"],
                                    "price_promo": entry["price_promo"],
                                    "price_basis": entry["basis"]}, today, pricing)
            coatings.append({"coating": c, "price": entry["price"],
                             "price_promo": entry["price_promo"],
                             "price_now": now, "basis": basis,
                             "code": entry["code"], "bands": bands})
        coatings.sort(key=lambda c: (c["price_now"] is None, c["price_now"] or 0,
                                     c["coating"].lower()))
        g["blanks"] = sorted(g["blanks"])
        g["coatings"] = coatings
        g["price_from"] = next((c["price_now"] for c in coatings
                                if c["price_now"] is not None), None)
        products.append(g)

    products.sort(key=lambda p: (not p["orderable"], p["supplier"].lower(),
                                 p["index"] or 0, p["name"].lower()))
    return products


def mark_preferred(products: list, cfg: dict | None) -> list:
    """Flag the lenses this practice uses day-to-day (the ones on its own price
    list) and float them to the top of the library, so the everyday lenses are
    what you skim first. cfg["preferred"] has "match" (name snippets to boost)
    and optional "exclude" (snippets that veto a match), both case-insensitive
    substring checks against brand+name. Order within the preferred and the
    rest is preserved (a stable sort), and every product gets a "preferred"
    flag either way. Retired lenses never float."""
    pref = (cfg or {}).get("preferred") or {}
    match = [m.strip().lower() for m in pref.get("match", []) if str(m).strip()]
    exclude = [x.strip().lower() for x in pref.get("exclude", []) if str(x).strip()]

    def rank(p):
        """First matching 'match' snippet's position = display priority, so the
        ORDER of the match list decides what sits highest. len(match) means not
        preferred (sinks below everything preferred)."""
        if not p.get("orderable", True):
            return len(match) + 1
        label = f"{p.get('brand', '')} {p.get('name', '')}".lower()
        if any(x in label for x in exclude):
            return len(match)
        for i, m in enumerate(match):
            if m in label:
                return i
        return len(match)

    ranks = {id(p): rank(p) for p in products}
    for p in products:
        p["preferred"] = ranks[id(p)] < len(match)
    # Stable sort by rank keeps group_products' order within each match snippet.
    return sorted(products, key=lambda p: ranks[id(p)])


def _idx_key(p):
    try:
        return f"{float(p.get('index')):.2f}" if p.get("index") is not None else None
    except (TypeError, ValueError):
        return None


def attach_cec_price(products: list, cfg: dict | None) -> list:
    """Tag each coating of each product with Concord's own selling price (per
    pair) and the practice's tier name, so the library can show them beside
    the supplier cost.

    Current scheme — 'tiers': a list of {name, match, by_index, category?}.
    A product belongs to the first tier whose snippet appears in
    supplier+brand+name (case-insensitive); its base price is by_index[index].
    'addons' then add to the base when the snippet appears in the product name,
    material or coating (BluePro +$50, PhotoFusion +$130), and the special key
    'grind' adds the made-to-order surcharge on single-vision grind rows.
    'blank_names' suppress a price entirely (polarised is priced off the
    sunglasses list).

    Legacy scheme (still honoured, for the Hoya file): 'design_prices'
    (name snippet + index), 'sv_tiers' (coating snippet + index) and
    'surcharge' (name snippet -> +$).

    Sets each coating's cec_price (per pair, or None), the product's
    cec_price (cheapest coating that has one) and the product's tier.
    """
    cfg = cfg or {}
    blank = [b.strip().lower() for b in cfg.get("blank_names", []) if str(b).strip()]
    tiers = []
    for e in cfg.get("tiers", []) or []:
        snippets = e.get("match", [])
        if isinstance(snippets, str):
            snippets = [snippets]
        snippets = [str(m).strip().lower() for m in snippets if str(m).strip()]
        by = {str(k).strip(): v for k, v in (e.get("by_index") or {}).items()}
        if snippets and by:
            tiers.append((str(e.get("name", "")).strip(), snippets, by,
                          normalise_category(e.get("category")) if e.get("category") else None))
    addons = {str(k).strip().lower(): v for k, v in (cfg.get("addons") or {}).items()}
    grind_addon = addons.pop("grind", 0) or 0
    surcharge = {str(k).strip().lower(): v
                 for k, v in (cfg.get("surcharge") or {}).items()}
    design = []
    for e in cfg.get("design_prices", []) or []:
        m = str(e.get("match", "")).strip().lower()
        by = {str(k).strip(): v for k, v in (e.get("by_index") or {}).items()}
        if m and by:
            design.append((m, by))
    sv_tiers = []
    for e in cfg.get("sv_tiers", []) or []:
        c = str(e.get("coating", "")).strip().lower()
        by = {str(k).strip(): v for k, v in (e.get("by_index") or {}).items()}
        if c and by:
            sv_tiers.append((c, by))

    for p in products:
        label = f"{p.get('supplier', '')} {p.get('brand', '')} {p.get('name', '')}".lower()
        idx = _idx_key(p)
        cat = normalise_category(p.get("category", ""))
        is_sv = cat == "Single vision"
        blanked = idx is None or any(b in label for b in blank)
        p["tier"] = ""
        base = None
        if not blanked:
            for name, snippets, by, tcat in tiers:
                if tcat and tcat != cat:
                    continue
                if any(m in label for m in snippets):
                    p["tier"] = name
                    base = by.get(idx)
                    break
            if base is None and not p["tier"]:
                for m, by in design:
                    if m in label and idx in by:
                        base = by[idx]
                        break
        legacy_add = sum(v for k, v in surcharge.items() if k in label)
        for coat in p.get("coatings", []):
            price = None
            if not blanked:
                cl = (coat.get("coating") or "").lower()
                if base is not None:
                    hay = f"{label} {p.get('material', '')} {cl}".lower()
                    price = base + sum(v for k, v in addons.items()
                                       if any(part.strip() and part.strip() in hay
                                              for part in k.split("|")))
                    if is_sv and p.get("type") == "grind":
                        price += grind_addon
                elif is_sv and sv_tiers:
                    for csnip, by in sv_tiers:
                        if csnip in cl and idx in by:
                            price = by[idx]
                            break
            coat["cec_price"] = (price + legacy_add) if price is not None else None
        priced = [c["cec_price"] for c in p.get("coatings", [])
                  if c.get("cec_price") is not None]
        p["cec_price"] = min(priced) if priced else None
    return products


def sv_only(lenses: list) -> list:
    """Just the single-vision lenses. Kept for callers that only want the
    stock-vs-grind question; find_options() takes a `kind` instead."""
    return [l for l in lenses
            if normalise_category(l.get("category", "")) == "Single vision"]


def of_kind(lenses: list, kind: str | None) -> list:
    """Rows of one category (canonical name); None/'' means everything."""
    if not kind:
        return list(lenses)
    want = normalise_category(kind)
    return [l for l in lenses if normalise_category(l.get("category", "")) == want]


# Minimum lens index we'd want for a given power, so the finder leads with a
# lens that won't be needlessly thick — a 1.50 technically covers -6.00 but
# comes out thick, which is bad for the patient and the margin. Read each row
# as "strongest meridian up to this power -> at least this index"; anything
# stronger than the last row uses INDEX_ABOVE. EDIT HERE to retune the practice
# thresholds. Concord's thinner-leaning table (set 2026-07-17).
INDEX_BY_POWER = [
    (2.00, 1.50),
    (4.00, 1.60),
    (6.00, 1.67),
]
INDEX_ABOVE = 1.74

# Beyond this cyl the labs bill a surcharge (ZEISS/synchrony: 4.25D+).
CYL_SURCHARGE_FROM = 4.25


def strongest_meridian(sph: float, cyl: float = 0.0) -> float:
    """The larger-magnitude principal meridian — what drives thickness."""
    return max(abs(sph), abs(sph + (cyl or 0.0)))


def recommended_index(sph: float, cyl: float = 0.0) -> float:
    """Minimum lens index we'd want for this Rx's strongest meridian."""
    power = strongest_meridian(sph, cyl)
    for limit, idx in INDEX_BY_POWER:
        if power <= limit:
            return idx
    return INDEX_ABOVE


def _fmt_index(v):
    return f"{v:.2f}"


# Index "steps" as the practice thinks of them: 1.50 → 1.60 → 1.67 → 1.74.
# 1.53 (Trivex) sits with 1.50, 1.59 (poly) with 1.60, 1.76 with 1.74.
def index_step(idx) -> int:
    if idx is None:
        return 0
    if idx < 1.57:
        return 0
    if idx < 1.64:
        return 1
    if idx < 1.71:
        return 2
    return 3


DEFAULT_PREFERRED_COATINGS = ["Platinum", "HMC+"]
HARD_COAT_WORDS = ("hard coat", "hardcoat", "dshc", " hc")


def is_hard_coat(coating: str) -> bool:
    c = f" {coating or ''}".lower()
    return any(w in c for w in HARD_COAT_WORDS) and "hmc" not in c


def find_options(lenses: list, sph: float, cyl: float = 0.0,
                 min_blank: float | None = None, add: float | None = None,
                 kind: str | None = None, fh: float | None = None,
                 tint: bool = False, today=None,
                 pricing: dict | None = None) -> dict:
    """Which lenses can make this Rx, in the order the practice would reach
    for them, and in plain words why.

    Order: still-ordered lenses first; then the right thickness (a thinner-
    index lens that only technically fits is flagged, not led with); then —
    when pricing["prefer_stock"] is on, the default — a stock lens ahead of a
    grind even if the grind is cheaper (the practice's own rule: stock keeps
    the job in the supplier's finished range and off the surfacing bench);
    then price.

    cyl is taken in minus-cyl form; a plus cyl is transposed automatically
    (sph + cyl, cyl sign flipped) so it's checked the way stock ranges are
    written. min_blank is the smallest blank diameter the frame needs. add
    is checked against the add range of multifocal-type rows. fh is the
    fitting height, checked against a design's minimum (warning only).
    kind narrows to one category (None = the rows as given). tint: the job
    is being tinted, so a hard-coat row is the standard and coloured
    materials (BluePro, photochromic, polarised) are out.

    Coatings: every coating column in the price file is a row, but the
    practice orders one as standard (pricing["preferred_coatings"], default
    Platinum / HMC+). Those rows rank ahead of the others at the same
    product, so the pick is the lens as it would actually be ordered; the
    cheaper hard-coat row is still listed underneath.
    """
    pricing = pricing or {}
    prefer_stock = bool(pricing.get("prefer_stock", True))
    preferred = [str(c).lower() for c in
                 pricing.get("preferred_coatings") or DEFAULT_PREFERRED_COATINGS]
    # Lenses on the practice's own sheet (lens_filter "preferred" names) win
    # ties on price — the order of that list is the priority.
    pref_names = [str(n).strip().lower() for n in pricing.get("preferred_names") or []
                  if str(n).strip()]

    def pref_rank(lens):
        label = f"{lens.get('supplier', '')} {lens.get('brand', '')} {lens.get('name', '')}".lower()
        for i, n in enumerate(pref_names):
            if n in label:
                return i
        return len(pref_names)
    cyl = cyl or 0.0
    transposed = False
    if cyl > 0:
        sph, cyl, transposed = sph + cyl, -cyl, True
    rec_index = recommended_index(sph, cyl)
    combined = sph + cyl          # the minus meridian in minus-cyl form

    rows = of_kind(lenses, kind)
    # Two readings of a row's power range (lenses/README.md, "How ranges are
    # read"):
    #  sphere   — the range is the SPHERE, the cyl is capped separately, and
    #             the starred number is a cap on sphere+cyl. Hoya's charts,
    #             and every made-to-order table, work this way.
    #  combined — the range is the lens's power in its strongest meridian:
    #             sphere+cyl for a minus lens, the sphere for a plus lens.
    #             ZEISS/synchrony STOCK bands tile that axis in 0.25 steps
    #             (-8.00 to -6.25 on 70mm, -6.00 to 0.00 on 75mm, +0.25 up on
    #             70mm…), so a -6.50/-2.00 is a -8.50 lens and comes as the
    #             70mm blank. The converter marks those rows range_basis=combined.
    plus_lens = sph > 0 and (sph + cyl) >= 0        # both meridians plus
    key_power = sph if plus_lens else sph + cyl     # the meridian a stock band is about

    options, misses = [], []
    for lens in rows:
        reasons, warnings = [], []
        cat = normalise_category(lens.get("category", ""))
        needs_add = cat in ADD_CATEGORIES
        by_combined = lens.get("range_basis") == "combined" and lens["type"] == "stock"

        if lens["sph_min"] is None:
            warnings.append("power range isn't in the file — check the "
                            "supplier guide before ordering")
        elif by_combined:
            if not (lens["sph_min"] <= key_power <= lens["sph_max"]):
                reasons.append(
                    f"its power {_fmt_power(key_power)} ({'sphere' if plus_lens else 'sphere + cyl'}) "
                    f"is outside this band ({_fmt_power(lens['sph_min'])} to "
                    f"{_fmt_power(lens['sph_max'])}, {_fmt_mm(lens['blank_mm']) if lens['blank_mm'] else 'stock'})")
        elif not (lens["sph_min"] <= sph <= lens["sph_max"]):
            reasons.append(
                f"sphere {_fmt_power(sph)} is outside its range "
                f"({_fmt_power(lens['sph_min'])} to {_fmt_power(lens['sph_max'])})")
        if cyl != 0:
            if lens["cyl_max"] is None:
                warnings.append("cyl limit isn't in the file — check the "
                                "supplier guide before ordering")
            elif abs(cyl) > lens["cyl_max"]:
                reasons.append(
                    f"cyl {_fmt_power(cyl)} is beyond its limit "
                    f"(-{lens['cyl_max']:.2f})")
            elif abs(cyl) >= CYL_SURCHARGE_FROM and lens["type"] == "grind":
                warnings.append(f"cyl {_fmt_power(cyl)} — the lab's high-cyl "
                                "surcharge applies")
        # The starred number in the supplier books is the maximum MINUS
        # combined power (sphere + cyl, minus-cyl form). It never limits a
        # plus prescription.
        if (not by_combined and lens["combined_max"] is not None and combined < 0
                and abs(combined) > lens["combined_max"]):
            reasons.append(
                f"sphere and cyl combined ({_fmt_power(combined)}) is beyond "
                f"its limit (-{lens['combined_max']:.2f})")
        if min_blank is not None:
            if lens["blank_mm"] is None:
                if lens["type"] == "stock":
                    warnings.append("blank size isn't in the file — check it "
                                    "covers the frame")
            elif lens["blank_mm"] < min_blank:
                reasons.append(
                    f"its {_fmt_mm(lens['blank_mm'])} blank is smaller than "
                    f"the {_fmt_mm(min_blank)} this frame needs")
        if needs_add:
            if add is None:
                warnings.append("type the add to check it against this "
                                "design's add range")
            elif lens.get("add_min") is None:
                warnings.append("add range isn't in the file — check the "
                                "supplier guide before ordering")
            elif not (lens["add_min"] <= add <= lens["add_max"]):
                reasons.append(
                    f"add {_fmt_power(add)} is outside its range "
                    f"({lens['add_min']:.2f} to {lens['add_max']:.2f})")
        if fh is not None and lens.get("min_fh_mm") and fh < lens["min_fh_mm"]:
            warnings.append(f"fitting height {fh:g}mm is under this design's "
                            f"minimum ({lens['min_fh_mm']:g}mm) — pick a "
                            "shorter corridor or a deeper frame")
        orderable = bool(lens.get("orderable", True))
        if not orderable:
            warnings.append("no longer ordered — reference only")
        material = str(lens.get("material") or "Clear")
        plain = material.lower() in ("", "clear", "clear / tinted", "tinted")
        coating = str(lens.get("coating") or "")
        c = coating.lower()
        if tint:
            if not plain:
                reasons.append(f"a tint goes on a clear lens, not {material}")
            standard = is_hard_coat(coating)
            if not standard:
                reasons.append("a tint needs a hard-coat lens — this is an AR "
                               "coating (can't be tinted after coating)")
        else:
            standard = (not coating or
                        any(p in c for p in preferred) and "back surface" not in c)

        # How many index steps thinner than the table's pick. One step under
        # on a STOCK lens is a live option (the practice would rather a 1.67
        # off the shelf than a 1.74 grind); two steps, or any grind that is
        # thinner than the table, is "too thick" and sinks.
        steps_under = (index_step(rec_index) - index_step(lens["index"])
                       if lens["index"] is not None else 0)
        under = steps_under >= 1
        hard_under = steps_under >= 2 or (steps_under == 1 and lens["type"] != "stock")
        price, basis = price_now(lens, today, pricing)
        entry = {**lens, "warnings": warnings, "under_index": under,
                 "hard_under": hard_under, "steps_under": max(steps_under, 0),
                 "price_now": price, "basis": basis, "orderable": orderable,
                 "standard_coating": standard, "plain": plain}
        if reasons:
            misses.append({**entry, "reasons": reasons})
        else:
            options.append(entry)

    def sort_key(o):
        return (not o["orderable"], o["hard_under"],
                (o["type"] != "stock") if prefer_stock else False,
                o["under_index"],
                not o["standard_coating"], not o["plain"],
                o["price_now"] is None, o["price_now"] or 0, pref_rank(o),
                o["index"] or 0)

    options.sort(key=sort_key)
    appropriate = [o for o in options
                   if o["orderable"] and not o["hard_under"]
                   and o["price_now"] is not None]
    best = (appropriate[0] if appropriate
            else next((o for o in options
                       if o["orderable"] and o["price_now"] is not None), None))
    if best:
        best["best"] = True
        for o in options:
            if o is not best and o["price_now"] is not None:
                o["dearer_by"] = round(o["price_now"] - best["price_now"], 2)

    return {
        "rx": {"sph": sph, "cyl": cyl, "add": add, "tint": tint, "transposed": transposed,
               "display": (f"{_fmt_power(sph)} / {_fmt_power(cyl)}" if cyl
                           else _fmt_power(sph))
                          + (f" add {add:+.2f}" if add is not None else "")},
        "kind": normalise_category(kind) if kind else "",
        "rec_index": rec_index,
        "min_blank": min_blank,
        "options": options,
        "misses": misses,
        "verdict": _verdict(options, best, rec_index, prefer_stock,
                            made_to_order=(normalise_category(kind) != "Single vision")
                            if kind else False),
    }


def _label(lens):
    sup = str(lens.get("supplier") or "")
    brand = str(lens.get("brand") or "")
    lead = brand or sup
    if sup and brand and sup.lower() not in brand.lower() and brand.lower() not in sup.lower():
        lead = f"{sup} {brand}"
    return f"{lead} {lens['name']}".strip()


def _price_tag(o):
    b = o.get("basis") or ""
    tag = {"deal": "deal price", "promo": "promo price"}.get(b, "")
    return f"${o['price_now']:.2f} a lens" + (f", {tag}" if tag else "")


def _verdict(options, best, rec_index=None, prefer_stock=True, made_to_order=False):
    """The plain-words line at the top of the results: STOCK or GRIND first,
    then the lens, then what the other route would have cost. Multifocal-type
    lenses are always made to order, so they skip the stock/grind framing."""
    if not options:
        return ("Nothing in the catalogue covers this job. Check the Rx, or "
                "it may need a lens that isn't loaded yet — ask Mark.")
    if best is None:
        if all(not o["orderable"] for o in options):
            return ("Only lenses we no longer order fit this — nothing in the "
                    "current range covers it. Check with Mark.")
        return ("Some lenses fit, but none of them have a price loaded, so "
                "there's no cheapest to point at yet.")

    live = [o for o in options if o["orderable"] and o["price_now"] is not None]
    right_index = [o for o in live if not o.get("hard_under")]
    std = [o for o in right_index if o.get("standard_coating")] or right_index
    grind = next((o for o in std if o["type"] == "grind"), None)
    # the grind at the table's own index, for the thickness trade-off line
    grind_full = next((o for o in std if o["type"] == "grind" and not o.get("under_index")), None)

    if made_to_order:
        line = f"{_label(best)} — {_price_tag(best)} (made to order)."
        base = best["name"].replace(" Short", "")
        runner = next((o for o in std if o is not best
                       and o["name"].replace(" Short", "") != base), None)
        if runner:
            line += f" Next: {_label(runner)}, {_price_tag(runner)}."
    elif best["type"] == "stock":
        line = f"STOCK covers this — {_label(best)}, {_price_tag(best)}."
        if rec_index is not None and best["index"] and index_step(best["index"]) > index_step(rec_index):
            line += (f" No {_fmt_index(rec_index)} stock lens covers this cyl, so it "
                     f"goes up to {_fmt_index(best['index'])} stock rather than a grind.")
        elif rec_index is not None and best.get("under_index"):
            line += (f" That is one step thicker than the table's {_fmt_index(rec_index)} "
                     f"for this power — no {_fmt_index(rec_index)} stock lens covers it")
            if grind_full:
                line += (f", and the {_fmt_index(rec_index)} route is a grind at "
                         f"{_price_tag(grind_full)} ({_label(grind_full)}).")
            else:
                line += "."
            grind = None if grind is grind_full else grind   # already quoted
        if grind and grind is not best:
            diff = grind["price_now"] - best["price_now"]
            if diff >= 0:
                line += (f" A grind would cost {_price_tag(grind)} "
                         f"({_label(grind)}) — stock saves ${diff:.2f} a lens.")
            elif prefer_stock:
                line += (f" The cheapest grind ({_label(grind)}, {_price_tag(grind)}) "
                         f"is ${-diff:.2f} a lens less, but stock comes first by practice rule.")
    else:
        line = "GRIND — no stock lens covers this"
        if rec_index is not None:
            line += f" at {_fmt_index(rec_index)} or higher"
        thin_stock = next((o for o in live if o["type"] == "stock" and o.get("under_index")), None)
        if thin_stock:
            line += f" ({_label(thin_stock)} stock fits but would be too thick at this power)"
        line += f". Cheapest: {_label(best)}, {_price_tag(best)}."

    if rec_index is not None and best.get("hard_under"):
        line += (f" ⚠ Nothing at {_fmt_index(rec_index)} (what we'd use for this "
                 "power) is loaded and priced for this Rx, so this is the thinnest "
                 "that fits — expect it thick and double-check.")
    elif rec_index is not None:
        cheaper_thin = next((o for o in live if o.get("under_index")
                             and o["price_now"] < best["price_now"]), None)
        if cheaper_thin:
            line += (f" ({_label(cheaper_thin)} at {_fmt_index(cheaper_thin['index'])} "
                     f"fits for ${cheaper_thin['price_now']:.2f} but would be too thick — "
                     f"{_fmt_index(rec_index)} is the sensible minimum for this power.)")
    if best["type"] == "stock" and best.get("blank_mm"):
        line += f" Comes as the {_fmt_mm(best['blank_mm'])} blank."
    if best["warnings"]:
        line += " Check its amber notes first."
    return line


def min_blank_from_frame(frame: dict):
    """Frame measurements -> smallest blank the job needs (mm), or None.

    Same rule as the page helper: ED (or eye size + 2 if no ED) plus the
    total decentration (frame PD - patient PD) plus 2mm spare. A monocular
    PD (under 40 — Optomate stores per-eye PDs) is doubled first.
    """
    frame = frame or {}
    a = _num(frame.get("a") or frame.get("eye") or frame.get("size"))
    dbl = _num(frame.get("dbl") or frame.get("bridge"))
    pd = _num(frame.get("pd"))
    ed = _num(frame.get("ed") or frame.get("depth"))
    if not a or a <= 0 or dbl is None or dbl < 0 or not pd or pd <= 0:
        return None
    if pd < 40:
        pd *= 2
    return math.ceil((ed if ed and ed > 0 else a + 2)
                     + max(a + dbl - pd, 0) + 2)


def _product_key(option: dict):
    return (option.get("supplier") or option["brand"], option["name"],
            option["code"], option["coating"], option["type"])


def check_job(lenses: list, right: dict | None = None, left: dict | None = None,
              min_blank: float | None = None, chosen: dict | None = None,
              add: float | None = None, kind: str | None = None,
              today=None, pricing: dict | None = None) -> dict:
    """The order-screen question: for this pair of eyes (and blank size),
    which products cover the WHOLE job, what's the cheapest, and does the
    stock/grind call that was made look right.

    right/left: {"sph": -0.75, "cyl": -1.00} (either may be omitted for a
    single-lens job). chosen (optional): {"code": ..., "type": "Stk"/"Grd"}
    — what was actually put on the order. kind defaults to single vision,
    the only category with a stock-vs-grind question.
    """
    kind = kind or "Single vision"
    pricing = pricing or {}
    eyes = {}
    for label, rx in (("right", right), ("left", left)):
        sph = _num((rx or {}).get("sph"))
        if sph is not None:
            cyl = _num((rx or {}).get("cyl")) or 0.0
            eyes[label] = find_options(lenses, sph, cyl, min_blank, add=add,
                                       kind=kind, today=today, pricing=pricing)
    if not eyes:
        return {"status": "no_rx", "headline":
                "No Rx on this job yet — nothing to check.",
                "eyes": {}, "options": [], "best": None, "chosen": None,
                "min_blank": min_blank}

    # A product covers the job when EVERY eye matches one of its rows.
    covering = None
    details, warnings, thick = {}, {}, {}
    for result in eyes.values():
        keys = set()
        for option in result["options"]:
            if not option["orderable"]:
                continue
            key = _product_key(option)
            keys.add(key)
            details.setdefault(key, option)
            warnings.setdefault(key, []).extend(option.get("warnings") or [])
            # too thick for either eye = too thick for the job
            thick[key] = thick.get(key, False) or bool(option.get("under_index"))
        covering = keys if covering is None else covering & keys

    per_lens = len(eyes)
    products = []
    for key in covering:
        o = details[key]
        seen = list(dict.fromkeys(warnings[key]))
        products.append({
            "supplier": o.get("supplier") or o["brand"],
            "brand": o["brand"], "name": o["name"], "code": o["code"],
            "coating": o["coating"], "type": o["type"], "index": o["index"],
            "price": o["price_now"], "basis": o.get("basis", ""),
            "price_job": round(o["price_now"] * per_lens, 2)
                         if o["price_now"] is not None else None,
            "warnings": seen, "under_index": thick.get(key, False),
            "standard_coating": bool(o.get("standard_coating", True)),
            "plain": bool(o.get("plain", True)),
        })
    # Same order as the finder: right thickness first (a 1.50 that only
    # technically covers a -5.00 job is not the answer), the coating we
    # actually order, a clear lens before a polarised one, then price.
    products.sort(key=lambda p: (p["under_index"], not p["standard_coating"],
                                 not p["plain"], p["price"] is None,
                                 p["price"] or 0, p["index"] or 0))
    best = next((p for p in products if p["price"] is not None), None)
    best_stock = next((p for p in products
                       if p["type"] == "stock" and p["price"] is not None), None)
    best_grind = next((p for p in products
                       if p["type"] == "grind" and p["price"] is not None), None)

    unit = "a pair" if per_lens == 2 else "a lens"
    if not products:
        status = "none"
        headline = ("Nothing in the loaded price files covers this Rx — "
                    "check it by hand against the supplier guide.")
    elif best_stock:
        status = "stock"
        headline = (f"Stock job — {_label(best_stock)} "
                    f"({best_stock['coating']}) at "
                    f"${best_stock['price_job']:.2f} {unit}.")
        if best_grind:
            saving = best_grind["price_job"] - best_stock["price_job"]
            if saving > 0:
                headline += (f" That's ${saving:.2f} {unit} under the "
                             "cheapest grind.")
        if best_stock["warnings"]:
            headline += " Check its amber notes first."
    else:
        status = "grind"
        headline = (f"Grind job — no stock lens covers this Rx. Cheapest: "
                    f"{_label(best)} ({best['coating']}) at "
                    f"${best['price_job']:.2f} {unit}." if best else
                    "Grind job — no stock lens covers this Rx, and the "
                    "grind options have no price loaded.")

    chosen_out = None
    if chosen and (chosen.get("code") or chosen.get("type")):
        notes = []
        raw_type = str(chosen.get("type") or "").strip().lower()
        chosen_type = ("stock" if raw_type in ("stk", "stock") else
                       "grind" if raw_type in ("grd", "grind") else "")
        code = str(chosen.get("code") or "").strip()
        by_code = {(l["code"] or "").lower(): l for l in lenses if l["code"]}
        hit = by_code.get(code.lower()) if code else None
        code_known = hit is not None
        retired = hit is not None and not hit.get("orderable", True)
        if code and not code_known:
            notes.append(f"code {code} isn't in the loaded price files — "
                         "the chosen lens itself wasn't range-checked")
        elif retired:
            notes.append(f"code {code} is a lens we no longer order "
                         f"({hit.get('supplier') or hit['brand']}) — "
                         "use the current range")
        mismatch = False
        if chosen_type == "grind" and best_stock:
            notes.append(f"marked Grind, but a stock lens covers this Rx: "
                         f"{_label(best_stock)} at "
                         f"${best_stock['price_job']:.2f} {unit}")
            mismatch = True
        if chosen_type == "stock" and status in ("grind", "none"):
            notes.append("marked Stock, but no stock lens in the loaded "
                         "files covers this Rx — double-check")
            mismatch = True
        chosen_out = {"code": code, "type": chosen_type,
                      "code_known": code_known, "notes": notes}
        if mismatch or retired:
            status = "check"

    return {
        "status": status,
        "headline": headline,
        "min_blank": min_blank,
        "eyes": {label: {"rx": result["rx"]["display"],
                         "fits": len([o for o in result["options"] if o["orderable"]])}
                 for label, result in eyes.items()},
        "options": products[:10],
        "best": best,
        "chosen": chosen_out,
    }


def safe_csv_name(name: str) -> str:
    """A supplier/file name -> a tame 'something.csv' filename."""
    stem = str(name or "")
    if stem.lower().endswith(".csv"):
        stem = stem[:-4]
    stem = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return f"{stem or 'lenses'}.csv"


def save_upload(lenses_dir: Path, name: str, text: str):
    """Validate an uploaded CSV and save it into the lenses folder.

    Returns (ok, payload): on success payload has filename / count /
    replaced / errors (row-level warnings); on failure an error message.
    """
    parsed, errors = parse_csv_text(text, name or "the uploaded file")
    if not parsed:
        detail = " ".join(errors[:3]) if errors else ""
        return False, {"status": 400, "error":
                       ("No lenses could be read from that file. " + detail).strip()}

    lenses_dir = Path(lenses_dir)
    try:
        lenses_dir.mkdir(parents=True, exist_ok=True)
        target = lenses_dir / safe_csv_name(name)
        replaced = target.exists()
        target.write_text(text, encoding="utf-8")
    except OSError:
        return False, {"status": 500, "error":
                       "The file couldn't be saved just now — try again, "
                       "or ask Mark."}
    return True, {"status": 200, "filename": target.name,
                  "count": len(parsed), "replaced": replaced,
                  "errors": errors}
