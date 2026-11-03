"""Read adapters - the ONLY things an assistant tool can reach (Codex
2026-11-03-hub-assistant #3; accepted response ACCEPT 3).

The route builds one ReadContext per question from data it has already read
(the lens files, the price config, and - for the Lens Finder box - the
comparison engine bound to the patient or typed Rx the PAGE picked). A tool
gets ctx.view(approved sources): asking for anything else raises. There is no
HTTP client, SQL, shell, file path, Optomate config or credential in here, and
no write route: the assistant cannot mark, tick, send, draft or save anything.
The core itself writes only its own spend ledger.
"""

from __future__ import annotations

import copy
import re
from types import MappingProxyType

from hub import lenses

SOURCES = ("lens_catalogue", "lens_pricing", "cec_prices", "bound_rx_compare")


class SourceRefused(Exception):
    pass


class _View:
    __slots__ = ("_allowed", "_data")

    def __init__(self, data: dict, allowed: frozenset):
        object.__setattr__(self, "_allowed", frozenset(allowed))
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name):
        if name not in self._allowed or name not in SOURCES:
            raise SourceRefused(name)
        v = self._data.get(name)
        if v is None:
            raise SourceRefused(name)
        return v

    def __setattr__(self, name, value):
        raise SourceRefused("read only")


class ReadContext:
    """Built by the route. rows: catalogue rows; pricing / cec_prices: config
    dicts; engine: filters -> compare() result for the bound Rx, or None."""

    def __init__(self, rows=None, pricing=None, cec_prices=None, engine=None, base_filters=None):
        self._data = {
            "lens_catalogue": tuple(MappingProxyType(dict(r)) for r in (rows or [])),
            "lens_pricing": MappingProxyType(copy.deepcopy(dict(pricing or {}))),
            "cec_prices": MappingProxyType(copy.deepcopy(dict(cec_prices or {}))),
            "bound_rx_compare": (_Bound(engine, base_filters) if engine else None),
        }
        self.bound = engine is not None
        self.vocab = lens_vocab(rows or [])

    def view(self, sources) -> _View:
        return _View(self._data, frozenset(sources))


class _Bound:
    """The comparison for the Rx the page picked. The model can change only
    the filter words; the patient / Rx is fixed here."""
    __slots__ = ("_engine", "base_filters")

    def __init__(self, engine, base_filters):
        self._engine = engine
        self.base_filters = MappingProxyType(dict(base_filters or {}))

    def run(self, filters: dict):
        return self._engine(dict(filters))


def short_name(name) -> str:
    return re.sub(r"\s+1\.\d\d$", "", str(name or "")).strip()


def lens_vocab(rows) -> frozenset:
    """Every lens-file string a tool may send as a word: brands, suppliers,
    product names (with and without the index suffix), categories,
    materials, coatings. guard.check_view accepts a 'lens_word' only from here."""
    out = {"Clear"}
    for r in rows:
        for k in ("brand", "supplier", "material", "coating"):
            v = r.get(k)
            if v:
                out.add(str(v))
        out.add(str(r.get("name") or ""))
        out.add(short_name(r.get("name")))
        out.add(lenses.normalise_category(r.get("category", "")))
    out.discard("")
    return frozenset(out)
