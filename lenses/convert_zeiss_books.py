"""ZEISS + synchrony price books (+ the negotiated schedule) -> zeiss.csv, synchrony.csv.

Usage (from the Hub folder):
    python lenses/convert_zeiss_books.py            # writes both CSVs, prints a report
    python lenses/convert_zeiss_books.py --check    # parse + reconcile only, write nothing

Inputs, all in lenses/source/ (supplier price data, no patient data):
    zeiss-L20-2026-07.txt        ZEISS Price Book L20 AU, July 2026, as `pdftotext` text
    synchrony-L20-2026-07.txt    synchrony Price Book L20 AU, July 2026, same
    ZEISS-Full-treatments-Concord-V2-2026-08-27.xlsx
                                 the negotiated "Concord" schedule: ZEISS order code,
                                 product, flat per-piece price (fixed for the term)
    zeiss-portfolio-rx-ranges.txt
                                 the ZEISS Product Portfolio's Rx Range pages — used
                                 to cross-check the book's ranges (report only)

What comes out: one CSV row per product x index x material x coating column
(x diameter band for stock), with the sphere range, cyl limit, max minus
combined power, add range, and TWO prices:
    price        the ongoing price: the deal price where the schedule quotes
                 the line (price_basis=deal), else the book at L25
    price_promo  the launch level (L50) for unquoted lines, blank for deal lines
The Finder picks price_promo while today < config/lens_pricing.json
promo_until, then price.

Levels: the book on disk is L20 = list x 0.80. L50 = list x 0.50 = book x
0.625; L25 = list x 0.75 = book x 0.9375. (L25 after the promo is Trisha's
verbal confirmation, Sep 2026 — change LEVELS if the written deal differs.)

The schedule overlay FAILS LOUDLY (non-zero exit, nothing written) if any
quoted line matches zero catalogue rows or two quoted lines land on the same
row. Fix the matcher, never the CSV by hand.
"""

import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "source"
ZEISS_TXT = SRC / "zeiss-L20-2026-07.txt"
SYNC_TXT = SRC / "synchrony-L20-2026-07.txt"
DEAL_XLSX = SRC / "ZEISS-Full-treatments-Concord-V2-2026-08-27.xlsx"
GUIDE_TXT = SRC / "zeiss-portfolio-rx-ranges.txt"
OUT_ZEISS = HERE / "zeiss.csv"
OUT_SYNC = HERE / "synchrony.csv"

LEVELS = {"L50": 0.625, "L25": 0.9375}   # multipliers on the L20 book
ONGOING, PROMO = "L25", "L50"

COLUMNS = ["supplier", "brand", "lens", "code", "category", "index", "material",
           "form", "type", "blank_mm", "sph_min", "sph_max", "cyl_max",
           "combined_max", "range_basis", "add_min", "add_max", "add_range", "price",
           "price_promo", "price_basis", "coating", "min_fh_mm", "notes"]

NUM = r"[-+]?\d+(?:\.\d+)?"
RANGE_RE = re.compile(rf"^\s*({NUM})(\*?)\s+to\s+({NUM})(\*?)\s*$")
ADD_RE = re.compile(rf"^\s*\+?({NUM})\s+to\s+\+?({NUM})\s*$")
PRICE_RE = re.compile(r"^\$\s*([\d,]+\.\d\d)$")
INDEX_RE = re.compile(r"^1\.\d\d$")
DIA_RE = re.compile(r"^(\d\d)(?:/(\d\d))?\s*mm$")

# Minimum fitting height per progressive design (ZEISS Product Portfolio p47).
MIN_FH = {"Pure": 14, "Plus": 13, "Superb": 13, "Individual 3": 13}


# --- Small parsers -----------------------------------------------------------

def pages_of(text):
    parts = re.split(r"--- PAGE (\d+) ---", text)
    return {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts), 2)}


def cells_of(line):
    return [c.strip() for c in line.split("|")]


def parse_range(s):
    """'-6.00* to +6.00' -> (sph_min, sph_max, combined_max|None)."""
    m = RANGE_RE.match(s.replace("−", "-"))
    if not m:
        return None
    a, star_a, b, star_b = float(m.group(1)), m.group(2), float(m.group(3)), m.group(4)
    combined = abs(a) if star_a else (abs(b) if star_b else None)
    return min(a, b), max(a, b), combined


def parse_price(s):
    m = PRICE_RE.match(s.strip())
    return float(m.group(1).replace(",", "")) if m else None


def money(v):
    return f"{v:.2f}" if v is not None else ""


def num(v):
    if v is None:
        return ""
    return f"{v:g}" if float(v) != int(v) else f"{int(v)}"


def fmt_power(v):
    return "" if v is None else f"{v:+.2f}"


def material_key(material):
    """'PhotoFusionX® Grey/Brown/…' -> 'PhotoFusionX'; 'Clear BluePro' -> 'Clear BluePro'."""
    m = material.replace("®", "").strip()
    low = m.lower()
    if low.startswith("adaptivesun polarised"):
        return "AdaptiveSun Polarised"
    if low.startswith("adaptivesun"):
        return "AdaptiveSun PhotoFusion"
    if low.startswith("photofusionx") or low.startswith("photofusion x"):
        return "PhotoFusionX"
    if low.startswith("photofusion") or low.startswith("photo "):
        return "Photochromic"
    if low.startswith("transitions"):
        return "Transitions"
    if low.startswith("polarised"):
        return "Polarised"
    if low.startswith("clear bluepro"):
        return "Clear BluePro"
    if low.startswith("clear"):
        return "Clear"
    return m


# --- Made-to-order tables (ZEISS pp4-21, 26; synchrony pp4-9) -------------------

def parse_mto_page(page_text, coating_names=None):
    """Every 'X | Rx Range | ...' table on a page -> list of dicts.

    Row grammar: [index |] material | range | cyl | [add |] price ... price.
    The index sits on ONE row of its block (a vertically merged cell); a block
    starts at each row whose material begins 'Clear' (but not 'Clear BluePro').
    coating_names: fixed column names (synchrony); ZEISS derives them from the
    header + its sub-label line.
    """
    out, tables = [], []
    lines = page_text.splitlines()
    i = 0
    current = None
    while i < len(lines):
        cells = cells_of(lines[i])
        if len(cells) >= 3 and cells[1] == "Rx Range":
            has_add = cells[2] == "Add"
            head = cells[3:] if has_add else cells[2:]
            names = list(coating_names) if coating_names else []
            if not names:
                sub = cells_of(lines[i + 1]) if i + 1 < len(lines) else []
                sub = [s for s in sub if s]
                for h in head:
                    hc = h.replace("®", "").strip()
                    if hc in ("DuraVision Plus", "Plus"):
                        names.append("DuraVision Plus " + (sub.pop(0) if sub else "?"))
                    elif hc == "Plus Sun UV":
                        names.append("DuraVision Plus Sun UV")
                    elif hc == "DSHC":
                        names.append("DSHC hard coat")
                    else:
                        names.append(hc)
            current = {"product": cells[0].replace("®", "").strip(), "has_add": has_add,
                       "coatings": names, "blocks": [], "page_rows": 0}
            tables.append(current)
            i += 1
            continue
        if current is None:
            i += 1
            continue
        row = _parse_mto_row(cells, current["has_add"], len(current["coatings"]))
        if row is None:
            i += 1
            continue
        mat = row["material"].lower()
        new_block = mat.startswith("clear") and not mat.startswith("clear bluepro")
        if new_block or not current["blocks"]:
            current["blocks"].append({"index": None, "rows": []})
        block = current["blocks"][-1]
        if row["index"] is not None:
            block["index"] = row["index"]
        block["rows"].append(row)
        i += 1

    for t in tables:
        for b in t["blocks"]:
            if b["index"] is None:
                raise SystemExit(f"no index found for a block of '{t['product']}' "
                                 f"(first material {b['rows'][0]['material']!r})")
            for r in b["rows"]:
                out.append({**r, "index": b["index"], "product": t["product"],
                            "coatings": t["coatings"]})
    return out


def _parse_mto_row(cells, has_add, ncoat):
    cells = [c for c in cells]
    idx = None
    if cells and INDEX_RE.match(cells[0]):
        idx = float(cells.pop(0))
    if len(cells) < 4:
        return None
    material, rng = cells[0], parse_range(cells[1])
    if rng is None:
        return None
    cyl = re.match(rf"^\(?({NUM})\)?$", cells[2].replace("−", "-"))
    if not cyl:
        return None
    rest = cells[3:]
    add = None
    if has_add:
        if not rest:
            return None
        m = ADD_RE.match(rest[0])
        add = (float(m.group(1)), float(m.group(2))) if m else None
        rest = rest[1:]
    prices = [parse_price(c) if c != "-" else None for c in rest]
    if len(prices) != ncoat:
        # tolerate a missing trailing cell (some rows end early in the text)
        prices = (prices + [None] * ncoat)[:ncoat]
    if all(p is None for p in prices):
        return None
    return {"index": idx, "material": material, "sph_min": rng[0], "sph_max": rng[1],
            "combined_max": rng[2], "cyl_max": abs(float(cyl.group(1))),
            "add": add, "prices": prices}


# --- Finished stock tables (ZEISS p3, synchrony p3) -------------------------------

def parse_fsv_page(page_text, index_from_name=None):
    """Per-diameter stock rows -> list of dicts (one per diameter band).

    Row grammar: [index |] [product | design |] diameter | range | cyl | price.
    The product name sits on ONE row of its diameter block (often the middle
    one); a block starts when the diameter drops. The index cell marks a run
    of products; unmarked blocks take the previous marker (or the next one).
    index_from_name(product) may override that (ZEISS names carry the index).
    """
    blocks, prev_dia = [], None
    for line in page_text.splitlines():
        cells = cells_of(line)
        idx = None
        if cells and INDEX_RE.match(cells[0]):
            idx = float(cells.pop(0))
        # find the diameter cell
        dpos = next((k for k, c in enumerate(cells) if DIA_RE.match(c)), None)
        if dpos is None or dpos + 3 > len(cells):
            continue
        rng = parse_range(cells[dpos + 1])
        price = parse_price(cells[dpos + 3]) if dpos + 3 < len(cells) else None
        cyl = re.match(rf"^\(?({NUM})\)?$", cells[dpos + 2].replace("−", "-"))
        if rng is None or cyl is None or price is None:
            continue
        m = DIA_RE.match(cells[dpos])
        dias = [int(m.group(1))] + ([int(m.group(2))] if m.group(2) else [])
        product = cells[0] if dpos >= 1 else ""
        design = cells[1] if dpos >= 2 else ""
        if prev_dia is None or dias[0] < prev_dia or (product and blocks and blocks[-1]["product"]):
            blocks.append({"product": "", "design": "", "index": None, "rows": []})
        b = blocks[-1]
        if product:
            b["product"], b["design"] = product.replace("®", "").strip(), design
        if idx is not None:
            b["index"] = idx
        for dia in dias:
            b["rows"].append({"blank_mm": dia, "sph_min": rng[0], "sph_max": rng[1],
                              "combined_max": rng[2], "cyl_max": abs(float(cyl.group(1))),
                              "price": price})
        prev_dia = dias[-1]

    # index: name first, else previous marker, else next marker
    last = None
    for b in blocks:
        if not b["product"]:
            raise SystemExit(f"stock block with no product name: {b['rows']}")
        if index_from_name:
            b["index"] = index_from_name(b["product"]) or b["index"]
        if b["index"] is not None:
            last = b["index"]
        elif last is not None:
            b["index"] = last
    nxt = None
    for b in reversed(blocks):
        if b["index"] is not None:
            nxt = b["index"]
        elif nxt is not None:
            b["index"] = nxt
    out = []
    for b in blocks:
        if b["index"] is None:
            raise SystemExit(f"no index for stock product {b['product']!r}")
        for r in b["rows"]:
            out.append({**r, "product": b["product"], "design": b["design"], "index": b["index"]})
    return out


# --- ZEISS ------------------------------------------------------------------

ZEISS_MTO_PAGES = {
    # page: (category, family prefix stripped from the product name)
    4: "Single vision", 5: "Single vision", 6: "Single vision", 7: "Single vision",
    8: "Single vision",
    9: "Anti-fatigue", 10: "Anti-fatigue", 11: "Anti-fatigue", 12: "Anti-fatigue",
    13: "Occupational",
    14: "Progressive", 15: "Progressive", 16: "Progressive", 17: "Progressive",
    18: "Progressive", 19: "Progressive", 20: "Progressive", 21: "Progressive",
}


def zeiss_fsv_index(product):
    m = re.search(r"FSV\s+(15|16|167|174)\b", product)
    if not m:
        return 1.50 if "Hardcoat" in product else None
    return {"15": 1.50, "16": 1.60, "167": 1.67, "174": 1.74}[m.group(1)]


def zeiss_fsv_split(product):
    """'ZEISS ClearView FSV 16 DuraVision® Platinum BluePro UV' ->
    (lens name, material, coating)."""
    p = product.replace("®", "").replace("ZEISS ", "").strip()
    material = "Clear"
    if "BluePro" in p:
        material = "Clear BluePro"
    elif "PFX" in p or "PhotoFusion" in p:
        material = "PhotoFusionX"
    coating = "Hardcoat UV"
    m = re.search(r"DuraVision\s+(\w+)", p)
    if m:
        coating = "DuraVision " + m.group(1) + " UV"
    if p.endswith("Chrome"):
        coating = "DuraVision Chrome UV"
    name = re.sub(r"\s*(PFX Extra Grey|DuraVision.*|Clear Hardcoat UV)\s*$", "", p).strip()
    name = re.sub(r"\s+BluePro.*$", "", name).strip()
    name = re.sub(r"\s+(15|16|167|174)$", "", name).strip()      # index is a column
    return name, material, coating


def build_zeiss():
    pages = pages_of(ZEISS_TXT.read_text(encoding="utf-8"))
    rows = []
    # Finished stock (page 3)
    for r in parse_fsv_page(pages[3], zeiss_fsv_index):
        name, material, coating = zeiss_fsv_split(r["product"])
        label = name + (" PhotoFusionX" if material == "PhotoFusionX" else
                        " BluePro" if material == "Clear BluePro" else "")
        rows.append(dict(
            supplier="ZEISS", brand="ZEISS", lens=f"{label} {r['index']:.2f}",
            category="Single vision", index=r["index"], material=material,
            form=r["design"] or "", type="stock", blank_mm=r["blank_mm"],
            sph_min=r["sph_min"], sph_max=r["sph_max"], cyl_max=r["cyl_max"],
            combined_max=r["combined_max"], add=None, coating=coating,
            book=r["price"], family=name, notes="ZEISS book p3"))
    # Made to order
    for page, category in ZEISS_MTO_PAGES.items():
        for r in parse_mto_page(pages[page]):
            product = r["product"].replace("ZEISS ", "")
            fam = product.replace("Office Lens Superb - Book / Near / Room",
                                  "Office Lens Superb (Book/Near/Room)")
            min_fh = ""
            if category == "Progressive":
                for design, fh in MIN_FH.items():
                    if fam.endswith(design):
                        min_fh = fh
            for coating, price in zip(r["coatings"], r["prices"]):
                if price is None:
                    continue
                rows.append(dict(
                    supplier="ZEISS", brand="ZEISS", lens=f"{fam} {r['index']:.2f}",
                    category=category, index=r["index"], material=material_key(r["material"]),
                    form="Freeform", type="grind", blank_mm=None,
                    sph_min=r["sph_min"], sph_max=r["sph_max"], cyl_max=r["cyl_max"],
                    combined_max=r["combined_max"], add=r["add"], coating=coating,
                    book=price, family=fam, min_fh=min_fh,
                    notes=f"ZEISS book p{page}; {r['material'].replace('®', '')}"))
    # Bifocal / trifocal (page 26, second table only)
    bif = pages[26]
    start = bif.index("Bifocal & Trifocal | Rx Range")
    end = bif.index("ZEISS Glass Portfolio")
    for r in parse_mto_page(bif[start:end]):
        for coating, price in zip(r["coatings"], r["prices"]):
            if price is None:
                continue
            mat = r["material"].replace("®", "").replace(" #", "")
            rows.append(dict(
                supplier="ZEISS", brand="ZEISS", lens=f"{mat} {r['index']:.2f}",
                category="Bifocal", index=r["index"], material=material_key(mat),
                form="", type="grind", blank_mm=None,
                sph_min=r["sph_min"], sph_max=r["sph_max"], cyl_max=r["cyl_max"],
                combined_max=r["combined_max"], add=r["add"], coating=coating,
                book=price, family=mat, notes="ZEISS book p26"))
    return rows


# --- synchrony --------------------------------------------------------------

SYNC_COATS_4 = ["HMC+", "HMC Blue", "HMC+ Back Surface Multi-Coat", "HC hard coat"]
SYNC_COATS_3 = ["HMC+", "HMC Blue", "HC hard coat"]
SYNC_MTO = [
    # (page, category, coating columns, product filter)
    (4, "Single vision", SYNC_COATS_4, None),
    (5, "Progressive", SYNC_COATS_4, None),
    (6, "Progressive", SYNC_COATS_4, None),
    (7, "Progressive", SYNC_COATS_4, None),
    (8, "Bifocal", SYNC_COATS_4, lambda p: p in ("synchrony Bifocal", "synchrony Trifocal")),
    (9, "Occupational", SYNC_COATS_3, lambda p: "Access" not in p),
]


def sync_fsv_split(product):
    """'FSV Photo Grey HMC+' -> (name, material, coating)."""
    p = product.replace("®", "").strip()
    coating = "HC hard coat" if re.search(r"\bHC\b", p) else "HMC+" if "HMC+" in p else \
        "DSHC hard coat" if "DSHC" in p else "HMC" if "HMC" in p else ""
    material = "Clear"
    if "Photo" in p:
        material = "Photochromic"
    elif "Polarised" in p:
        material = "Polarised"
    name = re.sub(r"\s*(HC|HMC\+|DSHC|HMC)(\s*\(tintable\))?\s*$", "", p).strip()
    if "(tintable)" in p:
        name += " (tintable)"
    return name, material, coating


def build_synchrony():
    pages = pages_of(SYNC_TXT.read_text(encoding="utf-8"))
    rows = []
    for r in parse_fsv_page(pages[3]):
        name, material, coating = sync_fsv_split(r["product"])
        rows.append(dict(
            supplier="Synchrony", brand="ZEISS Synchrony", lens=f"{name} {r['index']:.2f}",
            category="Single vision", index=r["index"], material=material,
            form=r["design"] or "", type="stock", blank_mm=r["blank_mm"],
            sph_min=r["sph_min"], sph_max=r["sph_max"], cyl_max=r["cyl_max"],
            combined_max=r["combined_max"], add=None, coating=coating,
            book=r["price"], family=name, notes="synchrony book p3"))
    for page, category, coats, keep in SYNC_MTO:
        for r in parse_mto_page(pages[page], coats):
            product = r["product"]
            if keep and not keep(product):
                continue
            fam = product.replace("synchrony ", "")
            fam = fam.replace("Single Vision Spherical", "Single Vision Grind")
            fam = fam.replace(" (Short or Regular Corridor)", "")
            if fam == "Performance HD":
                fam = "Progressive Performance HD"
            variants = [fam]
            if fam == "Progressive Performance HD":
                variants = ["Progressive Performance HD", "Progressive Performance HD Short"]
            if fam == "Work & Office HD":
                variants = ["Work & Office HD", "Work & Office HD Short"]
            mat = r["material"].replace("®", "")
            lens_mat = material_key(mat)
            for v in variants:
                for coating, price in zip(coats, r["prices"]):
                    if price is None:
                        continue
                    label = v if category != "Bifocal" else f"{v} {mat.split()[0]}"
                    rows.append(dict(
                        supplier="Synchrony", brand="ZEISS Synchrony",
                        lens=f"{label} {r['index']:.2f}", category=category,
                        index=r["index"], material=lens_mat, form="Freeform",
                        type="grind", blank_mm=None, sph_min=r["sph_min"],
                        sph_max=r["sph_max"], cyl_max=r["cyl_max"],
                        combined_max=r["combined_max"], add=r["add"], coating=coating,
                        book=price, family=v, notes=f"synchrony book p{page}; {mat}"))
    return rows


# --- The negotiated schedule -------------------------------------------------

def read_deal():
    import openpyxl
    wb = openpyxl.load_workbook(DEAL_XLSX, data_only=True)
    ws = wb.worksheets[0]
    deals = []
    for row in ws.iter_rows(min_row=1, values_only=True):
        if not row or row[3] is None:
            continue
        code, code2, product, price = (str(row[1] or "").strip(), str(row[2] or "").strip(),
                                       str(row[3] or "").strip(), row[4])
        if product in ("Product", "") or not isinstance(price, (int, float)):
            continue
        deals.append({"code": code, "code2": code2, "product": product, "price": float(price)})
    return deals


def deal_matcher(product):
    """Schedule product text -> a predicate over catalogue rows, or None to skip."""
    p = product.replace("  ", " ").strip()
    idx = re.search(r"\b1\.(\d)(\d)?", p)          # '1.53with' (schedule typo) still reads
    index = None
    if idx:
        index = float(f"1.{idx.group(1)}{idx.group(2) or '0'}")
    if "BluePro" in p:
        material = "Clear BluePro"
    elif "PFX" in p or "Photo" in p:
        material = "PhotoFusionX"
    elif "Polarised" in p:
        material = "Polarised"
    else:
        material = "Clear"

    if p.startswith("ClearView FSV"):
        dias = [int(d) for d in re.findall(r"(?<![.\d])(65|70|75)", p)]
        return dict(supplier="ZEISS", family="ClearView FSV", index=index, material=material,
                    coating="DuraVision Platinum UV", type="stock", dias=dias or None)
    if p.startswith("SML Dig Lens"):
        return dict(supplier="ZEISS", family="SmartLife Digital", index=index,
                    material=material, coating="DuraVision Plus Platinum UV", type="grind")
    if p.startswith("SML Progressive Ind 3"):
        return dict(supplier="ZEISS", family="SmartLife Progressive Individual 3", index=index,
                    material=material, coating="DuraVision Plus Platinum UV", type="grind")
    if p.startswith("SML Progressive Superb"):
        return dict(supplier="ZEISS", family="SmartLife Progressive Superb", index=index,
                    material=material, coating="DuraVision Plus Platinum UV", type="grind")
    if p.startswith("FSV Flat 1.50"):
        return dict(supplier="Synchrony", family="FSV Clear", index=1.50, material="Clear",
                    coating="HMC+", type="stock", dias=[70])
    if p.startswith("FSV 1.56 Aspheric"):
        return dict(supplier="Synchrony", family="FSV Clear", index=1.56, material="Clear",
                    coating="HMC+", type="stock")
    if p.startswith("FSV 1.6 HMC+"):
        return dict(supplier="Synchrony", family="FSV Clear", index=1.60, material="Clear",
                    coating="HMC+", type="stock")
    if p.startswith("Prog Ultra HDV"):
        mat = "Photochromic" if "Photo" in p else material
        return dict(supplier="Synchrony", family="Progressive Ultra HDV", index=index,
                    material=mat, coating="HMC+", type="grind")
    if p.startswith("Prog Performance HD"):
        mat = "Photochromic" if "Photo" in p else material
        fam = "Progressive Performance HD Short" if "Short" in p else "Progressive Performance HD"
        return dict(supplier="Synchrony", family=fam, index=index, material=mat,
                    coating="HMC+", type="grind")
    return None


def apply_deal(rows, deals):
    """Overlay deal prices. Returns (report lines, ok)."""
    report, ok = [], True
    hits_per_row = defaultdict(list)
    for d in deals:
        m = deal_matcher(d["product"])
        if m is None:
            report.append(f"SKIPPED (no matcher): {d['code']} {d['product']}")
            ok = False
            continue
        hits = []
        for i, r in enumerate(rows):
            if (r["supplier"] == m["supplier"] and r["family"] == m["family"]
                    and r["index"] == m["index"] and r["material"] == m["material"]
                    and r["coating"] == m["coating"] and r["type"] == m["type"]
                    and (not m.get("dias") or r["blank_mm"] in m["dias"])):
                hits.append(i)
        if not hits:
            report.append(f"NO MATCH: {d['code']} {d['product']} -> {m}")
            ok = False
            continue
        for i in hits:
            hits_per_row[i].append(d)
            rows[i]["deal"] = d
        report.append(f"ok  {d['code']:>7} {d['product'][:58]:<58} ${d['price']:>7.2f} "
                      f"-> {len(hits)} row{'s' if len(hits) != 1 else ''}")
    for i, ds in hits_per_row.items():
        if len({d["code"] for d in ds}) > 1:
            report.append(f"CONFLICT: row {rows[i]['lens']} {rows[i]['coating']} "
                          f"{rows[i]['blank_mm']} hit by {[d['code'] for d in ds]}")
            ok = False
    quoted = sum(1 for r in rows if r.get("deal"))
    report.append(f"{len(deals)} schedule lines -> {quoted} catalogue rows at deal price; "
                  f"{len(rows) - quoted} rows at book level ({PROMO} then {ONGOING}).")
    return report, ok


# --- Cross-check against the ZEISS product guide -----------------------------

GUIDE_PAGES = {  # guide page -> catalogue family suffix
    32: "ClearView Single Vision", 33: "SmartLife Single Vision",
    34: "ClearMind Single Vision", 44: "Progressive Individual 3",
    45: "Progressive Superb", 46: "Progressive Pure",
    55: "Office Lens Superb (Book/Near/Room)", 60: "Clear D28",
}
GUIDE_TUPLE = re.compile(rf"({NUM})(\*?)\s+0\s+\+?({NUM})(\*?)\s+\(\s*-?({NUM})\s*\)")


def _guide_materials(text):
    """One guide page -> {(index, material_key): (sph_min, sph_max, combined, cyl)}.
    Lines: a bare '1.50' sets the index; a line holding a range tuple names its
    material before the tuple (with the previous line as a continuation when
    that line had no tuple of its own)."""
    out, index, prev = {}, None, ""
    for raw in text.replace("−", "-").splitlines():
        line = raw.strip()
        if INDEX_RE.match(line):
            index, prev = float(line), ""
            continue
        m = GUIDE_TUPLE.search(line)
        if not m or index is None:
            prev = line if len(line) < 60 and not m else ""
            continue
        head = line[:m.start()].strip()
        head = re.sub(rf"\+?{NUM}\s+to\s+\+?{NUM}\s*$", "", head).strip()   # drop an add range
        if not head or head.startswith("("):
            head = (prev + " " + head).strip()
        prev = ""
        a, sa, b, sb, cyl = (float(m.group(1)), m.group(2), float(m.group(3)),
                             m.group(4), float(m.group(5)))
        comb = abs(a) if sa else (abs(b) if sb else None)
        tup = (min(a, b), max(a, b), comb, abs(cyl))
        low = head.lower()
        keys = []
        if low.startswith("polarised / adaptivesun"):
            keys = ["Polarised", "AdaptiveSun Polarised"]
        elif low.startswith("polarised"):
            keys = ["Polarised"]
        elif low.startswith("adaptivesun"):
            keys = ["AdaptiveSun PhotoFusion"]
        elif low.startswith("photofusion x") or low.startswith("photofusionx"):
            keys = ["PhotoFusionX"]
        elif low.startswith("photofusion"):
            keys = ["Photochromic"]
        elif low.startswith("clear bluepro"):
            keys = ["Clear BluePro"]
        elif low.startswith("clear"):
            keys = ["Clear"]
        for k in keys:
            out.setdefault((index, k), tup)
    return out


def guide_check(rows):
    """Compare every ZEISS made-to-order row with the product guide's printed
    range for the same family / index / material. Where they differ the row
    keeps the BOOK range (the July 2026 pricing document) and gets a note
    quoting the guide, so the Finder shows the disagreement rather than
    silently picking a side. Returns report lines."""
    if not GUIDE_TXT.exists():
        return ["(product guide text not found — cross-check skipped)"]
    pages = pages_of(GUIDE_TXT.read_text(encoding="utf-8"))
    report = []
    for page, family in GUIDE_PAGES.items():
        if page not in pages:
            continue
        guide = _guide_materials(pages[page])
        same = differ = unseen = 0
        for r in rows:
            if r["supplier"] != "ZEISS" or r["type"] != "grind" or not r["family"].endswith(family):
                continue
            g = guide.get((r["index"], r["material"]))
            if g is None:
                unseen += 1
                continue
            book = (r["sph_min"], r["sph_max"], r["combined_max"], r["cyl_max"])
            if book == g:
                same += 1
                r["notes"] += "; range confirmed by ZEISS product guide"
            else:
                differ += 1
                r["notes"] += (f"; RANGE DIFFERS — product guide prints {fmt_power(g[0])} to "
                               f"{fmt_power(g[1])}, cyl -{g[3]:.2f}"
                               + (f", max combined -{g[2]:.2f}" if g[2] is not None else "")
                               + " — confirm in VISUSTORE")
        report.append(f"guide p{page} {family}: {same} rows match the guide, {differ} differ "
                      f"(noted on the row), {unseen} materials not printed in the guide")
    return report


# --- Output ------------------------------------------------------------------

def to_csv_rows(rows):
    out = []
    for r in rows:
        deal = r.get("deal")
        if deal:
            price, promo, basis, code = deal["price"], None, "deal", deal["code"]
        else:
            price = round(r["book"] * LEVELS[ONGOING], 2)
            promo = round(r["book"] * LEVELS[PROMO], 2)
            basis, code = ONGOING, ""
        add = r.get("add")
        out.append({
            "supplier": r["supplier"], "brand": r["brand"], "lens": r["lens"],
            "code": code, "category": r["category"], "index": f"{r['index']:.2f}",
            "material": r["material"], "form": r["form"], "type": r["type"],
            "blank_mm": num(r["blank_mm"]) if r["blank_mm"] is not None else "",
            "sph_min": fmt_power(r["sph_min"]), "sph_max": fmt_power(r["sph_max"]),
            "cyl_max": f"-{r['cyl_max']:.2f}" if r["cyl_max"] is not None else "",
            "combined_max": f"{r['combined_max']:.2f}" if r["combined_max"] is not None else "",
            # Stock bands are ranges of the lens's strongest-meridian power
            # (they tile that axis in 0.25 steps); made-to-order tables are
            # sphere ranges with a separate combined cap.
            "range_basis": "combined" if r["type"] == "stock" else "sphere",
            "add_min": f"{add[0]:.2f}" if add else "", "add_max": f"{add[1]:.2f}" if add else "",
            "add_range": f"Add {add[0]:.2f} to {add[1]:.2f}" if add else "",
            "price": money(price), "price_promo": money(promo), "price_basis": basis,
            "coating": r["coating"], "min_fh_mm": num(r["min_fh"]) if r.get("min_fh") else "",
            "notes": (r["notes"] + (f"; book ${r['book']:.2f} L20" if not deal else
                                    f"; deal price fixed for the term (book ${r['book']:.2f} L20)")),
        })
    return out


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv):
    check_only = "--check" in argv
    z, s = build_zeiss(), build_synchrony()
    rows = z + s
    deals = read_deal()
    report, ok = apply_deal(rows, deals)
    print("\n".join(report))
    print()
    print("\n".join(guide_check(rows)))
    print()
    for name, part in (("zeiss", z), ("synchrony", s)):
        c = Counter((r["category"], r["type"]) for r in part)
        print(f"{name}: {len(part)} rows — " + ", ".join(f"{k[0]} {k[1]} {v}" for k, v in sorted(c.items())))
    if not ok:
        print("\nNOT WRITTEN — fix the schedule matching above first.")
        return 1
    if check_only:
        print("\n--check: nothing written.")
        return 0
    write_csv(OUT_ZEISS, to_csv_rows(z))
    write_csv(OUT_SYNC, to_csv_rows(s))
    print(f"\nwrote {OUT_ZEISS.name} ({len(z)} rows) and {OUT_SYNC.name} ({len(s)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
