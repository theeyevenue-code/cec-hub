"""Which tools the assistant may use - approval is CODE, config only narrows
(Codex 2026-11-03-hub-assistant #1; accepted response MODIFY 1-2).

APPROVED_TOOLS is the only place a tool is allowed. Each entry pins:

  module          the one module that implements it
  sources         the read adapters it may touch (ReadContext enforces this)
  source_class    how sensitive what it READS is: public / internal /
                  business / patient. Patient-derived stays patient even
                  when what leaves is an aggregate.
  outbound_class  what may LEAVE the server, kept separate from the above
  outbound        the exact schema of what the model may see (guard.check_view)
  reason_codes    the reason codes its answers may use
  max_audience    mark (Mark only) or staff (staff and Mark)
  approval        who said yes, and when
  review          the independent review it passed

At startup AND on every call, a tool runs only when: it is in APPROVED_TOOLS,
its module's own SPEC matches the entry (same name, classes, a subset of the
sources and codes, read_only true), every class is a known one, config has it
switched on, config's audience is no wider than max_audience, and the person
asking is inside that audience. config/assistant.json can switch a tool off or
narrow its audience; it can never add a tool or widen one.

Adding or widening an entry is a commit Server Desk cannot self-approve:
public-source tools need a Codex note; business, patient, Optomate-read or
new-source tools need Mark's yes logged plus a Codex note.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from hub import compare
from hub.assistant import guard

log = logging.getLogger("cec-hub.assistant")

SOURCE_CLASSES = ("public", "internal", "business", "patient")
OUTBOUND_CLASSES = {
    "words_flags": "lens-file words, enum values and yes/no flags; no figures",
    "words_flags_ranks": "as words_flags, plus cost RANK order; never a price",
}
AUDIENCES = ("mark", "staff")              # narrow -> wide
MAX_TOOLS = 12                             # design §9: review routing past this


def audience_rank(a) -> int:
    return AUDIENCES.index(a) if a in AUDIENCES else 99


# --- outbound schemas (what the model may see, field by field) --------------

def _enum(*values):
    return ("enum", tuple(values))


REQUIREMENTS = {
    "lens_type": _enum("Single vision", "Progressive", "Occupational", "Bifocal", "Anti-fatigue"),
    "feature": _enum(*compare.FEATURES), "colour": _enum(*compare.COLOURS),
    "index": _enum(*compare.INDEXES), "coating": _enum(*compare.COATINGS),
    "type": _enum(*compare.TYPES),
}

COMPARE_VIEW = {
    "requirements": REQUIREMENTS,
    "matching": "count", "verified": "count",
    "candidates": [{
        "id": "id", "brand": "lens_word", "product": "lens_word", "index": "index",
        "lens_type": "lens_word", "material": "lens_word", "coating": "lens_word",
        "stock_or_grind": _enum("stock", "grind"),
        "blank": _enum("made to order", "fits frame", "too small for frame", "frame not checked"),
        "range_right": _enum("verified", "not verified", "no lens"),
        "range_left": _enum("verified", "not verified", "no lens"),
        "colour": _enum("named", "other", "not stated", "not asked"),
        "still_ordered": "bool", "cost": _enum("priced", "unavailable"),
        "cost_rank": "rank", "verified": "bool", "thicker_than_index_estimate": "bool",
    }],
}

LOOKUP_KEY = {
    "brand": "lens_word", "product": "lens_word", "lens_type": "lens_word",
    "index": _enum(*compare.INDEXES), "stock_or_grind": _enum(*compare.TYPES),
}

PRICE_VIEW = {
    "asked": LOOKUP_KEY, "matching": "count", "shown": "count",
    "items": [{
        "id": "id", "brand": "lens_word", "product": "lens_word", "index": "index",
        "lens_type": "lens_word", "coating": "lens_word", "stock_or_grind": _enum("stock", "grind"),
        "cost": _enum("priced", "unavailable"), "cost_rank": "rank",
        "sell": _enum("on price sheet", "not on price sheet"), "still_ordered": "bool",
    }],
}

RANGES_VIEW = {
    "asked": LOOKUP_KEY, "matching": "count", "shown": "count",
    "items": [{
        "id": "id", "brand": "lens_word", "product": "lens_word", "index": "index",
        "lens_type": "lens_word", "material": "lens_word",
        "stock_or_grind": _enum("stock", "grind"),
        "range": _enum("stated", "not in the file"), "confirm_with_supplier": "bool",
        "add_range": _enum("stated", "not stated", "not needed"),
        "blank_sizes": _enum("listed", "not listed", "made to order"), "still_ordered": "bool",
    }],
}

# --- reason codes: one central list, each tool may use only its own --------

REASON_CODES = {
    # compare (the Lens Finder chat's codes, unchanged)
    "lowest_verified_cost": "Lowest verified cost among priced matching options",
    "stock_available": "Stock lens covers this Rx",
    "grind_needed": "Made to order (grind)",
    "thinner_index": "Thinner index",
    "matches_colour": "Lens file names the colour asked for",
    "colour_not_confirmed": "Colour not named in the lens file - confirm",
    "range_not_verified": "Range not verified for this Rx",
    "cost_unavailable": "Cost unavailable - supplier confirmation needed",
    "standard_coating": "The practice's standard coating",
    "brand_alternative": "Another brand for the same need",
    "not_ordered": "Not ordered any more (reference only)",
    "thicker_than_estimate": "Thicker than the index estimate",
    # price
    "lowest_cost": "Lowest cost in the lens files among these",
    "on_price_sheet": "On the practice's price sheet",
    "not_on_price_sheet": "Not on the practice's price sheet",
    # ranges
    "range_stated": "Range stated in the lens file",
    "range_not_in_file": "Range not in the lens file",
    "confirm_with_supplier": "Sources disagree - confirm the range with the supplier",
    "stock_listed": "Comes as a stock lens",
    "made_to_order": "Made to order",
    # any tool
    "not_answerable": "Not something these lens tools answer",
}
EMPTY_CODE = "not_answerable"

COMPARE_CODES = ("lowest_verified_cost", "stock_available", "grind_needed", "thinner_index",
                 "matches_colour", "colour_not_confirmed", "range_not_verified",
                 "cost_unavailable", "standard_coating", "brand_alternative", "not_ordered",
                 "thicker_than_estimate")
PRICE_CODES = ("lowest_cost", "cost_unavailable", "on_price_sheet", "not_on_price_sheet",
               "not_ordered", "brand_alternative", "thinner_index", "standard_coating")
RANGES_CODES = ("range_stated", "range_not_in_file", "confirm_with_supplier", "stock_listed",
                "made_to_order", "not_ordered", "brand_alternative", "thinner_index")

PHASE_1A_REVIEW = ("Codex 2026-11-03-hub-assistant.md + -response.md "
                   "(second-brain e5da6bf2): phase 1a, Mark-only")


@dataclass(frozen=True)
class Approved:
    name: str
    module: str
    sources: frozenset
    source_class: str
    outbound_class: str
    outbound: dict
    reason_codes: tuple
    max_audience: str
    approval: str
    review: str


APPROVED_TOOLS: dict[str, Approved] = {a.name: a for a in (
    Approved(
        name="lens_compare", module="hub.assistant.tools.lens_compare",
        sources=frozenset({"bound_rx_compare"}), source_class="patient",
        outbound_class="words_flags_ranks", outbound=COMPARE_VIEW, reason_codes=COMPARE_CODES,
        max_audience="mark",
        approval="Mark 3 Nov 2026 (Lens Finder chat; ongoing A$15/month, no end date)",
        review="Codex 2026-11-03-lens-finder-chat.md + -response.md; " + PHASE_1A_REVIEW),
    Approved(
        name="lens_price", module="hub.assistant.tools.lens_price",
        sources=frozenset({"lens_catalogue", "lens_pricing", "cec_prices"}),
        source_class="business", outbound_class="words_flags_ranks", outbound=PRICE_VIEW,
        reason_codes=PRICE_CODES, max_audience="mark",
        approval="Mark 3 Nov 2026: 'full Jarvis style for price list, lens parameters'",
        review=PHASE_1A_REVIEW),
    Approved(
        name="lens_ranges", module="hub.assistant.tools.lens_ranges",
        sources=frozenset({"lens_catalogue"}), source_class="public",
        outbound_class="words_flags", outbound=RANGES_VIEW, reason_codes=RANGES_CODES,
        max_audience="mark",
        approval="Mark 3 Nov 2026: 'full Jarvis style for price list, lens parameters'",
        review=PHASE_1A_REVIEW),
)}

# The modules the Hub loads, in order. A module here whose SPEC isn't
# approved is refused; nothing is discovered by scanning folders.
TOOL_MODULES = ("hub.assistant.tools.lens_compare", "hub.assistant.tools.lens_price",
                "hub.assistant.tools.lens_ranges")


class ToolRefused(Exception):
    pass


def spec_problem(mod, approved: Approved | None) -> str:
    """'' when the module may run as `approved`, else a short reason."""
    spec = getattr(mod, "SPEC", None)
    if not isinstance(spec, dict):
        return "no SPEC"
    if approved is None:
        return "not in APPROVED_TOOLS"
    if spec.get("name") != approved.name or mod.__name__ != approved.module:
        return "name or module differs from the approval"
    if approved.source_class not in SOURCE_CLASSES or spec.get("source_class") not in SOURCE_CLASSES:
        return "unknown source class"
    if approved.outbound_class not in OUTBOUND_CLASSES or spec.get("outbound_class") not in OUTBOUND_CLASSES:
        return "unknown outbound class"
    if spec.get("source_class") != approved.source_class:
        return "source class differs from the approval"
    if spec.get("outbound_class") != approved.outbound_class:
        return "outbound class differs from the approval"
    if not set(spec.get("sources") or ()) <= approved.sources:
        return "reads a source that is not approved"
    if not set(spec.get("reason_codes") or ()) <= set(approved.reason_codes):
        return "uses a reason code that is not approved"
    if not set(approved.reason_codes) <= set(REASON_CODES):
        return "approval names an unknown reason code"
    if spec.get("read_only") is not True:
        return "not read_only"
    if audience_rank(spec.get("audience")) > audience_rank(approved.max_audience) \
            or approved.max_audience not in AUDIENCES:
        return "module audience wider than approved"
    for fn in ("definition", "clean_args", "run", "model_view", "render"):
        if not callable(getattr(mod, fn, None)):
            return f"missing {fn}()"
    return ""


def config_problem(name: str, tool_cfg, approved: Approved) -> str:
    """'' when config lets the tool run, else why not. Config may only switch
    off or narrow."""
    if not isinstance(tool_cfg, dict) or tool_cfg.get("enabled") is not True:
        return "switched off"
    aud = tool_cfg.get("audience", approved.max_audience)
    if aud not in AUDIENCES:
        return "unknown audience in config"
    if audience_rank(aud) > audience_rank(approved.max_audience):
        return "config widens the audience - refused"
    return ""


def _import(module: str):
    return importlib.import_module(module)


def build(config: dict | None, importer=_import, modules=TOOL_MODULES) -> dict:
    """The startup check. Returns {"tools": {name: (module, approved, audience)},
    "refused": {name_or_module: reason}}. Never raises."""
    tools_cfg = ((config or {}).get("tools") or {}) if isinstance(config, dict) else {}
    ok, refused = {}, {}
    for modname in modules:
        try:
            mod = importer(modname)
        except Exception as e:                  # noqa: BLE001
            refused[modname] = f"import failed ({type(e).__name__})"
            continue
        name = (getattr(mod, "SPEC", None) or {}).get("name") or modname
        approved = APPROVED_TOOLS.get(name)
        why = spec_problem(mod, approved)
        if not why:
            why = config_problem(name, tools_cfg.get(name), approved)
        if why:
            refused[name] = why
            continue
        ok[name] = (mod, approved, tools_cfg[name].get("audience", approved.max_audience))
    for name in tools_cfg:
        if name not in APPROVED_TOOLS and name not in refused:
            refused[name] = "not in APPROVED_TOOLS"
    if len(ok) > MAX_TOOLS:
        refused["*"] = "more tools than the design allows without a router review"
        ok = {}
    for name, why in refused.items():
        if why != "switched off":
            log.warning("assistant: tool refused at startup (%s: %s)", name, why)
    return {"tools": ok, "refused": refused, "importer": importer}


def usable(reg: dict, user_audience: str, bound: bool, config: dict | None = None) -> dict:
    """The tools this person may use for this question: passed the startup
    check, still allowed by the CURRENT config (so switching a tool off needs
    no restart), inside its audience. A tool that reads the bound Rx is
    offered only when the page has bound one."""
    tools_cfg = ((config or {}).get("tools") or {}) if isinstance(config, dict) else None
    out = {}
    for name, (mod, approved, aud) in reg["tools"].items():
        if tools_cfg is not None:
            if config_problem(name, tools_cfg.get(name), approved):
                continue
            aud = tools_cfg[name].get("audience", approved.max_audience)
        if audience_rank(user_audience) > audience_rank(aud):
            continue
        if mod.SPEC.get("needs_binding") and not bound:
            continue
        out[name] = (mod, approved, aud)
    return out


def recheck(name: str, reg: dict, config: dict | None, user_audience: str):
    """Every dispatch: re-run the startup checks for this one tool against the
    CURRENT config and module. Returns (module, approved) or raises ToolRefused."""
    approved = APPROVED_TOOLS.get(name)
    if approved is None or name not in reg["tools"]:
        raise ToolRefused("unlisted")
    mod = reg.get("importer", _import)(approved.module)
    why = spec_problem(mod, approved)
    if not why:
        tools_cfg = ((config or {}).get("tools") or {}) if isinstance(config, dict) else {}
        why = config_problem(name, tools_cfg.get(name), approved)
        if not why:
            aud = tools_cfg[name].get("audience", approved.max_audience)
            if audience_rank(user_audience) > audience_rank(aud):
                why = "outside audience"
    if why:
        log.warning("assistant: tool refused at dispatch (%s: %s)", name, why)
        raise ToolRefused(why)
    return mod, approved


def schemas(tools: dict) -> dict:
    return {name: approved.outbound for name, (_m, approved, _a) in tools.items()}


def codes_for(tools: dict) -> list:
    out = []
    for _n, (_m, approved, _a) in tools.items():
        out += [c for c in approved.reason_codes if c not in out]
    return out + [EMPTY_CODE]


# re-exported for tests
check_view = guard.check_view
