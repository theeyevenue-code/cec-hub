"""Check-in tile - the Hub side of the iPad registration form.

A patient fills the form on the reception iPad; staff check it here against
what Optomate holds; one tap saves it. Everything that touches Optomate is the
Optomate agent's `checkin` engine (`python -m checkin.cli <command>`, one JSON
object on the last stdout line) - the Hub never talks to Optomate itself. Same
shape and defensiveness as hub/recall.py: validated arguments only, a timeout,
last-JSON-line parsing, never raises, "not connected" when the agent folder has
no checkin\\ package.

PATIENT DATA. This tile shows patient details on staff screens and on the iPad.
The form itself lives ONLY in the engine's session folder,
<agent_dir>\\local-reports\\checkin\\sessions\\<token>.json (git-ignored, on this
machine). Nothing patient-identifying goes to hub.log (patient ID and state
only), to a tracked file, to a URL query string, or to the browser's storage.

Session states: sent -> filling -> submitted -> saved | discarded, and
filling -> paused -> sent (the iPad's inactivity lock, resumed by reception).
- One iPad slot: the session in state sent/filling/paused. Sending another asks
  first, then the old one is discarded.
- paused = the iPad was left untouched (about 5 minutes): it hid the form and
  shows "Please return the iPad to reception". The answers so far are kept HERE
  as a draft (never on the iPad); only a staff "Resume on the iPad" brings them
  back.
- discarded = the file is deleted at once (answers go with it).
- A LIVE save that the engine reports as saved deletes the file at once; the
  engine keeps its journal. A small marker (IDs only) lets today's list say
  "Saved".
- No signature and no signed PDF (Mark, 9 Oct 2026: the paper form never had
  one). The last iPad screen is "Check and send"; the dated Optomate note quotes
  the consent sentence and says who sent the form.
- In dry run the session is kept so it can be checked again; staff discard it.
- Forms not yet submitted (sent / filling / paused) are deleted 2 days after
  they were made (Mark, 9 Oct 2026). Submitted forms wait for staff to check,
  save or discard them.
- A submitted form's token is remembered (as a hash, 2 days) so an iPad that
  lost the "received" answer and sends again is told "received", not "gone".

Staff vs iPad (Codex #1, 9 Oct 2026): the device key opens the iPad routes
ONLY. Every staff route goes through app.py's staff guard: a request carrying
the device key is refused outright, and otherwise the request must come from
the server itself or from a browser unlocked once with the staff code
(staff_cookie_value / staff_code_ok below).
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("cec-hub.checkin")

TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")          # question ids / logical fields
SEARCH_STRIP_RE = re.compile(r"[^A-Za-zÀ-ɏ' .-]")

AUDIENCES = ("adult", "child")
SLOT_STATES = ("sent", "filling", "paused")       # the one form the iPad slot holds
IPAD_STATES = ("sent", "filling")                 # ...and the ones the iPad may open
STATUS_WORDS = {None: "Not sent", "sent": "On the iPad", "filling": "On the iPad",
                "paused": "Paused", "submitted": "Ready to check", "saved": "Saved",
                "discarded": "Not sent"}

PURGE_AFTER = timedelta(days=2)                   # unsubmitted forms (Mark, 9 Oct 2026)
RECEIPT_KEEP = timedelta(days=2)
MAX_SCREEN_INDEX = 30
STAFF_COOKIE = "cec_checkin_staff"
STAFF_COOKIE_DAYS = 400
UNLOCK_TRIES = 5                                  # wrong staff codes ...
UNLOCK_WINDOW_S = 15 * 60                         # ... per 15 minutes, then a 15-minute wait
LISTS_MAX_AGE_S = 600
MAX_ANSWERS = 300
MAX_TEXT = 2000
MAX_EDIT = 200
CHANGED = "The record changed - check it again"

# The engine's live-write gate lives in ONE place: the agent's own .env. Strip
# these from what the Hub hands the subprocess so nothing in the Hub's
# environment can switch the engine to live, or point it at another data folder
# than the one this module reads and writes.
_ENGINE_ONLY_ENV = ("CHECKIN_DRY_RUN", "CHECKIN_LIVE_WRITES", "CHECKIN_DATA_DIR")

TIMEOUTS = {"save": 240}
DEFAULT_TIMEOUT = 60

NOT_CONNECTED = "The check-in engine isn't connected on this computer yet."

# Engine `today` row keys that are for the iPad prefill only, never the staff
# list: the online booking's reason text goes into the local session file.
_PREFILL_ONLY_KEYS = ("booking_reason",)

_lock = threading.RLock()
_lists_cache = {"at": 0.0, "dir": None, "data": None}


# ---------------------------------------------------------------------------
# Config + paths
# ---------------------------------------------------------------------------

def _agent_cfg(cfg: dict) -> dict:
    return (cfg or {}).get("optomate_agent", {}) or {}


def agent_dir(cfg: dict) -> Path | None:
    d = _agent_cfg(cfg).get("agent_dir")
    return Path(d) if d else None


def connected(cfg: dict) -> bool:
    d = agent_dir(cfg)
    return bool(d and (d / "checkin").is_dir())


def checkin_root(cfg: dict) -> Path | None:
    d = agent_dir(cfg)
    return d / "local-reports" / "checkin" if d else None


def sessions_dir(cfg: dict) -> Path | None:
    r = checkin_root(cfg)
    return r / "sessions" if r else None


def ipad_key(cfg: dict) -> str:
    """The device key from config\\integrations.json (checkin.ipad_key). A
    placeholder or a short key counts as not set up."""
    key = str(((cfg or {}).get("checkin") or {}).get("ipad_key") or "").strip()
    if len(key) < 16 or key.upper().startswith("CHANGE"):
        return ""
    return key


def key_ok(cfg: dict, given) -> bool:
    key = ipad_key(cfg)
    return bool(key) and hmac.compare_digest(key.encode(), str(given or "").encode())


# ---- staff side (Codex #1) ---------------------------------------------------

def staff_code(cfg: dict) -> str:
    """config\\integrations.json checkin.staff_code: what a front-desk computer
    types once to unlock Check-in. A placeholder or under 4 characters = not set (4 digits OK: Mark, 13 Oct 2026; 5 tries then a 15-min wait)
    (then only the server itself can use Check-in)."""
    code = str(((cfg or {}).get("checkin") or {}).get("staff_code") or "").strip()
    if len(code) < 4 or code.upper().startswith("CHANGE"):
        return ""
    return code


def staff_cookie_value(cfg: dict) -> str:
    """The unlocked-computer cookie: derived from the staff code, so changing
    the code locks every computer out again. '' when no code is set."""
    code = staff_code(cfg)
    if not code:
        return ""
    return hmac.new(code.encode(), b"cec-checkin-staff-v1", "sha256").hexdigest()


def staff_cookie_ok(cfg: dict, given) -> bool:
    want = staff_cookie_value(cfg)
    return bool(want) and hmac.compare_digest(want.encode(), str(given or "").encode())


_unlock_fails: list[float] = []


def staff_code_ok(cfg: dict, given) -> tuple[bool, str]:
    """Check a typed staff code, with a lock-out after UNLOCK_TRIES wrong codes
    in UNLOCK_WINDOW_S (all computers together - it is the iPad we keep out)."""
    now = time.time()
    with _lock:
        _unlock_fails[:] = [t for t in _unlock_fails if now - t < UNLOCK_WINDOW_S]
        if len(_unlock_fails) >= UNLOCK_TRIES:
            return False, "Too many wrong codes. Wait 15 minutes, or use the Hub on the server."
        code = staff_code(cfg)
        if not code:
            return False, "No staff code is set up yet. Ask Mark (config, checkin.staff_code)."
        if hmac.compare_digest(code.encode(), str(given or "").strip().encode()):
            return True, ""
        _unlock_fails.append(now)
    logger.warning("Check-in: wrong staff code typed")
    return False, "That code isn't right."


def session_path(cfg: dict, token) -> Path | None:
    """The one place a session path is built: a 32-hex token inside the
    sessions folder, nothing else."""
    token = str(token or "")
    base = sessions_dir(cfg)
    if base is None or not TOKEN_RE.match(token):
        return None
    p = (base / f"{token}.json").resolve()
    if p.parent != base.resolve():
        return None
    return p


# ---------------------------------------------------------------------------
# The engine subprocess
# ---------------------------------------------------------------------------

def _engine_env() -> dict:
    env = dict(os.environ)          # CHECKIN_FIXTURE passes straight through
    for k in _ENGINE_ONLY_ENV:
        env.pop(k, None)
    return env


def run_engine(cfg: dict, *args: str, timeout: int | None = None) -> dict:
    """Run `python -m checkin.cli <args>` in the agent folder and return its
    JSON answer. Callers pass validated arguments only. Never raises."""
    if not connected(cfg):
        return {"connected": False, "error": NOT_CONNECTED}
    python = _agent_cfg(cfg).get("python") or sys.executable
    cmd = args[0] if args else ""
    try:
        proc = subprocess.run(
            [python, "-m", "checkin.cli", *args],
            cwd=str(agent_dir(cfg)), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout or TIMEOUTS.get(cmd, DEFAULT_TIMEOUT), env=_engine_env(),
        )
    except subprocess.TimeoutExpired:
        if cmd == "save":
            return {"error": "The save took too long. Check the patient in Optomate "
                             "before trying again."}
        return {"error": "That took too long - is Optomate running on the server?"}
    except OSError:
        return {"connected": False, "error": "Couldn't start the check-in engine on this computer."}
    line = ""
    for candidate in reversed((proc.stdout or "").splitlines()):
        if candidate.strip():
            line = candidate.strip()
            break
    if not line:
        return {"error": "The check-in engine didn't return anything."}
    try:
        data = json.loads(line)
    except ValueError:
        return {"error": "Couldn't read the check-in engine's answer."}
    if not isinstance(data, dict):
        return {"error": "Couldn't read the check-in engine's answer."}
    return data


def lists(cfg: dict) -> dict:
    """Pick lists (sources, occupations, optometrists). No patient data;
    cached in memory for 10 minutes."""
    now = time.time()
    key = str(agent_dir(cfg))
    c = _lists_cache
    if c["data"] and c["dir"] == key and now - c["at"] < LISTS_MAX_AGE_S:
        return c["data"]
    data = run_engine(cfg, "lists")
    if data.get("error"):
        return {}
    c.update(at=now, dir=key, data=data)
    return data


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat(timespec="seconds")


def _parse(iso) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.astimezone()


def load_session(cfg: dict, token) -> dict | None:
    p = session_path(cfg, token)
    if p is None or not p.is_file():
        return None
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(s, dict) or s.get("token") != p.stem:
        return None
    return s


def _write_session(cfg: dict, s: dict) -> None:
    p = session_path(cfg, s.get("token"))
    if p is None:
        raise ValueError("bad token")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _delete_session(cfg: dict, token) -> None:
    p = session_path(cfg, token)
    if p is not None:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def all_sessions(cfg: dict) -> list[dict]:
    base = sessions_dir(cfg)
    if base is None or not base.is_dir():
        return []
    out = []
    for f in base.glob("*.json"):
        s = load_session(cfg, f.stem)
        if s is not None:
            out.append(s)
    return sorted(out, key=lambda s: str(s.get("created") or ""))


def purge(cfg: dict, now: datetime | None = None) -> int:
    """Delete forms not yet submitted that are older than PURGE_AFTER (2 days),
    plus stray temp files. Forms waiting to be checked (`submitted`) stay.
    Returns how many went."""
    base = sessions_dir(cfg)
    if base is None or not base.is_dir():
        return 0
    now = now or _now()
    gone = 0
    with _lock:
        for f in base.glob("*.json.tmp"):
            try:
                if time.time() - f.stat().st_mtime > 3600:
                    f.unlink()
            except OSError:
                pass
        for s in all_sessions(cfg):
            created = _parse(s.get("created"))
            if s.get("state") == "submitted":
                continue
            if created is None or now - created > PURGE_AFTER:
                _delete_session(cfg, s["token"])
                gone += 1
    return gone


def current_slot(cfg: dict) -> dict | None:
    """The one session the iPad slot holds (sent / filling / paused), if any."""
    live = [s for s in all_sessions(cfg) if s.get("state") in SLOT_STATES]
    return live[-1] if live else None


def display_name(s: dict) -> str:
    """Staff-screen name: what Optomate had, else what the form says."""
    p, a = s.get("prefill") or {}, s.get("answers") or {}
    given = str(p.get("given_name") or a.get("given_name") or "").strip()
    family = str(p.get("family_name") or a.get("family_name") or "").strip()
    name = " ".join(x for x in (given, family) if x)
    if name:
        return name
    return "New patient (child)" if s.get("audience") == "child" else "New patient"


def greeting_name(s: dict) -> str:
    """First name for the iPad welcome: preferred name, else first given name.
    For a child this is the CHILD's name; the iPad greets the parent and names
    the child ("This form is for ..."), never greets the child."""
    p = s.get("prefill") or {}
    pref = str(p.get("preferred_name") or "").strip()
    if pref:
        return pref
    given = str(p.get("given_name") or "").split()
    return given[0] if given else ""


# ---- received receipts (hashes only) -----------------------------------------

def _receipts_path(cfg: dict) -> Path | None:
    r = checkin_root(cfg)
    return r / "hub-received.json" if r else None


def _token_digest(token: str) -> str:
    return hashlib.sha256(("cec-checkin-received:" + str(token)).encode()).hexdigest()


def _receipts(cfg: dict) -> list[dict]:
    p = _receipts_path(cfg)
    try:
        rows = json.loads(p.read_text(encoding="utf-8")) if p and p.is_file() else []
    except (OSError, ValueError):
        rows = []
    now = _now()
    return [r for r in rows if isinstance(r, dict) and r.get("t")
            and (_parse(r.get("at")) or now) > now - RECEIPT_KEEP]


def _add_receipt(cfg: dict, token: str) -> None:
    p = _receipts_path(cfg)
    if p is None:
        return
    rows = _receipts(cfg) + [{"t": _token_digest(token), "at": _iso()}]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, indent=1), encoding="utf-8")


def was_received(cfg: dict, token) -> bool:
    if not TOKEN_RE.match(str(token or "")):
        return False
    d = _token_digest(token)
    return any(hmac.compare_digest(r["t"], d) for r in _receipts(cfg))


# ---- saved markers (IDs only) ----------------------------------------------

def _markers_path(cfg: dict) -> Path | None:
    r = checkin_root(cfg)
    return r / "hub-saved.json" if r else None


def _markers(cfg: dict, today: str) -> list[dict]:
    p = _markers_path(cfg)
    try:
        rows = json.loads(p.read_text(encoding="utf-8")) if p and p.is_file() else []
    except (OSError, ValueError):
        rows = []
    return [r for r in rows if isinstance(r, dict) and str(r.get("saved_at", ""))[:10] == today]


def _add_marker(cfg: dict, s: dict, patient_id) -> None:
    p = _markers_path(cfg)
    if p is None:
        return
    rows = _markers(cfg, _now().date().isoformat())
    rows.append({"appointment_id": s.get("appointment_id"), "patient_id": patient_id,
                 "saved_at": _iso()})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# Staff: today, search, send
# ---------------------------------------------------------------------------

def _int(v):
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if i > 0 else None


def _status(s: dict | None) -> str:
    """Staff word for a form. A form the iPad saved by itself in test mode reads
    "Saved (test)"; one waiting for a person still reads "Ready to check"."""
    if s and s.get("state") == "submitted" and (s.get("auto") or {}).get("result") == "test_saved":
        return "Saved (test)"
    return STATUS_WORDS.get(s.get("state") if s else None, "Not sent")


def _summary(s: dict) -> dict:
    return {"token": s["token"], "state": s.get("state"),
            "status": _status(s), "check_reasons": s.get("check_reasons") or [],
            "self": s.get("via") == "self",
            "name": display_name(s), "audience": s.get("audience"),
            "patient_id": s.get("patient_id"), "appointment_id": s.get("appointment_id"),
            "created": s.get("created"), "submitted": s.get("submitted"),
            "test_saved": s.get("hub_test_saved")}


def today(cfg: dict) -> dict:
    if not connected(cfg):
        return {"connected": False, "message": NOT_CONNECTED}
    purge(cfg)
    data = run_engine(cfg, "today")
    if data.get("error"):
        return {"connected": True, "error": data["error"], "dry_run": data.get("dry_run", True)}
    day = str(data.get("date") or _now().date().isoformat())
    sessions = all_sessions(cfg)
    todays = [s for s in sessions if str(s.get("created") or "")[:10] == day]
    marks = _markers(cfg, day)

    def latest(match):
        hits = [s for s in todays if match(s)]
        return hits[-1] if hits else None

    used = set()
    rows = []
    for a in data.get("appointments") or []:
        aid, pid = a.get("appointment_id"), a.get("patient_id")
        s = latest(lambda s: s.get("appointment_id") == aid and aid) or \
            latest(lambda s: s.get("patient_id") == pid and pid and not s.get("appointment_id"))
        mk = [m for m in marks if (aid and m.get("appointment_id") == aid)
              or (pid and m.get("patient_id") == pid)]
        state = s.get("state") if s else None
        if mk and (not s or max(m["saved_at"] for m in mk) >= str(s.get("created"))):
            state = "saved"
        if s:
            used.add(s["token"])
        audience = (s or {}).get("audience") or ("child" if a.get("is_child") else "adult")
        a = {k: v for k, v in a.items() if k not in _PREFILL_ONLY_KEYS}
        rows.append({**a, "name": " ".join(x for x in (a.get("given"), a.get("surname")) if x),
                     "audience": audience, "state": state,
                     "status": _status(s) if s and state == s.get("state") else STATUS_WORDS.get(state, "Not sent"),
                     "check_reasons": (s or {}).get("check_reasons") or [],
                     "self": (s or {}).get("via") == "self",
                     "token": s["token"] if s and state not in ("saved", "discarded") else None})
    waiting = [_summary(s) for s in sessions
               if s.get("state") == "submitted" and s["token"] not in used]
    slot = current_slot(cfg)
    return {"connected": True, "date": day, "appointments": rows, "waiting": waiting,
            "ipad": _summary(slot) if slot else None,
            "ipad_ready": bool(ipad_key(cfg)),
            "self_checkin": self_checkin_on(cfg), "auto_save": auto_save_on(cfg),
            "dry_run": data.get("dry_run", True), "fixture": bool(data.get("fixture"))}


def search(cfg: dict, q) -> dict:
    q = SEARCH_STRIP_RE.sub(" ", str(q or ""))
    q = " ".join(q.split())[:60]
    if len(q.replace(" ", "")) < 2:
        return {"error": "Type at least two letters of the name."}
    data = run_engine(cfg, "search", "--q", q)
    if data.get("error"):
        return {"error": data["error"], "connected": data.get("connected", True)}
    return {"patients": data.get("patients") or [], "more": bool(data.get("more")),
            "dry_run": data.get("dry_run", True)}


def send(cfg: dict, body: dict) -> tuple[int, dict]:
    """Make a session and put it on the iPad. body: kind = appointment |
    patient | new, plus appointment_id / patient_id, audience, replace."""
    body = body if isinstance(body, dict) else {}
    if not connected(cfg):
        return 200, {"connected": False, "error": NOT_CONNECTED}
    kind = str(body.get("kind") or "")
    audience = str(body.get("audience") or "")
    if audience and audience not in AUDIENCES:
        return 400, {"error": "Pick Adult or Child."}
    pid = aid = opt_id = None
    prefill: dict = {}
    last_exam = ""
    booking_reason = ""
    if kind == "appointment":
        aid = _int(body.get("appointment_id"))
        if aid is None:
            return 400, {"error": "That appointment isn't valid."}
        t = run_engine(cfg, "today")
        if t.get("error"):
            return 200, {"error": t["error"]}
        row = next((a for a in t.get("appointments") or [] if a.get("appointment_id") == aid), None)
        if row is None:
            return 404, {"error": "That appointment isn't on today's list any more. Refresh."}
        pid, opt_id = _int(row.get("patient_id")), _int(row.get("optometrist_id"))
        audience = audience or ("child" if row.get("is_child") else "adult")
        # The online booking's reason pre-fills "What would you like from today's
        # visit?" for the patient to confirm or edit. Session file only.
        booking_reason = str(row.get("booking_reason") or "").strip()[:MAX_EDIT]
    elif kind == "patient":
        pid = _int(body.get("patient_id"))
        if pid is None:
            return 400, {"error": "That patient isn't valid."}
    elif kind == "new":
        if audience not in AUDIENCES:
            return 400, {"error": "Pick Adult or Child for the new patient."}
    else:
        return 400, {"error": "Unknown kind of check-in."}

    if pid is not None:
        p = run_engine(cfg, "patient", "--id", str(pid))
        if p.get("error"):
            return 200, {"error": p["error"]}
        prefill = p.get("details") or {}
        audience = audience or ("child" if p.get("is_child") else "adult")
        # A previous exam here = a returning patient (v5): the iPad asks
        # "Anything new since your last visit?" instead of the full history.
        last_exam = str(p.get("last_exam") or "")[:10]
    if booking_reason:
        prefill = {**prefill, "booking_reason": booking_reason}

    with _lock:
        purge(cfg)
        busy = current_slot(cfg)
        if busy and not body.get("replace"):
            return 409, {"busy": True, "state": busy.get("state"), "name": display_name(busy)}
        if busy:
            _delete_session(cfg, busy["token"])
            logger.info("Check-in: patient %s session replaced on the iPad (was %s)",
                        busy.get("patient_id") or "new", busy.get("state"))
        s = {"token": secrets.token_hex(16), "created": _iso(), "state": "sent",
             "patient_id": pid, "appointment_id": aid, "optometrist_id": opt_id,
             "audience": audience, "prefill": prefill, "answers": {},
             "returning": bool(last_exam), "last_exam": last_exam or None,
             "skipped_screens": [], "submitted": None,
             "staff_overrides": {"rejected_fields": [], "edited": {}},
             "questions_version": None}
        _write_session(cfg, s)
    logger.info("Check-in: patient %s sent to the iPad", pid or "new")
    return 200, {"ok": True, "token": s["token"], "state": "sent", "name": display_name(s)}


def resume(cfg: dict, token) -> tuple[int, dict]:
    """Reception gives a paused form back to the iPad: it opens with the
    answers kept so far, at the screen the patient was on."""
    with _lock:
        s = load_session(cfg, token)
        if s is None:
            return 404, {"error": "That form isn't here any more."}
        if s.get("state") != "paused":
            return 409, {"error": "That form isn't paused."}
        busy = current_slot(cfg)
        if busy and busy["token"] != s["token"]:
            return 409, {"error": "The iPad has another form on it now."}
        s["state"] = "sent"
        s["resumed"] = _iso()
        _write_session(cfg, s)
    logger.info("Check-in: patient %s form resumed on the iPad", s.get("patient_id") or "new")
    return 200, {"ok": True, "state": "sent"}


def discard(cfg: dict, token) -> tuple[int, dict]:
    with _lock:
        s = load_session(cfg, token)
        if s is None:
            return 404, {"error": "That form isn't here any more."}
        _delete_session(cfg, s["token"])
    logger.info("Check-in: patient %s form discarded (was %s)",
                s.get("patient_id") or "new", s.get("state"))
    return 200, {"ok": True}


# ---------------------------------------------------------------------------
# Staff: check screen
# ---------------------------------------------------------------------------

def detail(cfg: dict, token) -> tuple[int, dict]:
    if not connected(cfg):
        return 200, {"connected": False, "error": NOT_CONNECTED}
    s = load_session(cfg, token)
    if s is None:
        return 404, {"error": "That form isn't here any more. It may have been saved, "
                              "discarded or replaced."}
    out = {"connected": True, "session": {**_summary(s),
                                          "optometrist_id": s.get("optometrist_id"),
                                          "overrides": s.get("staff_overrides") or {}}}
    if s.get("state") != "submitted":
        out["dry_run"] = True
        return 200, out
    plan = run_engine(cfg, "plan", "--session", f"{s['token']}.json")
    out["dry_run"] = plan.get("dry_run", True)
    out["fixture"] = bool(plan.get("fixture"))
    if plan.get("error"):
        out["error"] = plan["error"]
        return 200, out
    out["plan"] = plan
    out["optometrists"] = lists(cfg).get("optometrists") or []
    return 200, out


def override(cfg: dict, token, body: dict) -> tuple[int, dict]:
    """Apply one staff decision to the session, then re-plan."""
    body = body if isinstance(body, dict) else {}
    action = str(body.get("action") or "")
    with _lock:
        s = load_session(cfg, token)
        if s is None:
            return 404, {"error": "That form isn't here any more."}
        if s.get("state") != "submitted":
            return 409, {"error": "That form hasn't been filled in yet."}
        ov = s.setdefault("staff_overrides", {})
        rejected = set(ov.get("rejected_fields") or [])
        edited = dict(ov.get("edited") or {})
        field = str(body.get("field") or "")
        if action in ("accept", "reject", "edit") and not NAME_RE.match(field):
            return 400, {"error": "Unknown field."}
        if action == "accept":
            rejected.discard(field)
        elif action == "reject":
            rejected.add(field)
        elif action == "edit":
            value = body.get("value")
            if not isinstance(value, str) or len(value) > MAX_EDIT:
                return 400, {"error": "That value is too long."}
            if value.strip():
                edited[field] = value.strip()
            else:
                edited.pop(field, None)          # blank = back to the form's answer
        elif action == "optometrist":
            oid = _int(body.get("optometrist_id"))
            known = {o.get("id") for o in lists(cfg).get("optometrists") or []}
            if oid is None or (known and oid not in known):
                return 400, {"error": "Pick an optometrist from the list."}
            s["optometrist_id"] = oid
        elif action == "use_record":
            pid = _int(body.get("patient_id"))
            plan = run_engine(cfg, "plan", "--session", f"{s['token']}.json")
            dupes = {d.get("id") for d in plan.get("duplicates") or []}
            if pid is None or pid not in dupes:
                return 400, {"error": "That record isn't one of the matches."}
            s["patient_id"] = pid
            ov.pop("confirm_new_patient", None)
        elif action == "create_new":
            ov["confirm_new_patient"] = True
        else:
            return 400, {"error": "Unknown action."}
        ov["rejected_fields"] = sorted(rejected)
        ov["edited"] = edited
        s.pop("hub_test_saved", None)
        _write_session(cfg, s)
    return detail(cfg, token)


def save(cfg: dict, token, expect_hash, staff: str) -> tuple[int, dict]:
    """Ask the engine to save (dry run: the would-save summary). The hash is
    the one on the staff member's screen; a mismatch means re-check."""
    expect_hash = str(expect_hash or "").strip().lower()
    if not HASH_RE.match(expect_hash):
        return 400, {"error": "Reload the check screen first."}
    staff = re.sub(r"[^\w .'-]", "", str(staff or ""), flags=re.UNICODE).strip()[:60] \
        or "someone (no name picked)"
    with _lock:   # no override can land while the engine is saving
        s = load_session(cfg, token)
        if s is None:
            return 404, {"error": "That form isn't here any more."}
        if s.get("state") != "submitted":
            return 409, {"error": "That form hasn't been filled in yet."}
        result = run_engine(cfg, "save", "--session", f"{s['token']}.json",
                            "--expect-hash", expect_hash, "--staff", staff)
        if result.get("connected") is False:
            return 200, result
        if result.get("error") == CHANGED:
            return 409, {"changed": True, "error": CHANGED}

        # Re-read: the engine may have written the new patient's ID into the
        # file mid-save. A patient_id in the result is authoritative.
        s = load_session(cfg, token) or s
        new_pid = _int(result.get("patient_id"))
        if new_pid and s.get("patient_id") != new_pid:
            s["patient_id"] = new_pid

        if result.get("dry_run") is True and "would_save" in result:
            s["hub_test_saved"] = _iso()
            _write_session(cfg, s)
            logger.info("Check-in: patient %s test save (dry run, nothing written)",
                        s.get("patient_id") or "new")
            return 200, {**result, "test_saved": True}
        if result.get("dry_run") is False and result.get("saved") is True:
            _delete_session(cfg, s["token"])           # answers gone
            _add_marker(cfg, s, s.get("patient_id"))
            logger.info("Check-in: patient %s saved to Optomate", s.get("patient_id"))
            return 200, result
        _write_session(cfg, s)
        logger.info("Check-in: patient %s save not completed", s.get("patient_id") or "new")
        return 200, result



# ---------------------------------------------------------------------------
# iPad
# ---------------------------------------------------------------------------

def ipad_current(cfg: dict) -> dict:
    with _lock:
        purge(cfg)
        s = current_slot(cfg)
    if s and s["state"] == "paused":
        return {"state": "paused"}             # no token: the iPad cannot reopen it
    if s:
        return {"token": s["token"], "state": s["state"]}
    return {"state": "idle", "self": True} if self_checkin_on(cfg) else {"state": "idle"}


def _slot_session(cfg: dict, token) -> dict | None:
    s = load_session(cfg, token)
    return s if s and s.get("state") in IPAD_STATES else None


def _questions(cfg: dict, s: dict) -> dict:
    """The question set for this form. A returning patient (a previous exam
    here, v5) gets the returning set: 'Anything new since your last visit?'
    gates, and no new-patient questions (occupation, how heard, last exam)."""
    args = ["questions", "--audience", s["audience"]]
    if s.get("returning"):
        args.append("--returning")
    return run_engine(cfg, *args)


def ipad_session(cfg: dict, token) -> tuple[int, dict]:
    """Everything the iPad needs to show one form: the question set for this
    audience (rendered generically), pick lists, and the prefill."""
    s = _slot_session(cfg, token)
    if s is None:
        return 409, {"gone": True}
    qs = _questions(cfg, s)
    if qs.get("error") or not isinstance(qs.get("questions"), dict):
        return 200, {"error": qs.get("error") or "No questions."}
    needed = {q.get("list") for sc in qs["questions"].get("screens") or []
              for q in sc.get("questions") or [] if q.get("type") == "picklist"}
    pl = lists(cfg)
    with _lock:
        s2 = _slot_session(cfg, token)
        if s2 is None:
            return 409, {"gone": True}
        if s2.get("questions_version") != qs.get("version"):
            s2["questions_version"] = qs.get("version")
            _write_session(cfg, s2)
    out = {"token": s["token"], "state": s["state"], "audience": s["audience"],
           "name": greeting_name(s), "prefill": s.get("prefill") or {},
           "version": qs.get("version"), "questions": qs["questions"],
           "lists": {k: pl.get(k) or [] for k in needed if k in ("occupations", "sources")}}
    draft = s.get("draft")
    if s.get("resumed") and isinstance(draft, dict) and draft.get("version") == qs.get("version"):
        out["draft"] = {k: draft.get(k) for k in ("answers", "skipped", "idx")}
    return 200, out


def ipad_filling(cfg: dict, token) -> tuple[int, dict]:
    with _lock:
        s = _slot_session(cfg, token)
        if s is None:
            return 409, {"gone": True}
        if s["state"] == "sent":
            s["state"] = "filling"
            _write_session(cfg, s)
            logger.info("Check-in: patient %s filling on the iPad", s.get("patient_id") or "new")
    return 200, {"ok": True}


def _shown(q: dict, answers: dict, index: dict) -> bool:
    """Same rule as the engine's schema.is_shown."""
    nia = q.get("not_if_answered")
    if nia and answers.get(nia):
        return False
    for ref, want in (q.get("show_if") or {}).items():
        if ref in index and not _shown(index[ref], answers, index):
            return False
        got = answers.get(ref)
        wants = set(want) if isinstance(want, list) else {want}
        gots = set(got) if isinstance(got, list) else ({got} if got not in (None, "") else set())
        if not gots & wants:
            return False
    return True


def _clean_answers(raw) -> dict | None:
    if not isinstance(raw, dict) or len(raw) > MAX_ANSWERS:
        return None
    out = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not NAME_RE.match(k):
            return None
        if isinstance(v, str):
            out[k] = v.strip()[:MAX_TEXT]
        elif isinstance(v, list) and len(v) <= 60 and all(isinstance(x, str) for x in v):
            out[k] = [x.strip()[:200] for x in v if x.strip()]
        elif v is None:
            continue
        else:
            return None
    return out


def ipad_draft(cfg: dict, token, body: dict) -> tuple[int, dict]:
    """The iPad's answers so far, kept HERE (not on the iPad) so the inactivity
    lock can wipe the iPad and reception can resume. body: answers,
    skipped_screens, idx (the screen showing), pause (true = the lock).
    """
    body = body if isinstance(body, dict) else {}
    pause = body.get("pause") is True
    with _lock:
        s = load_session(cfg, token)
        if s is None or s.get("state") not in SLOT_STATES:
            return 409, {"gone": True}
        if s["state"] == "paused":
            return 200, {"ok": True, "state": "paused"}        # already locked: keep the draft
        if "answers" in body:
            answers = _clean_answers(body.get("answers"))
            skipped = body.get("skipped_screens") or []
            idx = body.get("idx")
            if answers is None or not isinstance(skipped, list) or len(skipped) > 30 or \
                    not all(isinstance(x, str) and NAME_RE.match(x) for x in skipped) or \
                    not (isinstance(idx, int) and -1 <= idx <= MAX_SCREEN_INDEX):
                return 400, {"error": "The answers could not be read."}
            s["draft"] = {"answers": answers, "skipped": sorted(set(skipped)), "idx": idx,
                          "version": s.get("questions_version"), "at": _iso()}
        if pause:
            s["state"] = "paused"
            s["paused"] = _iso()
            s.pop("resumed", None)
        _write_session(cfg, s)
    if pause:
        logger.info("Check-in: patient %s form paused on the iPad (left untouched)",
                    s.get("patient_id") or "new")
    return 200, {"ok": True, "state": s["state"]}



def _already_or_gone(cfg: dict, token) -> tuple[int, dict]:
    """Codex #11: the form was saved here but the iPad never heard back, so it
    sends again. Same token, already submitted (or since checked / saved /
    discarded by staff) = success, "received". Anything else = gone."""
    s = load_session(cfg, token)
    if (s and s.get("state") == "submitted") or was_received(cfg, token):
        return 200, {"ok": True, "already": True}
    return 409, {"gone": True}


def ipad_submit(cfg: dict, token, body: dict) -> tuple[int, dict]:
    body = body if isinstance(body, dict) else {}
    answers = _clean_answers(body.get("answers"))
    if answers is None:
        return 400, {"error": "The answers could not be read."}
    skipped = body.get("skipped_screens") or []
    if not isinstance(skipped, list) or len(skipped) > 30 or \
            not all(isinstance(x, str) and NAME_RE.match(x) for x in skipped):
        return 400, {"error": "The answers could not be read."}
    s = _slot_session(cfg, token)
    if s is None:
        return _already_or_gone(cfg, token)
    # Required questions (from questions.json), checked here as well as on the iPad.
    qs = _questions(cfg, s)
    screens = (qs.get("questions") or {}).get("screens") or []
    index = {q["id"]: q for sc in screens for q in sc.get("questions") or [] if q.get("id")}
    for sc in screens:
        if sc.get("id") in skipped and sc.get("skippable"):
            continue
        for q in sc.get("questions") or []:
            if not q.get("required") or q.get("type") == "info":
                continue
            if _shown(q, answers, index) and not answers.get(q["id"]):
                return 400, {"error": "Please fill in: " + str(q.get("label") or q["id"]),
                             "field": q["id"]}
    with _lock:
        s = _slot_session(cfg, token)
        if s is None:
            return _already_or_gone(cfg, token)
        s.update(answers=answers, skipped_screens=sorted(set(skipped)),
                 submitted=_iso(), state="submitted")
        s.pop("signature_png", None)          # a form begun before 9 Oct: none kept
        s.pop("draft", None)
        if qs.get("version"):
            s["questions_version"] = qs["version"]
        _write_session(cfg, s)
        _add_receipt(cfg, s["token"])
    logger.info("Check-in: patient %s form submitted from the iPad", s.get("patient_id") or "new")
    if auto_save_on(cfg) and s.get("patient_id"):
        _run_later(auto_save, cfg, s["token"])
    return 200, {"ok": True}


def _run_later(fn, *args) -> None:
    """The iPad gets its "Thank you" at once; the save runs behind it. (Tests
    swap this for a direct call.)"""
    threading.Thread(target=fn, args=args, daemon=True).start()


# ---------------------------------------------------------------------------
# Self check-in + automatic save (Mark, 13 Oct 2026, "option A": "it has to be
# almost seamless"). The iPad waits on "Check in here"; the patient types their
# surname and date of birth (or mobile); the engine matches it to ONE of today's
# bookings and the form opens - no staff step. When they press Send, the form
# saves itself unless something needs a person: a changed name, birth date,
# Medicare, mobile or email, a new record, an exam already open, or any engine
# warning. Those wait on the Check-in page as "Check" with the reason.
# Live writes are still held by the engine's own gate (agent .env): in dry run
# the automatic save is a TEST save and nothing reaches Optomate.
# ---------------------------------------------------------------------------

SELF_TRIES = 6                      # failed look-ups ...
SELF_WINDOW_S = 10 * 60             # ... per 10 minutes, then "please see reception"
SURNAME_MAX = 40
AUTO_STAFF = "iPad - automatic"     # who saved it, in the Optomate note (save() drops brackets)
# A change to any of these is never saved without a person looking first.
CHECK_FIELDS = {"given_name": "Name", "family_name": "Name", "dob": "Date of birth",
                "medicare_number": "Medicare", "medicare_ref": "Medicare",
                "medicare_expiry": "Medicare", "phone_mobile": "Mobile", "email": "Email"}

_self_fails: list[float] = []


def _flag(cfg: dict, key: str, default: bool = True) -> bool:
    v = ((cfg or {}).get("checkin") or {}).get(key)
    return default if v is None else v is True


def self_checkin_on(cfg: dict) -> bool:
    """config checkin.self_checkin (default on): the iPad's idle screen is
    "Check in here". false = back to "Please see reception"."""
    return _flag(cfg, "self_checkin")


def auto_save_on(cfg: dict) -> bool:
    """config checkin.auto_save (default on): a form with nothing to check saves
    itself when the patient presses Send."""
    return _flag(cfg, "auto_save")


def _self_locked(now: float) -> bool:
    _self_fails[:] = [t for t in _self_fails if now - t < SELF_WINDOW_S]
    return len(_self_fails) >= SELF_TRIES


SEE_RECEPTION = "Please see reception."


def ipad_self(cfg: dict, body: dict) -> tuple[int, dict]:
    """The patient's own look-up on the iPad. body: surname + mobile (the
    default, Mark 14 Oct 2026) or surname + dob (dd/mm/yyyy, the fallback).
    Answers carry no patient data except, on a match, the form's token (the
    form greets them by first name as it always has), or - for a family on one
    mobile - their first names to tap."""
    body = body if isinstance(body, dict) else {}
    if not self_checkin_on(cfg):
        return 403, {"error": SEE_RECEPTION}
    surname = " ".join(SEARCH_STRIP_RE.sub(" ", str(body.get("surname") or "")).split())[:SURNAME_MAX]
    dob = str(body.get("dob") or "").strip()
    mobile = re.sub(r"[^\d+]", "", str(body.get("mobile") or ""))[:16]
    if len(surname.replace(" ", "")) < 2:
        return 400, {"error": "Type your surname.", "field": "surname"}
    if dob and not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", dob):
        return 400, {"error": "Check the date of birth.", "field": "dob"}
    if not dob and len(re.sub(r"\D", "", mobile)) < 9:
        return 400, {"error": "Type your mobile number.", "field": "mobile"}
    now = time.time()
    with _lock:
        if _self_locked(now):
            return 429, {"error": SEE_RECEPTION, "locked": True}
        if current_slot(cfg):
            return 409, {"error": SEE_RECEPTION, "busy": True}
    args = ["match", "--surname", surname] + (["--dob", dob] if dob else ["--mobile", mobile])
    m = run_engine(cfg, *args)
    if m.get("error"):
        if m.get("connected") is False:
            return 200, {"error": SEE_RECEPTION}
        if dob:
            return 400, {"error": "Check the date of birth.", "field": "dob"}
        return 200, {"error": SEE_RECEPTION}
    count = int(m.get("count") or 0)
    hits = [(_int(h.get("appointment_id")), _int(h.get("patient_id"))) for h in m.get("matches") or []]
    hits = [(a, p) for a, p in hits if a and p]
    if count == 0:
        with _lock:
            _self_fails.append(now)
        logger.info("Check-in: iPad self check-in - no booking matched")
        return 200, {"found": False, "many": False}
    if count == 1 and len(hits) == 1:
        return _self_open(cfg, *hits[0])
    if 2 <= count <= PICK_MAX and len(hits) == count:
        return _offer_choice(cfg, hits, now)
    logger.info("Check-in: iPad self check-in - %d bookings matched, sent to reception", count)
    return 200, {"found": False, "many": True}


# A family on one mobile, booked the same day (14 Oct 2026: 2 of 22 patients' families):
# "Who's checking in?" with each person's FIRST name only - shown only to someone who
# already typed that family's surname AND mobile. The choice lives here, never on the iPad.
PICK_MAX = 3
PICK_TTL_S = 3 * 60
_picks: dict[str, dict] = {}


def _offer_choice(cfg: dict, hits: list, now: float) -> tuple[int, dict]:
    names = []
    for _aid, pid in hits:
        p = run_engine(cfg, "patient", "--id", str(pid))
        d = p.get("details") or {}
        first = str(d.get("preferred_name") or "").strip() or \
            (str(d.get("given_name") or "").split() or [""])[0]
        if p.get("error") or not first:
            return 200, {"found": False, "many": True}
        names.append(first)
    if len(set(n.casefold() for n in names)) != len(names):     # two "Sam"s: can't tell apart
        return 200, {"found": False, "many": True}
    pick = secrets.token_hex(16)
    with _lock:
        for k in [k for k, v in _picks.items() if now - v["at"] > PICK_TTL_S]:
            _picks.pop(k, None)
        _picks[pick] = {"at": now, "hits": hits}
    logger.info("Check-in: iPad self check-in - %d family members matched, asked who", len(hits))
    return 200, {"choose": [{"n": i, "name": n} for i, n in enumerate(names)], "pick": pick}


def ipad_pick(cfg: dict, body: dict) -> tuple[int, dict]:
    """The family member tapped on "Who's checking in?". body: pick, n."""
    body = body if isinstance(body, dict) else {}
    if not self_checkin_on(cfg):
        return 403, {"error": SEE_RECEPTION}
    pick = str(body.get("pick") or "")
    n = body.get("n")
    with _lock:
        p = _picks.pop(pick, None) if TOKEN_RE.match(pick) else None
    if p is None or time.time() - p["at"] > PICK_TTL_S:
        return 200, {"error": SEE_RECEPTION, "expired": True}
    if not isinstance(n, int) or not 0 <= n < len(p["hits"]):
        return 400, {"error": SEE_RECEPTION}
    with _lock:
        if current_slot(cfg):
            return 409, {"error": SEE_RECEPTION, "busy": True}
    return _self_open(cfg, *p["hits"][n])


def _self_open(cfg: dict, aid: int, pid: int) -> tuple[int, dict]:
    """Open this booking's form on the iPad (or say it's already done today)."""
    day = _now().date().isoformat()
    done = [s for s in all_sessions(cfg) if str(s.get("created") or "")[:10] == day
            and s.get("appointment_id") == aid and s.get("state") in ("submitted", "saved")]
    if done or any(r.get("appointment_id") == aid for r in _markers(cfg, day)):
        logger.info("Check-in: patient %s self check-in - already done today", pid)
        return 200, {"done": True}
    code, out = send(cfg, {"kind": "appointment", "appointment_id": aid})
    if code != 200 or not out.get("token"):
        return 200, {"error": SEE_RECEPTION}
    with _lock:
        s = load_session(cfg, out["token"])
        if s is not None:
            s["via"] = "self"
            _write_session(cfg, s)
    logger.info("Check-in: patient %s checked themselves in on the iPad", pid)
    return 200, {"ok": True, "token": out["token"]}


def _check_reasons(plan: dict) -> list[str]:
    """Why a person must look at this form before it saves. [] = save it."""
    reasons: list[str] = []
    if plan.get("error"):
        return ["Couldn't check it automatically"]
    if plan.get("is_new_patient"):
        reasons.append("New patient")
    for c in plan.get("patient_changes") or []:
        word = CHECK_FIELDS.get(c.get("field"))
        if word and c.get("accepted", True) and word not in reasons:
            reasons.append(f"{word} changed" if word != "Name" else "Name changed")
    if plan.get("duplicates"):
        reasons.append("May already be in Optomate")
    if (plan.get("exam") or {}).get("action") == "exists":
        reasons.append("Exam already open")
    if plan.get("blockers") or not plan.get("can_save"):
        reasons.append("Needs a staff decision")
    elif plan.get("warnings") and not reasons:
        reasons.append("Something to look at")
    return reasons


def auto_save(cfg: dict, token) -> None:
    """Run straight after a form is submitted (background thread). Saves it -
    a TEST save in dry run - when nothing needs a person, else records why."""
    s = load_session(cfg, token)
    if s is None or s.get("state") != "submitted":
        return
    plan = run_engine(cfg, "plan", "--session", f"{s['token']}.json")
    reasons = _check_reasons(plan)
    if not reasons:
        code, result = save(cfg, token, plan.get("hash"), AUTO_STAFF)
        if code == 200 and (result.get("saved") is True or result.get("test_saved")):
            if result.get("test_saved"):
                with _lock:
                    s2 = load_session(cfg, token)
                    if s2 is not None:
                        s2["auto"] = {"at": _iso(), "result": "test_saved"}
                        _write_session(cfg, s2)
            logger.info("Check-in: patient %s saved automatically%s", s.get("patient_id"),
                        " (test - nothing written)" if result.get("test_saved") else "")
            return
        reasons = ["Didn't save - check it"]
    with _lock:
        s2 = load_session(cfg, token)
        if s2 is not None and s2.get("state") == "submitted":
            s2["check_reasons"] = reasons[:4]
            _write_session(cfg, s2)
    logger.info("Check-in: patient %s left for staff to check (%d reason%s)",
                s.get("patient_id"), len(reasons), "" if len(reasons) == 1 else "s")
