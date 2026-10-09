"""Medicare — rejected and short-paid claims, and what to do about each.

Mark, 9 Oct 2026: Karen works Tuesdays. First thing she pulls the Medicare
processing and payment reports in Optomate Touch, then opens this page. It reads
the freshly pulled reports LIVE (no waiting for a nightly run). The page is ONE
"Do now" list for Karen; everything else is folded away with totals.

Mark's item review box: when Medicare rejects a comprehensive consult (160, limit
reached) and that day's exam notes point to another item (10913 / 10914 / 10915),
the agent's local rules put a card in front of Mark — ONLY when the name picked at
the top is Mark. He accepts an item (Karen then sees "change item to ..., resubmit"
in Do now) or says not eligible (written off). Nothing is ever accepted for him.

The Hub has no Optomate client by design. Like hub/recent_rx.py it runs a
READ-ONLY command in the agent repo and reads the one JSON line it prints:
    python -m medicare.rejections --json [--review] [--fixture]
--review (note snippets on the cards) is passed ONLY for Mark. Ticks go back the
same way (the agent owns the state file, in its git-ignored local-reports folder):
    python -m medicare.rejections --mark KEY --status STATUS --by NAME

Privacy: the agent sends given name + surname initial only. No card number, DOB
or patient ID reaches the Hub. Note snippets (Mark only, <= 2 x 140 characters)
pass straight through to his screen: never logged, endpoint served no-store.
Every $ is the agent's copy of Optomate's figures.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 60
KEY_RE = re.compile(r"^[0-9a-f]{12}$")
ACCEPT_ITEMS = ("10913", "10914", "10915")
STAFF_STATUSES = ("done", "written_off", "open")
MARK_STATUSES = ("not_eligible",) + tuple(f"accepted_{i}" for i in ACCEPT_ITEMS)
LANES = ("review", "do_now", "mark", "waiting", "written_off", "expired", "recovered")
ITEM_KEYS = ("key", "service_date", "deadline", "days_left", "patient_label", "item",
             "amount", "code", "meaning", "action_group", "steps", "fact", "claim", "flag",
             "item_change")
CONFIDENCE = ("High", "Medium", "Weak")


def is_mark(name) -> bool:
    """The Hub's name picker (cookie hub_staff) says Mark. Case-insensitive."""
    return str(name or "").strip().lower() == "mark"


def not_connected(msg: str) -> dict:
    return {"connected": False, "message": msg}


def _agent_cfg(cfg: dict) -> dict:
    return (cfg or {}).get("optomate_agent", {}) or {}


def agent_dir(cfg: dict):
    d = _agent_cfg(cfg).get("agent_dir")
    return Path(d) if d else None


def _run(cfg: dict, args: list, fixture: bool):
    """-> (dict | None, error dict | None). Never raises."""
    dpath = agent_dir(cfg)
    if dpath is None:
        return None, not_connected("Optomate isn't connected on this computer.")
    if not (dpath / "medicare" / "rejections.py").is_file():
        return None, not_connected("The Medicare list isn't set up on this computer yet.")
    python = _agent_cfg(cfg).get("python") or sys.executable
    argv = [python, "-m", "medicare.rejections", *args] + (["--fixture"] if fixture else [])
    try:
        proc = subprocess.run(argv, cwd=str(dpath), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, {"connected": True, "error": "Optomate took too long — try again."}
    except OSError:
        return None, not_connected("Couldn't reach Optomate from this computer.")
    line = ""
    for candidate in reversed((proc.stdout or "").splitlines()):
        if candidate.strip():
            line = candidate.strip()
            break
    try:
        data = json.loads(line) if line else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict) or not data:
        return None, {"connected": True, "error": "Couldn't read the Medicare list."}
    if not data.get("ok"):
        return None, {"connected": True,
                      "error": str(data.get("error") or "Couldn't read the Medicare list.")}
    return data, None


def _money(v) -> float:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _s(v, n: int) -> str:
    return str(v or "")[:n]


def clean_review(r, mark_view: bool):
    """Mark: the card's facts and up to 2 snippets per item. Anyone else: confidence only."""
    if not isinstance(r, dict):
        return None
    conf = r.get("confidence") if r.get("confidence") in CONFIDENCE else ""
    if not mark_view:
        return {"confidence": conf}
    sugs = []
    for s in (r.get("suggestions") or [])[:3]:
        if not isinstance(s, dict) or s.get("item") not in ACCEPT_ITEMS:
            continue
        snips = [{"section": _s(x.get("section"), 30), "text": _s(x.get("text"), 140)}
                 for x in (s.get("snippets") or [])[:2] if isinstance(x, dict)]
        sugs.append({"item": s["item"], "name": _s(s.get("name"), 40),
                     "confidence": s.get("confidence") if s.get("confidence") in CONFIDENCE else "",
                     "history_only": bool(s.get("history_only")), "snippets": snips})
    return {"confidence": conf, "why": _s(r.get("why"), 120), "age": _int_or_none(r.get("age")),
            "minutes": _int_or_none(r.get("minutes")), "suggestions": sugs}


def clean_item(i: dict, mark_view: bool = False) -> dict | None:
    """Copy only whitelisted fields, so nothing new the agent might send can reach
    the screen by accident."""
    out = {k: i.get(k) for k in ITEM_KEYS}
    if not KEY_RE.match(str(out["key"] or "")) or out["action_group"] not in LANES:
        return None
    for k in ("service_date", "deadline", "patient_label", "item", "code", "meaning",
              "steps", "fact", "claim", "flag", "item_change"):
        out[k] = str(out[k] or "")
    out["patient_label"] = out["patient_label"][:20] or "Patient"
    if out["item_change"] not in ACCEPT_ITEMS:
        out["item_change"] = ""
    out["amount"] = _money(out["amount"])
    out["days_left"] = _int_or_none(out["days_left"])
    s = i.get("status") if isinstance(i.get("status"), dict) else None
    out["status"] = ({"status": str(s.get("status") or ""), "by": str(s.get("by") or "")[:40],
                      "at": str(s.get("at") or "")[:19]} if s else None)
    out["review"] = clean_review(i.get("review"), mark_view) if out["action_group"] == "review" else None
    return out


def clean_today(rows) -> list:
    out = []
    for t in (rows or [])[:80]:
        if not isinstance(t, dict):
            continue
        hhmm = str(t.get("time") or "")
        out.append({"time": hhmm if re.match(r"^\d{2}:\d{2}$", hhmm) else "",
                    "patient_label": _s(t.get("patient_label"), 20) or "Patient",
                    "lines": [_s(x, 200) for x in (t.get("lines") or [])[:3]]})
    return out


def worklist(cfg: dict, fixture: bool = False, mark_view: bool = False) -> dict:
    """The page's data. Never raises. Review cards (with note snippets) only for Mark;
    everyone else gets their count."""
    data, err = _run(cfg, ["--json"] + (["--review"] if mark_view else []), fixture)
    if err:
        return err
    summary = {}
    for g in LANES:
        s = (data.get("summary") or {}).get(g) or {}
        summary[g] = {"n": _int_or_none(s.get("n")) or 0, "amount": _money(s.get("amount"))}
    unrep = data.get("unreported_claims") or {}
    items = [c for c in (clean_item(i, mark_view) for i in (data.get("items") or [])
                         if isinstance(i, dict)) if c]
    if not mark_view:
        items = [i for i in items if i["action_group"] != "review"]
    return {
        "connected": True,
        "fixture": bool(data.get("fixture")),
        "is_mark": bool(mark_view),
        "generated_at": str(data.get("generated_at") or ""),
        "last_report_pull": str(data.get("last_report_pull") or ""),
        "days_since_pull": _int_or_none(data.get("days_since_pull")),
        "pull_overdue": bool(data.get("pull_overdue")),
        "unreported_claims": {"count": _int_or_none(unrep.get("count")) or 0,
                              "oldest_days": _int_or_none(unrep.get("oldest_days"))},
        "summary": summary,
        "items": items,
        "today": clean_today(data.get("today")),
        "today_error": _s(data.get("today_error"), 80),
    }


def mark(cfg: dict, key: str, status: str, by: str, fixture: bool = False) -> dict:
    """Record a tick. -> {ok} or {ok: False, error}. Accept / not eligible: Mark only."""
    key = str(key or "").strip().lower()
    status = str(status or "").strip().lower()
    if not KEY_RE.match(key):
        return {"ok": False, "error": "Unknown item."}
    if status not in STAFF_STATUSES + MARK_STATUSES:
        return {"ok": False, "error": "Unknown action."}
    by = re.sub(r"[^\w .'\-]", "", str(by or "")).strip()[:40]
    if not by:
        return {"ok": False, "error": "Pick your name at the top first."}
    if status in MARK_STATUSES and not is_mark(by):
        return {"ok": False, "error": "Only Mark can decide the item."}
    data, err = _run(cfg, ["--mark", key, "--status", status, "--by", by], fixture)
    if err:
        return {"ok": False, "error": err.get("error") or err.get("message") or "Couldn't save."}
    return {"ok": True, "status": status}
