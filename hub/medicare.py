"""Medicare — rejected and short-paid claims, and what to do about each.

Mark, 9 Oct 2026: Karen works Tuesdays. First thing she pulls the Medicare
processing and payment reports in Optomate Touch, then opens this page. It reads
the freshly pulled reports LIVE (no waiting for a nightly run), she works the
"Fix now" rows and ticks them; Mark decides "Check with Mark" and accepts
write-offs.

The Hub has no Optomate client by design. Like hub/recent_rx.py it runs a
READ-ONLY command in the agent repo and reads the one JSON line it prints:
    python -m medicare.rejections --json [--fixture]
Ticks go back the same way (the agent owns the state file, in its git-ignored
local-reports folder):
    python -m medicare.rejections --mark KEY --status done|written_off|open --by NAME

Privacy: the agent sends given name + surname initial only. No card number, DOB
or patient ID reaches the Hub. This module logs nothing about any patient and the
endpoint is served no-store. Every $ is the agent's copy of Optomate's figures.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 60
KEY_RE = re.compile(r"^[0-9a-f]{12}$")
STATUSES = ("done", "written_off", "open")
GROUPS = ("fix_now", "check", "write_off", "expired", "waiting", "written_off", "recovered")
ITEM_KEYS = ("key", "service_date", "deadline", "days_left", "patient_label", "item",
             "amount", "code", "meaning", "action_group", "steps", "fact", "claim", "flag")


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
                              timeout=TIMEOUT_S)
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


def clean_item(i: dict) -> dict | None:
    """Copy only whitelisted fields, so nothing new the agent might send can reach
    the screen by accident."""
    out = {k: i.get(k) for k in ITEM_KEYS}
    if not KEY_RE.match(str(out["key"] or "")) or out["action_group"] not in GROUPS:
        return None
    for k in ("service_date", "deadline", "patient_label", "item", "code", "meaning",
              "steps", "fact", "claim", "flag"):
        out[k] = str(out[k] or "")
    out["patient_label"] = out["patient_label"][:20] or "Patient"
    out["amount"] = _money(out["amount"])
    out["days_left"] = _int_or_none(out["days_left"])
    s = i.get("status") if isinstance(i.get("status"), dict) else None
    out["status"] = ({"status": str(s.get("status") or ""), "by": str(s.get("by") or "")[:40],
                      "at": str(s.get("at") or "")[:19]} if s else None)
    return out


def worklist(cfg: dict, fixture: bool = False) -> dict:
    """The page's data. Never raises."""
    data, err = _run(cfg, ["--json"], fixture)
    if err:
        return err
    summary = {}
    for g in GROUPS:
        s = (data.get("summary") or {}).get(g) or {}
        summary[g] = {"n": _int_or_none(s.get("n")) or 0, "amount": _money(s.get("amount"))}
    unrep = data.get("unreported_claims") or {}
    items = [c for c in (clean_item(i) for i in (data.get("items") or []) if isinstance(i, dict)) if c]
    return {
        "connected": True,
        "fixture": bool(data.get("fixture")),
        "generated_at": str(data.get("generated_at") or ""),
        "last_report_pull": str(data.get("last_report_pull") or ""),
        "days_since_pull": _int_or_none(data.get("days_since_pull")),
        "pull_overdue": bool(data.get("pull_overdue")),
        "unreported_claims": {"count": _int_or_none(unrep.get("count")) or 0,
                              "oldest_days": _int_or_none(unrep.get("oldest_days"))},
        "summary": summary,
        "items": items,
    }


def mark(cfg: dict, key: str, status: str, by: str, fixture: bool = False) -> dict:
    """Record a tick. -> {ok} or {ok: False, error}."""
    key = str(key or "").strip().lower()
    status = str(status or "").strip().lower()
    if not KEY_RE.match(key):
        return {"ok": False, "error": "Unknown item."}
    if status not in STATUSES:
        return {"ok": False, "error": "Unknown action."}
    by = re.sub(r"[^\w .'\-]", "", str(by or "")).strip()[:40]
    if not by:
        return {"ok": False, "error": "Pick your name at the top first."}
    data, err = _run(cfg, ["--mark", key, "--status", status, "--by", by], fixture)
    if err:
        return {"ok": False, "error": err.get("error") or err.get("message") or "Couldn't save."}
    return {"ok": True, "status": status}
