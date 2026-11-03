"""Who got what - every text and email one patient got, from every lane (Mark, 20 Oct 2026).

Same shape as the Recalls tile (hub/recall.py): ask the Optomate agent
(`python -m recall.contact_log ... --json`, JSON on the last stdout line) and show
the answer. READ-ONLY: the agent only reads its own send logs, the review bot's
sent_log.json and Optomate's SmsTransactions - nothing sends, nothing is written.

The name goes to the agent as a subprocess argument, never in a URL, and nothing
about a patient is logged here. Every function returns a dict and never raises.
"""

import json
import re
import subprocess
import sys

from hub.recall import _agent_cfg, agent_dir, not_connected

TIMEOUT_S = 60
_NAME_SAFE = re.compile(r"[^A-Za-z0-9 '\-]")


def clean_query(q) -> str:
    """Name characters only, at most 60 of them (the agent cleans again)."""
    return " ".join(_NAME_SAFE.sub(" ", str(q or "")).split())[:60]


def _review_log(cfg: dict) -> str:
    return str(((cfg or {}).get("review_bot") or {}).get("sent_log") or "")


def _run(cfg: dict, args: list) -> dict:
    try:
        dpath = agent_dir(cfg)
        if dpath is None or not (dpath / "recall" / "contact_log.py").is_file():
            return not_connected("The contact lookup isn't set up on this computer yet.")
        python = _agent_cfg(cfg).get("python") or sys.executable
        proc = subprocess.run([python, "-m", "recall.contact_log", *args, "--json"], cwd=str(dpath),
                              capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"connected": True, "error": "That took too long - is Optomate running on the server?"}
    except (OSError, ValueError, TypeError):
        return not_connected("Couldn't start the contact lookup on this computer.")
    line = next((c.strip() for c in reversed((proc.stdout or "").splitlines()) if c.strip()), "")
    try:
        data = json.loads(line) if line else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict) or not data:
        return {"connected": True, "error": "No answer from the contact lookup."}
    data["connected"] = True
    return data


def search(cfg: dict, q) -> dict:
    q = clean_query(q)
    terms = q.split()
    if not terms or max(len(t) for t in terms) < 2:
        return {"connected": True, "results": [], "more": False,
                "note": "Type a surname and first name (either order)."}
    return _run(cfg, ["--q", q])


def timeline(cfg: dict, pid) -> dict:
    try:
        pid = int(str(pid).strip())
    except (TypeError, ValueError):
        return {"error": "Pick a patient from the list."}
    if not 0 < pid < 10_000_000:
        return {"error": "Pick a patient from the list."}
    args = ["--pid", str(pid)]
    review = _review_log(cfg)
    if review:
        args += ["--review-log", review]
    return _run(cfg, args)
