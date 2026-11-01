"""python <hub>\\hub\\guide_bridge.py <command> - the Send guide tile's link to
Optomate (read only) and to the practice Gmail (info@).

Run by hub/sendguide.py as a subprocess with cwd = the Optomate agent folder,
the same shape as the Check-in engine (hub/checkin.py): one JSON object on the
last stdout line, exit 0 even on failure ({"error": "plain words"}). Running in
the agent folder lets it reuse the agent's proven pieces where they already
live - optomate.client (Optomate API, GET only here) and gmailer.auth (the
info@ OAuth token) - so no credential, token or .env is copied into the Hub.

Commands
  mode                     -> {"live": bool, "fixture": bool}
  search --q TEXT          -> {"patients": [...], "more": bool}
  patient --id N           -> {"patient": {...}}
  deliver                  (JSON on stdin) -> {"mode": "draft"|"live", "id": "..."}

THE LIVE GATE lives here and only here, read from the agent's .env (the Hub
strips these two names from the environment it hands this process, so nothing
in the Hub can flip it):
    GUIDE_DRAFT_MODE=false  AND  GUIDE_LIVE_SEND=true   -> really send
    anything else (the default)                        -> Gmail DRAFT in info@
Mark turns it on with "turn on live guide sends".

FIXTURE: CHECKIN_FIXTURE=1 or GUIDE_FIXTURE=1 -> patients come from the agent's
checkin\\fixtures\\patients.json (fake ZZTEST people) and Gmail is NEVER
touched: deliver returns a pretend draft id.

PRIVACY: nothing here logs a name or an address. Optomate is only ever read.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sys
from email.message import EmailMessage
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parent.parent
AGENT = Path.cwd()
# The script's own folder (hub\) holds modules such as checkin.py that would
# shadow the agent's packages of the same name: take it off the path.
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]
for p in (str(AGENT), str(HUB_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

EMAIL_RE = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+$")
# "Concord Eyecare - <manifest title>.pdf": any script, no control characters,
# nothing a file system or mail client refuses (matches sendguide.TITLE_RE).
FILENAME_RE = re.compile(r"^(?![ .])[^\x00-\x1f\x7f<>:\"/\\|?*]{1,120}\.pdf$")
CID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_INLINE = 200_000
PATIENT_FIELDS = ("ID", "GIVEN", "SURNAME", "NICKNAME", "BIRTHDATE", "EMAIL",
                  "NOEMAIL", "IS_BADEMAIL")


class BridgeError(Exception):
    """A plain-words problem for staff."""


# ---------------------------------------------------------------------------
# Gate + mode
# ---------------------------------------------------------------------------

def live_send_enabled(env=None) -> bool:
    """True only when BOTH keys are turned. Anything else means draft."""
    env = os.environ if env is None else env
    draft_mode = (env.get("GUIDE_DRAFT_MODE", "true") or "").strip().lower() != "false"
    live = (env.get("GUIDE_LIVE_SEND", "") or "").strip().lower() == "true"
    return (not draft_mode) and live


def fixture_mode(env=None) -> bool:
    env = os.environ if env is None else env
    return any((env.get(k, "") or "").strip().lower() in ("1", "true", "yes")
               for k in ("CHECKIN_FIXTURE", "GUIDE_FIXTURE"))


# ---------------------------------------------------------------------------
# Optomate (read only)
# ---------------------------------------------------------------------------

def _fixture_rows() -> list[dict]:
    path = AGENT / "checkin" / "fixtures" / "patients.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BridgeError("The fake test patients aren't on this computer.")
    return [r for r in rows if isinstance(r, dict)]


def _client():
    from optomate.client import OptomateClient
    return OptomateClient.from_env()


def _flag(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "y", "yes", "t")


def _patient_out(r: dict) -> dict:
    return {
        "patient_id": r.get("ID"),
        "given": str(r.get("GIVEN") or "").strip(),
        "surname": str(r.get("SURNAME") or "").strip(),
        "nickname": str(r.get("NICKNAME") or "").strip(),
        "dob": str(r.get("BIRTHDATE") or "")[:10],
        "email": str(r.get("EMAIL") or "").strip(),
        # Optomate's own contact flags (agent tracker/app.py reads the same two).
        "noemail": _flag(r.get("NOEMAIL")),
        "bademail": _flag(r.get("IS_BADEMAIL")),
    }


def cmd_search(q: str) -> dict:
    from hub import guide_search
    if fixture_mode():
        rows = _fixture_rows()

        def fetch(filt, top, spec):
            return guide_search.fixture_match(rows, spec)[:top]
    else:
        client = _client()

        def fetch(filt, top, spec):
            return client.get_value("Patients", filter=filt,
                                    select="ID,GIVEN,SURNAME,BIRTHDATE",
                                    orderby="SURNAME,GIVEN", top=str(top))
    rows, more = guide_search.search(fetch, q)
    return {"patients": [{"id": r.get("ID"),
                          "given": str(r.get("GIVEN") or "").strip(),
                          "surname": str(r.get("SURNAME") or "").strip(),
                          "dob": str(r.get("BIRTHDATE") or "")[:10]} for r in rows],
            "more": more}


def cmd_patient(pid: int) -> dict:
    if fixture_mode():
        r = next((x for x in _fixture_rows() if x.get("ID") == pid), None)
        if r is None:
            raise BridgeError("That patient isn't in the fake test list.")
        return {"patient": _patient_out(r)}
    import requests
    try:
        r = _client()._get(f"Patients({int(pid)})")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise BridgeError("That patient isn't in Optomate.")
        raise
    return {"patient": _patient_out(dict(r))}


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------

def _b64_checked(a: dict, what: str) -> bytes:
    try:
        data = base64.b64decode(str(a.get("data_b64") or ""), validate=True)
    except ValueError:
        raise BridgeError(f"A {what} didn't arrive whole.")
    if hashlib.sha256(data).hexdigest() != str(a.get("sha256") or ""):
        raise BridgeError(f"A {what} changed on the way. Nothing was sent.")
    return data


def build_message(spec: dict) -> EmailMessage:
    """The email exactly as composed by the Hub's fixed template:

        multipart/mixed
          multipart/alternative
            text/plain                  (the plain-text fallback)
            multipart/related
              text/html                 (same words + the house signature)
              image/png  cid:cec-logo   (signature logo, inline)
          application/pdf ...           (each guide, hash-checked again here)
    """
    to = str(spec.get("to") or "").strip()
    subject = str(spec.get("subject") or "")
    body = str(spec.get("body") or "")
    html_body = str(spec.get("html") or "")
    if not EMAIL_RE.match(to):
        raise BridgeError("The email address doesn't look right.")
    if not subject or "\n" in subject or "\r" in subject or len(subject) > 200:
        raise BridgeError("The email subject is missing.")
    atts = spec.get("attachments") or []
    if not body or not atts:
        raise BridgeError("Nothing to send.")
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
        html_part = msg.get_payload()[-1]
        for im in spec.get("inline") or []:
            cid = str(im.get("cid") or "")
            if not CID_RE.match(cid) or f'src="cid:{cid}"' not in html_body:
                raise BridgeError("The email logo isn't set up right.")
            data = _b64_checked(im, "logo")
            if not data.startswith(PNG_MAGIC) or len(data) > MAX_INLINE:
                raise BridgeError("The email logo isn't set up right.")
            html_part.add_related(data, maintype="image", subtype="png", cid=f"<{cid}>",
                                  filename=str(im.get("filename") or f"{cid}.png"),
                                  disposition="inline")
    for a in atts:
        name = str(a.get("filename") or "")
        if not FILENAME_RE.match(name):
            raise BridgeError("A guide file name isn't valid.")
        data = _b64_checked(a, "guide file")
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return msg


def deliver(spec: dict, service=None, env=None) -> dict:
    """Draft (default) or live send. Returns {"mode", "id"}. Never logs an address."""
    msg = build_message(spec)
    if fixture_mode(env):
        return {"mode": "draft", "id": "fixture-" + str(spec.get("op_id") or "")[:12],
                "fixture": True}
    live = live_send_enabled(env)
    if service is None:
        from gmailer.auth import get_gmail_service
        service = get_gmail_service()
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    if live:
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        logging.getLogger("guide_bridge").warning("Send guide LIVE SEND (op %s)", spec.get("op_id"))
        return {"mode": "live", "id": str(sent.get("id") or "")}
    draft = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return {"mode": "draft", "id": str(draft.get("id") or "")}


# ---------------------------------------------------------------------------

def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(AGENT / ".env", override=False)
    except Exception:
        pass


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    argv = list(sys.argv[1:] if argv is None else argv)
    _load_env()
    cmd = argv[0] if argv else ""
    try:
        if cmd == "mode":
            result = {}
        elif cmd == "search" and len(argv) == 3 and argv[1] == "--q":
            result = cmd_search(argv[2])
        elif cmd == "patient" and len(argv) == 3 and argv[1] == "--id":
            result = cmd_patient(int(argv[2]))
        elif cmd == "deliver":
            result = deliver(json.loads(sys.stdin.read() or "{}"))
        else:
            result = {"error": "Bad command."}
    except BridgeError as exc:
        result = {"error": str(exc)}
    except Exception as exc:  # Optomate down, token expired, ...
        name = type(exc).__name__
        msg = "Something went wrong."
        if "Connection" in name or "Max retries" in str(exc):
            msg = "Optomate is not answering - is Optomate Touch running on the server?"
        elif "RefreshError" in name or "credentials" in str(exc).lower():
            msg = "The info@ Gmail link needs signing in again. Ask Mark."
        result = {"error": msg, "error_code": name[:60]}
    result.setdefault("fixture", fixture_mode())
    result.setdefault("live", live_send_enabled() and not fixture_mode())
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
