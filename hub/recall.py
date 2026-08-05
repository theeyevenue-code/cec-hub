"""Recall tile — ask the Optomate agent to build a recall batch PREVIEW.

Mark picks a month and a touch (T-4 a month early / T0 due now / T+6 overdue)
and gets back the full batch: every patient, the exact message, and tick boxes
to pull individuals out of the batch (his request, 2026-07-30 — the list shows
in full from the start, like the Payment follow-ups tile shows names).

The patient list stays on the practice network exactly like the rest of the
Hub; this module still logs nothing about any patient. Un-ticked patients are
remembered in the agent's local-reports folder per month+touch, so the eventual
sender honours them.

Nothing here can send. `recall.preview` has no send path at all; live sending
still lives behind the agent's own two-key gate and Mark's explicit go-ahead.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

# Only ever pass a validated month/touch to the subprocess.
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TOUCHES = ("T-4", "T0", "T+6")

TIMEOUT_S = 120  # the agent pulls the whole exam/appointment history


def not_connected(msg: str) -> dict:
    return {"connected": False, "message": msg}


def _agent_cfg(cfg: dict) -> dict:
    return (cfg or {}).get("optomate_agent", {}) or {}


def agent_dir(cfg: dict) -> Path | None:
    d = _agent_cfg(cfg).get("agent_dir") or _agent_cfg(cfg).get("speccheck_dir")
    return Path(d) if d else None


def _stem(month: str, touch: str) -> str:
    """Filename the agent writes for a given month+touch. Mirrors
    recall/preview.py — keep the two in step."""
    y, m = month.split("-")
    safe_touch = touch.replace("+", "plus").replace("-", "minus")
    return f"recall-sms-preview-{y}{m}-{safe_touch}"


def preview_file(cfg: dict, month: str, touch: str) -> Path | None:
    """Path to the generated patient list, or None. Built ONLY from a validated
    month+touch — no caller-supplied path ever reaches the filesystem."""
    month = str(month or "").strip()
    touch = str(touch or "").strip().upper()
    if not MONTH_RE.match(month) or touch not in TOUCHES:
        return None
    dpath = agent_dir(cfg)
    if dpath is None:
        return None
    p = (dpath / "local-reports" / f"{_stem(month, touch)}.html").resolve()
    # Defensive: must still be inside the agent's local-reports folder.
    try:
        p.relative_to((dpath / "local-reports").resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def preview(cfg: dict, month: str, touch: str) -> dict:
    """Build (or rebuild) the preview for one month+touch. Never raises —
    always returns a dict the tile can render."""
    month = str(month or "").strip()
    touch = str(touch or "").strip().upper()

    if not MONTH_RE.match(month):
        return {"connected": True, "error": "Pick a month first."}
    if touch not in TOUCHES:
        return {"connected": True,
                "error": f"Touch must be one of {', '.join(TOUCHES)}."}

    dpath = agent_dir(cfg)
    if dpath is None:
        return not_connected("The recall engine isn't connected on this computer yet.")
    if not (dpath / "recall").is_dir():
        return not_connected("The recall engine isn't in place yet "
                             "(not deployed to the agent folder).")

    python = _agent_cfg(cfg).get("python") or sys.executable
    try:
        # --max-age 600: reuse the agent's 10-minute Optomate snapshot, so
        # un-ticking someone doesn't cost a fresh ~40s pull every time.
        proc = subprocess.run(
            [python, "-m", "recall.preview", "--month", month,
             "--touch", touch, "--json", "--max-age", "600"],
            cwd=str(dpath), capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"connected": True,
                "error": "That took too long — is Optomate running on the server?"}
    except OSError:
        return not_connected("Couldn't start the recall engine on this computer.")

    # The agent prints one JSON object on the last non-blank stdout line.
    line = ""
    for candidate in reversed((proc.stdout or "").splitlines()):
        if candidate.strip():
            line = candidate.strip()
            break
    if not line:
        return {"connected": True,
                "error": "The recall engine didn't return anything."}
    try:
        data = json.loads(line)
    except ValueError:
        return {"connected": True,
                "error": "Couldn't read the recall engine's answer."}
    if data.get("error"):
        return {"connected": True, "error": str(data["error"])}

    data["connected"] = True
    return data


HASH_RE = re.compile(r"^[0-9a-f]{8,64}$")


def send(cfg: dict, month: str, touch: str, batch_hash: str,
         limit=None) -> dict:
    """Send the reviewed batch. The agent refuses unless the hash of a FRESH
    rebuild matches what Mark reviewed, the config gates are on, Twilio is
    configured, and the batch is under the safety cap. Never raises."""
    month = str(month or "").strip()
    touch = str(touch or "").strip().upper()
    batch_hash = str(batch_hash or "").strip().lower()
    if not MONTH_RE.match(month) or touch not in TOUCHES:
        return {"error": "Bad month or reminder."}
    if not HASH_RE.match(batch_hash):
        return {"error": "Missing batch fingerprint — refresh the list first."}
    argv_limit = []
    if limit is not None:
        try:
            argv_limit = ["--limit", str(max(1, int(limit)))]
        except (TypeError, ValueError):
            return {"error": "Bad limit."}
    dpath = agent_dir(cfg)
    if dpath is None or not (dpath / "recall").is_dir():
        return not_connected("The recall engine isn't connected on this computer yet.")
    python = _agent_cfg(cfg).get("python") or sys.executable
    try:
        proc = subprocess.run(
            [python, "-m", "recall.send_batch", "--month", month,
             "--touch", touch, "--expect-hash", batch_hash, "--json",
             *argv_limit],
            cwd=str(dpath), capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return {"error": "The send took too long — check the journal before "
                         "trying again (some messages may have gone out)."}
    except OSError:
        return not_connected("Couldn't start the recall engine on this computer.")
    line = ""
    for candidate in reversed((proc.stdout or "").splitlines()):
        if candidate.strip():
            line = candidate.strip()
            break
    try:
        return json.loads(line) if line else {"error": "No answer from the sender."}
    except ValueError:
        return {"error": "Couldn't read the sender's answer."}


def history(cfg: dict) -> dict:
    """Everything that has been SENT — by batch and by patient — from the
    agent's dedup log (the same file the sender checks, so this view IS the
    double-text protection, made visible). Never raises."""
    dpath = agent_dir(cfg)
    if dpath is None or not (dpath / "recall").is_dir():
        return not_connected("The recall engine isn't connected on this computer yet.")
    python = _agent_cfg(cfg).get("python") or sys.executable
    try:
        proc = subprocess.run(
            [python, "-m", "recall.history", "--json"],
            cwd=str(dpath), capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"connected": True, "error": "That took too long."}
    except OSError:
        return not_connected("Couldn't start the recall engine on this computer.")
    line = ""
    for candidate in reversed((proc.stdout or "").splitlines()):
        if candidate.strip():
            line = candidate.strip()
            break
    try:
        data = json.loads(line) if line else {}
    except ValueError:
        data = {}
    if not data:
        return {"connected": True, "error": "Couldn't read the sent history."}
    data["connected"] = True
    return data


def success(cfg: dict, months=None) -> dict:
    """Did the recalls work — how many texted patients went on to book, over
    the whole record or the last N months. Never raises."""
    dpath = agent_dir(cfg)
    if dpath is None or not (dpath / "recall").is_dir():
        return not_connected("The recall engine isn't connected on this computer yet.")
    argv_months = []
    if months not in (None, "", "all"):
        try:
            argv_months = ["--months", str(max(1, min(60, int(months))))]
        except (TypeError, ValueError):
            return {"error": "Bad period."}
    python = _agent_cfg(cfg).get("python") or sys.executable
    try:
        proc = subprocess.run(
            [python, "-m", "recall.success", "--json", *argv_months],
            cwd=str(dpath), capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"connected": True, "error": "That took too long."}
    except OSError:
        return not_connected("Couldn't start the recall engine on this computer.")
    line = ""
    for candidate in reversed((proc.stdout or "").splitlines()):
        if candidate.strip():
            line = candidate.strip()
            break
    try:
        data = json.loads(line) if line else {}
    except ValueError:
        data = {}
    if not data:
        return {"connected": True, "error": "Couldn't read the success figures."}
    data["connected"] = True
    return data


CHASE_RE = re.compile(r"^recall-call-sheet-(chase-\d{8}|\d{6})\.html$")


def chase_sheet_file(cfg: dict, month: str = "") -> Path | None:
    """The phone-list file to serve. With a month (YYYY-MM) -> that month's
    sheet; without -> the newest sheet of any kind. Strict patterns only —
    nothing else is ever served."""
    dpath = agent_dir(cfg)
    if dpath is None:
        return None
    reports = dpath / "local-reports"
    if not reports.is_dir():
        return None
    month = str(month or "").strip()
    if month:
        if not MONTH_RE.match(month):
            return None
        p = reports / f"recall-call-sheet-{month.replace('-', '')}.html"
        return p if p.is_file() else None
    candidates = [p for p in reports.iterdir() if CHASE_RE.match(p.name)]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def latest_chase_sheet(cfg: dict) -> Path | None:
    """Kept for callers that just want 'the newest phone list'."""
    return chase_sheet_file(cfg, "")


def refresh_chase_sheet(cfg: dict, month: str = "") -> dict:
    """Regenerate the phone recall list (read-only against Optomate). With a
    month (YYYY-MM) -> everyone due that month still unbooked (Mark's preferred
    view); without -> the rolling chase window. Never raises."""
    month = str(month or "").strip()
    if month and not MONTH_RE.match(month):
        return {"error": "Pick a month first."}
    dpath = agent_dir(cfg)
    if dpath is None or not (dpath / "recall").is_dir():
        return not_connected("The recall engine isn't connected on this computer yet.")
    python = _agent_cfg(cfg).get("python") or sys.executable
    argv = [python, "-m", "recall.call_sheet"]
    if month:
        argv += ["--month", month]
    try:
        proc = subprocess.run(
            argv, cwd=str(dpath), capture_output=True, text=True,
            timeout=TIMEOUT_S + 60,
        )
    except subprocess.TimeoutExpired:
        return {"connected": True,
                "error": "That took too long — is Optomate running on the server?"}
    except OSError:
        return not_connected("Couldn't start the recall engine on this computer.")
    m = re.search(r"patients to call:\s*(\d+)\s+households:\s*(\d+)",
                  proc.stdout or "")
    sheet = chase_sheet_file(cfg, month)
    if sheet is None:
        return {"connected": True,
                "error": "The list didn't generate — tell Mark."}
    out = {"connected": True, "ok": True, "month": month}
    if m:
        out["patients"] = int(m.group(1))
        out["households"] = int(m.group(2))
    return out


def set_deselected(cfg: dict, month: str, touch: str, pids) -> dict:
    """Remember which patients Mark un-ticked for one month+touch batch.
    Written to the agent's local-reports so the eventual sender sees the same
    list. Path is built ONLY from the validated month+touch."""
    month = str(month or "").strip()
    touch = str(touch or "").strip().upper()
    if not MONTH_RE.match(month) or touch not in TOUCHES:
        return {"error": "Bad month or reminder."}
    try:
        clean = sorted({int(p) for p in (pids or [])})
    except (TypeError, ValueError):
        return {"error": "Bad patient list."}
    dpath = agent_dir(cfg)
    if dpath is None or not (dpath / "local-reports").is_dir():
        return {"error": "The recall engine isn't connected on this computer yet."}
    out = (dpath / "local-reports" /
           f"recall-deselect-{month.replace('-', '')}-"
           f"{touch.replace('+', 'plus').replace('-', 'minus')}.json")
    out.write_text(json.dumps({"month": month, "touch": touch, "pids": clean},
                              indent=1), encoding="utf-8")
    return {"ok": True, "deselected": len(clean)}
