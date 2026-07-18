"""Readers for the other CEC systems (review bot, Optomate agent).

Everything in here degrades gracefully: if a path in config\\integrations.json
doesn't exist on this machine, the page says "not connected" in plain words —
it never errors. The Hub only READS these files; the one write it ever does
is renaming a stock proposal CSV to *.approved.csv.
"""

import csv
import json
import re
from datetime import datetime, date, timedelta
from pathlib import Path

MAX_CSV_ROWS = 200

NOT_CONNECTED = (
    "Not connected on this computer. Nothing is wrong — this part runs on a "
    "different machine. Ask Mark if you need these numbers."
)

FINISHED_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Review bot finished\. "
    r"Sent: (\d+), Skipped: (\d+), Errors: (\d+)"
)

# The helper sends every evening, so two quiet days means something is wrong.
REVIEW_STALE_DAYS = 2


def _reviews_alert(bot_enabled, last_run, last_run_date, last_errors, today):
    """Plain-words warning when the review helper needs a human, else "".

    This page used to ask staff to notice the number was "stuck on zero for a
    couple of weeks" — nobody ever notices a slow-moving number, and reviews
    quietly stopping costs real growth. So the page works it out itself."""
    if bot_enabled is False:
        return ("The review helper is switched OFF, so no review texts are "
                "going out. Tell Mark if that isn't on purpose.")
    if last_run_date is None:
        return ("The review helper hasn't recorded a run yet. Tell Mark if it "
                "stays like this.")
    days = (today - last_run_date).days
    if days >= REVIEW_STALE_DAYS:
        return (f"The review helper hasn't run since {last_run} — it normally "
                f"runs every evening. Tell Mark.")
    if last_errors:
        s = "" if last_errors == 1 else "s"
        return (f"The last run hit {last_errors} error{s} — some patients may "
                f"not have been texted. Tell Mark.")
    return ""


def load_integrations(path: Path) -> dict:
    """Read config\\integrations.json. Missing or broken file -> {}."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _read_text(path_str: str):
    """Read a text file if it exists, else None. Never raises."""
    if not path_str:
        return None
    try:
        p = Path(path_str)
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return None


def _mtime_display(path_str: str):
    """'DD/MM/YYYY H:MM am/pm' for a file's last change, or None."""
    try:
        ts = Path(path_str).stat().st_mtime
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%d/%m/%Y %I:%M %p").replace(" 0", " ").lower()
    except (OSError, ValueError):
        return None


# --- Reviews -----------------------------------------------------------------

def reviews_status(cfg: dict, today: date | None = None) -> dict:
    """Status of the Google-review SMS bot: last-7-days sent count, on/off,
    and the last run it logged."""
    rb = cfg.get("review_bot", {}) or {}
    today = today or date.today()

    sent_raw = _read_text(rb.get("sent_log", ""))
    log_raw = _read_text(rb.get("bot_log", ""))
    config_raw = _read_text(rb.get("config", ""))

    if sent_raw is None and log_raw is None and config_raw is None:
        return {"connected": False, "message": NOT_CONNECTED}

    sent_last_7_days = None
    if sent_raw is not None:
        sent_last_7_days = 0
        try:
            entries = json.loads(sent_raw)
            for record in entries.values():
                try:
                    d = date.fromisoformat(str(record.get("last_sent", "")))
                except ValueError:
                    continue
                if timedelta(0) <= (today - d) < timedelta(days=7):
                    sent_last_7_days += 1
        except (ValueError, AttributeError):
            sent_last_7_days = None

    bot_enabled = None
    if config_raw is not None:
        try:
            bot_enabled = bool(json.loads(config_raw).get("enabled"))
        except (ValueError, AttributeError):
            pass

    last_run, last_result = None, None
    last_run_date, last_errors = None, None
    if log_raw is not None:
        for line in reversed(log_raw.splitlines()):
            m = FINISHED_RE.match(line.strip())
            if m:
                dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                last_run = dt.strftime("%d/%m/%Y %I:%M %p").replace(" 0", " ").lower()
                last_result = (f"sent {m.group(2)} · skipped {m.group(3)}"
                               f" · errors {m.group(4)}")
                last_run_date, last_errors = dt.date(), int(m.group(4))
                break

    return {
        "connected": True,
        "sent_last_7_days": sent_last_7_days,
        "bot_enabled": bot_enabled,
        "last_run": last_run,
        "last_result": last_result,
        "alert": _reviews_alert(bot_enabled, last_run, last_run_date,
                                last_errors, today),
        "message": "",
    }


# --- Invoices (the supplier-invoice helper) ----------------------------------

# It runs every evening, so two quiet days means something is wrong.
INVOICE_STALE_DAYS = 2


def _invoice_alert(suppliers, newest_dt, today):
    """Plain-words warning when the invoice helper needs a human, else "".

    Same idea as the review-bot alert: nobody notices a number sitting still,
    so the page works it out itself and says so in words."""
    if not suppliers:
        return ""
    if any(s.get("error") for s in suppliers):
        return ("The invoice helper hit an error on its last run, so some invoices "
                "may not be entered. Tell Mark.")
    if newest_dt is None:
        return ("The invoice helper hasn't recorded a run yet. Tell Mark if it "
                "stays like this.")
    days = (today - newest_dt.date()).days
    if days >= INVOICE_STALE_DAYS:
        return (f"The invoice helper hasn't run since "
                f"{newest_dt.strftime('%d/%m/%Y')} — it normally runs every "
                f"evening. Tell Mark.")
    if all(not s.get("live") for s in suppliers):
        return ("The invoice helper is in TRIAL mode: it reads the invoices but "
                "is NOT entering them into Optomate yet. That's Mark's switch.")
    waiting = sum(len(s.get("needs_human") or []) for s in suppliers)
    if waiting:
        return (f"{waiting} invoice{'s' if waiting != 1 else ''} couldn't be entered "
                f"automatically and {'are' if waiting != 1 else 'is'} waiting for "
                f"Mark. Nothing is lost — they just need a person to look.")
    return ""


def invoice_status(cfg: dict, today: date | None = None) -> dict:
    """Per-supplier status of the invoice helper, from the JSON each batch
    drops after every run. Plain enough for the front desk to eyeball."""
    agent = cfg.get("optomate_agent", {}) or {}
    dir_str = agent.get("logs_dir", "")
    logs_dir = Path(dir_str) if dir_str else None
    if not logs_dir or not logs_dir.is_dir():
        return {"connected": False, "suppliers": [], "message": NOT_CONNECTED}

    suppliers, newest = [], None
    for path in sorted(logs_dir.glob("inventory-status-*.json")):
        raw = _read_text(str(path))
        if raw is None:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        when = None
        try:
            when = datetime.fromisoformat(str(rec.get("ts", "")))
        except ValueError:
            pass
        if when and (newest is None or when > newest):
            newest = when
        suppliers.append({
            "supplier": str(rec.get("supplier", "?")),
            "live": bool(rec.get("live")),
            "written": int(rec.get("written") or 0),
            "value": float(rec.get("value") or 0),
            "credits": int(rec.get("credits") or 0),
            "credit_value": float(rec.get("credit_value") or 0),
            "needs_human": list(rec.get("needs_human") or []),
            "error": rec.get("error"),
            "last_run": when.strftime("%d/%m/%Y %I:%M %p").replace(" 0", " ").lower()
                        if when else None,
        })

    if not suppliers:
        return {"connected": False, "suppliers": [], "message": NOT_CONNECTED}

    today = today or date.today()
    failed_marker = _read_text(str(logs_dir / "INVENTORY-LAST-RUN-FAILED.txt"))
    alert = _invoice_alert(suppliers, newest, today)
    if failed_marker and not alert:
        alert = "The invoice helper's last run FAILED. Tell Mark."

    return {
        "connected": True,
        "suppliers": sorted(suppliers, key=lambda s: s["supplier"]),
        "entered_total": round(sum(s["value"] for s in suppliers), 2),
        "entered_count": sum(s["written"] for s in suppliers),
        "waiting": sum(len(s["needs_human"]) for s in suppliers),
        "live": any(s["live"] for s in suppliers),
        "last_run": newest.strftime("%d/%m/%Y %I:%M %p").replace(" 0", " ").lower()
                    if newest else None,
        "alert": alert,
        "message": "",
    }


# --- Revenue follow-ups (patients with unpaid recent sales) -------------------

def revenue_status(cfg: dict, today: date | None = None) -> dict:
    """The monthly reconciliation's follow-up list: patients whose recent sale
    is still unpaid past the grace period. Written by the agent's
    pulls\\revenue_recon.py as logs\\revenue-chase.json. Names + amounts only —
    it's a front-desk follow-up list, no clinical detail."""
    agent = cfg.get("optomate_agent", {}) or {}
    dir_str = agent.get("logs_dir", "")
    logs_dir = Path(dir_str) if dir_str else None
    raw = _read_text(str(logs_dir / "revenue-chase.json")) if logs_dir else None
    if raw is None:
        return {"connected": False, "chase": [], "message": NOT_CONNECTED}
    try:
        rec = json.loads(raw)
    except ValueError:
        return {"connected": False, "chase": [], "message": NOT_CONNECTED}

    when = None
    try:
        when = datetime.fromisoformat(str(rec.get("ts", "")))
    except ValueError:
        pass
    today = today or date.today()
    stale = bool(when and (today - when.date()).days > 45)
    return {
        "connected": True,
        "chase": list(rec.get("chase") or []),
        "chase_value": round(sum(float(e.get("owed") or 0)
                                 for e in rec.get("chase") or []), 2),
        "fully_paid": int(rec.get("fully_paid") or 0),
        "fresh_unpaid": int(rec.get("fresh_unpaid") or 0),
        "legacy_count": int(rec.get("legacy_count") or 0),
        "legacy_value": float(rec.get("legacy_value") or 0),
        "grace_days": int(rec.get("grace_days") or 30),
        "last_run": when.strftime("%d/%m/%Y") if when else None,
        "alert": ("This list hasn't refreshed in over 6 weeks — "
                  "the monthly reconciliation may have stopped. Tell Mark."
                  if stale else None),
        "message": "",
    }


# --- Lens jobs (spectacle orders extracted by the agent) ----------------------

MAX_LENS_JOBS = 50


def lens_jobs(cfg: dict) -> dict:
    """Recent spectacle lens jobs written by the Optomate agent
    (lens-jobs.jsonl, one JSON object per line). Job numbers, Rx values and
    frame measurements only — the agent never includes patient details."""
    agent = cfg.get("optomate_agent", {}) or {}
    path_str = agent.get("lens_jobs", "")
    raw = _read_text(path_str)
    if raw is None:
        return {"connected": False, "jobs": [], "message": NOT_CONNECTED}

    jobs = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            jobs.append(record)
    jobs = jobs[-MAX_LENS_JOBS:]
    jobs.reverse()  # newest (last written) first
    return {"connected": True, "jobs": jobs,
            "updated": _mtime_display(path_str), "message": ""}


# --- Stock proposals ---------------------------------------------------------

def _parse_csv(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as f:
            rows = list(csv.reader(f))
        headers = rows[0] if rows else []
        body = rows[1:]
        return {
            "headers": headers,
            "rows": body[:MAX_CSV_ROWS],
            "row_count": len(body),
            "truncated": len(body) > MAX_CSV_ROWS,
            "error": None,
        }
    except (OSError, csv.Error) as e:
        return {"headers": [], "rows": [], "row_count": 0,
                "truncated": False,
                "error": f"This file couldn't be read ({e.__class__.__name__})."}


def stock_proposals(cfg: dict) -> dict:
    """Every CSV waiting in the Optomate agent's inventory\\proposals folder."""
    agent = cfg.get("optomate_agent", {}) or {}
    dir_str = agent.get("proposals_dir", "")
    proposals_dir = Path(dir_str) if dir_str else None

    if not proposals_dir or not proposals_dir.is_dir():
        return {"connected": False, "proposals": [], "message": NOT_CONNECTED}

    proposals = []
    for path in sorted(proposals_dir.glob("*.csv"),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        parsed = _parse_csv(path)
        proposals.append({
            "filename": path.name,
            "approved": path.name.lower().endswith(".approved.csv"),
            "updated": _mtime_display(str(path)),
            **parsed,
        })
    return {"connected": True, "proposals": proposals, "message": ""}


def approve_proposal(cfg: dict, filename: str):
    """Rename <name>.csv -> <name>.approved.csv to flag it for entry.

    Returns (ok, payload). The actual entry into Optomate stays a
    supervised/CLI step — this only flags the file.
    """
    agent = cfg.get("optomate_agent", {}) or {}
    dir_str = agent.get("proposals_dir", "")
    proposals_dir = Path(dir_str) if dir_str else None

    if not proposals_dir or not proposals_dir.is_dir():
        return False, {"status": 400, "error": NOT_CONNECTED}

    # No path tricks: the name must be a bare CSV filename.
    if (not filename or filename != Path(filename).name
            or "/" in filename or "\\" in filename or ".." in filename
            or not filename.lower().endswith(".csv")):
        return False, {"status": 400,
                       "error": "That doesn't look like one of the stock files."}

    if filename.lower().endswith(".approved.csv"):
        return False, {"status": 400,
                       "error": "That one is already approved."}

    source = proposals_dir / filename
    if not source.is_file():
        return False, {"status": 404,
                       "error": "That file isn't there any more — "
                                "refresh the page to see the current list."}

    stem = filename[:-len(".csv")]
    target = proposals_dir / f"{stem}.approved.csv"
    counter = 2
    while target.exists():
        target = proposals_dir / f"{stem}-{counter}.approved.csv"
        counter += 1

    try:
        source.rename(target)
    except OSError:
        return False, {"status": 500,
                       "error": "The file couldn't be marked just now — "
                                "it may be open in another program. "
                                "Close it and try again."}
    return True, {"status": 200, "new_filename": target.name}
