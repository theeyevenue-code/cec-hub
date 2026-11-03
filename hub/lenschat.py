"""Lens Finder chat - shared pieces (Mark, 3 Nov 2026). The question loop, the
tool registry and the spend ledger now live in hub/assistant/ (phase 1a of the
Hub assistant, docs/HUB-ASSISTANT-DESIGN.md); this module keeps what both use:

  * settings / key / Mark-device gate (config/lenschat.json, which Mark set up:
    enabled, key_file, ledger, cap_aud per month, allow_ips).
  * guard_text: the local pre-send check on typed text - the patient's own
    name, ANY first name or surname in Optomate's patient table (unless it is
    a lens word such as "brown"), dates, phone / Medicare / ID-shaped numbers,
    contact details and text over 300 characters are blocked. hub.assistant.guard
    normalises Unicode first and wraps it to fail closed.
  * name_words: the patient-name word list, read once from Optomate (read
    only), memory only, 12 hours; None = can't load = every question blocked.
  * candidates / model_view / clean_filters: the comparison's model view
    (attributes, fit flags, cost rank - no money, no Rx).
  * session_for: chat context bound to one patient / typed Rx, memory only,
    30 minutes, reset on change.

Logs: counts and reason codes only - never text, Rx or ids.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from decimal import Decimal, ROUND_UP
from pathlib import Path

from hub import compare

log = logging.getLogger("cec-hub.lenschat")

MODEL = "claude-sonnet-5-5"
CAP_AUD = Decimal("10.00")
USD_PER_MTOK_IN = Decimal("2")
USD_PER_MTOK_OUT = Decimal("10")
AUD_PER_USD = Decimal("1.60")
TRIAL_DAYS = 7
MAX_TEXT = 300
KEY_ENV = "CEC_LENSCHAT_API_KEY"
PRIVATE_DIR = Path(r"C:\CEC\hub-private")
DEFAULTS = {
    "enabled": False,
    "allow_ips": [],
    "model": MODEL,
    "key_file": str(PRIVATE_DIR / "anthropic_key.txt"),
    "ledger": str(PRIVATE_DIR / "lenschat-spend.json"),
    "practice_weekdays": [0, 1, 2, 3, 4, 5],     # Mon-Sat
    # Mark 3 Nov 2026: "lets build on assumption we leave this running". cap_aud is per
    # calendar month when monthly is true (spend rolls into total_aud on the 1st);
    # trial_days 0 = no end date. Defaults keep the original A$10 lifetime / 7-day trial.
    "cap_aud": str(CAP_AUD),
    "monthly": False,
    "trial_days": TRIAL_DAYS,
}



# Words that may also be someone's name but mean lenses here, plus everyday
# question words. A token in the patient-name list is blocked UNLESS it is here.
LENS_WORDS = set("""
brown grey gray green black blue pioneer burgundy amber graphite gold silver platinum
photochromic photochromatic photo transitions transition sensity photofusion photofusionx pfx
adaptivesun polarised polarized polar tint tinted clear sun sunglasses dark fast shine
single vision sv progressive progressives multifocal multifocals bifocal bifocals office
occupational computer reading near distance driving digital individual superb smartlife
clearview clearmind ultra hdv hdc performance work go stock grind grinding surfaced
index thin thinner thinnest thick thicker light lighter lightweight coating coat coated
hard hardcoat hmc duravision bluepro bluecontrol uv filter blank blanks diameter
lens lenses pair pairs price prices cost costs cheap cheaper cheapest dear dearer
expensive value best better good compare comparison option options alternative alternatives
zeiss synchrony hoya nulux hilux eyas eynoa phoenix miyosmart brand brands supplier
range ranges fit fits fitting power powers sphere cyl cylinder axis add plus minus
right left both eye eyes high low mid frame frames size wrap sport sports kids child
the a an and or but for with without of to in on at by from as is are be can could
would should will may might do does did not no yes than then what which who why how
this that these those it its we us our you your i me my any all some more less most
same other another next first last only also just very much many each per please
show list give tell find need needs want wants like about between versus vs
over under up down same similar instead still whats it's does
sell sells selling sold charge charges much parameter parameters spec specs specification
made order ordered available availability come comes height fitting tall
maximum minimum widest largest smallest bigger smaller larger blank stocked
""".split())

MONTHS = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
NUMBER_WORDS = re.compile(r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
                          r"twelve|twenty|thirty|forty|fifty|hundred|thousand|dollars?|cents?|"
                          r"percent|half|quarter|double|twice)\b", re.I)
CLINICAL_WORDS = re.compile(r"diagnos|myopia|prescrib|treatment|disease|best for (this|the) "
                            r"patient|change the rx|medical", re.I)


# ---------------------------------------------------------------------------
# Config, key, gate
# ---------------------------------------------------------------------------

def settings(cfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if k in DEFAULTS:
            out[k] = v
    return out


def cap_of(s: dict) -> Decimal:
    """The configured cap; anything unreadable or <= 0 falls back to A$10."""
    try:
        c = Decimal(str(s.get("cap_aud")))
        return c if c > 0 else CAP_AUD
    except Exception:
        return CAP_AUD


def api_key(s: dict) -> str:
    """Environment first, then the key file outside the repo. Never logged."""
    k = (os.environ.get(KEY_ENV) or "").strip()
    if k:
        return k
    try:
        return Path(s["key_file"]).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def mark_device(s: dict, remote_ip: str, loopback: bool) -> bool:
    return bool(loopback or (remote_ip and remote_ip in [str(x) for x in s.get("allow_ips") or []]))


def aud(in_tokens: int, out_tokens: int) -> Decimal:
    usd = (Decimal(in_tokens) * USD_PER_MTOK_IN + Decimal(out_tokens) * USD_PER_MTOK_OUT) \
        / Decimal(1_000_000)
    return (usd * AUD_PER_USD).quantize(Decimal("0.0001"), rounding=ROUND_UP)


def _plain(o):
    if hasattr(o, "model_dump"):
        return o.model_dump()
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if hasattr(o, "__dict__"):
        return {k: _plain(v) for k, v in vars(o).items()}
    return o


# ---------------------------------------------------------------------------
# Local pre-send guard on typed text
# ---------------------------------------------------------------------------

def deidentify_free_text(text: str, given: str, surname: str) -> str:
    """Same approach as the referral tool's deidentify_free_text: the bound
    patient's own names, whole words, as written / UPPER / Title."""
    pairs = []
    if given and surname:
        pairs += [f"{given} {surname}", f"{surname} {given}"]
    pairs += [n for n in (surname, given) if n]
    for name in pairs:
        if len(name) < 2:
            continue
        for form in {name, name.upper(), name.title()}:
            text = re.sub(r"(?<![\w\[])" + re.escape(form) + r"(?![\w\]])", "[NAME]", text)
    return text


_NUM_OK = [re.compile(p) for p in (
    r"[+\-]\d{1,2}(\.\d{1,2})?",     # a power: +3.25, -1.25, -6
    r"\d{1,2}(\.\d{1,2})?",          # index 1.50 / 1.6, unsigned power, small count, mm figure
    r"x\d{1,3}",                     # axis x82
    r"\d{2}(\.\d)?mm",               # 65mm
)]
_NUM_TOKEN = re.compile(r"x?[+\-]?\d[\d.,/:\-]*(mm)?")


def guard_text(text, names: set | frozenset | None, given: str = "", surname: str = "") -> tuple[str, str]:
    """(clean text, "") when the text may be sent, else ("", reason code).
    Reason codes are logged; the text never is."""
    if not isinstance(text, str):
        return "", "empty"
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "", "empty"
    if len(text) > MAX_TEXT:
        return "", "too_long"
    if names is None:
        return "", "names_unavailable"
    if deidentify_free_text(text, given, surname) != text:
        return "", "patient_name"
    low = text.lower()
    if "@" in low or "http" in low or "www." in low:
        return "", "contact"
    if re.search(MONTHS + r"\.?\s*\d|\d\s*(st|nd|rd|th)?\s*" + MONTHS + r"\b", low):
        return "", "date"
    if re.search(r"\b\d{1,2}\s+\d{1,2}\s+\d{2,4}\b", low):
        return "", "date"
    # Rx notation: split +3.25/-1.25 and -1.25x82 into their parts first.
    spaced = re.sub(r"(?<=\d)/(?=[+\-]?\d)", " / ", low)
    spaced = re.sub(r"(?<=\d)\s*x\s*(?=\d)", " x", spaced)
    spaced = re.sub(r"(?<=\d)\s+mm\b", "mm", spaced)
    for m in _NUM_TOKEN.finditer(spaced):
        tok = m.group(0).rstrip(".,-:/")
        if not any(p.fullmatch(tok) for p in _NUM_OK):
            return "", "number"
    for w in re.findall(r"[a-z]+(?:'[a-z]+)?", low):
        w = w.replace("'", "")
        if len(w) >= 2 and w in names and w not in LENS_WORDS:
            return "", "name"
    return text, ""


# Patient name words: loaded once from Optomate (read only), memory only.
_NAMES: dict = {"words": None, "at": 0.0}
_NAMES_LOCK = threading.Lock()


def name_words(cfg: dict, loader=None) -> frozenset | None:
    """The patient table's name words, cached in memory for 12 hours. None
    when they can't be read (the chat then refuses: fail closed)."""
    with _NAMES_LOCK:
        if _NAMES["words"] is not None and time.time() - _NAMES["at"] < 12 * 3600:
            return _NAMES["words"]
        if loader is None:
            from hub import lensoptions
            data = lensoptions.run_bridge(cfg, "names")
        else:
            data = loader()
        words = data.get("names") if isinstance(data, dict) else None
        if not isinstance(words, list) or not words:
            return None
        _NAMES["words"] = frozenset(str(w).lower() for w in words)
        _NAMES["at"] = time.time()
        return _NAMES["words"]


# ---------------------------------------------------------------------------
# The outbound schema (the ONLY thing the model sees)
# ---------------------------------------------------------------------------

def candidates(result: dict, limit: int = 15) -> list[dict]:
    """Engine rows -> the model's view: attributes and flags, no money, no Rx."""
    rows = result.get("_all") or result.get("rows") or []
    priced = [x for x in rows if x["verified"]]
    rank = {x["id"]: i + 1 for i, x in enumerate(priced)}
    out = []
    for x in rows[:limit]:
        blank = ("made to order" if x["type"] == "Grind" else
                 "fits frame" if x["blank_ok"] else
                 "too small for frame" if x["blank_ok"] is False else "frame not checked")
        out.append({
            "id": x["id"], "brand": x["brand"], "product": x["product"], "index": x["index"],
            "lens_type": x["category"], "material": x["material"], "coating": x["coating"],
            "stock_or_grind": x["type"].lower(), "blank": blank,
            "range_right": x["fit"].get("R", "no lens"), "range_left": x["fit"].get("L", "no lens"),
            "colour": x["colour"] or "not asked", "still_ordered": x["orderable"],
            "cost": "priced" if x["cost_status"] == "priced" else "unavailable",
            "cost_rank": rank.get(x["id"]), "verified": x["verified"],
            "thicker_than_index_estimate": x["thicker"],
        })
    return out


def model_view(result: dict, filters: dict) -> dict:
    """The tool / first-turn payload. Requirement words without the add."""
    return {"requirements": {"lens_type": result["requirements"][0], **filters},
            "matching": result["counts"]["matching"],
            "verified": result["counts"]["verified"],
            "candidates": candidates(result)}


# ---------------------------------------------------------------------------
# Sessions: context bound to one patient, reset on change, memory only
# ---------------------------------------------------------------------------

_SESSIONS: dict = {}
_SESS_LOCK = threading.Lock()
SESSION_TTL = 30 * 60
MAX_HISTORY = 3


def session_for(token, binding: str) -> tuple[str, list]:
    """(token, history) for this binding; a new empty session when the token is
    unknown, expired or bound to a different patient / typed Rx."""
    now = time.time()
    with _SESS_LOCK:
        for k in [k for k, v in _SESSIONS.items() if now - v["t"] > SESSION_TTL]:
            _SESSIONS.pop(k, None)
        s = _SESSIONS.get(token) if isinstance(token, str) else None
        if not s or s["binding"] != binding:
            if s:
                _SESSIONS.pop(token, None)
            token = secrets.token_urlsafe(16)
            s = _SESSIONS[token] = {"binding": binding, "history": [], "t": now}
            while len(_SESSIONS) > 20:
                _SESSIONS.pop(min(_SESSIONS, key=lambda k: _SESSIONS[k]["t"]), None)
        s["t"] = now
        return token, s["history"]


def make_client(key: str):
    import anthropic
    # max_retries=0: every billed attempt goes through the ledger's reserve() first.
    return anthropic.Anthropic(api_key=key, max_retries=0, timeout=60.0)


def clean_filters(inp) -> dict | None:
    if not isinstance(inp, dict):
        return None
    allowed = {"feature": compare.FEATURES, "colour": compare.COLOURS, "index": compare.INDEXES,
               "coating": compare.COATINGS, "type": compare.TYPES}
    if set(inp) - set(allowed):           # anything else (a patient, an Rx) is refused
        return None
    out = {}
    for k, vals in allowed.items():
        v = inp.get(k, "any")
        if v not in vals:
            return None
        out[k] = v
    return out


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "init-ledger":
        print("usage: python -m hub.lenschat init-ledger [path]")
        return 2
    from hub.assistant.ledger import Ledger
    path = Path(argv[1]) if len(argv) > 1 else Path(DEFAULTS["ledger"])
    if Ledger(path, cap=CAP_AUD).init():
        print(f"Ledger created: {path} (nothing spent; the cap comes from config/lenschat.json).")
        return 0
    print(f"A ledger already exists at {path} - left as it is (it never resets).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
