"""lens_ranges - a lens's parameters from the lens files: power bands (sphere,
cyl, sph + cyl), stock blank sizes, add range, minimum fitting height.

The model sees words and flags only (range stated / not in the file; sources
disagree - confirm with the supplier); the server renders every power and mm
figure, copied from the files with the Lens Finder's own band wording.
"""

from __future__ import annotations

from hub import compare, lenses
from hub.assistant.adapters import short_name
from hub.assistant.tools import lens_price

SPEC = {
    "name": "lens_ranges",
    "source_class": "public",
    "outbound_class": "words_flags",
    "sources": ("lens_catalogue",),
    "reason_codes": ("range_stated", "range_not_in_file", "confirm_with_supplier", "stock_listed",
                     "made_to_order", "not_ordered", "brand_alternative", "thinner_index"),
    "audience": "mark",
    "read_only": True,
    "needs_binding": False,
}

SHOW = 15


def definition(vocab=None) -> dict:
    return {
        "name": "lens_ranges",
        "description": ("Look up lens parameters in the practice's lens files: power ranges, stock "
                        "blank sizes, add range, fitting height. Returns matching lenses (ids, "
                        "attributes, flags) - never the figures; the software shows those."),
        "strict": True, "input_schema": lens_price.lookup_schema(vocab),
    }


def clean_args(args, vocab=None) -> dict | None:
    return lens_price.clean_lookup(args, vocab)


def _band_key(r):
    return (r.get("blank_mm"), r.get("sph_min"), r.get("sph_max"), r.get("cyl_max"),
            r.get("combined_max"))


def run(args: dict, src) -> dict:
    groups, order = {}, []
    for r in src.lens_catalogue:
        if not lens_price.matches(r, args):
            continue
        k = (r.get("supplier"), r.get("name"), r.get("index"), r.get("type"), r.get("material"))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)
    recs = []
    for k in order:
        rows = groups[k]
        first = rows[0]
        bands, seen = [], set()
        for r in sorted(rows, key=lambda r: (r.get("blank_mm") or 0,
                                             r.get("sph_min") if r.get("sph_min") is not None else 0)):
            if _band_key(r) in seen:
                continue
            seen.add(_band_key(r))
            bands.append(r)
        cat = lenses.normalise_category(first.get("category", ""))
        idx = compare._fmt_index(first.get("index"))
        stated = any(r.get("sph_min") is not None for r in rows)
        add = next((str(r.get("add_range")) for r in rows if r.get("add_range")), "")
        if not add:
            a = next((r for r in rows if r.get("add_min") is not None), None)
            add = (f"{a['add_min']:+.2f} to {a['add_max']:+.2f}" if a and a.get("add_max") is not None
                   else "")
        fh = next((r.get("min_fh_mm") for r in rows if r.get("min_fh_mm")), None)
        lows = [r["sph_min"] for r in rows if r.get("sph_min") is not None]
        highs = [r["sph_max"] for r in rows if r.get("sph_max") is not None]
        cyls = [r["cyl_max"] for r in rows if r.get("cyl_max") is not None]
        recs.append({
            "id": lens_price.rec_id("G", *k),
            "brand": first.get("brand") or first.get("supplier"), "product": short_name(first.get("name")),
            "index": idx, "lens_type": cat, "material": first.get("material") or "Clear",
            "stock_or_grind": first.get("type"), "orderable": bool(first.get("orderable", True)),
            "label": f"{idx} · {cat} · {short_name(first.get('name'))}",
            "bands": [lenses.range_text(b) for b in bands],
            "blanks": [(f"{b['blank_mm']:g} mm" if first.get("type") == "stock" and b.get("blank_mm")
                        else "") for b in bands],
            "range_stated": stated,
            "confirm": any(r.get("check") for r in rows),
            "add": add, "needs_add": cat != "Single vision",
            "fh": f"{fh:g} mm" if fh else "",
            "span": (f"{lenses._fmt_band_power(min(lows))} to {lenses._fmt_band_power(max(highs))}"
                     if lows and highs else ""),
            "cyl_to": f"-{max(cyls):.2f}" if cyls else "",
            "coatings": sorted({str(r.get("coating") or "") for r in rows} - {""}),
            "source": compare.source_of(first),
        })
    recs.sort(key=lambda r: (not r["orderable"], r["brand"], r["product"], r["index"]))
    return {"args": args, "records": recs}


def model_view(out: dict) -> dict:
    a = out["args"]
    recs = out["records"]
    shown = recs[:SHOW]

    def blank_word(r):
        if r["stock_or_grind"] != "stock":
            return "made to order"
        return "listed" if any(r["blanks"]) else "not listed"
    return {
        "asked": {"brand": "" if a["brand"] == "any" else a["brand"], "product": a["product"],
                  "lens_type": "" if a["lens_type"] == "any" else a["lens_type"],
                  "index": a["index"], "stock_or_grind": a["stock_or_grind"]},
        "matching": len(recs), "shown": len(shown),
        "items": [{"id": r["id"], "brand": r["brand"], "product": r["product"], "index": r["index"],
                   "lens_type": r["lens_type"], "material": r["material"],
                   "stock_or_grind": r["stock_or_grind"],
                   "range": "stated" if r["range_stated"] else "not in the file",
                   "confirm_with_supplier": r["confirm"],
                   "add_range": ("not needed" if not r["needs_add"] else
                                 "stated" if r["add"] else "not stated"),
                   "blank_sizes": blank_word(r), "still_ordered": r["orderable"]} for r in shown],
    }


def render(out: dict, picked: list) -> dict:
    rows = []
    for r in picked:
        missing = []
        if not r["range_stated"]:
            missing.append("Range not in the lens file")
        if r["confirm"]:
            missing.append("Book and product guide disagree - confirm the range with the supplier")
        if not r["orderable"]:
            missing.append("Not ordered any more (reference only)")
        bands = r["bands"] or ["range not in the file"]
        for i, band in enumerate(bands):
            blank = ("Made to order" if r["stock_or_grind"] != "stock" else
                     (r["blanks"][i] if i < len(r["blanks"]) else "") or "—")
            rows.append({"cls": "stock" if r["stock_or_grind"] == "stock" else "grind",
                         "cells": [r["label"] if i == 0 else "", r["brand"] if i == 0 else "",
                                   ("Stock" if r["stock_or_grind"] == "stock" else "Grind") if i == 0 else "",
                                   blank, band,
                                   (r["add"] or ("not stated" if r["needs_add"] else "—")) if i == 0 else "",
                                   (r["fh"] or "—") if i == 0 else "",
                                   r["source"] if i == 0 else ""],
                         "missing": missing if i == 0 else []})
    lead = picked[0] if picked else None
    facts = []
    if lead:
        facts.append(["Sphere, all bands", lead["span"] or "not in the file"])
        if lead["cyl_to"]:
            facts.append(["Cyl to", lead["cyl_to"]])
        blanks = [b for b in dict.fromkeys(lead["blanks"]) if b]
        facts.append(["Stock blanks", " / ".join(blanks)] if blanks else
                     ["Blank", "Made to order" if lead["stock_or_grind"] != "stock" else "not listed"])
        if lead["needs_add"]:
            facts.append(["Add", lead["add"] or "not stated"])
    return {
        "tool": "lens_ranges", "kind": "table", "title": "Lens parameters",
        "lead": lead and {"label": lead["label"], "brand": lead["brand"], "facts": facts},
        "columns": ["Lens", "Brand", "Stock / grind", "Blank", "Power range", "Add",
                    "Min fitting height", "Source"],
        "num_cols": [],
        "rows": rows,
        "notes": ["Ranges copied from the lens files. A stock lens lists one line per blank size."],
    }
