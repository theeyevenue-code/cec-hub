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
from datetime import date
from pathlib import Path

HUB_DATA = Path(__file__).resolve().parent.parent / "data"


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


# --- to-do list ---------------------------------------------------------------

def tasks_path() -> Path:
    return HUB_DATA / "tasks.json"


def tasks_list() -> list:
    # open tasks first, newest first inside each group
    items = _load(tasks_path(), "tasks")
    return sorted(items, key=lambda t: (bool(t.get("done")), str(t.get("added", ""))),
                  reverse=False) if items else []


def tasks_mutate(action: str, payload: dict, who: str):
    items = _load(tasks_path(), "tasks")
    if action == "add":
        text = str(payload.get("text") or "").strip()[:200]
        if not text:
            return False, "Nothing to add — type the task first."
        items.insert(0, {"id": _new_id("t"), "text": text, "by": who,
                         "added": date.today().isoformat(), "done": False})
    else:
        t = next((x for x in items if x.get("id") == payload.get("id")), None)
        if t is None:
            return False, "That task isn't there any more — refresh the page."
        if action == "toggle":
            t["done"] = not t.get("done")
            t["done_by"] = who if t["done"] else None
            t["done_date"] = date.today().isoformat() if t["done"] else None
        elif action == "edit":
            text = str(payload.get("text") or "").strip()[:200]
            if not text:
                return False, "The task text can't be empty."
            t["text"] = text
        elif action == "delete":
            items = [x for x in items if x.get("id") != payload.get("id")]
        else:
            return False, "Unknown action."
    _save(tasks_path(), "tasks", items)
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


def credits_list(cfg: dict) -> list:
    order = {"possible": 0, "open": 1, "arrived": 2, "done": 3}
    items = _load(credits_path(cfg), "credits")
    return sorted(items, key=lambda c: (order.get(c.get("status"), 1),
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
