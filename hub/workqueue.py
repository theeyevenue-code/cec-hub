"""Work queue: one list of manual jobs ("To do") and the questions waiting on Mark.

Why it exists (Mark, 5 Nov 2026): manual jobs were scattered across MANUAL-TASKS.md, nightly
emails, scan backlogs and letters, and approvals bounced between Claude sessions. This is the
single answer to "what do I need to do today?".

Storage: ONE JSON file, git-ignored (`data\\workqueue.json` under the Hub folder, or the path in
the CEC_HUB_WORKQUEUE environment variable). Items can name patients, so the file must never
reach git: `data/` is in .gitignore and nothing here logs item text.

Two kinds of item share the file:
  kind "todo" - a job. Fields: owner, first_seen, last_seen, source, status open/done.
  kind "ask"  - a question for Mark. Adds recommendation, origin (session or branch) and, once
                answered, answer ("yes" / "not_now"), answered_by, answered_at.

Rules that matter:
  * Same `key` never makes a second item. A repeat updates last_seen / seen_count (and the text
    and detail, so a nightly line stays current). A DONE item that comes back on a LATER day is
    reopened and marked "came back" (the problem was not fixed); the same day it is left done.
  * Recording an answer ONLY records it. Nothing in this module (or the routes that call it)
    sends, merges, runs a command or touches Optomate. The Claude session that asked reads the
    answer and acts on its own.
  * Every write is: take the file lock, read, change, write a temp file, atomic replace. Two
    people clicking at once, or a nightly job plus a click, can't lose each other's changes. The
    lock is an OS file lock, so a crashed process can never leave it stuck.
"""

import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

HUB_DATA = Path(__file__).resolve().parent.parent / "data"

OWNERS = ["Karen", "Angie", "Mark", "Claude", "Unassigned"]
KINDS = ("todo", "ask")
STALE_DAYS = 7                      # an open job older than this shows amber
MAX_TEXT, MAX_DETAIL, MAX_SHORT = 300, 600, 120
LOCK_WAIT_S = 10.0
DATE_UNKNOWN = None                 # stored as null, shown as "date unknown"

_KEY_OK = re.compile(r"^[A-Za-z0-9:_./#@ -]{1,120}$")
_thread_lock = threading.Lock()


class WorkQueueError(Exception):
    """A refusal that is safe to show staff in plain words."""


def data_path() -> Path:
    env = os.environ.get("CEC_HUB_WORKQUEUE", "").strip()
    return Path(env) if env else HUB_DATA / "workqueue.json"


# --- file lock + atomic write -------------------------------------------------

@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.with_name(path.name + ".lock")
    if not _thread_lock.acquire(timeout=LOCK_WAIT_S):
        raise WorkQueueError("The list is busy. Try again in a moment.")
    fh = open(lock_file, "a+b")
    try:
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                _os_lock(fh)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise WorkQueueError("The list is busy. Try again in a moment.")
                time.sleep(0.02)
        try:
            yield
        finally:
            _os_unlock(fh)
    finally:
        fh.close()
        _thread_lock.release()


try:                                # Windows (the practice server)
    import msvcrt

    def _os_lock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _os_unlock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
except ImportError:                 # anywhere else (CI)
    import fcntl

    def _os_lock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _os_unlock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _read(path: Path) -> dict:
    """The stored data. Missing file = empty. A file that exists but can't be parsed is NEVER
    overwritten: raise, so a bad write elsewhere can't wipe the list."""
    for attempt in range(5):
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"items": []}
        except OSError:
            time.sleep(0.02)        # a replace is in flight on Windows
            continue
        if not raw.strip():
            return {"items": []}
        try:
            data = json.loads(raw)
        except ValueError:
            raise WorkQueueError("The work list file is damaged. Ask Mark - nothing was changed.")
        if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
            raise WorkQueueError("The work list file is damaged. Ask Mark - nothing was changed.")
        data.setdefault("items", [])
        return data
    raise WorkQueueError("The work list could not be read. Try again in a moment.")


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # a reader has it open for a moment (Windows)
                time.sleep(0.03)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _mutate(fn):
    """Run fn(data) under the lock; save only if it returns without raising."""
    path = data_path()
    with _locked(path):
        data = _read(path)
        result = fn(data)
        _write(path, data)
        return result


# --- cleaning -----------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _clip(value, limit) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean_key(key) -> str:
    key = str(key or "").strip()
    if not _KEY_OK.match(key):
        raise WorkQueueError("The source key must be 1-120 plain characters (letters, digits, : _ . / # @ - space).")
    return key


def clean_owner(owner) -> str:
    owner = str(owner or "").strip()
    if not owner:
        return "Unassigned"
    for o in OWNERS:
        if o.lower() == owner.lower():
            return o
    raise WorkQueueError(f"Owner must be one of: {', '.join(OWNERS)}.")


def clean_date(value):
    """ISO date or None ('date unknown'). Never invents one."""
    s = str(value or "").strip()[:10]
    if not s:
        return DATE_UNKNOWN
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        raise WorkQueueError("Dates must look like 2026-11-05.")


def _same_text(a, b) -> bool:
    return " ".join(str(a).lower().split()) == " ".join(str(b).lower().split())


# --- the one entry point scheduled jobs, sessions and the page all use -----------

def add_item(*, key=None, text, owner="Unassigned", kind="todo", source="", first_seen=None,
             detail="", recommendation="", origin="", added_by=""):
    """Add an item, or update the existing one with the same key.

    Returns {"result": "added"|"updated"|"reopened", "item": {...}}.
    No key (a person typing into the page): a made-up unique key, but an identical open job is
    reported as "updated" rather than duplicated (a double click must not make two).
    """
    text = _clip(text, MAX_TEXT)
    if not text:
        raise WorkQueueError("Say what needs doing.")
    if kind not in KINDS:
        raise WorkQueueError("Kind must be todo or ask.")
    owner = "Mark" if kind == "ask" else clean_owner(owner)
    first = clean_date(first_seen)
    key = clean_key(key) if key else None
    source = _clip(source, MAX_SHORT) or ("Added by hand" if not key else "")
    detail = _clip(detail, MAX_DETAIL)
    recommendation = _clip(recommendation, MAX_DETAIL)
    origin = _clip(origin, MAX_SHORT)
    now, today = _now(), _today()

    def go(data):
        items = data["items"]
        found = None
        if key:
            found = next((i for i in items if i.get("key") == key), None)
        else:
            found = next((i for i in items if i.get("status") == "open" and i.get("kind") == kind
                          and str(i.get("key", "")).startswith("manual:")
                          and i.get("owner") == owner and _same_text(i.get("text", ""), text)), None)
        if found:
            found["last_seen"] = now
            found["seen_count"] = int(found.get("seen_count", 1)) + 1
            if found.get("status") in ("open",):
                # keep the item current (a nightly line's numbers change); never touch who/when
                found["text"] = text
                if detail:
                    found["detail"] = detail
                if kind == "ask":
                    if recommendation:
                        found["recommendation"] = recommendation
                    if origin:
                        found["origin"] = origin
                if source:
                    found["source"] = source
                return {"result": "updated", "item": dict(found)}
            if found.get("status") == "done" and str(found.get("done_at", ""))[:10] < today:
                found.update(status="open", came_back=int(found.get("came_back", 0)) + 1,
                             reopened_at=now, text=text)
                found["done_by"] = found["done_at"] = None
                if detail:
                    found["detail"] = detail
                return {"result": "reopened", "item": dict(found)}
            return {"result": "updated", "item": dict(found)}   # done today, or an answered ask
        item = {
            "id": uuid.uuid4().hex[:12],
            "key": key or f"manual:{uuid.uuid4().hex[:12]}",
            "kind": kind, "text": text, "detail": detail, "owner": owner,
            "source": source, "first_seen": first or today,   # added now = first seen now
            "added_at": now, "added_by": _clip(added_by, 60),
            "last_seen": now, "seen_count": 1, "status": "open",
            "done_by": None, "done_at": None, "came_back": 0,
        }
        if kind == "ask":
            item.update(recommendation=recommendation, origin=origin,
                        answer=None, answered_by=None, answered_at=None)
        items.append(item)
        return {"result": "added", "item": dict(item)}

    return _mutate(go)


def import_item(entry: dict) -> dict:
    """Add one reviewed importer entry. Unlike add_item, an unknown date STAYS unknown."""
    key = clean_key(entry.get("key"))
    text = _clip(entry.get("text"), MAX_TEXT)
    if not text:
        raise WorkQueueError("Say what needs doing.")
    owner = clean_owner(entry.get("owner"))
    first = clean_date(entry.get("first_seen"))
    now = _now()

    def go(data):
        found = next((i for i in data["items"] if i.get("key") == key), None)
        if found:
            found["last_seen"] = now
            found["seen_count"] = int(found.get("seen_count", 1)) + 1
            return {"result": "updated", "item": dict(found)}
        item = {"id": uuid.uuid4().hex[:12], "key": key, "kind": "todo", "text": text,
                "detail": _clip(entry.get("detail"), MAX_DETAIL), "owner": owner,
                "source": _clip(entry.get("source"), MAX_SHORT), "first_seen": first,
                "added_at": now, "added_by": "importer", "last_seen": now, "seen_count": 1,
                "status": "open", "done_by": None, "done_at": None, "came_back": 0}
        data["items"].append(item)
        return {"result": "added", "item": dict(item)}

    return _mutate(go)


# --- actions from the page ------------------------------------------------------

def _find(data, item_id):
    item = next((i for i in data["items"] if i.get("id") == str(item_id)), None)
    if item is None:
        raise WorkQueueError("That item isn't on the list any more.")
    return item


def mark_done(item_id, who):
    def go(data):
        item = _find(data, item_id)
        if item.get("kind") == "ask":
            raise WorkQueueError("Questions for Mark are answered with Yes or Not now, not ticked off.")
        if item.get("status") == "done":
            return dict(item)       # a second click from someone else: keep the FIRST who/when
        item.update(status="done", done_by=_clip(who, 60) or "someone (no name picked)",
                    done_at=_now())
        return dict(item)
    return _mutate(go)


def reopen(item_id, who):
    def go(data):
        item = _find(data, item_id)
        if item.get("kind") == "ask":
            raise WorkQueueError("Use Undo on the answered question instead.")
        if item.get("status") == "done":
            item.update(status="open", done_by=None, done_at=None, reopened_by=_clip(who, 60))
        return dict(item)
    return _mutate(go)


def record_answer(item_id, answer, who):
    """Record Mark's answer on a question. THIS AND NOTHING ELSE: no send, no merge, no command."""
    if answer not in ("yes", "not_now"):
        raise WorkQueueError("The answer must be Yes or Not now.")

    def go(data):
        item = _find(data, item_id)
        if item.get("kind") != "ask":
            raise WorkQueueError("That isn't a question for Mark.")
        if item.get("status") == "answered":
            raise WorkQueueError(
                f"Already answered ({_answer_label(item.get('answer'))}) by "
                f"{item.get('answered_by') or 'someone'}. Use Undo first to change it.")
        item.update(status="answered", answer=answer, answered_by=_clip(who, 60),
                    answered_at=_now())
        return dict(item)
    return _mutate(go)


def undo_answer(item_id, who):
    def go(data):
        item = _find(data, item_id)
        if item.get("kind") == "ask" and item.get("status") == "answered":
            item.update(status="open", answer=None, answered_by=None, answered_at=None,
                        undone_by=_clip(who, 60))
        return dict(item)
    return _mutate(go)


def resolve_by_key(key, who="job"):
    """A scheduled job saying 'that problem has cleared'. Marks the open item done."""
    key = clean_key(key)

    def go(data):
        item = next((i for i in data["items"] if i.get("key") == key), None)
        if item is None:
            return {"result": "not_found"}
        if item.get("status") == "open" and item.get("kind") == "todo":
            item.update(status="done", done_by=_clip(who, 60) or "job", done_at=_now())
            return {"result": "resolved", "item": dict(item)}
        return {"result": "unchanged", "item": dict(item)}
    return _mutate(go)


def get_by_key(key):
    key = clean_key(key)
    item = next((i for i in _read(data_path())["items"] if i.get("key") == key), None)
    return dict(item) if item else None


# --- what the page shows ------------------------------------------------------------

def _answer_label(a):
    return {"yes": "Yes", "not_now": "Not now"}.get(a, "")


def _age_days(item, today: date):
    fs = item.get("first_seen")
    if not fs:
        return None
    try:
        return max(0, (today - date.fromisoformat(str(fs)[:10])).days)
    except ValueError:
        return None


def view(today: date = None) -> dict:
    """Everything the page needs, already sorted: oldest first, unknown dates after dated ones."""
    today = today or date.today()
    items = _read(data_path())["items"]
    out = []
    for i in items:
        row = dict(i)
        row["age_days"] = _age_days(i, today)
        row["stale"] = row["age_days"] is not None and row["age_days"] >= STALE_DAYS
        out.append(row)

    def oldest_first(r):
        return (r["age_days"] is None, -(r["age_days"] or 0), r.get("added_at", ""))

    todos = sorted([r for r in out if r["kind"] == "todo" and r["status"] == "open"], key=oldest_first)
    dated = [r for r in todos if r["age_days"] is not None]
    if dated:
        oldest_age = dated[0]["age_days"]
        for r in dated:
            r["oldest"] = r["age_days"] == oldest_age and oldest_age > 0
    done = sorted([r for r in out if r["kind"] == "todo" and r["status"] == "done"],
                  key=lambda r: r.get("done_at") or "", reverse=True)[:30]
    asks = sorted([r for r in out if r["kind"] == "ask" and r["status"] == "open"], key=oldest_first)
    answered = sorted([r for r in out if r["kind"] == "ask" and r["status"] == "answered"],
                      key=lambda r: r.get("answered_at") or "", reverse=True)[:30]
    counts = {o: sum(1 for r in todos if r["owner"] == o) for o in OWNERS}
    return {"today": today.isoformat(), "owners": OWNERS, "stale_days": STALE_DAYS,
            "todos": todos, "done": done, "asks": asks, "answered": answered,
            "counts": counts, "total_open": len(todos), "waiting_on_mark": len(asks)}
