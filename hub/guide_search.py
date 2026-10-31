"""Patient search for the Send guide tile, to Mark's standing rule
(CEC-Optomate-Agent CLAUDE.md, "Patient search takes a FULL NAME, either order").

Ported from the Optomate agent's tracker/app.py::_filter_exact/_filter_broad
(and optomate/patient_search.py, the shared copy of the same logic). Kept pure -
no Flask, no Optomate client - so the Hub's tests cover it and the guide bridge
(hub/guide_bridge.py, which runs in the agent folder) imports it.

- Two words = SURNAME + GIVEN, queried as separate fields, either way round,
  and those hits come FIRST; then the wide net (every term in either field), so
  double-barrelled surnames and middle names still turn up.
- Results ordered SURNAME,GIVEN by the query; fetch one more than shown and
  report "more", so a capped list never looks complete.
- Input sanitised to name characters so a stray bracket cannot 400 the filter.
- ' -> '' escape so O'Brien works (agent KNOWN-QUIRKS #3).
"""

from __future__ import annotations

import re

SAFE = re.compile(r"[^A-Za-z0-9 '\-]")
LIMIT = 12


def esc(t: str) -> str:
    return t.replace("'", "''")


def terms(q) -> list[str]:
    """Sanitised search terms; [] when nothing is worth sending (judged on the
    longest term, so 'Smith J' is fine but 'S m' is not)."""
    ts = SAFE.sub(" ", str(q or "")).split()[:4]
    if not ts or max(len(t) for t in ts) < 2:
        return []
    return [t[:40] for t in ts]


def filter_broad(ts: list[str]) -> str:
    """EVERY term must match the surname OR the given name (the wide net)."""
    return " and ".join(
        f"(contains(SURNAME,'{esc(t)}') or contains(GIVEN,'{esc(t)}'))" for t in ts)


def filter_exact(ts: list[str]) -> str | None:
    """Two terms as SURNAME + GIVEN, either way round. None for any other count."""
    if len(ts) != 2:
        return None
    a, b = esc(ts[0]), esc(ts[1])
    return (f"((contains(SURNAME,'{a}') and contains(GIVEN,'{b}')) or "
            f"(contains(SURNAME,'{b}') and contains(GIVEN,'{a}')))")


def search(fetch, q, limit: int = LIMIT) -> tuple[list[dict], bool]:
    """fetch(filter, top, spec) -> rows already ordered SURNAME,GIVEN.

    spec is ("exact", [a, b]) or ("broad", terms): the same question as the
    filter string, for a fixture fetch that cannot run OData. Returns
    (rows[:limit], more). Exact pairs first, then the wide net, no duplicates.
    """
    ts = terms(q)
    if not ts:
        return [], False
    rows, seen = [], set()
    passes = [(filter_exact(ts), ("exact", ts)), (filter_broad(ts), ("broad", ts))]
    for filt, spec in passes:
        if filt is None or len(rows) > limit:
            continue
        for r in fetch(filt, limit + 1, spec) or []:
            rid = r.get("ID")
            if rid is not None and rid not in seen:
                seen.add(rid)
                rows.append(r)
    return rows[:limit], len(rows) > limit


def fixture_match(rows: list[dict], spec) -> list[dict]:
    """The same two questions answered in Python over a list of fixture rows,
    ordered SURNAME,GIVEN (only ever used with fake ZZTEST patients)."""
    kind, ts = spec
    low = [t.casefold() for t in ts]

    def has(field, t):
        return t in str(field or "").casefold()

    out = []
    for r in rows:
        s, g = r.get("SURNAME"), r.get("GIVEN")
        if kind == "exact":
            a, b = low
            ok = (has(s, a) and has(g, b)) or (has(s, b) and has(g, a))
        else:
            ok = all(has(s, t) or has(g, t) for t in low)
        if ok:
            out.append(r)
    return sorted(out, key=lambda r: (str(r.get("SURNAME") or "").casefold(),
                                      str(r.get("GIVEN") or "").casefold()))
