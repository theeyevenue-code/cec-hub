"""Jen's frames - frames Jennifer Chea (upstairs) takes from our shelf (#/jen-frames).

Mark, 26 Nov 2026: "Jen scans would be helpful" / "Front desk scanner ideally the one that we use
for job arrivals". Jen scans each frame she takes upstairs with the front desk NETUM scanner; the
frame goes straight onto her list for the month at OUR COST INCL. GST, copied from Optomate
(SPECFRAME_BRANCH.COSTINC at MJ, via the agent's read-only stocktake catalogue - the same cache the
stocktake page uses). The list replaces her Google sheet for shelf frames; frames ordered in on a
supplier invoice are caught separately by the agent's invoice check (inventory/whose.py).

Nothing here changes Optomate (the shelf count stays as it is until someone changes it in Touch).

What it keeps: one JSON file per month in data\\jen-frames\\ (git-ignored): barcode, the frame
description / supplier / cost as Optomate had them at the scan, an optional "for" note (a patient
name or "Staff"), who scanned and when. Removing a line keeps it, marked removed with who and when,
so the month's total can always be traced. No clinical data, no DOB, no Medicare or card numbers.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path

from hub import stocktake

BASE_DIR = Path(__file__).resolve().parent.parent
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
LINE_RE = re.compile(r"^[0-9a-f]{10}$")
MAX_LINES = 2000

_lock = threading.Lock()


def data_dir() -> Path:
    return Path(os.getenv("CEC_HUB_JENFRAMES_DIR", str(BASE_DIR / "data" / "jen-frames")))


def _now() -> datetime:
    return datetime.now()


def _path(month: str) -> Path:
    if not MONTH_RE.match(month or ""):
        raise ValueError("bad month")
    return data_dir() / f"{month}.json"


def _read(month: str) -> dict:
    p = _path(month)
    if not p.is_file():
        return {"month": month, "lines": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _write(doc: dict) -> None:
    p = _path(doc["month"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _money(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def month_view(month: str | None = None) -> dict:
    month = month or _now().strftime("%Y-%m")
    doc = _read(month)
    today = _now().strftime("%Y-%m-%d")
    lines = []
    for ln in doc["lines"]:
        if ln.get("removed"):
            continue
        lines.append({**ln, "can_remove": (ln.get("at") or "")[:10] == today})
    lines.sort(key=lambda x: x.get("at") or "", reverse=True)
    priced = [ln["cost_inc"] for ln in lines if ln.get("cost_inc") is not None]
    months = sorted({p.stem for p in data_dir().glob("*.json") if MONTH_RE.match(p.stem)} | {month},
                    reverse=True)
    return {"ok": True, "month": month, "lines": lines, "count": len(lines),
            "total": round(sum(priced), 2), "unpriced": len(lines) - len(priced), "months": months}


def add(barcode, by, get_cat) -> dict:
    """One scan = one frame on this month's list. A barcode Optomate doesn't know is still kept
    (price blank, shown amber) so nothing Jen scans is lost."""
    bc = stocktake.norm_barcode(barcode)
    if not bc:
        return {"ok": False, "error": "Scan the frame's barcode."}
    cat = get_cat()
    f = stocktake.lookup(cat, bc) if cat.get("frames") else None
    if f is None and cat.get("frames"):          # entered in Touch since the list was read
        cat = get_cat(True)
        f = stocktake.lookup(cat, bc) if cat.get("frames") else None
    now = _now()
    month = now.strftime("%Y-%m")
    line = {"id": secrets.token_hex(5), "at": now.isoformat(timespec="seconds"), "barcode": bc,
            "description": stocktake.clean_text((f or {}).get("d"), 80),
            "supplier": stocktake.clean_text((f or {}).get("s"), 20),
            "cost_inc": _money((f or {}).get("c")) if f else None,
            "found": bool(f), "offline": not cat.get("frames"),
            "for": "", "by": stocktake.clean_text(by, 30)}
    with _lock:
        doc = _read(month)
        if len(doc["lines"]) >= MAX_LINES:
            return {"ok": False, "error": "This month's list is full - tell Mark."}
        before = sum(1 for ln in doc["lines"] if ln.get("barcode") == bc and not ln.get("removed"))
        doc["lines"].append(line)
        _write(doc)
    return {"ok": True, "line": {**line, "can_remove": True}, "same_before": before}


def _edit(line_id: str, fn) -> dict:
    if not LINE_RE.match(line_id or ""):
        return {"ok": False, "error": "That line isn't there any more."}
    month = _now().strftime("%Y-%m")
    with _lock:
        doc = _read(month)
        for ln in doc["lines"]:
            if ln.get("id") == line_id and not ln.get("removed"):
                err = fn(ln)
                if err:
                    return {"ok": False, "error": err}
                _write(doc)
                return {"ok": True, "line": ln}
    return {"ok": False, "error": "That line isn't on this month's list."}


def set_for(line_id, text, by) -> dict:
    """Who the frame is for: a patient's name or "Staff". Optional; any line this month."""
    val = stocktake.clean_text(text, 60)

    def fn(ln):
        ln["for"] = val
        ln["for_by"] = stocktake.clean_text(by, 30)
    return _edit(str(line_id or ""), fn)


def remove(line_id, by) -> dict:
    """A scan made by mistake - same day only. Kept in the file, marked removed."""
    today = _now().strftime("%Y-%m-%d")

    def fn(ln):
        if (ln.get("at") or "")[:10] != today:
            return "Only today's scans can be removed. Tell Mark about older ones."
        ln["removed"] = _now().isoformat(timespec="seconds")
        ln["removed_by"] = stocktake.clean_text(by, 30)
    return _edit(str(line_id or ""), fn)
