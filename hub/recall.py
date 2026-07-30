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
        proc = subprocess.run(
            [python, "-m", "recall.preview", "--month", month,
             "--touch", touch, "--json"],
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
