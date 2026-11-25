"""Jobs arrived - scan job cards, confirm once (Mark, 3 Nov 2026).

Staff scan job cards with the NETUM scanner (it types the job number + Enter); each scan
looks the job up; one Confirm marks every job received in Optomate and texts the patient
"your glasses have arrived" - as far as the switches in the agent's
orders\\jobscan_config.json allow (shipped: nothing written, nothing sent).

25 Nov 2026 (Mark: "build all 3"):
  - DON'T TEXT per job: staff tick it and pick a reason from the agent's fixed list (status gives
    the list); Confirm still marks the job received, no text. Sent as --hold G35144:knows.
  - NOT TEXTED YET list: received orders of the last 30 days, not collected, with no delivered
    ready text, with the reason (agent: python -m orders.jobscan nottexted).
  - TEXT NOW on a list row = the agent's resend (same text, gates, dedupe and checks as Confirm),
    always previewed first (--dry); CALLED THEM - REMOVE = the agent's remove (key, staff name, time).

The Hub holds no logic and no credential: like the order checker (hub/ordercheck.py) it
runs the agent's engine as a subprocess -
    python -m orders.jobscan status|lookup|preview|confirm|nottexted|resend|remove --json
in the agent folder (integrations.json optomate_agent.agent_dir) and passes its JSON on.
Only validated job numbers are ever passed to the subprocess: a bare number, or one typed
"G35144" (glasses) / "C5100" (contacts) - contact lens orders share numbers with glasses jobs
(agent quirk #87); the engine takes whichever is open and asks staff to pick when both are.

PRIVACY: names come back in the answer for the staff page only. Nothing here logs a name,
a mobile or a message - app.py logs counts.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

from hub import checkin

ID_RE = re.compile(r"^(?:[GC])?\d{1,9}$")
TYPED_RE = re.compile(r"^[GC]\d{1,9}$")
TOKEN_RE = re.compile(r"^(?:(G|C|CL)[:-]?)?(\d{1,9})$", re.I)
HOLD_RE = re.compile(r"^[a-z]{2,12}$")
WHO_RE = re.compile(r"[^A-Za-z .'\-]")
MAX_IDS = 60
TIMEOUTS = {"status": 30, "lookup": 30, "preview": 45, "confirm": 300, "nottexted": 45,
            "resend": 90, "remove": 20}
NOT_CONNECTED = "Jobs arrived isn't connected to Optomate on this computer."
TIMEOUT_CONFIRM = ("Optomate took too long, so some jobs may be done and some not. "
                   "Scan the cards again: finished jobs show grey and are never texted twice.")
TIMEOUT_TEXTNOW = ("Optomate took too long - the text may or may not have gone. Refresh the list: "
                   "a sent text is never sent twice.")


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
        m = TOKEN_RE.match(s)
        if m:
            kind = (m.group(1) or "").upper()[:1]
            s = kind + str(int(m.group(2)))
            if s not in seen:
                seen.add(s)
                good.append(s)
        else:
            bad.append(re.sub(r"[^\w-]", "", s)[:20])
    return good, bad


def clean_holds(raw, good: list[str]) -> tuple[list[str], bool]:
    """["G35144:knows", ...] or {"G35144": "knows"} -> (["G35144:knows"], all_ok). Only jobs also
    being confirmed; a hold without a plain reason code is NOT dropped quietly (all_ok False), so the
    job can never be texted by mistake - the agent checks the code against its fixed list."""
    if isinstance(raw, dict):
        raw = [f"{k}:{v}" for k, v in raw.items()]
    if not isinstance(raw, list):
        raw = [raw] if raw not in (None, "") else []
    out, ok = [], True
    for v in raw[:MAX_IDS]:
        key, _, code = str(v or "").partition(":")
        ids, _bad = clean_ids([key.strip()])
        code = code.strip().lower()
        if not ids:
            ok = False
            continue
        if ids[0] not in good:
            continue                       # not in this confirm: nothing to hold
        if not HOLD_RE.match(code):
            ok = False
            continue
        out.append(f"{ids[0]}:{code}")
    return out, ok


def run(cfg: dict, command: str, ids: list[str] | None = None,
        own: list[str] | None = None, hold: list[str] | None = None,
        extra: list[str] | None = None) -> dict:
    """Run the agent's engine. Never raises. own = glasses jobs switched to "Replacement lenses";
    hold = "Don't text" jobs with their reason code."""
    if not connected(cfg):
        return {"ok": False, "connected": False, "error": NOT_CONNECTED}
    args = [command, "--json"]
    if ids:
        if not all(ID_RE.match(i) for i in ids):      # belt and braces
            return {"ok": False, "error": "Only job numbers can be checked."}
        args += ["--ids", ",".join(ids)]
    if own:
        if not all(ID_RE.match(i) for i in own):
            return {"ok": False, "error": "Only job numbers can be checked."}
        args += ["--own", ",".join(own)]
    if hold:
        if not all(ID_RE.match(h.partition(":")[0]) and HOLD_RE.match(h.partition(":")[2]) for h in hold):
            return {"ok": False, "error": "Only job numbers can be checked."}
        args += ["--hold", ",".join(hold)]
    args += extra or []
    python = (cfg.get("optomate_agent") or {}).get("python") or sys.executable
    try:
        proc = subprocess.run([python, "-m", "orders.jobscan", *args],
                              cwd=str(checkin.agent_dir(cfg)), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUTS[command],
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        if command == "confirm":
            return {"ok": False, "timeout": True, "error": TIMEOUT_CONFIRM}
        if command == "resend":
            return {"ok": False, "timeout": True, "error": TIMEOUT_TEXTNOW}
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


def _own(own_raw, good: list[str]) -> list[str]:
    own_good, _ = clean_ids(own_raw)
    return [i for i in own_good if i in good and i.startswith("G")]


def preview(cfg: dict, raw, own_raw=None, hold_raw=None) -> dict:
    """The text each scanned job would get (Mark, 4 Nov 2026: staff see what goes out).
    A dry run of Confirm in the agent: nothing written, nothing sent."""
    good, _ = clean_ids(raw)
    if not good:
        return {"ok": True, "texts": []}
    hold, ok = clean_holds(hold_raw, good)
    if not ok:
        return {"ok": False, "error": "Pick a reason for each \"Don't text\" job."}
    return run(cfg, "preview", good, _own(own_raw, good), hold)


def confirm(cfg: dict, raw, expected, own_raw=None, hold_raw=None) -> dict:
    """expected = how many jobs the dialog promised; a mismatch changes nothing.
    own_raw = glasses jobs staff switched to "Replacement lenses" (own frame, patient has it;
    Mark 4 Nov 2026) - only ones also being confirmed, glasses only.
    hold_raw = "Don't text" jobs with their reason (25 Nov 2026); a hold without a usable reason
    stops the whole confirm (nothing changed) rather than texting that patient."""
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
    hold, ok = clean_holds(hold_raw, good)
    if not ok:
        return {"ok": False, "error": "A \"Don't text\" job has no reason picked. Nothing changed - "
                                      "pick a reason, then Confirm again."}
    return run(cfg, "confirm", good, _own(own_raw, good), hold)


# --- Not texted yet (25 Nov 2026) -------------------------------------------------------------

def not_texted(cfg: dict) -> dict:
    return run(cfg, "nottexted")


def _one_typed(raw) -> str | None:
    good, bad = clean_ids([raw])
    return good[0] if (good and not bad and TYPED_RE.match(good[0])) else None


def text_now(cfg: dict, raw_key, send: bool = False) -> dict:
    """send=False -> the exact text (agent resend --dry, nothing sent). send=True -> the agent's
    resend: the same approved text, gates, dedupe and checks as Confirm. One typed job only."""
    key = _one_typed(raw_key)
    if not key:
        return {"ok": False, "error": "Text now takes one job from the list."}
    return run(cfg, "resend", [key], extra=[] if send else ["--dry"])


def remove(cfg: dict, raw_key, who: str) -> dict:
    """'Called them - remove': the row leaves the list; the agent logs key, staff name, time."""
    key = _one_typed(raw_key)
    if not key:
        return {"ok": False, "error": "Remove takes one job from the list."}
    name = WHO_RE.sub("", str(who or ""))[:60].strip() or "someone (no name picked)"
    return run(cfg, "remove", [key], extra=["--who", name])
