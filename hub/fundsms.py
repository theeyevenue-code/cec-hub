"""Health-fund texts - the end-of-year "use your optical extras" SMS (Mark, 19 Oct 2026).

Same shape as the Recalls tile (hub/recall.py): ask the Optomate agent for the full
list, show it with tick boxes, and send ONLY the list Mark reviewed. All the safety
lives in the agent (recall/fundsms.py): it rebuilds the list fresh at send time and
refuses if anything changed, keeps a crash journal, caps each run, starts with a
25-text pilot, and stays a dry run until its own two settings are switched on.

Promotional, so deliberately separate from recalls: its own endpoints, its own
un-tick file, its own gate. Nothing about any patient is logged here.
"""

import json
import re
import subprocess
import sys

from hub.recall import _agent_cfg, agent_dir, not_connected

TOUCHES = ("HF1", "HF2")
HASH_RE = re.compile(r"^[0-9a-f]{8,64}$")
TIMEOUT_S = 180


def _run(cfg: dict, args: list, timeout: int = TIMEOUT_S) -> dict:
    dpath = agent_dir(cfg)
    if dpath is None or not (dpath / "recall" / "fundsms.py").is_file():
        return not_connected("The health-fund texts aren't set up on this computer yet.")
    python = _agent_cfg(cfg).get("python") or sys.executable
    try:
        proc = subprocess.run([python, "-m", "recall.fundsms", *args, "--json"], cwd=str(dpath),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"connected": True, "error": "That took too long - is Optomate running on the server?"}
    except OSError:
        return not_connected("Couldn't start the health-fund texts on this computer.")
    line = next((c.strip() for c in reversed((proc.stdout or "").splitlines()) if c.strip()), "")
    try:
        data = json.loads(line) if line else {}
    except ValueError:
        data = {}
    if not data:
        return {"connected": True, "error": "No answer from the health-fund texts."}
    data["connected"] = True
    return data


def preview(cfg: dict, touch: str) -> dict:
    touch = str(touch or "").strip().upper()
    if touch not in TOUCHES:
        return {"connected": True, "error": "Pick the first or last reminder."}
    return _run(cfg, ["preview", "--touch", touch])


def send(cfg: dict, touch: str, batch_hash: str, limit=None) -> dict:
    touch = str(touch or "").strip().upper()
    batch_hash = str(batch_hash or "").strip().lower()
    if touch not in TOUCHES:
        return {"error": "Pick the first or last reminder."}
    if not HASH_RE.match(batch_hash):
        return {"error": "Missing list fingerprint - refresh the list first."}
    args = ["send", "--touch", touch, "--expect-hash", batch_hash]
    if limit not in (None, ""):
        try:
            args += ["--limit", str(max(1, int(limit)))]
        except (TypeError, ValueError):
            return {"error": "Bad number to send."}
    return _run(cfg, args, timeout=900)


def set_deselected(cfg: dict, touch: str, year, pids) -> dict:
    """Remember who Mark un-ticked for this year's HF1 / HF2 (agent's local-reports,
    path built only from validated values)."""
    touch = str(touch or "").strip().upper()
    try:
        year = int(year)
        clean = sorted({int(p) for p in (pids or [])})
    except (TypeError, ValueError):
        return {"error": "Bad patient list."}
    if touch not in TOUCHES or not 2026 <= year <= 2100:
        return {"error": "Bad reminder."}
    dpath = agent_dir(cfg)
    if dpath is None or not (dpath / "local-reports").is_dir():
        return {"error": "The health-fund texts aren't set up on this computer yet."}
    out = dpath / "local-reports" / f"fundsms-deselect-{year}-{touch}.json"
    out.write_text(json.dumps({"touch": touch, "year": year, "pids": clean}, indent=1), encoding="utf-8")
    return {"ok": True, "deselected": len(clean)}
