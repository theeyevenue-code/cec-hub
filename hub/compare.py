"""Compare - one deterministic lens comparison, shared by the Compare button and
the Lens Finder chat (Mark, 3 Nov 2026; design: second-brain codex-reviews
2026-11-03-lens-finder-chat.md + -response.md, the response wins).

No AI here. Every number the page shows comes from this module, copied from
the lens CSVs (lenses/*.csv) or added up from those copies with Decimal:

  per-lens cost   the CSV's own figure that applies today (lenses.price_now:
                  the L50 launch level while before promo_until, else price)
  pair total      right-eye cost + left-eye cost, each from that eye's own row
  vs lowest       pair total - the lowest VERIFIED pair total

A row is VERIFIED only when: it is still ordered, it has a cost above $0,
both eyes sit inside a range the file states (no "range not in the file", no
"confirm with ZEISS"), the stock blank covers the frame when a frame is known,
and - when a colour was asked for - the row names that colour. Everything
else is still listed, with what is missing, but never counts as cheapest.

Wording (design): "lowest verified cost among priced matching options";
never "best for this patient". Clinical suitability stays with the optom; the
index line is an estimate with its assumption stated.

Brands, not suppliers: Synchrony is ZEISS's own value brand, ordered from
ZEISS, so the comparison is labelled by brand.
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, ROUND_HALF_UP

from hub import lenses

LOWEST_WORDS = "Lowest verified cost among priced matching options"
COST_MISSING = "Cost unavailable — supplier confirmation needed"
SHOW = 12

KINDS = {
    "sv": "Single vision", "single vision": "Single vision", "distance": "Single vision",
    "near": "Single vision", "intermediate": "Single vision",
    "progressive": "Progressive", "multifocal": "Progressive",
    "office": "Occupational", "occupational": "Occupational",
    "bifocal": "Bifocal", "digital": "Anti-fatigue", "anti-fatigue": "Anti-fatigue",
}
FEATURES = ("any", "clear", "photochromic", "polarised", "blue")
COLOURS = ("any", "grey", "brown", "green", "pioneer", "burgundy", "black", "blue")
TYPES = ("any", "stock", "grind")
INDEXES = ("any", "1.50", "1.53", "1.56", "1.59", "1.60", "1.67", "1.74")
COATINGS = ("any", "platinum", "gold", "silver", "bluepro", "hmc+", "hmc blue", "hard coat")

PHOTO_RE = re.compile(r"photo|transition|sensity|pfx|adaptivesun", re.I)
POLAR_RE = re.compile(r"polar", re.I)
BLUE_RE = re.compile(r"bluepro|bluecontrol|blue ?cut|blueguard|hmc blue", re.I)
COLOUR_WORDS = {"grey": "grey", "gray": "grey", "brown": "brown", "green": "green",
                "pioneer": "pioneer", "burgundy": "burgundy", "black": "black", "blue": "blue",
                "amethyst": "amethyst", "sapphire": "sapphire", "emerald": "emerald",
                "graphite": "graphite", "amber": "amber"}
# Blue-light filters are not a colour.
NOT_COLOUR_RE = re.compile(r"bluepro|bluecontrol|blue\s*cut|blueguard|blue\s*filter", re.I)

CENT = Decimal("0.01")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def money(v) -> Decimal | None:
    """A CSV price as Decimal cents; None for missing, zero or negative (a $0
    ZEISS row or a blank cell is unknown, not free)."""
    if v is None or v == "":
        return None
    try:
        d = Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)
    except Exception:
        return None
    return d if d > 0 else None


def fmt_money(d: Decimal | None) -> str:
    return f"${d:,.2f}" if d is not None else ""


def row_id(lens: dict) -> str:
    """Stable id for one CSV row (same row, same id, across reloads)."""
    parts = [lens.get("source", ""), lens.get("supplier", ""), lens.get("name", ""),
             lens.get("index"), lens.get("type", ""), lens.get("material", ""),
             lens.get("coating", ""), lens.get("blank_mm"), lens.get("sph_min"),
             lens.get("sph_max"), lens.get("cyl_max"), lens.get("price"), lens.get("code", "")]
    return "R" + hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


def product_id(key: tuple) -> str:
    return "P" + hashlib.sha1("|".join(str(p) for p in key).encode("utf-8")).hexdigest()[:10]


def _fmt_index(v) -> str:
    return f"{float(v):.2f}" if v is not None else ""


def _hay(lens: dict) -> str:
    return f"{lens.get('name', '')} {lens.get('material', '')} {lens.get('notes', '')}"


def has_feature(lens: dict, feature: str) -> bool:
    if feature in ("", "any", None):
        return True
    hay = _hay(lens)
    if feature == "photochromic":
        return bool(PHOTO_RE.search(hay))
    if feature == "polarised":
        return bool(POLAR_RE.search(hay))
    if feature == "blue":
        return bool(BLUE_RE.search(hay + " " + str(lens.get("coating") or "")))
    if feature == "clear":
        mat = str(lens.get("material") or "Clear").lower()
        return not PHOTO_RE.search(hay) and not POLAR_RE.search(hay) and \
            mat in ("", "clear", "clear / tinted", "tinted", "clear bluepro")
    return True


def row_colours(lens: dict) -> list[str]:
    """Colours the row itself names. The lens name wins (a stock 'PFX Extra
    Grey' is grey only); otherwise the material / notes list
    ('Photo Grey/Brown/Pioneer/...'). [] = the file doesn't say."""
    for text in (str(lens.get("name") or ""),
                 f"{lens.get('material', '')} {lens.get('notes', '')}"):
        text = NOT_COLOUR_RE.sub(" ", text)
        found = []
        for w in re.findall(r"[A-Za-z]+", text):
            c = COLOUR_WORDS.get(w.lower())
            if c and c not in found:
                found.append(c)
        if found:
            return found
    return []


def colour_status(lens: dict, colour: str) -> str:
    """'named' (row names it), 'other' (row names only other colours),
    'not stated' (file doesn't say), or '' when no colour was asked for."""
    if colour in ("", "any", None):
        return ""
    cols = row_colours(lens)
    if not cols:
        return "not stated"
    return "named" if colour in cols else "other"


def book_l20(lens: dict) -> Decimal | None:
    m = re.search(r"book \$([\d,]+\.\d\d) L20", str(lens.get("notes") or ""))
    return money(m.group(1).replace(",", "")) if m else None


def source_of(lens: dict) -> str:
    notes = str(lens.get("notes") or "")
    m = re.search(r"(?i)\b(zeiss|synchrony) book p\d+", notes)
    if m:
        return m.group(0)
    m = re.search(r"(?i)hoya product guide[^;]*", notes)
    if m:
        return m.group(0).strip()
    return lens.get("source", "")


def source_date(lens: dict, basis: str) -> str:
    sup = str(lens.get("supplier") or "").lower()
    if basis == "deal":
        return "ZEISS deal schedule 27 Aug 2026"
    if sup in ("zeiss", "synchrony"):
        return "price book Jul 2026 (L20)"
    if sup == "hoya":
        return "ProVision T3 price list (date not in the file)"
    return "date not in the file"


def basis_label(basis: str, pricing: dict) -> str:
    if basis == "promo":
        until = (pricing or {}).get("promo_until") or ""
        return f"L50 launch level{' until ' + until if until else ''}"
    return {"L25": "L25 (book x 0.9375)", "deal": "Deal price, fixed for the term",
            "T3": "Hoya ProVision T3", "L50": "L50"}.get(basis, basis or "basis not stated")


def gst_label(lens: dict, basis: str) -> str:
    """The lens files' README says the price column is cost per lens ex GST; the
    rows themselves don't carry a GST column. Hoya's file predates that note."""
    sup = str(lens.get("supplier") or "").lower()
    if sup in ("zeiss", "synchrony") and basis in ("L25", "deal", "promo", "L50"):
        return "ex GST (lens files README)"
    return "GST basis not stated"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def _num(v, lo, hi):
    try:
        x = float(lenses._rx_num(v)) if lenses._rx_num(v) is not None else None
    except (TypeError, ValueError):
        return None
    if x is None or not (lo <= x <= hi):
        return None
    return x


def clean_eye(e) -> dict | None:
    if not isinstance(e, dict):
        return None
    sph = _num(e.get("sph"), -30, 30)
    if sph is None:
        return None
    cyl = _num(e.get("cyl"), -12, 12) if str(e.get("cyl") or "").strip() else 0.0
    if cyl is None:
        return None
    return {"sph": sph, "cyl": cyl}


def clean_request(req: dict) -> tuple[dict | None, str]:
    """Validated requirements, or (None, why). Fixed vocab only."""
    req = req if isinstance(req, dict) else {}
    right, left = clean_eye(req.get("right")), clean_eye(req.get("left"))
    if not right and not left:
        return None, "Type at least one eye's sphere."
    kind = KINDS.get(str(req.get("kind") or "sv").strip().lower())
    if not kind:
        return None, "Pick a lens type."
    add = None
    if kind != "Single vision":
        add = _num(req.get("add"), 0.25, 4.0)
        if add is None:
            return None, "Type the add for a multifocal, office or bifocal lens."

    def pick(name, allowed):
        v = str(req.get(name) or "any").strip().lower()
        return v if v in allowed else "any"
    blank = _num(req.get("min_blank"), 40, 90) if req.get("min_blank") not in (None, "") else None
    need = None
    if isinstance(req.get("blank_need"), dict):
        need = {s: float(v) for s, v in req["blank_need"].items()
                if s in ("R", "L") and isinstance(v, (int, float)) and 40 <= v <= 90}
    elif blank is not None:
        need = {s: blank for s, e in (("R", right), ("L", left)) if e}
    return {"right": right, "left": left, "kind": kind, "add": add,
            "index": pick("index", INDEXES), "feature": pick("feature", FEATURES),
            "colour": pick("colour", COLOURS), "coating": pick("coating", COATINGS),
            "type": pick("type", TYPES), "blank_need": need or None}, ""


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

def _eye_fits(rows: list, eye: dict | None, q: dict, pricing: dict) -> dict:
    """{coated product key: {"rows": [option rows], "status": verified|not verified}}
    for ONE eye, from the Lens Finder's own range logic (find_options:
    transposition, sph/cyl/combined bands, add range, both stock readings)."""
    if not eye:
        return {}
    res = lenses.find_options(rows, eye["sph"], eye["cyl"], None, add=q["add"],
                              kind=q["kind"], pricing=pricing)
    out = {}
    for o in res.get("options") or []:
        k = lenses._product_of(o)
        ent = out.setdefault(k, {"rows": [], "status": "verified", "why": ""})
        ent["rows"].append(o)
        unknown = [w for w in o.get("warnings") or [] if "isn't in the file" in w]
        if o.get("a_ok") is None or unknown:
            ent["status"] = "not verified"
            ent["why"] = "range not in the lens file" if o.get("a_ok") is None else unknown[0]
    for p in res.get("possible_rows") or []:
        k = lenses._product_of(p)
        if k not in out:
            out[k] = {"rows": [p], "status": "not verified",
                      "why": p.get("possible_why") or "confirm the range with the supplier"}
    return out


def _filter(rows: list, q: dict) -> tuple[list, dict]:
    kept, dropped = [], {"other_colour": 0}
    for l in rows:
        if q["index"] != "any" and _fmt_index(l.get("index")) != q["index"]:
            continue
        if q["type"] != "any" and l.get("type") != q["type"]:
            continue
        if not has_feature(l, q["feature"]):
            continue
        if q["coating"] != "any" and q["coating"] not in str(l.get("coating") or "").lower():
            continue
        if colour_status(l, q["colour"]) == "other":
            dropped["other_colour"] += 1
            continue
        kept.append(l)
    return kept, dropped


def _blank_line(typ: str, blanks: dict, need: dict | None) -> tuple[bool | None, str]:
    """(fits or None when unknown, plain words)."""
    if typ != "stock":
        return True, "Made to order"
    sizes = sorted({b for v in blanks.values() for b in v})
    if not need:
        return None, ("Stock " + " / ".join(f"{b:g} mm" for b in sizes)) if sizes else "Stock"
    ok, got = True, {}
    for side, req in need.items():
        have = sorted(blanks.get(side) or [])
        pick = next((b for b in have if b >= req - 1e-9), None)
        got[side] = pick
        ok = ok and pick is not None
    if ok:
        vals = sorted({v for v in got.values()})
        return True, f"Stock {' / '.join(f'{v:g} mm' for v in vals)} covers the {max(need.values()):g} mm needed"
    return False, (f"Needs {max(need.values()):g} mm - stock for this Rx only "
                   + (" / ".join(f"{b:g} mm" for b in sizes) if sizes else "unknown size"))


def compare(rows: list, req: dict, pricing: dict | None = None, sells: dict | None = None,
            preferred_coatings: list | None = None) -> dict:
    """The comparison. rows = the full catalogue (orderable or not); req = a
    clean_request() result. Returns {"requirements", "rows", "lowest_id", ...}.
    Never raises on data gaps; they become plain words on the row."""
    pricing = pricing or {}
    sells = sells or {}
    pref = [c.lower() for c in (preferred_coatings or pricing.get("preferred_coatings")
                                 or lenses.DEFAULT_PREFERRED_COATINGS)]
    q = req
    eyes = [s for s, e in (("R", q["right"]), ("L", q["left"])) if e]
    kept, dropped = _filter(rows, q)
    fits = {s: _eye_fits(kept, q["right" if s == "R" else "left"], q, pricing) for s in eyes}
    keys = set.intersection(*[set(f) for f in fits.values()]) if fits else set()

    # One line per lens (supplier, name, index, material, type); pick its coating.
    by_lens = {}
    for k in keys:
        sup, name, idx, mat, coat, typ = k
        by_lens.setdefault((sup, name, idx, mat, typ), []).append(k)

    lines = []
    for lk, coat_keys in by_lens.items():
        def coat_rank(k):
            row = fits[eyes[0]][k]["rows"][0]
            c = str(k[4] or "").lower()
            price = money(lenses.price_now(row, pricing=pricing)[0])
            if q["coating"] != "any":
                std = 0
            else:
                std = 0 if (not c or any(p in c for p in pref)) and "back surface" not in c else 1
            return (std, price is None, price or Decimal(0), c)
        coat_keys.sort(key=coat_rank)
        k = coat_keys[0]
        lines.append(_line(k, fits, eyes, q, pricing, sells, others=len(coat_keys) - 1))

    priced_ok = [x for x in lines if x["verified"]]
    lowest = min(priced_ok, key=lambda x: (x["pair_total_d"], x["brand"], x["product"]), default=None)
    for x in lines:
        if x is lowest:
            x["vs_lowest"] = "lowest"
        elif lowest is not None and x["pair_total_d"] is not None:
            x["vs_lowest"] = fmt_money_signed(x["pair_total_d"] - lowest["pair_total_d"])
        else:
            x["vs_lowest"] = ""
    lines.sort(key=lambda x: (not x["verified"], x["pair_total_d"] is None,
                              x["pair_total_d"] or Decimal(0), x["brand"], x["product"]))
    for x in lines:
        x.pop("pair_total_d", None)

    rec = None
    if q["right"] or q["left"]:
        rec = max(lenses.recommended_index(e["sph"], e["cyl"]) for e in (q["right"], q["left"]) if e)
    counts = {"matching": len(lines), "verified": len(priced_ok),
              "cost_unavailable": sum(1 for x in lines if x["cost_status"] != "priced"),
              "not_verified": sum(1 for x in lines if not x["verified"]),
              "other_colour": dropped["other_colour"]}
    return {
        "requirements": _req_words(q),
        "lowest_id": lowest["id"] if lowest else "",
        "lowest_words": LOWEST_WORDS,
        "lowest": lowest and {k: lowest[k] for k in ("id", "label", "brand", "pair_total",
                                                     "type")},
        "rows": lines[:SHOW], "more": max(0, len(lines) - SHOW),
        "all_ids": [x["id"] for x in lines],
        "_all": lines,
        "counts": counts,
        "index_estimate": (f"Index estimate: {rec:.2f} or higher keeps this power from being "
                           "thick (from the strongest meridian; an estimate, not a prescription)."
                           if rec else ""),
        "notes": _notes(q, counts),
    }


def fmt_money_signed(d: Decimal) -> str:
    if d == 0:
        return "same"
    return ("+" if d > 0 else "−") + f"${abs(d):,.2f}"


def _line(k, fits, eyes, q, pricing, sells, others: int) -> dict:
    sup, name, idx, mat, coat, typ = k
    per_eye, blanks, status, whys = {}, {}, {}, []
    first = None
    for s in eyes:
        ent = fits[s][k]
        row = sorted(ent["rows"], key=lambda r: r.get("blank_mm") or 0)[0]
        first = first or row
        price, basis = lenses.price_now(row, pricing=pricing)
        per_eye[s] = {"row_id": row_id(row), "cost_d": money(price), "basis": basis}
        blanks[s] = sorted({r["blank_mm"] for r in ent["rows"] if r.get("blank_mm")})
        status[s] = ent["status"]
        if ent["status"] != "verified" and ent["why"]:
            whys.append(f"{'Right' if s == 'R' else 'Left'}: {ent['why']}")
    orderable = bool(first.get("orderable", True))
    costs = [per_eye[s]["cost_d"] for s in eyes]
    priced = all(c is not None for c in costs)
    pair = sum(costs, Decimal(0)) if priced else None
    blank_ok, blank_words = _blank_line(typ, blanks, q.get("blank_need"))
    cstat = colour_status(first, q["colour"])
    range_ok = all(v == "verified" for v in status.values())
    verified = bool(orderable and priced and range_ok and blank_ok is not False
                    and cstat in ("", "named"))
    basis = per_eye[eyes[0]]["basis"]
    sell_by = sells.get((sup, name, _fmt_index(idx), typ)) or {}
    sell = sell_by.get(coat, sell_by.get(""))
    cat = lenses.normalise_category(first.get("category", ""))
    short = re.sub(r"\s+1\.\d\d$", "", str(name or "")).strip()
    missing = []
    if not orderable:
        missing.append("Not ordered any more (reference only)")
    if not priced:
        missing.append(COST_MISSING)
    if not range_ok:
        missing.append("Range not verified for this Rx")
    if blank_ok is False:
        missing.append("Stock blank too small for the frame")
    if cstat == "not stated":
        missing.append(f"{q['colour'].title()} not named in the lens file - confirm the colour")
    steps = lenses.index_step(rec_of(q)) - lenses.index_step(idx) if idx is not None else 0
    out = {
        "id": product_id(k),
        "row_ids": {s: per_eye[s]["row_id"] for s in eyes},
        "label": f"{_fmt_index(idx)} · {cat} · {short}"
                 + (f" · {mat}" if mat and mat.lower() != "clear" else ""),
        "index": _fmt_index(idx), "category": cat, "product": short,
        "brand": str(first.get("brand") or sup), "supplier": sup,
        "material": mat or "Clear", "coating": coat, "other_coatings": others,
        "type": "Stock" if typ == "stock" else "Grind",
        "fit": {s: status[s] for s in eyes}, "fit_why": whys,
        "blank": blank_words, "blank_ok": blank_ok,
        "colour": cstat, "colours": row_colours(first),
        "cost": {s: fmt_money(per_eye[s]["cost_d"]) for s in eyes},
        "cost_status": "priced" if priced else "unavailable",
        "pair_total": fmt_money(pair) if pair is not None else COST_MISSING,
        "pair_total_d": pair,
        "basis": basis_label(basis, pricing),
        "price_csv": fmt_money(money(first.get("price"))),
        "price_promo_csv": fmt_money(money(first.get("price_promo"))),
        "csv_basis": str(first.get("price_basis") or "") or "not stated",
        "book_l20": fmt_money(book_l20(first)),
        "source": source_of(first), "source_date": source_date(first, basis),
        "gst": gst_label(first, basis),
        "sell_pair": fmt_money(money(sell)) if sell is not None else "",
        "orderable": orderable, "verified": verified, "missing": missing,
        "thicker": steps >= 1,
        "code": str(first.get("code") or ""),
    }
    return out


def rec_of(q: dict) -> float:
    es = [e for e in (q["right"], q["left"]) if e]
    return max(lenses.recommended_index(e["sph"], e["cyl"]) for e in es) if es else 1.5


def _req_words(q: dict) -> list[str]:
    out = [q["kind"]]
    if q["feature"] != "any":
        out.append({"blue": "blue filter"}.get(q["feature"], q["feature"]))
    if q["colour"] != "any":
        out.append(q["colour"])
    if q["index"] != "any":
        out.append(f"index {q['index']}")
    if q["coating"] != "any":
        out.append(f"coating {q['coating']}")
    if q["type"] != "any":
        out.append(q["type"])
    if q["add"] is not None:
        out.append(f"add {q['add']:+.2f}")
    return out


def _notes(q: dict, counts: dict) -> list[str]:
    n = []
    if counts["other_colour"]:
        n.append(f"{counts['other_colour']} lens file rows name only other colours - left out.")
    if counts["cost_unavailable"]:
        n.append(f"{counts['cost_unavailable']} matching lenses have no cost in the file - "
                 "listed, never counted as cheapest.")
    n.append("Costs are the expected contract cost from the lens files, not invoiced cost. "
             "Synchrony is ZEISS's own value brand, ordered from ZEISS.")
    return n


def public(result: dict) -> dict:
    """The result without the internal full list (for JSON)."""
    return {k: v for k, v in result.items() if not k.startswith("_")}
