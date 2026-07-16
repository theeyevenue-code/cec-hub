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
    if log_raw is not None:
        for line in reversed(log_raw.splitlines()):
            m = FINISHED_RE.match(line.strip())
            if m:
                dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                last_run = dt.strftime("%d/%m/%Y %I:%M %p").replace(" 0", " ").lower()
                last_result = (f"sent {m.group(2)} · skipped {m.group(3)}"
                               f" · errors {m.group(4)}")
                break

    return {
        "connected": True,
        "sent_last_7_days": sent_last_7_days,
        "bot_enabled": bot_enabled,
        "last_run": last_run,
        "last_result": last_result,
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
