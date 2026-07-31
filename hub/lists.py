"""Shared staff lists: the to-do list and the credits watch-list.

Storage is two small JSON files:
  * tasks   -> <hub>\\data\\tasks.json          (Hub-only feature)
  * credits -> <agent logs dir>\\credits-watch.json  (the agent's nightly
               credit_watch.py reads the same file and marks entries when a
               matching supplier credit actually lands in Optomate)

Both are read-modify-write on every call; volumes are tiny (a practice's
to-do list, not a database). Every mutation records who did it (the Hub's
who-is-this picker) and when, so the lists are self-explanatory.
"""

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

HUB_DATA = Path(__file__).resolve().parent.parent / "data"

# A credit still waiting on the supplier this many days is "overdue — chase it".
# Soft, not an alarm: supplier statements are monthly, so ~4 weeks = one cycle
# gone by with nothing back. (Mark, 23 Jul 2026.)
CREDIT_OVERDUE_DAYS = 28


def _load(path: Path, key: str) -> list:
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get(key) or [])
    except (OSError, ValueError):
        return []


def _save(path: Path, key: str, items: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: items}, indent=1, ensure_ascii=False),
                    encoding="utf-8")


def _new_id(prefix: str) -> str:
    return f"{prefix}{int(time.time() * 1000)}"


# --- tile layout (order + hidden), editable from the Hub itself ----------------

def layout_path() -> Path:
    return HUB_DATA / "tile-layout.json"


def layout_get() -> dict:
    try:
        d = json.loads(layout_path().read_text(encoding="utf-8"))
        return {"order": list(d.get("order") or []), "hidden": list(d.get("hidden") or [])}
    except (OSError, ValueError):
        return {"order": [], "hidden": []}


def layout_save(order, hidden) -> None:
    layout_path().parent.mkdir(parents=True, exist_ok=True)
    layout_path().write_text(json.dumps(
        {"order": [str(x) for x in (order or [])][:50],
         "hidden": [str(x) for x in (hidden or [])][:50]}, indent=1),
        encoding="utf-8")


def apply_layout(tiles: list) -> list:
    """Order tiles by the saved layout (unknown ids keep config order at the
    end) and drop hidden ones. Editable from the Hub's 'Edit layout' mode."""
    lay = layout_get()
    hidden = set(lay["hidden"])
    pos = {tid: i for i, tid in enumerate(lay["order"])}
    keep = [t for t in tiles if t.get("id") not in hidden]
    return sorted(keep, key=lambda t: (pos.get(t.get("id"), 999),))


# --- to-do list (smart: due dates + recurrence + roll-forward) -----------------
#
# Storage stays a small JSON file (`data\tasks.json`, gitignored) with two lists:
#   "tasks"  -> individual to-do items (one-offs AND generated recurrence
#               occurrences). Record: {id,text,by,added,done,done_by,done_date,
#               due(ISO|null), series_id(str|null)}.
#   "series" -> the recurrence RULES (templates), kept separate from occurrences
#               as the Codex review asked. Record: {id,text,by,repeat,next_due,
#               active,added}.
#
# Recurrence model: ONE open occurrence per series at a time ("one rolling
# obligation"), so a daily task missed for a week is a single overdue item, not
# seven. When an occurrence is ticked, the series' next_due jumps to the next
# scheduled date in the FUTURE; the next occurrence is generated lazily on read.
# Dates are practice-local (the Hub runs on the Sydney practice PC).

REPEAT_KINDS = {"daily", "weekdays", "weekly", "every"}


def tasks_path() -> Path:
    return HUB_DATA / "tasks.json"


def _parse_due(value):
    """Accept an ISO 'YYYY-MM-DD' due date, else None."""
    s = str(value or "").strip()[:10]
    if not s:
        return None
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def _repeat_from_payload(value):
    """Validate a repeat rule dict, else None (a one-off task)."""
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "").strip().lower()
    if kind not in REPEAT_KINDS:
        return None
    rule = {"kind": kind}
    if kind == "every":
        try:
            n = int(value.get("n"))
        except (TypeError, ValueError):
            return None
        if not 1 <= n <= 365:
            return None
        rule["n"] = n
    return rule


def _advance(repeat: dict, after: date) -> date:
    """The next scheduled date STRICTLY AFTER `after` for this rule."""
    kind = repeat.get("kind")
    if kind == "daily":
        return after + timedelta(days=1)
    if kind == "every":
        return after + timedelta(days=int(repeat.get("n") or 1))
    if kind == "weekly":
        return after + timedelta(days=7)
    if kind == "weekdays":
        nxt = after + timedelta(days=1)
        while nxt.weekday() >= 5:            # Sat=5, Sun=6
            nxt += timedelta(days=1)
        return nxt
    return after + timedelta(days=1)


def _occurrence_id(series_id: str, due: str) -> str:
    return f"{series_id}#{due}"


def _materialize(items: list, series: list, today: date) -> bool:
    """Ensure each active series has its one current open occurrence once its
    next_due has arrived. Idempotent (keyed by series#due). Returns True if it
    changed anything."""
    changed = False
    open_by_series = {t.get("series_id") for t in items
                      if t.get("series_id") and not t.get("done")}
    have_ids = {t.get("id") for t in items}
    for s in series:
        if not s.get("active"):
            continue
        sid, due = s.get("id"), s.get("next_due")
        if not sid or not due or sid in open_by_series:
            continue
        try:
            due_d = date.fromisoformat(due)
        except (TypeError, ValueError):
            continue
        if due_d > today:                    # not due yet — generate later
            continue
        oid = _occurrence_id(sid, due)
        if oid in have_ids:                  # already generated (belt & braces)
            continue
        items.insert(0, {"id": oid, "text": s.get("text"), "by": s.get("by"),
                         "added": today.isoformat(), "done": False,
                         "due": due, "series_id": sid})
        changed = True
    return changed


def _annotate_task(t: dict, today: date) -> dict:
    """Add a derived `overdue` flag for the UI (does not touch storage)."""
    out = dict(t)
    due = t.get("due")
    out["overdue"] = bool(due and not t.get("done") and due < today.isoformat())
    return out


def tasks_list() -> list:
    today = date.today()
    items = _load(tasks_path(), "tasks")
    series = _load(tasks_path(), "series")
    if _materialize(items, series, today):
        _save_tasks(items, series)
    # open first; within open, soonest due first (overdue floats up), then newest
    def key(t):
        done = bool(t.get("done"))
        due = t.get("due") or "9999-99-99"
        return (done, due, _neg_added(t))
    ordered = sorted(items, key=key)
    series_by_id = {s.get("id"): s for s in series}

    def shape(t):
        out = _annotate_task(t, today)
        s = series_by_id.get(t.get("series_id"))
        if s:
            out["repeat"] = s.get("repeat")     # let the UI show a 🔁 label
        return out
    return [shape(t) for t in ordered]


def _neg_added(t: dict):
    # newest-first tiebreak within the same due bucket
    return tuple(-c for c in bytes(str(t.get("added", "")), "utf-8"))


def _save_tasks(items: list, series: list) -> None:
    tasks_path().parent.mkdir(parents=True, exist_ok=True)
    tasks_path().write_text(
        json.dumps({"tasks": items, "series": series}, indent=1, ensure_ascii=False),
        encoding="utf-8")


def tasks_mutate(action: str, payload: dict, who: str):
    items = _load(tasks_path(), "tasks")
    series = _load(tasks_path(), "series")
    today = date.today()

    if action == "add":
        text = str(payload.get("text") or "").strip()[:200]
        if not text:
            return False, "Nothing to add — type the task first."
        due = _parse_due(payload.get("due"))
        repeat = _repeat_from_payload(payload.get("repeat"))
        if repeat:
            sid = _new_id("s")
            series.insert(0, {"id": sid, "text": text, "by": who,
                              "repeat": repeat, "next_due": due or today.isoformat(),
                              "active": True, "added": today.isoformat()})
            _materialize(items, series, today)   # create the first occurrence now
        else:
            items.insert(0, {"id": _new_id("t"), "text": text, "by": who,
                             "added": today.isoformat(), "done": False,
                             "due": due, "series_id": None})
    else:
        t = next((x for x in items if x.get("id") == payload.get("id")), None)
        if t is None:
            return False, "That task isn't there any more — refresh the page."
        sid = t.get("series_id")
        s = next((x for x in series if x.get("id") == sid), None) if sid else None
        if action == "toggle":
            t["done"] = not t.get("done")
            t["done_by"] = who if t["done"] else None
            t["done_date"] = today.isoformat() if t["done"] else None
            if s:
                if t["done"]:
                    base = t.get("due") or today.isoformat()
                    anchor = max(date.fromisoformat(base), today)
                    s["next_due"] = _advance(s["repeat"], anchor).isoformat()
                else:                              # un-tick: make it current again
                    s["next_due"] = t.get("due") or s.get("next_due")
        elif action == "edit":
            text = str(payload.get("text") or "").strip()[:200]
            if not text:
                return False, "The task text can't be empty."
            t["text"] = text
            if "due" in payload:
                t["due"] = _parse_due(payload.get("due"))
            if s:
                s["text"] = text
        elif action == "delete":
            items = [x for x in items if x.get("id") != payload.get("id")]
            if s:                                  # stop a series regenerating
                s["active"] = False
        else:
            return False, "Unknown action."
    _save_tasks(items, series)
    return True, ""


# --- credits watch-list -------------------------------------------------------

# supplier choices staff can pick from (identifier -> label shown)
CREDIT_SUPPLIERS = {
    "HOY": "Hoya", "COOPER": "CooperVision", "MEN": "Menicon", "ALC": "Alcon",
    "SAFILO": "Safilo", "DERIGO": "De Rigo", "MARCH": "Marchon",
    "MAUIJIM": "Maui Jim", "VMD": "VMD", "LUX": "Luxottica", "EYECU": "Eye CU",
    "L4E": "Little 4 Eyes", "AVIV": "Aviva Mann", "BOLLE": "Bolle", "OTHER": "Other",
}


def credits_path(cfg: dict) -> Path:
    agent = cfg.get("optomate_agent", {}) or {}
    dir_str = agent.get("logs_dir", "")
    return (Path(dir_str) if dir_str else HUB_DATA) / "credits-watch.json"


def _parse_amount(raw) -> float | None:
    """Turn whatever a human typed for a dollar amount into a number, or None.
    Accepts '12.50', '$1,234.50', 12.5, '' -> None. Never raises."""
    if raw is None:
        return None
    s = re.sub(r"[^0-9.]", "", str(raw))
    if not s:
        return None
    try:
        val = round(float(s), 2)
    except ValueError:
        return None
    return val if val > 0 else None


def _days_since(iso: str) -> int | None:
    """Whole days between an ISO date string and today. None if unparseable."""
    try:
        return (date.today() - date.fromisoformat(str(iso)[:10])).days
    except (ValueError, TypeError):
        return None


def _annotate(c: dict) -> dict:
    """Add read-time ageing fields (not persisted): how long it's been waiting
    and whether that's overdue. Only entries still waiting on the supplier
    (open / possible) can be overdue — 'arrived' just needs a tick-off."""
    days = _days_since(c.get("added", ""))
    c["days_open"] = days
    c["overdue"] = bool(
        days is not None
        and days >= CREDIT_OVERDUE_DAYS
        and c.get("status") in ("open", "possible")
    )
    return c


def credits_list(cfg: dict) -> list:
    order = {"possible": 0, "open": 1, "arrived": 2, "done": 3}
    items = [_annotate(c) for c in _load(credits_path(cfg), "credits")]
    # Overdue first (chase these), then by the existing status order, oldest first.
    return sorted(items, key=lambda c: (0 if c.get("overdue") else 1,
                                        order.get(c.get("status"), 1),
                                        str(c.get("added", ""))))


def credits_mutate(cfg: dict, action: str, payload: dict, who: str):
    path = credits_path(cfg)
    items = _load(path, "credits")
    if action == "add":
        text = str(payload.get("text") or "").strip()[:200]
        supplier = str(payload.get("supplier") or "OTHER").upper()
        if not text:
            return False, "Say who/what the credit is for first."
        if supplier not in CREDIT_SUPPLIERS:
            supplier = "OTHER"
        items.insert(0, {"id": _new_id("c"), "text": text, "supplier": supplier,
                         "amount": _parse_amount(payload.get("amount")),
                         "by": who, "added": date.today().isoformat(),
                         "status": "open", "matched": None})
    else:
        c = next((x for x in items if x.get("id") == payload.get("id")), None)
        if c is None:
            return False, "That entry isn't there any more — refresh the page."
        if action == "done":            # human confirms it's settled
            c["status"] = "done"
            c["done_by"] = who
            c["done_date"] = date.today().isoformat()
        elif action == "reopen":
            c["status"] = "open"
            c["matched"] = None
        elif action == "delete":
            items = [x for x in items if x.get("id") != payload.get("id")]
        else:
            return False, "Unknown action."
    _save(path, "credits", items)
    return True, ""
