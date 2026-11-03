"""lens_price - what a lens costs the practice (per lens, from the lens files)
and what it sells for (per pair, from the practice's price sheet).

Business data: the model never sees a figure. It sees which items are priced,
their cost RANK and whether the price sheet has them; the server renders every
dollar from the files (Mark's rule: every item is priced PER LENS; the pair
column is the per-lens figure times two, labelled so).
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal

from hub import compare, lenses
from hub.assistant.adapters import short_name

SPEC = {
    "name": "lens_price",
    "source_class": "business",
    "outbound_class": "words_flags_ranks",
    "sources": ("lens_catalogue", "lens_pricing", "cec_prices"),
    "reason_codes": ("lowest_cost", "cost_unavailable", "on_price_sheet", "not_on_price_sheet",
                     "not_ordered", "brand_alternative", "thinner_index", "standard_coating"),
    "audience": "mark",
    "read_only": True,
    "needs_binding": False,
}

SHOW = 15
LENS_TYPES = ("any", "Single vision", "Progressive", "Occupational", "Bifocal", "Anti-fatigue")
NO_MATCH = "No lens in the files matches those words. Use words from a lens name."


def _enum(values):
    return {"type": "string", "enum": list(values)}


def brands(vocab) -> list:
    return sorted(w for w in (vocab or ()) if w in ("Hoya", "ZEISS", "ZEISS Synchrony")) or ["ZEISS"]


def lookup_schema(vocab) -> dict:
    return {
        "type": "object",
        "properties": {
            "brand": _enum(["any"] + brands(vocab)),
            "product": {"type": "string", "description": "Words from the lens name, or 'any'."},
            "lens_type": _enum(LENS_TYPES), "index": _enum(compare.INDEXES),
            "stock_or_grind": _enum(compare.TYPES),
        },
        "required": ["brand", "product", "lens_type", "index", "stock_or_grind"],
        "additionalProperties": False,
    }


def definition(vocab=None) -> dict:
    schema = lookup_schema(vocab)
    schema["properties"]["coating"] = _enum(compare.COATINGS)
    schema["required"].append("coating")
    return {
        "name": "lens_price",
        "description": ("Look up lens prices in the practice's lens files and price sheet. Returns "
                        "matching lenses (ids, attributes, whether priced, cost rank, whether on "
                        "the price sheet) - never the figures; the software shows those."),
        "strict": True, "input_schema": schema,
    }


def clean_lookup(args, vocab, extra=()) -> dict | None:
    """Validated lookup args or None. The product words are a FILTER on the
    lens files only - they never go back to the model."""
    if not isinstance(args, dict):
        return None
    keys = {"brand", "product", "lens_type", "index", "stock_or_grind", *extra}
    if set(args) - keys:
        return None
    out = {}
    if args.get("brand", "any") not in ["any"] + brands(vocab):
        return None
    out["brand"] = args.get("brand", "any")
    for k, allowed in (("lens_type", LENS_TYPES), ("index", compare.INDEXES),
                       ("stock_or_grind", compare.TYPES)):
        v = args.get(k, "any")
        if v not in allowed:
            return None
        out[k] = v
    p = args.get("product", "any")
    if not isinstance(p, str) or len(p) > 60 or not re.fullmatch(r"[A-Za-z0-9 .+()/\-]*", p):
        return None
    words = [w for w in re.findall(r"[a-z0-9.+]+", p.lower()) if w != "any"]
    out["product_words"] = words
    out["product"] = p if p in (vocab or ()) else ""
    return out


def clean_args(args, vocab=None) -> dict | None:
    if not isinstance(args, dict) or args.get("coating", "any") not in compare.COATINGS:
        return None
    out = clean_lookup(args, vocab, extra=("coating",))
    if out is not None:
        out["coating"] = args.get("coating", "any")
    return out


def matches(p: dict, args: dict) -> bool:
    if args["brand"] != "any" and args["brand"] not in (p.get("brand"), p.get("supplier")):
        return False
    if args["lens_type"] != "any" and lenses.normalise_category(p.get("category", "")) != args["lens_type"]:
        return False
    if args["index"] != "any" and compare._fmt_index(p.get("index")) != args["index"]:
        return False
    if args["stock_or_grind"] != "any" and p.get("type") != args["stock_or_grind"]:
        return False
    hay = f"{p.get('brand', '')} {p.get('name', '')} {p.get('material', '')}".lower()
    return all(w in hay for w in args["product_words"])


def rec_id(prefix: str, *parts) -> str:
    return prefix + hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


def run(args: dict, src) -> dict:
    pricing = dict(src.lens_pricing)
    products = lenses.group_products(list(src.lens_catalogue), pricing=pricing)
    products = lenses.attach_cec_price(products, dict(src.cec_prices))
    recs = []
    for p in products:
        if not matches(p, args):
            continue
        for c in p.get("coatings") or []:
            coat = c.get("coating") or ""
            if args["coating"] != "any" and args["coating"] not in coat.lower():
                continue
            cost = compare.money(c.get("price_now"))
            sell = compare.money(c.get("cec_price"))
            basis = c.get("basis") or ""
            idx = compare._fmt_index(p.get("index"))
            cat = lenses.normalise_category(p.get("category", ""))
            recs.append({
                "id": rec_id("Q", p.get("supplier"), p.get("name"), p.get("index"), p.get("type"), coat),
                "brand": p.get("brand") or p.get("supplier"), "product": short_name(p.get("name")),
                "index": idx, "lens_type": cat, "coating": coat,
                "stock_or_grind": p.get("type"), "orderable": bool(p.get("orderable", True)),
                "label": f"{idx} · {cat} · {short_name(p.get('name'))}",
                "cost_d": cost, "sell_d": sell,
                "basis": compare.basis_label(basis, pricing),
                "source": compare.source_of(p), "source_date": compare.source_date(p, basis),
                "gst": compare.gst_label(p, basis),
            })
    recs.sort(key=lambda r: (not r["orderable"], r["cost_d"] is None, r["cost_d"] or Decimal(0),
                             r["brand"], r["product"], r["coating"]))
    return {"args": args, "records": recs}


def model_view(out: dict) -> dict:
    a = out["args"]
    recs = out["records"]
    rank, n = {}, 0
    for r in recs:
        if r["cost_d"] is not None and r["orderable"]:
            n += 1
            rank[r["id"]] = n
    shown = recs[:SHOW]
    return {
        "asked": {"brand": "" if a["brand"] == "any" else a["brand"], "product": a["product"],
                  "lens_type": "" if a["lens_type"] == "any" else a["lens_type"],
                  "index": a["index"], "stock_or_grind": a["stock_or_grind"]},
        "matching": len(recs), "shown": len(shown),
        "items": [{"id": r["id"], "brand": r["brand"], "product": r["product"], "index": r["index"],
                   "lens_type": r["lens_type"], "coating": r["coating"],
                   "stock_or_grind": r["stock_or_grind"],
                   "cost": "priced" if r["cost_d"] is not None else "unavailable",
                   "cost_rank": rank.get(r["id"]),
                   "sell": "on price sheet" if r["sell_d"] is not None else "not on price sheet",
                   "still_ordered": r["orderable"]} for r in shown],
    }


def render(out: dict, picked: list) -> dict:
    rows = []
    for r in picked:
        pair = r["cost_d"] * 2 if r["cost_d"] is not None else None
        rows.append({"cls": "stock" if r["stock_or_grind"] == "stock" else "grind",
                     "cells": [r["label"], r["brand"],
                               "Stock" if r["stock_or_grind"] == "stock" else "Grind",
                               r["coating"] or "—",
                               compare.fmt_money(r["cost_d"]) or compare.COST_MISSING,
                               compare.fmt_money(pair) or "—",
                               compare.fmt_money(r["sell_d"]) or "not on price sheet",
                               f"{r['basis']} · {r['source']} · {r['source_date']} · {r['gst']}"],
                     "missing": ([] if r["orderable"] else ["Not ordered any more (reference only)"])})
    lead = picked[0] if picked else None
    return {
        "tool": "lens_price", "kind": "table", "title": "Lens price",
        "lead": lead and {"label": lead["label"], "brand": lead["brand"],
                          "facts": [["Cost / lens", compare.fmt_money(lead["cost_d"]) or compare.COST_MISSING],
                                    ["Sells / pair", compare.fmt_money(lead["sell_d"]) or "not on price sheet"]]},
        "columns": ["Lens", "Brand", "Stock / grind", "Coating", "Cost / lens",
                    "Pair cost (2 lenses)", "Sells / pair", "Price source"],
        "num_cols": [4, 5, 6],
        "rows": rows,
        "notes": ["Cost is per lens, from the lens files: the expected contract cost, not "
                  "invoiced cost. Sells is per pair, from the practice's price sheet."],
    }
