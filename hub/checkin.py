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

Session states: sent -> filling -> submitted -> saved | discarded.
- One iPad slot: the session in state sent/filling. Sending another asks first,
  then the old one is discarded.
- discarded = the file is deleted at once (answers and signature go with it).
- A LIVE save that the engine reports as saved deletes the file at once; the
  engine keeps the signed PDF and the journal. A small marker (IDs only) lets
  today's list say "Saved".
- In dry run the session is kept so it can be checked again; staff discard it.
- Files older than 24 hours that are not `submitted` are purged.
"""

import base64
import binascii
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
SIG_PREFIX = "data:image/png;base64,"

AUDIENCES = ("adult", "child")
SLOT_STATES = ("sent", "filling")
STATUS_WORDS = {None: "Not sent", "sent": "On the iPad", "filling": "On the iPad",
                "submitted": "Ready to check", "saved": "Saved", "discarded": "Not sent"}

PURGE_AFTER = timedelta(hours=24)
LISTS_MAX_AGE_S = 600
MAX_SIGNATURE_CHARS = 2_000_000
MAX_ANSWERS = 300
MAX_TEXT = 2000
MAX_EDIT = 200
CHANGED = "The record changed - check it again"

# The engine's live-write gate lives in ONE place: the agent's own .env. Strip
# these from what the Hub hands the subprocess so nothing in the Hub's
# environment can switch the engine to live, or point it at another data folder
# than the one this module reads and writes.
_ENGINE_ONLY_ENV = ("CHECKIN_DRY_RUN", "CHECKIN_LIVE_WRITES", "CHECKIN_DATA_DIR")

TIMEOUTS = {"save": 240, "pdf": 120}
DEFAULT_TIMEOUT = 60

NOT_CONNECTED = "The check-in engine isn't connected on this computer yet."

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
    """Delete session files older than 24 hours that are not waiting to be
    checked (`submitted`), plus stray temp files. Returns how many went."""
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
    """The one session the iPad should be showing (sent / filling), if any."""
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


def _summary(s: dict) -> dict:
    return {"token": s["token"], "state": s.get("state"),
            "status": STATUS_WORDS.get(s.get("state"), "Not sent"),
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
        rows.append({**a, "name": " ".join(x for x in (a.get("given"), a.get("surname")) if x),
                     "audience": audience, "state": state,
                     "status": STATUS_WORDS.get(state, "Not sent"),
                     "token": s["token"] if s and state not in ("saved", "discarded") else None})
    waiting = [_summary(s) for s in sessions
               if s.get("state") == "submitted" and s["token"] not in used]
    slot = current_slot(cfg)
    return {"connected": True, "date": day, "appointments": rows, "waiting": waiting,
            "ipad": _summary(slot) if slot else None,
            "ipad_ready": bool(ipad_key(cfg)),
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
             "skipped_screens": [], "signature_png": "", "submitted": None,
             "staff_overrides": {"rejected_fields": [], "edited": {}},
             "questions_version": None}
        _write_session(cfg, s)
    logger.info("Check-in: patient %s sent to the iPad", pid or "new")
    return 200, {"ok": True, "token": s["token"], "state": "sent", "name": display_name(s)}


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
            _delete_session(cfg, s["token"])           # answers + signature gone
            _add_marker(cfg, s, s.get("patient_id"))
            logger.info("Check-in: patient %s saved to Optomate", s.get("patient_id"))
            return 200, result
        _write_session(cfg, s)
        logger.info("Check-in: patient %s save not completed", s.get("patient_id") or "new")
        return 200, result


def pdf_file(cfg: dict, token) -> tuple[Path | None, str]:
    """Build the signed PDF for a submitted form and return its path, only if
    it lies inside the engine's checkin folder."""
    s = load_session(cfg, token)
    if s is None:
        return None, "That form isn't here any more."
    if s.get("state") != "submitted":
        return None, "That form hasn't been signed yet."
    data = run_engine(cfg, "pdf", "--session", f"{s['token']}.json")
    if data.get("error"):
        return None, str(data["error"])
    return confine_pdf(cfg, data.get("pdf")), "The signed form couldn't be found."


def confine_pdf(cfg: dict, path_text) -> Path | None:
    root = checkin_root(cfg)
    if root is None or not path_text:
        return None
    try:
        p = Path(str(path_text)).resolve()
        p.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return p if p.suffix.lower() == ".pdf" and p.is_file() else None


# ---------------------------------------------------------------------------
# iPad
# ---------------------------------------------------------------------------

def ipad_current(cfg: dict) -> dict:
    with _lock:
        purge(cfg)
        s = current_slot(cfg)
    return {"token": s["token"], "state": s["state"]} if s else {"state": "idle"}


def _slot_session(cfg: dict, token) -> dict | None:
    s = load_session(cfg, token)
    return s if s and s.get("state") in SLOT_STATES else None


def ipad_session(cfg: dict, token) -> tuple[int, dict]:
    """Everything the iPad needs to show one form: the question set for this
    audience (rendered generically), pick lists, and the prefill."""
    s = _slot_session(cfg, token)
    if s is None:
        return 409, {"gone": True}
    qs = run_engine(cfg, "questions", "--audience", s["audience"])
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
    return 200, {"token": s["token"], "state": s["state"], "audience": s["audience"],
                 "name": greeting_name(s), "prefill": s.get("prefill") or {},
                 "version": qs.get("version"), "questions": qs["questions"],
                 "lists": {k: pl.get(k) or [] for k in needed if k in ("occupations", "sources")}}


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


def _signature_ok(sig) -> bool:
    if not isinstance(sig, str) or not sig.startswith(SIG_PREFIX):
        return False
    if len(sig) > MAX_SIGNATURE_CHARS or len(sig) < len(SIG_PREFIX) + 40:
        return False
    try:
        head = base64.b64decode(sig[len(SIG_PREFIX):], validate=True)[:8]
    except (binascii.Error, ValueError):
        return False
    return head == b"\x89PNG\r\n\x1a\n"


def ipad_submit(cfg: dict, token, body: dict) -> tuple[int, dict]:
    body = body if isinstance(body, dict) else {}
    answers = _clean_answers(body.get("answers"))
    if answers is None:
        return 400, {"error": "The answers could not be read."}
    skipped = body.get("skipped_screens") or []
    if not isinstance(skipped, list) or len(skipped) > 30 or \
            not all(isinstance(x, str) and NAME_RE.match(x) for x in skipped):
        return 400, {"error": "The answers could not be read."}
    sig = body.get("signature_png")
    if not _signature_ok(sig):
        return 400, {"error": "Please sign the form.", "field": "signature"}

    s = _slot_session(cfg, token)
    if s is None:
        return 409, {"gone": True}
    # Required questions (from questions.json), checked here as well as on the iPad.
    qs = run_engine(cfg, "questions", "--audience", s["audience"])
    screens = (qs.get("questions") or {}).get("screens") or []
    index = {q["id"]: q for sc in screens for q in sc.get("questions") or [] if q.get("id")}
    for sc in screens:
        if sc.get("id") in skipped and sc.get("skippable"):
            continue
        for q in sc.get("questions") or []:
            if not q.get("required") or q.get("type") in ("signature", "info"):
                continue
            if _shown(q, answers, index) and not answers.get(q["id"]):
                return 400, {"error": "Please fill in: " + str(q.get("label") or q["id"]),
                             "field": q["id"]}
    with _lock:
        s = _slot_session(cfg, token)
        if s is None:
            return 409, {"gone": True}
        s.update(answers=answers, skipped_screens=sorted(set(skipped)), signature_png=sig,
                 submitted=_iso(), state="submitted")
        if qs.get("version"):
            s["questions_version"] = qs["version"]
        _write_session(cfg, s)
    logger.info("Check-in: patient %s form submitted from the iPad", s.get("patient_id") or "new")
    return 200, {"ok": True}
