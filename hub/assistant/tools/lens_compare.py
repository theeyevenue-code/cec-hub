"""lens_compare - the Lens Finder chat's comparison, moved into the registry
with no change in behaviour (hub/compare.py does all the work; the model sees
attributes, fit flags and a cost RANK only, and changes only the filter words).

Offered only when the page has bound a patient's saved Rx or typed numbers.
"""

from __future__ import annotations

from hub import compare, lenschat

SPEC = {
    "name": "lens_compare",
    "source_class": "patient",
    "outbound_class": "words_flags_ranks",
    "sources": ("bound_rx_compare",),
    "reason_codes": ("lowest_verified_cost", "stock_available", "grind_needed", "thinner_index",
                     "matches_colour", "colour_not_confirmed", "range_not_verified",
                     "cost_unavailable", "standard_coating", "brand_alternative", "not_ordered",
                     "thicker_than_estimate"),
    "audience": "mark",
    "read_only": True,
    "needs_binding": True,
}


def _enum(values):
    return {"type": "string", "enum": list(values)}


def definition(vocab=None) -> dict:
    return {
        "name": "lens_compare",
        "description": ("Re-run the practice's lens comparison for the Rx the page picked, with "
                        "different requirements. Returns candidate lenses (ids, attributes, fit "
                        "flags, cost rank). Use 'any' for no preference."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "feature": _enum(compare.FEATURES), "colour": _enum(compare.COLOURS),
                "index": _enum(compare.INDEXES), "coating": _enum(compare.COATINGS),
                "type": _enum(compare.TYPES),
            },
            "required": ["feature", "colour", "index", "coating", "type"],
            "additionalProperties": False,
        },
    }


def clean_args(args, vocab=None):
    return lenschat.clean_filters(args)


def base_args(src) -> dict:
    return dict(src.bound_rx_compare.base_filters)


def run(args: dict, src) -> dict:
    res = src.bound_rx_compare.run(args)
    return {"args": args, "result": res, "records": list(res["_all"])}


def model_view(out: dict) -> dict:
    return lenschat.model_view(out["result"], out["args"])


def render(out: dict, picked: list) -> dict:
    rows = [{k: v for k, v in x.items() if k != "pair_total_d"} for x in picked]
    return {"tool": "lens_compare", "kind": "compare", "rows": rows,
            "compare": compare.public(out["result"])}


def fallback(out: dict) -> dict:
    return compare.public(out["result"])
