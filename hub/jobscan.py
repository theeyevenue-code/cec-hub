"""Jobs arrived - scan job cards, confirm once (Mark, 3 Nov 2026).

Staff scan job cards with the NETUM scanner (it types the job number + Enter); each scan
looks the job up; one Confirm marks every job received in Optomate and texts the patient
"your glasses are ready" - as far as the switches in the agent's
orders\\jobscan_config.json allow (shipped: nothing written, nothing sent).

The Hub holds no logic and no credential: like the order checker (hub/ordercheck.py) it
runs the agent's engine as a subprocess -
    python -m orders.jobscan status|lookup|confirm --ids N,N --json
in the agent folder (integrations.json optomate_agent.agent_dir) and passes its JSON on.
Only validated job numbers are ever passed to the subprocess.

PRIVACY: names come back in the answer for the staff page only. Nothing here logs a name,
a mobile or a message - app.py logs counts.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

from hub import checkin

ID_RE = re.compile(r"^\d{1,9}$")
MAX_IDS = 60
TIMEOUTS = {"status": 30, "lookup": 30, "confirm": 300}
NOT_CONNECTED = "Jobs arrived isn't connected to Optomate on this computer."
TIMEOUT_CONFIRM = ("Optomate took too long, so some jobs may be done and some not. "
                   "Scan the cards again: finished jobs show grey and are never texted twice.")


def connected(cfg: dict) -> bool:
    d = checkin.agent_dir(cfg)
    return bool(d and (d / "orders" / "jobscan.py").is_file())


def clean_ids(raw) -> tuple[list[str], list[str]]:
    """(valid job numbers, other values) - unique, in scan order."""
    if not isinstance(raw, list):
        raw = [raw] if raw not in (None, "") else []
    good, bad, seen = [], [], set()
    for v in raw[:MAX_IDS]:
        s = str(v if v is not None else "").strip()
        if not s:
            continue
        if ID_RE.match(s):
            s = str(int(s))
            if s not in seen:
                seen.add(s)
                good.append(s)
        else:
            bad.append(re.sub(r"[^\w-]", "", s)[:20])
    return good, bad


def run(cfg: dict, command: str, ids: list[str] | None = None) -> dict:
    """Run the agent's engine. Never raises."""
    if not connected(cfg):
        return {"ok": False, "connected": False, "error": NOT_CONNECTED}
    args = [command, "--json"]
    if ids:
        if not all(ID_RE.match(i) for i in ids):      # belt and braces
            return {"ok": False, "error": "Only job numbers can be checked."}
        args += ["--ids", ",".join(ids)]
    python = (cfg.get("optomate_agent") or {}).get("python") or sys.executable
    try:
        proc = subprocess.run([python, "-m", "orders.jobscan", *args],
                              cwd=str(checkin.agent_dir(cfg)), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUTS[command])
    except subprocess.TimeoutExpired:
        if command == "confirm":
            return {"ok": False, "timeout": True, "error": TIMEOUT_CONFIRM}
        return {"ok": False, "error": "Optomate took too long - try again."}
    except OSError:
        return {"ok": False, "connected": False, "error": "Couldn't start the Jobs arrived helper."}
    line = next((c.strip() for c in reversed((proc.stdout or "").splitlines()) if c.strip()), "")
    try:
        data = json.loads(line) if line else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        if command == "confirm":
            return {"ok": False, "error": "The Jobs arrived helper didn't give a readable answer. "
                                          "Scan the cards again to see which are done."}
        return {"ok": False, "error": "The Jobs arrived helper didn't give a readable answer."}
    return data


def status(cfg: dict) -> dict:
    return run(cfg, "status")


def lookup(cfg: dict, raw) -> dict:
    good, bad = clean_ids(raw)
    if not good and not bad:
        return {"ok": False, "error": "Scan a job card first."}
    out = run(cfg, "lookup", good) if good else {"ok": True, "lines": []}
    if out.get("ok"):
        out["lines"] = list(out.get("lines") or []) + [
            {"id": b, "state": "bad", "reason": "Not a job number"} for b in bad]
    return out


def confirm(cfg: dict, raw, expected) -> dict:
    """expected = how many jobs the dialog promised; a mismatch changes nothing."""
    good, _ = clean_ids(raw)
    if not good:
        return {"ok": False, "error": "No jobs to confirm."}
    try:
        expected = int(expected)
    except (TypeError, ValueError):
        expected = -1
    if expected != len(good):
        return {"ok": False, "error": "The list changed while the box was open. Nothing changed - "
                                      "press Confirm again."}
    return run(cfg, "confirm", good)
