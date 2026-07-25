"""On-demand spectacle-order check.

The Hub does NOT contain the checking logic — it calls the `speccheck` engine
that lives in the CEC-Optomate-Agent repo (which reads Optomate and does the
Rx-expiry / script-match / lens-type checks). This module is a thin, safe proxy:
it validates the order number, runs the engine's CLI, and returns its JSON.

Wire-up (later): set in config\\integrations.json ->
    "optomate_agent": { "speccheck_dir": "C:\\\\CEC\\\\CEC-Optomate-Agent",
                        "python": "C:\\\\...\\\\python.exe" }   (python optional)
Until that's set, or if the engine isn't deployed there yet, every call returns
{"connected": false} with a plain-words message — nothing breaks.

PHI: the engine's verdict may carry dates/Rx in its reasons. That is shown on
the practice PC only and is NEVER logged here; this module logs nothing about
the order or patient.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

# Order numbers are short integers in Optomate. Validated before use so nothing
# but a number is ever passed to the subprocess.
ORDER_RE = re.compile(r"^\d{1,9}$")

# speccheck exits 0=GREEN, 1=ISSUE, 2=UNVERIFIED — all are valid results.
VALID_EXIT = {0, 1, 2}

TIMEOUT_S = 25


def _agent_cfg(cfg: dict) -> dict:
    return (cfg or {}).get("optomate_agent", {}) or {}


def not_connected(msg: str) -> dict:
    return {"connected": False, "message": msg}


def check(cfg: dict, order: str) -> dict:
    """Run the order check for one order number. Never raises — always returns
    a dict the panel can render."""
    order = str(order or "").strip()
    if not order:
        return {"connected": True, "error": "Scan or type an order number first."}
    if not ORDER_RE.match(order):
        return {"connected": True, "error": "That doesn't look like an order number."}

    agent = _agent_cfg(cfg)
    directory = agent.get("speccheck_dir")
    if not directory:
        return not_connected("The order checker isn't connected on this computer yet.")
    dpath = Path(directory)
    if not (dpath / "speccheck").is_dir():
        return not_connected("The order-checker engine isn't in place yet "
                             "(not deployed to the agent folder).")

    python = agent.get("python") or sys.executable
    try:
        proc = subprocess.run(
            [python, "-m", "speccheck", order, "--json", "--no-log"],
            cwd=str(dpath), capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"connected": True,
                "error": "The check timed out — is Optomate running on the server?"}
    except OSError:
        return not_connected("Couldn't start the order checker on this computer.")

    if proc.returncode not in VALID_EXIT:
        err = (proc.stderr or proc.stdout or "The order checker failed.").strip()
        return {"connected": True, "error": err[:200]}

    try:
        return {"connected": True, "result": json.loads(proc.stdout)}
    except (ValueError, TypeError):
        return {"connected": True,
                "error": "The order checker returned an unreadable result."}
