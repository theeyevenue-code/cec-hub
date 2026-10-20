"""Medicare — rejected and short-paid claims, and what to do about each.

21 Oct 2026 (Mark: "if there's any fixes based upon what Karen is doing, can you make the fixes"):
status follows OPTOMATE, not the Hub ticks. The agent (medicare.actions) reads Adjust Batch,
new claim batches and write-offs: resubmitted -> Waiting, Paid only on Medicare's report, rejected
again -> back on the list. Each line may carry red/amber "checks" (Optomate vs Hub) and "optomate"
(what was done, under which login). "login_note" (changes saved under another person's login) is
passed to the page only when the name picked is Mark or Karen.

OPTION C (Mark, 13 Oct 2026) supersedes the review/approval design below: "find out yourself
from the consult notes and give suggestions to Karen. If there's nothing, build it as a 10918."
Every line reaches Karen in ONE list grouped by claim batch, with a suggested item (or write off)
and a <=6-word cue; she may pick another allowed item before ticking. No PIN on this page (the
agent's medicare.pin module is kept, unused, for an easy restore). Ticks go through
    python -m medicare.rejections --tick --by NAME      (stdin: [{key, to}])
which logs suggested vs used, who and when. No note snippets are fetched for the page.

Mark, 9 Oct 2026: Karen works Tuesdays. First thing she pulls the Medicare
processing and payment reports in Optomate Touch, then opens this page. It reads
the freshly pulled reports LIVE (no waiting for a nightly run). The page is ONE
"Do now" list for Karen; everything else is folded away with totals.

Mark's review (Codex review applied 9 Oct 2026): when Medicare rejects a
comprehensive consult (160, limit reached), the agent puts the line in Mark's review
- a possible item where that day's notes have a keyword (10913 / 10914 / 10915), or
"check eligibility" where they don't. Snippets show ONLY when the name picked at the
top is Mark. He accepts an item (Karen then sees "Change to ..., add Mark's approved
claim note, then resubmit") or says not eligible (Karen sees "Write off in Optomate";
it shows Written off only once she ticks it). Nothing is accepted or written off
automatically. Accept / Not eligible / undoing either need Mark's 4-digit PIN, checked
by the agent (medicare.pin; salted hash in its git-ignored local-reports). The PIN
goes to the agent on stdin, never on a command line, and is never logged.

The Hub has no Optomate client by design. Like hub/recent_rx.py it runs a
READ-ONLY command in the agent repo and reads the one JSON line it prints:
    python -m medicare.rejections --json [--review] [--fixture]
--review (note snippets on the cards) is passed ONLY for Mark. Ticks go back the
same way (the agent owns the state file, in its git-ignored local-reports folder):
    python -m medicare.rejections --mark KEY --status STATUS --by NAME [--pin-stdin]

Privacy: the agent sends given name + surname initial only. No card number, DOB
or patient ID reaches the Hub. Note snippets (Mark only, <= 2 x 140 characters)
pass straight through to his screen: never logged, endpoint served no-store.
Every $ is the agent's copy of Optomate's figures.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 60
KEY_RE = re.compile(r"^[0-9a-f]{12}$")
ACCEPT_ITEMS = ("10913", "10914", "10915", "10916", "10918")   # 10916 short bookings; 10918 the minimum
STAFF_STATUSES = ("done", "resubmitted", "written_off", "open")
MARK_STATUSES = ("not_eligible",) + tuple(f"accepted_{i}" for i in ACCEPT_ITEMS)
LANES = ("review", "do_now", "mark", "waiting", "written_off", "expired", "recovered")
ITEM_KEYS = ("key", "service_date", "deadline", "days_left", "patient_label", "item",
             "amount", "code", "meaning", "action_group", "steps", "fact", "claim", "flag",
             "item_change", "claim_note", "claim_date")
WRITE_OFF = "write_off"
PICKS = ACCEPT_ITEMS + (WRITE_OFF,)
CLAIM_RE = re.compile(r"^[\w-]{1,20}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ITEM_RE = re.compile(r"^\d{5}$")          # any MBS item, e.g. 10910 -> 10911 for an age fix
MAX_PICKS = 300
# claim notes Karen types: the agent's three forms only (medicare.state.CLAIM_NOTE_RE)
KAREN_NOTE_RE = re.compile(r"^(progressive disorder|new symptoms|diabetic, pupils dilated)(: [\w /+'-]{1,40})?$")
BASIS = ("Finding noted", "History only", "Symptom noted")
PIN_RE = re.compile(r"^\d{4}$")
CLAIM_NOTE_RE = re.compile(r"^[\w ,:/+'-]{1,80}$")


def is_mark(name) -> bool:
    """The Hub's name picker (cookie hub_staff) says Mark. Case-insensitive."""
    return str(name or "").strip().lower() == "mark"


def not_connected(msg: str) -> dict:
    return {"connected": False, "message": msg}


def _agent_cfg(cfg: dict) -> dict:
    return (cfg or {}).get("optomate_agent", {}) or {}


def agent_dir(cfg: dict):
    d = _agent_cfg(cfg).get("agent_dir")
    return Path(d) if d else None


def _run(cfg: dict, args: list, fixture: bool, stdin: str | None = None):
    """-> (dict | None, error dict | None). Never raises. stdin carries Mark's PIN."""
    dpath = agent_dir(cfg)
    if dpath is None:
        return None, not_connected("Optomate isn't connected on this computer.")
    if not (dpath / "medicare" / "rejections.py").is_file():
        return None, not_connected("The Medicare list isn't set up on this computer yet.")
    python = _agent_cfg(cfg).get("python") or sys.executable
    argv = [python, "-m", "medicare.rejections", *args] + (["--fixture"] if fixture else [])
    try:
        proc = subprocess.run(argv, cwd=str(dpath), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT_S,
                              input=stdin if stdin is not None else "")
    except subprocess.TimeoutExpired:
        return None, {"connected": True, "error": "Optomate took too long — try again."}
    except OSError:
        return None, not_connected("Couldn't reach Optomate from this computer.")
    line = ""
    for candidate in reversed((proc.stdout or "").splitlines()):
        if candidate.strip():
            line = candidate.strip()
            break
    try:
        data = json.loads(line) if line else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict) or not data:
        return None, {"connected": True, "error": "Couldn't read the Medicare list."}
    if not data.get("ok"):
        return None, {"connected": True,
                      "error": str(data.get("error") or "Couldn't read the Medicare list.")}
    return data, None


def _money(v) -> float:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _s(v, n: int) -> str:
    return str(v or "")[:n]


def clean_review(r, mark_view: bool):
    """Mark: the row's facts and up to 2 snippets per item. Anyone else: nothing from
    the notes (the row shows as "Mark to review the item")."""
    if not isinstance(r, dict):
        return None
    kind = r.get("kind") if r.get("kind") in ("suggest", "check", "check_short") else ""
    if not mark_view:
        return {"kind": kind}
    sugs = []
    for s in (r.get("suggestions") or [])[:3]:
        if not isinstance(s, dict) or s.get("item") not in ACCEPT_ITEMS:
            continue
        snips = [{"section": _s(x.get("section"), 30), "text": _s(x.get("text"), 140)}
                 for x in (s.get("snippets") or [])[:2] if isinstance(x, dict)]
        note = _s(s.get("claim_note"), 80)
        sugs.append({"item": s["item"], "name": _s(s.get("name"), 40),
                     "basis": s.get("basis") if s.get("basis") in BASIS else "",
                     "claim_note": note if CLAIM_NOTE_RE.match(note) else "",
                     "snippets": snips})
    return {"kind": kind, "why": _s(r.get("why"), 120), "check": _s(r.get("check"), 120),
            "age": _int_or_none(r.get("age")), "minutes": _int_or_none(r.get("minutes")),
            "suggestions": sugs}


def clean_item(i: dict, mark_view: bool = False) -> dict | None:
    """Copy only whitelisted fields, so nothing new the agent might send can reach
    the screen by accident."""
    out = {k: i.get(k) for k in ITEM_KEYS}
    if not KEY_RE.match(str(out["key"] or "")) or out["action_group"] not in LANES:
        return None
    for k in ("service_date", "deadline", "patient_label", "item", "code", "meaning",
              "steps", "fact", "claim", "flag", "item_change", "claim_note"):
        out[k] = str(out[k] or "")
    out["claim_note"] = out["claim_note"][:80]
    out["patient_label"] = out["patient_label"][:20] or "Patient"
    if out["item_change"] not in ACCEPT_ITEMS:
        out["item_change"] = ""
    out["amount"] = _money(out["amount"])
    out["days_left"] = _int_or_none(out["days_left"])
    s = i.get("status") if isinstance(i.get("status"), dict) else None
    out["status"] = ({"status": str(s.get("status") or ""), "by": str(s.get("by") or "")[:40],
                      "at": str(s.get("at") or "")[:19]} if s else None)
    out["review"] = clean_review(i.get("review"), mark_view) if out["action_group"] == "review" else None
    out["claim_date"] = str(out["claim_date"] or "")[:10]
    if not DATE_RE.match(out["claim_date"]):
        out["claim_date"] = ""
    out["claim"] = out["claim"][:20]
    f = i.get("fix") if isinstance(i.get("fix"), dict) else None
    out["fix"] = clean_fix(f) if f and out["action_group"] == "do_now" else None
    out["checks"] = clean_checks(i.get("checks"))
    out["optomate"] = clean_optomate(i.get("optomate"))
    return out


def clean_checks(rows) -> list:
    """Red / amber checks the agent found by comparing Optomate with the Hub (21 Oct 2026)."""
    out = []
    for c in (rows if isinstance(rows, list) else [])[:6]:
        if isinstance(c, dict) and c.get("level") in ("red", "amber") and c.get("text"):
            out.append({"level": c["level"], "text": _s(c.get("text"), 200)})
    return out


def clean_optomate(o):
    """What Optomate shows done to the line, and under which login (21 Oct 2026)."""
    if not isinstance(o, dict) or not o.get("what"):
        return None
    return {"what": _s(o.get("what"), 60), "at": _s(o.get("at"), 16),
            "login": re.sub(r"[^\w]", "", _s(o.get("login"), 8)), "who": _s(o.get("who"), 40)}


LOGIN_NOTE_FOR = ("mark", "karen")       # Mark, 21 Oct 2026: the other-login note is for Mark and Karen


def clean_login_note(n, viewer: str):
    if not isinstance(n, dict) or str(viewer or "").strip().lower() not in LOGIN_NOTE_FOR:
        return None
    count = _int_or_none(n.get("n"))
    if not count:
        return None
    return {"n": count, "days": _int_or_none(n.get("days")) or 30,
            "hub_name": _s(n.get("hub_name"), 40), "login": re.sub(r"[^\w]", "", _s(n.get("login"), 8)),
            "login_name": _s(n.get("login_name"), 40)}


def _line(x) -> dict:
    """A prevention line: {"text": the action, "sub": the earlier item/date}."""
    if isinstance(x, dict):
        return {"text": _s(x.get("text"), 160), "sub": _s(x.get("sub"), 120)}
    return {"text": _s(x, 200), "sub": ""}


def clean_today(rows) -> list:
    out = []
    for t in (rows or [])[:80]:
        if not isinstance(t, dict):
            continue
        hhmm = str(t.get("time") or "")
        out.append({"time": hhmm if re.match(r"^\d{2}:\d{2}$", hhmm) else "",
                    "patient_label": _s(t.get("patient_label"), 20) or "Patient",
                    "lines": [_line(x) for x in (t.get("lines") or [])[:3]]})
    return out


def worklist(cfg: dict, fixture: bool = False, mark_view: bool = False, viewer: str = "") -> dict:
    """The page's data. Never raises. Note snippets only for Mark; everyone else sees
    the review lines as "Mark to review the item" under Awaiting Mark (Codex #5)."""
    # Option C (13 Oct 2026): no review step, so no note snippets for anyone (--review not sent)
    data, err = _run(cfg, ["--json"], fixture)
    if err:
        return err
    summary = {}
    for g in LANES:
        s = (data.get("summary") or {}).get(g) or {}
        summary[g] = {"n": _int_or_none(s.get("n")) or 0, "amount": _money(s.get("amount"))}
    unrep = data.get("unreported_claims") or {}
    items = [c for c in (clean_item(i, mark_view) for i in (data.get("items") or [])
                         if isinstance(i, dict)) if c]
    return {
        "connected": True,
        "fixture": bool(data.get("fixture")),
        "is_mark": bool(mark_view),
        "generated_at": str(data.get("generated_at") or ""),
        "last_report_pull": str(data.get("last_report_pull") or ""),
        "days_since_pull": _int_or_none(data.get("days_since_pull")),
        "pull_overdue": bool(data.get("pull_overdue")),
        "unreported_claims": {"count": _int_or_none(unrep.get("count")) or 0,
                              "oldest_days": _int_or_none(unrep.get("oldest_days"))},
        "summary": summary,
        "items": items,
        "today": clean_today(data.get("today")),
        "today_error": _s(data.get("today_error"), 80),
        "pin_set": bool(data.get("pin_set")),
        "checks": {"red": _int_or_none((data.get("checks") or {}).get("red")) or 0,
                   "amber": _int_or_none((data.get("checks") or {}).get("amber")) or 0},
        "optomate_read": bool(data.get("optomate_read")),
        "optomate_error": _s(data.get("optomate_error"), 120),
        "login_note": clean_login_note(data.get("login_note"), viewer),
    }


def _ok_to(o) -> bool:
    return o in ("same", WRITE_OFF) or bool(ITEM_RE.match(str(o or "")))


def clean_fix(f: dict):
    """Karen's suggested action (Option C, Mark 13 Oct 2026): the item to change to (or same /
    write off), a <=6-word reason, the items she may pick instead, and the claim note per item.
    Nothing from the notes beyond the reason's one cue word."""
    to = str(f.get("to") or "")
    if not _ok_to(to):
        return None
    opts = [str(o) for o in (f.get("options") or []) if _ok_to(o)][:8] or [to]
    if to not in opts:
        opts = [to] + opts
    notes = f.get("notes") if isinstance(f.get("notes"), dict) else {}
    notes = {k: _s(v, 80) for k, v in notes.items()
             if k in opts and ITEM_RE.match(k) and KAREN_NOTE_RE.match(_s(v, 80))}
    return {"to": to, "why": _s(f.get("why"), 60), "options": opts, "notes": notes}


def _who(by) -> str:
    return re.sub(r"[^\w .'\-]", "", str(by or "")).strip()[:40]


def tick(cfg: dict, lines, by: str, fixture: bool = False) -> dict:
    """Karen's tick - one line or a whole batch: [{"key", "to"}] where to is the item she used
    (or same / write_off). No PIN (Option C). The agent re-checks each line against today's
    list and its allowed items, saves all or none, and logs suggested vs used, who, when."""
    by = _who(by)
    if not by:
        return {"ok": False, "error": "Pick your name at the top first."}
    if not isinstance(lines, list) or not lines:
        return {"ok": False, "error": "Nothing ticked."}
    if len(lines) > MAX_PICKS:
        return {"ok": False, "error": "Too many lines at once."}
    clean = []
    for p in lines:
        if not isinstance(p, dict) or not KEY_RE.match(str(p.get("key") or "")) or not _ok_to(p.get("to")):
            return {"ok": False, "error": "Unknown item."}
        clean.append({"key": str(p["key"]), "to": str(p["to"])})
    data, err = _run(cfg, ["--tick", "--by", by], fixture, stdin=json.dumps(clean) + "\n")
    if err:
        return {"ok": False, "error": err.get("error") or err.get("message") or "Couldn't save."}
    return {"ok": True, "n": _int_or_none(data.get("n")) or 0}


def mark(cfg: dict, key: str, status: str, by: str, fixture: bool = False,
         pin: str = "", claim_note: str = "") -> dict:
    """Record a tick. -> {ok} or {ok: False, error}. Accept / not eligible: Mark only,
    with his PIN (the agent checks it, rate-limits it and logs the decision). An undo
    sent with a PIN is Mark undoing his own decision."""
    key = str(key or "").strip().lower()
    status = str(status or "").strip().lower()
    if not KEY_RE.match(key):
        return {"ok": False, "error": "Unknown item."}
    if status not in STAFF_STATUSES + MARK_STATUSES:
        return {"ok": False, "error": "Unknown action."}
    by = re.sub(r"[^\w .'\-]", "", str(by or "")).strip()[:40]
    if not by:
        return {"ok": False, "error": "Pick your name at the top first."}
    pin = str(pin or "").strip()
    if status in MARK_STATUSES or pin:
        if not is_mark(by):
            return {"ok": False, "error": "Only Mark can decide the item."}
        if not PIN_RE.match(pin):
            return {"ok": False, "error": "Enter your 4-digit PIN.", "pin": True}
    args = ["--mark", key, "--status", status, "--by", by]
    note = str(claim_note or "").strip()[:80]
    if status.startswith("accepted_") and CLAIM_NOTE_RE.match(note):
        args += ["--claim-note", note]
    if pin:
        args.append("--pin-stdin")
    data, err = _run(cfg, args, fixture, stdin=(pin + "\n") if pin else None)
    if err:
        return {"ok": False, "error": err.get("error") or err.get("message") or "Couldn't save."}
    return {"ok": True, "status": status}
