"""Send guide tile - email approved patient guides from info@ after a visit.

Item GUIDE-SEND-AFTER-COLLECTION-1101 (Mark, 1 Nov 2026; Codex review 1 Nov).
Staff pick a patient (today's appointments or a name search), tick one or more
approved guides and press Send. The family gets ONE email from info@ with the
guide PDF(s) attached. Until Mark says "turn on live guide sends", Send makes a
Gmail DRAFT in info@ instead (two-key gate, see hub/guide_bridge.py).

WHAT CAN BE SENT. Only the guides in config/guides.manifest.json - never a
folder scan. Each PDF sits in send_guide.guides_dir and its SHA-256 is checked
on the exact bytes attached; a mismatch refuses that guide (fail closed).

WHO IT GOES TO. Always the email on the Optomate record, read fresh on Send
(the browser's copy is never trusted). Optomate's NOEMAIL ("no email") or
IS_BADEMAIL flag, a missing email or a missing date of birth blocks the send.
Under 16 on the day = the address is shown as the parent/guardian's and the
email greets the parent neutrally, naming the child (never greeting the child).

REVIEW, NOT A TICK (Mark, 1 Nov). No confirm box: the same screen shows a
read-only panel - patient + DOB, the address and whose it is, guide titles and
versions, a repeat warning if the same guide went to this patient in the last
30 days - and Send is the only click.

SEND HISTORY. A private SQLite file OUTSIDE every repo and sync folder
(send_guide.history_db, default C:\\CEC\\runtime\\hub-send-guide\\). One row per
send: operation id, idempotency key, patient id, destination, guide ids + the
exact hashes, template version, staff name, times, mode, outcome, Gmail id.
Never deleted by the Hub (health-record retention); 30 days is only the
repeat-warning window. The row is written BEFORE Gmail is called, so a timeout
is recorded as "unknown", not retried blindly.

DOUBLE CLICKS. Every Send carries an idempotency key from the page; the same
key again returns the first result, never a second draft.

PRIVACY. Optomate is only read (GET). hub.log gets operation and patient ids
and the outcome - never a name, an address or which guide. Names travel in POST
bodies, never URLs.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from hub import checkin

logger = logging.getLogger("cec-hub.sendguide")

BASE_DIR = Path(__file__).resolve().parent.parent
BRIDGE = Path(__file__).resolve().with_name("guide_bridge.py")
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "send-guide-email.txt"
DEFAULT_MANIFEST = BASE_DIR / "config" / "guides.manifest.json"
# Mark 1 Nov 2026: "a" = green bar, circle logo, 3 lines (his pick after trying C2).
# "c2" = long wordmark over two contact lines; "b" and "tall" also kept.
SIGNATURE_STYLE = "a"
SIGNATURE_LOGOS = {"c2": "cec-logo-long.png"}   # long wordmark, 186x28 at 2x; every other style: the circle
LOGO_PATH = BASE_DIR / "static" / "img" / SIGNATURE_LOGOS.get(SIGNATURE_STYLE, "cec-logo-email.png")
LOGO_CID = "cec-logo"
DEFAULT_GUIDES_DIR = r"C:\CEC\runtime\hub-guides"
DEFAULT_HISTORY_DB = r"C:\CEC\runtime\hub-send-guide\send-history.sqlite3"

REPEAT_DAYS = 30
ADULT_AGE = 16   # Mark 1 Nov 2026: child = under 16, same as Check-in
MAX_GUIDES = 6
SEND_CLASSES = ("care", "decision")          # never "marketing" / "print-only"
GUIDE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_FILE_RE = re.compile(r"^[A-Za-z0-9 ._()\-]{1,120}\.pdf$")
# A guide title is used verbatim in the subject, the body AND the attachment
# name, so it must be safe as all three: no control characters (header
# injection), nothing Windows/Gmail refuse in a file name, no leading/trailing
# space or dot. Letters of any script, apostrophes and "&" are fine.
TITLE_RE = re.compile(r"^(?![ .])[^\x00-\x1f\x7f<>:\"/\\|?*]{1,100}(?<![ .])$")
ATTACH_PREFIX = "Concord Eyecare - "
SUBJECT_MAX = 120
IDEM_RE = re.compile(r"^[A-Za-z0-9-]{16,64}$")
LANG_RE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2,4})?$")    # en, zh, ko, zh-Hant ...
LANG_NAMES = {"en": "English", "zh": "Chinese", "ko": "Korean"}
EMAIL_RE = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+$")

# The live gate is read ONLY by the bridge, from the agent's .env. The Hub
# strips these names from what it hands the bridge so nothing here can flip it.
_GATE_ENV = ("GUIDE_DRAFT_MODE", "GUIDE_LIVE_SEND")
TIMEOUTS = {"deliver": 120}
DEFAULT_TIMEOUT = 60

NOT_CONNECTED = "Send guide isn't connected on this computer (no Optomate agent folder)."
UNKNOWN_WORDS = ("Gmail didn't answer in time, so we can't tell if it worked. "
                 "Look in info@ (Drafts and Sent) before sending again.")

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Config + paths
# ---------------------------------------------------------------------------

def _sg(cfg: dict) -> dict:
    return (cfg or {}).get("send_guide") or {}


def manifest_path(cfg: dict) -> Path:
    return Path(os.getenv("CEC_HUB_GUIDE_MANIFEST") or _sg(cfg).get("manifest") or DEFAULT_MANIFEST)


def guides_dir(cfg: dict) -> Path:
    return Path(_sg(cfg).get("guides_dir") or DEFAULT_GUIDES_DIR)


def _unsafe_store(p: Path, cfg: dict) -> str:
    """'' when the history file is somewhere private; else why not. It must
    sit outside the Hub and the agent folders (both are git repos) and outside
    any cloud-sync folder (Codex 1 Nov 2026, finding 2)."""
    rp = p.resolve()
    roots = [BASE_DIR]
    agent = checkin.agent_dir(cfg)
    if agent:
        roots.append(agent)
    for root in roots:
        try:
            rp.relative_to(Path(root).resolve())
            return "The send history must live outside the Hub and agent folders."
        except ValueError:
            pass
    low = str(rp).lower()
    if any(word in low for word in ("onedrive", "google drive", "dropbox", "icloud")):
        return "The send history must not live in a cloud-sync folder."
    return ""


def history_path(cfg: dict) -> Path:
    return Path(_sg(cfg).get("history_db") or DEFAULT_HISTORY_DB)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def email_filename(g: dict) -> str:
    """The attachment name, made from the manifest title and nothing else, so
    subject, body and file name can never drift apart (1 Nov 2026: a separate
    email_filename field still said 'Collecting' after the title changed)."""
    return f"{ATTACH_PREFIX}{g['title']}.pdf"


def load_manifest(cfg: dict) -> list[dict]:
    """Validated manifest entries (active and inactive). A malformed entry is
    dropped, never guessed at. The title is the ONE name for the guide: an old
    'email_filename' field, if present, is ignored."""
    try:
        data = json.loads(manifest_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Send guide: manifest missing or unreadable")
        return []
    out, seen = [], set()
    for g in (data.get("guides") or []) if isinstance(data, dict) else []:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or "")
        ok = (GUIDE_ID_RE.match(gid) and gid not in seen
              and isinstance(g.get("title"), str) and TITLE_RE.match(g["title"])
              and SAFE_FILE_RE.match(str(g.get("path") or ""))
              and HEX64_RE.match(str(g.get("sha256") or ""))
              and str(g.get("approval_ref") or "").strip()
              and LANG_RE.match(str(g.get("language") or ""))
              and isinstance(g.get("active"), bool))
        if not ok:
            logger.warning("Send guide: manifest entry %r ignored (incomplete)", gid[:40])
            continue
        seen.add(gid)
        out.append(g)
    return out


def guide_bytes(cfg: dict, g: dict) -> tuple[bytes | None, str]:
    """The guide's PDF bytes if they are exactly the approved file, else
    (None, plain-words reason). Fail closed on every doubt."""
    if not g.get("active"):
        return None, "This guide has been withdrawn."
    if g.get("classification") not in SEND_CLASSES:
        return None, "This guide is print-only."
    base = guides_dir(cfg).resolve()
    p = (base / str(g["path"])).resolve()
    if p.parent != base:
        return None, "This guide's file isn't in the guides folder."
    try:
        data = p.read_bytes()
    except OSError:
        return None, "This guide's file is missing. Ask Mark."
    if hashlib.sha256(data).hexdigest() != g["sha256"]:
        return None, "This guide's file has changed since it was approved. Ask Mark."
    return data, ""


def _lang_name(code: str) -> str:
    return LANG_NAMES.get(code.split("-")[0], code)


def guides_for_page(cfg: dict) -> list[dict]:
    out = []
    for g in load_manifest(cfg):
        if not g.get("active"):
            continue
        _, problem = guide_bytes(cfg, g)
        out.append({"id": g["id"], "title": g["title"], "version": g.get("version") or "",
                    "stage": str(g.get("stage") or "").title(), "language": g["language"],
                    "language_name": _lang_name(g["language"]),
                    "ok": not problem, "problem": problem})
    return out


# ---------------------------------------------------------------------------
# Bridge subprocess (Optomate read + Gmail)
# ---------------------------------------------------------------------------

def connected(cfg: dict) -> bool:
    d = checkin.agent_dir(cfg)
    return bool(d and (d / "optomate").is_dir() and (d / "gmailer").is_dir())


def _bridge_env() -> dict:
    env = dict(os.environ)          # CHECKIN_FIXTURE / GUIDE_FIXTURE pass through
    for k in _GATE_ENV:
        env.pop(k, None)
    return env


def run_bridge(cfg: dict, *args: str, stdin: str | None = None) -> dict:
    """Run hub/guide_bridge.py in the agent folder and return its JSON answer.
    Validated arguments only. Never raises."""
    if not connected(cfg):
        return {"connected": False, "error": NOT_CONNECTED}
    python = (cfg.get("optomate_agent") or {}).get("python") or sys.executable
    cmd = args[0] if args else ""
    try:
        proc = subprocess.run(
            [python, str(BRIDGE), *args], cwd=str(checkin.agent_dir(cfg)),
            input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUTS.get(cmd, DEFAULT_TIMEOUT), env=_bridge_env())
    except subprocess.TimeoutExpired:
        if cmd == "deliver":
            return {"error": UNKNOWN_WORDS, "timeout": True}
        return {"error": "That took too long - is Optomate running on the server?"}
    except OSError:
        return {"connected": False, "error": "Couldn't start the Send guide helper on this computer."}
    line = next((c.strip() for c in reversed((proc.stdout or "").splitlines()) if c.strip()), "")
    try:
        data = json.loads(line) if line else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {"error": "The Send guide helper didn't give a readable answer.",
                "unreadable": True}
    return data


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

def _iso_date(s) -> date | None:
    try:
        return date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def age_on(dob_iso, today: date) -> int | None:
    d = _iso_date(dob_iso)
    if d is None or d > today:
        return None
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def dob_display(dob_iso) -> str:
    d = _iso_date(dob_iso)
    return d.strftime("%d/%m/%Y") if d else ""


def _tidy_name(s) -> str:
    s = re.sub(r"[\x00-\x1f\x7f{}]", "", str(s or "")).strip()[:40]
    return s.title() if s and (s.isupper() or s.islower()) else s


def first_name(p: dict) -> str:
    """Preferred name, else the first given name (as Check-in greets)."""
    nick = _tidy_name(p.get("nickname"))
    if nick:
        return nick
    given = _tidy_name(p.get("given")).split()
    return given[0] if given else ""


def full_name(p: dict) -> str:
    """Staff screens show the name exactly as Optomate holds it."""
    clean = (re.sub(r"[\x00-\x1f\x7f]", "", str(p.get(k) or "")).strip()[:60]
             for k in ("given", "surname"))
    return " ".join(x for x in clean if x)


# ---------------------------------------------------------------------------
# Email template (fixed copy, never model-written)
# ---------------------------------------------------------------------------

def load_template(path: Path | None = None) -> dict:
    sections: dict[str, list[str]] = {}
    current = None
    for line in (path or TEMPLATE_PATH).read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        m = re.match(r"^\[([a-z0-9_]+)\]\s*$", line)
        if m:
            current = m.group(1)
            sections[current] = []
        elif current:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


_P = "margin:0 0 14px 0;"


def _html_paragraphs(text: str) -> str:
    """The plain-text body as HTML, word for word: blank-line paragraphs become
    <p>, a paragraph made only of "- " lines becomes a list. Every character is
    escaped, so a name or title can never become markup."""
    out = []
    for para in text.split("\n\n"):
        lines = [ln for ln in para.split("\n")]
        if lines and all(ln.startswith("- ") for ln in lines):
            items = "".join(f'<li style="margin:0 0 4px 0;">{html.escape(ln[2:])}</li>' for ln in lines)
            out.append(f'<ul style="margin:0 0 14px 0;padding:0 0 0 22px;">{items}</ul>')
        else:
            out.append(f'<p style="{_P}">' + "<br>".join(html.escape(ln) for ln in lines) + "</p>")
    return "\n".join(out)


def subject_for(guides: list[dict], t: dict) -> str:
    """Guide titles in the order ticked, then ' | Concord Eyecare' (Mark,
    1 Nov 2026); the neutral fallback when that would pass 120 characters."""
    subject = t["subject"].format(guide_titles=", ".join(g["title"] for g in guides))
    if not guides or len(subject) > SUBJECT_MAX:
        return t["subject_fallback"]
    return subject


def compose(p: dict, guides: list[dict], today: date, template: dict | None = None,
            signature: str | None = None) -> dict:
    """Subject, plain-text body and HTML body from the fixed template. Code
    fills in names and guide titles only; the HTML is the same words, escaped,
    then the house signature."""
    t = template or load_template()
    minor = (age_on(p.get("dob"), today) or 0) < ADULT_AGE
    intro_fills = {
        "guide_title": guides[0]["title"] if guides else "",
        "guide_list": "\n".join(f"- {g['title']}" for g in guides),
        "for_child": f" for {first_name(p)}" if minor else "",
    }
    intro = (t["intro_many"] if len(guides) > 1 else t["intro_one"]).format(**intro_fills)
    main = (t["child"] if minor else t["adult"]).format(first_name=first_name(p), intro=intro)
    body = main + "\n\n" + t["closing"] + "\n\n" + t["signature"] + "\n"
    html_body = (
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>\n'
        '<body style="margin:0;padding:0;">\n'
        '<div style="max-width:560px;font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'line-height:22px;color:#222222;">\n'
        + _html_paragraphs(main) + "\n"
        + f'<p style="margin:0 0 18px 0;">{html.escape(t["closing"])}</p>\n</div>\n'
        # the signature sits outside the 560px text column so the wide styles fit
        + t["signature_html_" + (signature or SIGNATURE_STYLE)] + "\n</body></html>\n")
    return {"subject": subject_for(guides, t), "body": body, "html": html_body,
            "version": t.get("version", ""), "minor": minor}


def logo_inline() -> dict:
    """The signature logo as an inline (CID) image for the bridge."""
    data = LOGO_PATH.read_bytes()
    return {"cid": LOGO_CID, "filename": "concord-eyecare-logo.png",
            "sha256": hashlib.sha256(data).hexdigest(),
            "data_b64": base64.b64encode(data).decode("ascii")}


# ---------------------------------------------------------------------------
# Send history (private SQLite, outside every repo)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sends (
    op_id TEXT PRIMARY KEY,
    idem_key TEXT NOT NULL UNIQUE,
    patient_id INTEGER NOT NULL,
    dest_email TEXT NOT NULL,
    recipient TEXT NOT NULL,
    template_version TEXT,
    basis TEXT,
    staff TEXT,
    repeat_warned INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL,
    finished TEXT,
    mode TEXT,
    outcome TEXT NOT NULL,
    gmail_id TEXT,
    error_code TEXT
);
CREATE TABLE IF NOT EXISTS send_guides (
    op_id TEXT NOT NULL,
    guide_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (op_id, guide_id)
);
CREATE INDEX IF NOT EXISTS sends_patient ON sends (patient_id, created);
"""

LIVE_OUTCOMES = ("sending", "draft", "sent", "unknown")   # count for the repeat warning


class History:
    def __init__(self, cfg: dict):
        self.path = history_path(cfg)
        why = _unsafe_store(self.path, cfg)
        if why:
            raise RuntimeError(why)

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path), timeout=10)
        con.row_factory = sqlite3.Row
        con.executescript(_SCHEMA)
        return con

    def by_key(self, key: str) -> dict | None:
        with self._db() as con:
            row = con.execute("SELECT * FROM sends WHERE idem_key = ?", (key,)).fetchone()
            if row is None:
                return None
            out = dict(row)
            out["guide_ids"] = sorted(r["guide_id"] for r in con.execute(
                "SELECT guide_id FROM send_guides WHERE op_id = ?", (row["op_id"],)))
        return out

    def begin(self, rec: dict, guides: list[dict]) -> bool:
        """Write the attempt before Gmail is called. False if the key is taken."""
        try:
            with self._db() as con:
                con.execute(
                    "INSERT INTO sends (op_id, idem_key, patient_id, dest_email, recipient, "
                    "template_version, basis, staff, repeat_warned, created, outcome) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?, 'sending')",
                    (rec["op_id"], rec["idem_key"], rec["patient_id"], rec["dest_email"],
                     rec["recipient"], rec["template_version"], rec["basis"], rec["staff"],
                     int(rec["repeat_warned"]), rec["created"]))
                con.executemany("INSERT INTO send_guides (op_id, guide_id, sha256) VALUES (?,?,?)",
                                [(rec["op_id"], g["id"], g["sha256"]) for g in guides])
        except sqlite3.IntegrityError:
            return False
        return True

    def finish(self, op_id: str, outcome: str, mode: str | None, gmail_id: str = "",
               error_code: str = "") -> None:
        with self._db() as con:
            con.execute("UPDATE sends SET outcome = ?, mode = ?, gmail_id = ?, error_code = ?, "
                        "finished = ? WHERE op_id = ?",
                        (outcome, mode, gmail_id[:200], error_code[:60], _now_iso(), op_id))

    def recent(self, patient_id: int, guide_ids: list[str], now: datetime) -> list[dict]:
        if not guide_ids or not self.path.exists():
            return []
        since = (now - timedelta(days=REPEAT_DAYS)).isoformat(timespec="seconds")
        q = ("SELECT g.guide_id, s.created, s.outcome FROM sends s JOIN send_guides g "
             "ON g.op_id = s.op_id WHERE s.patient_id = ? AND s.created >= ? AND s.outcome IN "
             f"({','.join('?' * len(LIVE_OUTCOMES))}) AND g.guide_id IN "
             f"({','.join('?' * len(guide_ids))}) ORDER BY s.created DESC")
        with self._db() as con:
            rows = con.execute(q, (patient_id, since, *LIVE_OUTCOMES, *guide_ids)).fetchall()
        latest: dict[str, dict] = {}
        for r in rows:
            latest.setdefault(r["guide_id"], {"guide_id": r["guide_id"], "when": r["created"],
                                              "outcome": r["outcome"]})
        return list(latest.values())


def _now() -> datetime:
    return datetime.now().astimezone()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Page data
# ---------------------------------------------------------------------------

def setup(cfg: dict) -> dict:
    """Guides list + mode for the page. No patient data."""
    if not connected(cfg):
        return {"connected": False, "message": NOT_CONNECTED, "guides": guides_for_page(cfg)}
    m = run_bridge(cfg, "mode")
    return {"connected": True, "guides": guides_for_page(cfg),
            "live": bool(m.get("live")) and not m.get("error"),
            "fixture": bool(m.get("fixture"))}


def today(cfg: dict) -> dict:
    """Today's appointments, via the Check-in engine (hub/checkin.py's reader)."""
    if not checkin.connected(cfg):
        return {"connected": False, "message": NOT_CONNECTED}
    data = checkin.run_engine(cfg, "today")
    if data.get("error"):
        return {"connected": True, "error": data["error"]}
    rows = [{"patient_id": a.get("patient_id"), "time": a.get("time") or "",
             "name": " ".join(x for x in (a.get("given"), a.get("surname")) if x)}
            for a in data.get("appointments") or [] if a.get("patient_id")]
    return {"connected": True, "appointments": rows, "fixture": bool(data.get("fixture"))}


def search(cfg: dict, q) -> dict:
    from hub import guide_search
    ts = guide_search.terms(q)
    if not ts:
        return {"error": "Type at least two letters of the name."}
    data = run_bridge(cfg, "search", "--q", " ".join(ts))
    if data.get("error"):
        return {"error": data["error"]}
    return {"patients": [{"id": p.get("id"), "given": str(p.get("given") or "")[:60],
                          "surname": str(p.get("surname") or "")[:60],
                          "dob": dob_display(p.get("dob"))}
                         for p in data.get("patients") or []],
            "more": bool(data.get("more"))}


# ---------------------------------------------------------------------------
# Review + send
# ---------------------------------------------------------------------------

def _pid(v) -> int | None:
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if i > 0 else None


def _guide_ids(v) -> list[str] | None:
    if not isinstance(v, list) or len(v) > MAX_GUIDES:
        return None
    ids = []
    for x in v:
        x = str(x or "")
        if not GUIDE_ID_RE.match(x):
            return None
        if x not in ids:
            ids.append(x)
    return ids


def _email_block(p: dict) -> str:
    if p.get("noemail"):
        return "Optomate says no email for this patient. Give the printed guide instead."
    if p.get("bademail"):
        return "Optomate marks this email as not working. Fix it in Optomate first."
    if not EMAIL_RE.match(str(p.get("email") or "")):
        return "No email in Optomate. Add it there first, then pick the patient again."
    return ""


def _review(cfg: dict, pid: int, ids: list[str], today_d: date, now: datetime) -> dict:
    """Everything the panel shows and Send needs. Reads Optomate fresh."""
    data = run_bridge(cfg, "patient", "--id", str(pid))
    if data.get("error"):
        return {"error": data["error"]}
    p = data.get("patient") or {}
    if _pid(p.get("patient_id")) != pid:
        return {"error": "Optomate gave back a different patient. Pick again."}
    blocks = []
    age = age_on(p.get("dob"), today_d)
    if age is None:
        blocks.append("No date of birth in Optomate. Add it there first.")
    minor = age is not None and age < ADULT_AGE
    eblock = _email_block(p)
    if eblock:
        blocks.append(eblock)

    manifest = {g["id"]: g for g in load_manifest(cfg)}
    guides, ready = [], []
    for gid in ids:
        g = manifest.get(gid)
        if g is None:
            guides.append({"id": gid, "title": gid, "version": "", "ok": False,
                           "problem": "This guide isn't on the approved list."})
            continue
        data_bytes, problem = guide_bytes(cfg, g)
        guides.append({"id": gid, "title": g["title"], "version": g.get("version") or "",
                       "language": g["language"], "language_name": _lang_name(g["language"]),
                       "ok": not problem, "problem": problem})
        if not problem:
            ready.append((g, data_bytes))
    if any(not g["ok"] for g in guides):
        blocks.append("A guide can't be sent: " +
                      "; ".join(f"{g['title']}: {g['problem']}" for g in guides if not g["ok"]))

    repeats = []
    if ids:
        titles = {g["id"]: g["title"] for g in guides}
        for r in History(cfg).recent(pid, ids, now):
            repeats.append({"guide_id": r["guide_id"], "title": titles.get(r["guide_id"], ""),
                            "when": r["when"][:10], "outcome": r["outcome"]})
    return {
        "patient": {"id": pid, "name": full_name(p), "dob": dob_display(p.get("dob")),
                    "age": age, "minor": minor},
        "to": {"email": "" if eblock else p.get("email"),
               "label": "Parent/guardian's email" if minor else "Patient's own email"},
        "guides": guides, "repeats": repeats, "blocks": blocks,
        "can_send": bool(ids) and not blocks,
        "live": bool(data.get("live")) and not data.get("fixture"),
        "fixture": bool(data.get("fixture")),
        "_p": p, "_ready": ready,
    }


def _public(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith("_")}


def review(cfg: dict, body: dict) -> tuple[int, dict]:
    body = body if isinstance(body, dict) else {}
    if not connected(cfg):
        return 200, {"connected": False, "error": NOT_CONNECTED}
    pid, ids = _pid(body.get("patient_id")), _guide_ids(body.get("guide_ids", []))
    if pid is None or ids is None:
        return 400, {"error": "That patient or guide list isn't valid."}
    try:
        r = _review(cfg, pid, ids, date.today(), _now())
    except RuntimeError as exc:
        return 500, {"error": str(exc)}
    if r.get("error"):
        return 200, r
    return 200, _public(r)


def _result_from_row(row: dict) -> dict:
    outcome = row.get("outcome")
    words = {"draft": "Draft made in info@ (Drafts). Nothing was sent.",
             "sent": "Sent from info@.",
             "unknown": UNKNOWN_WORDS,
             "failed": "That didn't go through. Nothing was sent.",
             "sending": "Still sending - wait a moment, then refresh."}
    return {"ok": outcome in ("draft", "sent"), "outcome": outcome, "op_id": row.get("op_id"),
            "message": words.get(outcome, "")}


def send(cfg: dict, body: dict, staff: str) -> tuple[int, dict]:
    body = body if isinstance(body, dict) else {}
    if not connected(cfg):
        return 200, {"connected": False, "error": NOT_CONNECTED}
    key = str(body.get("idempotency_key") or "")
    pid, ids = _pid(body.get("patient_id")), _guide_ids(body.get("guide_ids", []))
    if not IDEM_RE.match(key) or pid is None or not ids:
        return 400, {"error": "Pick a patient and at least one guide."}
    try:
        hist = History(cfg)
    except RuntimeError as exc:
        return 500, {"error": str(exc)}

    prior = hist.by_key(key)
    if prior is not None:
        if prior["patient_id"] != pid or prior["guide_ids"] != sorted(ids):
            return 409, {"error": "This page is out of date. Refresh and pick again."}
        return 200, {**_result_from_row(prior), "repeat_submit": True}

    r = _review(cfg, pid, ids, date.today(), _now())
    if r.get("error"):
        return 200, {"error": r["error"]}
    if not r["can_send"]:
        return 409, {"error": "Not sent: " + " ".join(r["blocks"]), "review": _public(r)}

    p, ready = r["_p"], r["_ready"]
    mail = compose(p, [g for g, _ in ready], date.today())
    try:
        logo = logo_inline()
    except OSError:
        return 500, {"error": "The email logo file is missing from the Hub. Nothing was sent. Ask Mark."}
    rec = {"op_id": uuid.uuid4().hex, "idem_key": key, "patient_id": pid,
           "dest_email": p["email"], "recipient": "parent/guardian" if r["patient"]["minor"] else "patient",
           "template_version": mail["version"],
           "basis": "staff send after the guide was discussed (review panel, no tick: Mark 1 Nov 2026)",
           "staff": str(staff or "")[:60], "repeat_warned": bool(r["repeats"]),
           "created": _now_iso()}
    with _lock:
        if not hist.begin(rec, [g for g, _ in ready]):
            prior = hist.by_key(key)
            return 200, {**_result_from_row(prior or {}), "repeat_submit": True}

    spec = {"op_id": rec["op_id"], "to": p["email"], "subject": mail["subject"], "body": mail["body"],
            "html": mail["html"], "inline": [logo],
            "attachments": [{"filename": email_filename(g), "sha256": g["sha256"],
                             "data_b64": base64.b64encode(b).decode("ascii")} for g, b in ready]}
    out = run_bridge(cfg, "deliver", stdin=json.dumps(spec))
    if out.get("timeout") or out.get("unreadable"):
        outcome, mode, gid, code = "unknown", None, "", "timeout" if out.get("timeout") else "unreadable"
    elif out.get("error"):
        outcome, mode, gid, code = "failed", None, "", str(out.get("error_code") or "error")
    else:
        mode = "live" if out.get("mode") == "live" else "draft"
        outcome, gid, code = ("sent" if mode == "live" else "draft"), str(out.get("id") or ""), ""
    hist.finish(rec["op_id"], outcome, mode, gid, code)
    logger.info("Send guide: op %s patient %s -> %s", rec["op_id"], pid, outcome)
    res = _result_from_row({"outcome": outcome, "op_id": rec["op_id"]})
    if outcome == "failed" and out.get("error"):
        res["message"] = f"{out['error']} Nothing was sent."
    if out.get("fixture") and outcome == "draft":
        res["message"] = "Test only (fake patients): no draft was made and nothing was sent."
    return 200, res
