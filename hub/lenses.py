"""Lens catalogue + best-option finder.

The catalogue is plain CSV files in the Hub's own lenses\\ folder — one file
per supplier price guide (e.g. hoya.csv). Files are read fresh on every
request, so dropping in a new CSV (or uploading one from the Lens Finder
page) takes effect on the next page load. Files whose name starts with an
underscore (like _template.csv) are ignored.

Column contract lives in lenses\\README.md. Headers are matched loosely
(case, spaces, a handful of aliases) and numbers shrug off "$", "mm" and
stray "+" signs, because these files get hand-edited from supplier PDFs.

find_options() answers the real question at the bench: for this Rx (and
this frame's blank size), which lenses can make the job, and which is the
cheapest — including when a dearer-index STOCK lens beats a 1.50 GRIND.
"""

import csv
import io
import math
import re
from pathlib import Path

MAX_ROWS_PER_FILE = 5000
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

# Loose header matching: lowercase, spaces/dashes -> underscore, then alias.
HEADER_ALIASES = {
    "brand": "brand", "supplier": "brand", "manufacturer": "brand",
    "lens": "name", "name": "name", "lens_name": "name", "product": "name",
    "code": "code", "product_code": "code", "lens_type": "code",
    "lenstype": "code", "barcode": "code", "order_code": "code",
    "index": "index", "material_index": "index", "refractive_index": "index",
    "type": "type", "stock_or_grind": "type", "stock_grind": "type",
    "category": "category", "lens_category": "category", "vision": "category",
    "form": "form", "spherical_aspherical": "form", "spheric_aspheric": "form",
    "add_range": "add_range", "add": "add_range", "add_power": "add_range",
    "addition": "add_range", "adds": "add_range",
    "design": "design", "vision_type": "design",
    "blank_mm": "blank_mm", "blank": "blank_mm", "blank_size": "blank_mm",
    "diameter": "blank_mm", "dia": "blank_mm", "size": "blank_mm",
    "sph_min": "sph_min", "sphere_min": "sph_min", "min_sph": "sph_min",
    "sph_max": "sph_max", "sphere_max": "sph_max", "max_sph": "sph_max",
    "sph_range": "sph_range", "sphere_range": "sph_range",
    "power_range": "sph_range",
    "cyl_max": "cyl_max", "cyl": "cyl_max", "max_cyl": "cyl_max",
    "cyl_to": "cyl_max", "cyl_range": "cyl_max",
    "combined_max": "combined_max", "max_combined": "combined_max",
    "total_power_max": "combined_max", "sph_plus_cyl_max": "combined_max",
    "price": "price", "cost": "price", "price_per_lens": "price",
    "cost_per_lens": "price",
    "coating": "coating", "coat": "coating",
    "notes": "notes", "note": "notes", "comments": "notes",
}

STOCK_WORDS = {"stock", "finished", "uncut", "fsv"}
GRIND_WORDS = {"grind", "grinding", "surfaced", "rx", "lab", "freeform",
               "made_to_order", "made to order", "mto"}

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

        blank_mm = _blank(cells.get("blank_mm"))
        cyl_max = _num(cells.get("cyl_max"))
        combined_max = _num(cells.get("combined_max"))
        lenses.append({
            "brand": cells.get("brand", ""),
            "name": name,
            "code": cells.get("code", ""),
            "category": cells.get("category", ""),
            "index": _num(cells.get("index")),
            "type": _lens_type(cells.get("type"), blank_mm),
            "form": cells.get("form", ""),
            "add_range": cells.get("add_range", ""),
            "design": cells.get("design", ""),
            "blank_mm": blank_mm,
            "sph_min": sph_min,
            "sph_max": sph_max,
            "cyl_max": abs(cyl_max) if cyl_max is not None else None,
            "combined_max": abs(combined_max) if combined_max is not None else None,
            "price": _num(cells.get("price")),
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


def apply_lens_filter(catalog: dict, cfg: dict | None) -> dict:
    """Narrow the catalogue to what THIS machine actually dispenses.

    cfg["keep_only"] maps a lens category (e.g. "Progressive") to the ranges
    that machine uses; within that category only lenses whose brand+name
    contains one of those snippets (case-insensitive) survive. Categories not
    named are left whole, so an empty/absent config changes nothing.

    This runs after load_catalog, so the shared price files stay complete —
    a git pull that refreshes the price list never fights a machine's own
    choices, which live in the git-ignored config/lens_filter.json.
    """
    keep_only = (cfg or {}).get("keep_only") or {}
    rules = {str(cat).strip().lower():
             [str(p).strip().lower() for p in pats if str(p).strip()]
             for cat, pats in keep_only.items() if pats}
    if not rules:
        return catalog

    kept = []
    for l in catalog.get("lenses", []):
        pats = rules.get(str(l.get("category", "")).strip().lower())
        if pats is None:
            kept.append(l)
            continue
        label = f"{l.get('brand', '')} {l.get('name', '')}".lower()
        if any(p in label for p in pats):
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


def sv_only(lenses: list) -> list:
    """Just the single-vision lenses — what the cost engine matches on.
    Progressives/bifocals/occupationals are browse-only (made to order,
    chosen by add power, and Hoyalog rejects out-of-range jobs anyway).
    A file with no category column (other suppliers) is treated as all SV."""
    return [l for l in lenses
            if str(l.get("category", "")).strip().lower()
            in ("", "single vision", "sv")]


def find_options(lenses: list, sph: float, cyl: float = 0.0,
                 min_blank: float | None = None) -> dict:
    """Which lenses can make this Rx, cheapest first, and in plain words why.

    cyl is taken in minus-cyl form; a plus cyl is transposed automatically
    (sph + cyl, cyl sign flipped) so it's checked the way stock ranges are
    written. min_blank is the smallest blank diameter the frame needs.
    """
    cyl = cyl or 0.0
    transposed = False
    if cyl > 0:
        sph, cyl, transposed = sph + cyl, -cyl, True

    options, misses = [], []
    for lens in lenses:
        reasons, warnings = [], []

        if lens["sph_min"] is None:
            warnings.append("power range isn't in the file — check the "
                            "supplier guide before ordering")
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
        if lens["combined_max"] is not None and abs(sph + cyl) > lens["combined_max"]:
            reasons.append(
                f"sphere and cyl combined ({_fmt_power(sph + cyl)}) is beyond "
                f"its limit ({lens['combined_max']:.2f})")
        if min_blank is not None:
            if lens["blank_mm"] is None:
                if lens["type"] == "stock":
                    warnings.append("blank size isn't in the file — check it "
                                    "covers the frame")
            elif lens["blank_mm"] < min_blank:
                reasons.append(
                    f"its {_fmt_mm(lens['blank_mm'])} blank is smaller than "
                    f"the {_fmt_mm(min_blank)} this frame needs")

        entry = {**lens, "warnings": warnings}
        if reasons:
            misses.append({**entry, "reasons": reasons})
        else:
            options.append(entry)

    options.sort(key=lambda o: (o["price"] is None, o["price"] or 0,
                                o["index"] or 0))
    best = options[0] if options and options[0]["price"] is not None else None
    if best:
        best["best"] = True
        for o in options[1:]:
            if o["price"] is not None:
                o["dearer_by"] = round(o["price"] - best["price"], 2)

    return {
        "rx": {"sph": sph, "cyl": cyl, "transposed": transposed,
               "display": f"{_fmt_power(sph)} / {_fmt_power(cyl)}" if cyl
                          else _fmt_power(sph)},
        "min_blank": min_blank,
        "options": options,
        "misses": misses,
        "verdict": _verdict(options, best),
    }


def _label(lens):
    return f"{lens['brand']} {lens['name']}".strip()


def _verdict(options, best):
    """One plain-words sentence for the top of the results."""
    if not options:
        return ("Nothing in the catalogue covers this job. Check the Rx, or "
                "it may need a lens that isn't loaded yet — ask Mark.")
    if best is None:
        return ("Some lenses fit, but none of them have a price loaded, so "
                "there's no cheapest to point at yet.")
    priced_grind = next((o for o in options
                         if o["type"] == "grind" and o["price"] is not None), None)
    if best["type"] == "stock":
        line = (f"Best value: {_label(best)} — ${best['price']:.2f} a lens, "
                "off the shelf.")
        if priced_grind and priced_grind is not best:
            saving = priced_grind["price"] - best["price"]
            if saving > 0:
                line += (f" That saves ${saving:.2f} a lens compared with "
                         f"grinding ({_label(priced_grind)} at "
                         f"${priced_grind['price']:.2f}).")
        if best["warnings"]:
            line += (" Check its amber notes first — not all of its limits "
                     "are in the file.")
        return line
    priced_stock = next((o for o in options
                         if o["type"] == "stock" and o["price"] is not None), None)
    if priced_stock:
        return (f"Grinding is actually cheaper here: {_label(best)} at "
                f"${best['price']:.2f} a lens beats the cheapest stock lens "
                f"({_label(priced_stock)} at ${priced_stock['price']:.2f}).")
    return (f"No stock lens covers this job — it needs a grind: "
            f"{_label(best)} at ${best['price']:.2f} a lens.")


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
    return (option["brand"], option["name"], option["code"],
            option["coating"], option["type"])


def check_job(lenses: list, right: dict | None = None, left: dict | None = None,
              min_blank: float | None = None, chosen: dict | None = None) -> dict:
    """The order-screen question: for this pair of eyes (and blank size),
    which products cover the WHOLE job, what's the cheapest, and does the
    stock/grind call that was made look right.

    right/left: {"sph": -0.75, "cyl": -1.00} (either may be omitted for a
    single-lens job). chosen (optional): {"code": ..., "type": "Stk"/"Grd"}
    — what was actually put on the order.
    """
    eyes = {}
    for label, rx in (("right", right), ("left", left)):
        sph = _num((rx or {}).get("sph"))
        if sph is not None:
            cyl = _num((rx or {}).get("cyl")) or 0.0
            eyes[label] = find_options(lenses, sph, cyl, min_blank)
    if not eyes:
        return {"status": "no_rx", "headline":
                "No Rx on this job yet — nothing to check.",
                "eyes": {}, "options": [], "best": None, "chosen": None,
                "min_blank": min_blank}

    # A product covers the job when EVERY eye matches one of its rows.
    covering = None
    details, warnings = {}, {}
    for result in eyes.values():
        keys = set()
        for option in result["options"]:
            key = _product_key(option)
            keys.add(key)
            details.setdefault(key, option)
            warnings.setdefault(key, []).extend(option.get("warnings") or [])
        covering = keys if covering is None else covering & keys

    per_lens = len(eyes)
    products = []
    for key in covering:
        o = details[key]
        seen = list(dict.fromkeys(warnings[key]))
        products.append({
            "brand": o["brand"], "name": o["name"], "code": o["code"],
            "coating": o["coating"], "type": o["type"], "index": o["index"],
            "price": o["price"],
            "price_job": round(o["price"] * per_lens, 2)
                         if o["price"] is not None else None,
            "warnings": seen,
        })
    products.sort(key=lambda p: (p["price"] is None, p["price"] or 0,
                                 p["index"] or 0))
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
        code_known = bool(code) and code.lower() in {
            (l["code"] or "").lower() for l in lenses if l["code"]}
        if code and not code_known:
            notes.append(f"code {code} isn't in the loaded price files — "
                         "the chosen lens itself wasn't range-checked")
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
        if mismatch:
            status = "check"

    return {
        "status": status,
        "headline": headline,
        "min_blank": min_blank,
        "eyes": {label: {"rx": result["rx"]["display"],
                         "fits": len(result["options"])}
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
