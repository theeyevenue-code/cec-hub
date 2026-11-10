"""iPad price sheet, steps 2 and 3: the staff side and Make job (Mark, 10 Nov 2026).

Direction: CEC-Optomate-Agent docs/designs/ipad-price-sheet-steps-2-3.md (branch
session/2026-11-10-ipad-sheet-direction). Mark's decisions of 10 Nov override it where they differ:
  1. the staff code EVERY time the patient list opens;
  2. live jobs only on lens links Mark has ticked (the engine refuses the rest when live);
  3. prism on the Rx = made to order only, no PD shift, no stock lens offered;
  4. rimless switch: 1.53, 1.60, 1.67 only;
  5. tints are in version 1 (the sheet map names every tint item; the engine checks the tint price).

The page (static/price-sheet.html) WORKS NOTHING OUT beyond its live total. Everything here is copied
or looked up: the Rx from Optomate (through the read-only bridge), which lenses cover it and the suggested
index from the Lens Finder, the prices from the one price file (hub/sheetprice.py), the stock blanks and
blank estimate from Lens options, the PD shift from hub/pdshift.py (Mark's rule, the one sanctioned
worked-out number), the job from the agent's engine (orders/pickjob.py), which honours DRY_RUN /
WRITE_LIVE / ZZTEST_ONLY. Nothing here writes to Optomate.

THE LOCK (director's section 2). The staff code opens ONE look at the patient list (2 minutes, until a
patient is picked). Picking a patient ties this iPad (an HttpOnly cookie) to that one patient: from then on
the server uses its OWN record of who is picked, ignores any patient number the page sends, and refuses
the patient list, the search and every other staff route of the Hub (app.py) until Next patient or the
staff code. 5 minutes untouched clears it all.

PRIVACY: patient data lives only in this process's memory and in no-store answers; nothing about a
patient reaches hub.log. The patient-facing answer (sheet()) carries the first name and card marks only:
no surname, DOB, Rx numbers, optom or Rx warnings.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time

from hub import checkin, lenses, lensoptions, pdshift, pickjob, sheetprice

logger = logging.getLogger("cec_hub.sheetjob")

COOKIE = "cec_sheet"
IDLE_S = 5 * 60                 # 5 minutes untouched: back to the sheet, everything cleared
LIST_S = 2 * 60                 # one staff-code unlock = one look at the patient list
RIMLESS = ("1.53", "1.60", "1.67")
HEIGHT_CATEGORIES = {"progressive", "anti-fatigue", "occupational", "bifocal"}
USE = {"sv": "D", "mf": "M", "af": "M", "office": "M", "bif": "B"}
TAB_OF_LENS_TYPE = {"distance": "sv", "near": "sv", "intermediate": "sv", "multifocal": "mf",
                    "office": "office", "bifocal": "bif"}
TYPE_WORDS = {"distance": "Distance", "near": "Reading", "intermediate": "Computer", "multifocal": "Multifocal",
              "office": "Office", "bifocal": "Bifocal"}
ASK_US = "Ask us about these lenses."
NOT_MADE = "Not made in your prescription"

_sessions: dict[str, dict] = {}
_lock = threading.RLock()


class Refusal(Exception):
    def __init__(self, status: int, body: dict):
        super().__init__(body.get("error", ""))
        self.status, self.body = status, body


def _err(status: int, msg: str, **extra) -> Refusal:
    return Refusal(status, {"error": msg, **extra})


# ---------------------------------------------------------------------------
# the lock
# ---------------------------------------------------------------------------

def _now() -> float:
    return time.time()


def _expire() -> None:
    now = _now()
    for t in [t for t, s in _sessions.items() if now - s["touched"] > IDLE_S]:
        _sessions.pop(t, None)


def session(token) -> dict | None:
    """The live session for this cookie, touched; None when there is none (or it timed out)."""
    with _lock:
        _expire()
        s = _sessions.get(str(token or ""))
        if s is not None:
            s["touched"] = _now()
        return s


def locked(token) -> bool:
    """True while this browser is tied to a patient (or holds the open list): app.py then refuses
    every other staff route for it."""
    with _lock:
        _expire()
        return str(token or "") in _sessions


def unlock(cfg: dict, code) -> str:
    ok, why = checkin.staff_code_ok(cfg, code)
    if not ok:
        raise _err(403, why)
    token = secrets.token_urlsafe(24)
    with _lock:
        _sessions[token] = {"touched": _now(), "list_until": _now() + LIST_S, "handles": {}, "pid": None}
    logger.info("Price sheet: staff code typed, patient list opened")
    return token


def end(token) -> None:
    with _lock:
        _sessions.pop(str(token or ""), None)


def _need(token, *, listing: bool = False) -> dict:
    s = session(token)
    if s is None:
        raise _err(401, "Timed out. Back to the price sheet.", expired=True)
    if listing:
        if s.get("pid") or _now() > s.get("list_until", 0):
            raise _err(403, "Type the staff code to see the patient list.", staff_needed=True)
    elif not s.get("pid"):
        raise _err(403, "Pick the patient first.", staff_needed=True)
    return s


def _handle(s: dict, pid: int, rx_id=None) -> str:
    h = secrets.token_urlsafe(9)
    s["handles"][h] = (int(pid), rx_id)
    return h


# ---------------------------------------------------------------------------
# screen 2: whose lenses?
# ---------------------------------------------------------------------------

def _rx_data(cfg: dict, pid: int) -> dict:
    data = lensoptions.run_bridge(cfg, "rx", "--pid", str(int(pid)))
    if data.get("error"):
        raise _err(503 if data.get("connected") is False else 400, data["error"])
    return data


def _type_of(rx: dict) -> str:
    lt = rx.get("lens_type") or {}
    if lt.get("source", "none") == "none":
        return ""
    return TYPE_WORDS.get(lt.get("type") or "", "")


def _blank_sphere(rx: dict) -> bool:
    eyes = [rx.get("right")] if rx.get("right_only") else [rx.get("left")] if rx.get("left_only") else [rx.get("right"), rx.get("left")]
    return any(not str((e or {}).get("sph") or "").strip() for e in eyes)


def patients(cfg: dict, token) -> dict:
    """The last three patients with a spectacle Rx saved today: name, time, optom, lens type written.
    No Rx numbers. Two Rx saved the same day: one line each, labelled by the type written."""
    s = _need(token, listing=True)
    got = lensoptions.recent(cfg)
    if got.get("error"):
        raise _err(503, got["error"])
    out = []
    for p in got.get("patients") or []:
        try:
            data = _rx_data(cfg, p["id"])
        except Refusal:
            data = {}
        rxs = [r for r in data.get("rxs") or [] if isinstance(r, dict) and str(r.get("saved_date") or "")[:10] == got.get("date")]
        for rx in rxs or [None]:
            out.append({"h": _handle(s, p["id"], (rx or {}).get("rx_id")), "name": p["name"],
                        "time": (rx or {}).get("saved") or p.get("saved") or "", "optom": (rx or {}).get("optom") or p.get("optom") or "",
                        "type": _type_of(rx) if rx else "", "two": len(rxs) > 1,
                        "stop": "Check the Rx in Optomate first." if rx and _blank_sphere(rx) else ""})
    return {"patients": out[:6], "none": "" if out else "No Rx saved today.", "fixture": bool(got.get("fixture"))}


def search(cfg: dict, token, q) -> dict:
    s = _need(token, listing=True)
    got = lensoptions.search(cfg, q)
    if got.get("error"):
        raise _err(400, got["error"])
    return {"patients": [{"h": _handle(s, p["id"]), "name": p["name"], "last_rx": p.get("last_rx") or ""}
                         for p in got.get("patients") or []], "more": bool(got.get("more"))}


def pick(cfg: dict, token, h) -> dict:
    """Tie this iPad to one patient. The page sends a handle from this session's own list, never a
    patient number."""
    s = _need(token, listing=True)
    ref = s["handles"].get(str(h or ""))
    if not ref:
        raise _err(400, "Pick the patient from the list again.")
    pid, rx_id = ref
    data = _rx_data(cfg, pid)
    rxs = [r for r in data.get("rxs") or [] if isinstance(r, dict)]
    if rx_id is None and len(rxs) > 1:
        return {"choose": [{"h": _handle(s, pid, r.get("rx_id")), "type": _type_of(r) or "Type not written",
                            "time": r.get("saved") or ""} for r in rxs]}
    rx = next((r for r in rxs if r.get("rx_id") == rx_id), rxs[0] if rxs else None)
    if rx is not None and _blank_sphere(rx):
        raise _err(400, "Check the Rx in Optomate first.")
    p = data.get("patient") or {}
    with _lock:
        s.update(pid=pid, rx=rx, first=str(p.get("given") or "").strip(), surname=str(p.get("surname") or "").strip(),
                 handles={}, list_until=0, frame_cache={})
    logger.info("Price sheet: a patient was picked (iPad now tied to that patient)")
    return {"ok": True}


# ---------------------------------------------------------------------------
# screen 3: the Rx on the sheet (patient-facing: first name and card marks only)
# ---------------------------------------------------------------------------

def _sv_typ(rx: dict) -> str:
    lt = rx.get("lens_type") or {}
    return "near" if lt.get("type") == "near" else "distance"


def _question(rx: dict, tab: str) -> tuple[dict, str, str]:
    """(Lens Finder question, lens type key, catalogue kind) for one sheet tab."""
    lt = rx.get("lens_type") or {}
    written_as = lt.get("type") if lt.get("as_written") else ""
    typ = {"sv": _sv_typ(rx), "mf": "multifocal", "af": "distance", "office": "office", "bif": "bifocal"}[tab]
    q = lensoptions.mode_question(rx, typ, written_as)
    kind = "Anti-fatigue" if tab == "af" else q["kind"]
    q["kind"] = kind
    return q, typ, kind


def _found(rows: list, pricing: dict, q: dict) -> dict:
    """Lens Finder over the price books for this question: {"keys": set, "rec": "1.60"}."""
    r, l = q.get("right"), q.get("left")
    first, other = (r, l) if r else (l, None)
    if not first:
        return {"keys": set(), "rec": ""}
    res = lenses.find_options(rows, first["sph"], first["cyl"], None, add=q.get("add"), kind=q["kind"],
                              pricing=pricing, left=other)
    keys = set()
    for o in res.get("options") or []:
        if not o.get("orderable", True):
            continue
        keys.add((str(o.get("supplier") or ""), str(o.get("name") or ""), lensoptions._fmt_index(o.get("index")),
                  str(o.get("material") or ""), str(o.get("coating") or ""), "stock" if o.get("type") == "stock" else "grind"))
    rec = res.get("rec_index")
    return {"keys": keys, "rec": lensoptions._fmt_index(rec) if rec else ""}


def _k(key: dict) -> tuple:
    return (key["supplier"], key["lens"], key["index"], key["material"], key["coating"], key["stockgrind"])


def resolve(book: dict, rx: dict | None, found: dict, ch: dict) -> dict:
    """One sheet choice against this Rx: {"state": stock | mto | none | plain, "choice": the choice with
    "made" settled, "quote": sheetprice.quote}. Single vision: the stock lens when it covers the Rx (and
    the Rx has no prism), else made to order at the sheet's own made-to-order price."""
    tab = ch["tab"]
    prism = bool(rx) and lensoptions.rx_has_prism(rx)
    tries = [ch]
    if tab == "sv":
        want = ch.get("made")
        tries = ([{**ch, "made": "mto"}] if (prism or want == "mto") else
                 [{**ch, "made": "stock"}, {**ch, "made": "mto"}])
    last = None
    for c in tries:
        q = sheetprice.quote(book, c)
        last = (c, q)
        if not q.get("ok"):
            return {"state": "off", "choice": c, "quote": q, "why": q.get("why", "")}
        if not q["map"].get("ok"):
            return {"state": "plain", "choice": c, "quote": q, "why": q["map"].get("why", "")}
        if prism and q["map"]["key"]["stockgrind"] == "stock":
            continue
        if _k(q["map"]["key"]) in found["keys"]:
            return {"state": "stock" if q["map"]["key"]["stockgrind"] == "stock" else "mto", "choice": c, "quote": q, "why": ""}
    c, q = last
    return {"state": "none", "choice": c, "quote": q, "why": NOT_MADE}


def card_states(book: dict, rx: dict, rows: list, pricing: dict) -> dict:
    """Every job tab: which cards suit, which are made to order for this Rx, which are not made in it.
    No numbers from the Rx leave this function."""
    out = {}
    data = book["data"]
    for pg in data["pages"]:
        tab = pg["key"]
        if tab not in USE:
            out[tab] = {"plain": True, "note": ""}
            continue
        q, typ, kind = _question(rx, tab)
        if q.get("unavailable"):
            out[tab] = {"plain": True, "note": ASK_US}
            continue
        found = _found(rows, pricing, q)
        cards = []
        for i, c in enumerate(pg["cards"]):
            per = {}
            for idx in sheetprice.indices(c):
                opts = sheetprice.options(book, c, idx) or [{"k": "clear"}]
                states = {}
                for o in opts:
                    ch = {"tab": tab, "card": i, "idx": idx, "opt": o["k"], "colour": "Grey", "depth": "s85", "pfx": "Grey"}
                    r = resolve(book, rx, found, ch)
                    states[o["k"]] = r["state"]
                clear = resolve(book, rx, found, {"tab": tab, "card": i, "idx": idx, "opt": "clear"})
                per[idx] = {"state": clear["state"], "opts": states,
                            "price": clear["quote"].get("total") if clear["quote"].get("ok") else None}
            cards.append(per)
        rec = found["rec"]
        out[tab] = {"plain": False, "rec": rec, "cards": cards,
                    "thicker": sorted({i for c in pg["cards"] for i in sheetprice.indices(c) if rec and i < rec and i != "1.53"}),
                    "note": "" if any(v["state"] in ("stock", "mto") for per in cards for v in per.values()) else ASK_US}
    return out


def first_tab(rx: dict | None) -> str:
    if not rx:
        return "sv"
    lt = rx.get("lens_type") or {}
    if lt.get("source", "none") == "none":
        return "sv"
    return TAB_OF_LENS_TYPE.get(lt.get("type") or "", "sv")


def sheet(cfg: dict, token, rows: list, pricing: dict) -> dict:
    """Screen 3, patient-facing: first name, the tab to open on, card marks. Nothing else."""
    s = _need(token)
    book = sheetprice.load()
    rx = s.get("rx")
    if not rx:
        return {"first": s.get("first", ""), "tab": "sv", "tabs": {}, "no_rx": True, "version": book["version"]}
    if "states" not in s:
        s["states"] = card_states(book, rx, rows, pricing)
    return {"first": s.get("first", ""), "tab": first_tab(rx), "tabs": s["states"], "version": book["version"]}


# ---------------------------------------------------------------------------
# screens 5-7: staff only
# ---------------------------------------------------------------------------

def _eye_words(e: dict | None) -> str:
    if not e:
        return "-"
    bits = [str(e.get("sph") or "").strip() or "(blank)"]
    if str(e.get("cyl") or "").strip() and str(e.get("cyl")).strip() not in ("0", "0.00"):
        bits.append(f"/ {e['cyl']} x {e.get('axis') or '?'}")
    if e.get("add"):
        bits.append(f"add {e['add']}")
    if e.get("prism"):
        bits.append(f"prism {e['prism']}")
    return " ".join(bits)


def staff_rx(s: dict) -> dict:
    rx = s.get("rx") or {}
    flags = lensoptions.rx_flags(rx) if rx else []
    if rx and not _type_of(rx):
        flags.append("Lens type not written on the Rx: confirm it with the optom.")
    if rx and lensoptions.rx_has_prism(rx):
        flags.append("Prism on the Rx: made to order only (no stock lens, no PD change).")
    pd = " / ".join(f"{s_} {(rx.get(k) or {}).get('pd')}" for s_, k in (("R", "right"), ("L", "left"))
                    if (rx.get(k) or {}).get("pd"))
    return {"name": f"{str(s.get('surname') or '').upper()}, {s.get('first') or ''}".strip(", "),
            "right": _eye_words(rx.get("right")), "left": _eye_words(rx.get("left")),
            "pd": pd or "PD missing on the Rx", "pd_missing": not pd, "saved": rx.get("saved") or "",
            "optom": rx.get("optom") or "", "type": _type_of(rx) or "not written", "warnings": flags}


def _frame_out(f: dict) -> dict:
    return {"barcode": f.get("barcode"), "brand": f.get("brand") or "", "model": f.get("model"), "colour": f.get("colour"),
            "a": f.get("a"), "dbl": f.get("dbl"), "ed": f.get("ed"), "price": f.get("price"), "qoh": f.get("qoh")}


def frame_lookup(cfg: dict, token, barcode="", q="") -> dict:
    """Digits + Enter = barcode; typing = the live list (brand, model, colour, size). Read only."""
    s = _need(token)
    barcode, q = str(barcode or "").strip()[:40], str(q or "").strip()[:60]
    if barcode:
        data = lensoptions.run_bridge(cfg, "frame", "--barcode", barcode)
        if data.get("error"):
            return {"error": "Not in Optomate. Scan again, search by name, or Own frame."}
        f = _frame_out(data.get("frame") or {})
        s.setdefault("frame_cache", {})[f["barcode"]] = f
        return {"frame": f}
    data = lensoptions.run_bridge(cfg, "frames", "--q", q)
    if data.get("error"):
        return {"frames": [], "more": False, "error": data["error"]}
    fs = [_frame_out(f) for f in data.get("frames") or []]
    for f in fs:
        s.setdefault("frame_cache", {})[f["barcode"]] = f
    return {"frames": fs, "more": bool(data.get("more"))}


def _clean_choice(ch) -> dict:
    ch = ch if isinstance(ch, dict) else {}
    out = {"tab": str(ch.get("tab") or "")[:8], "idx": str(ch.get("idx") or "")[:5], "opt": str(ch.get("opt") or "clear")[:8],
           "colour": str(ch.get("colour") or "Grey")[:12], "depth": str(ch.get("depth") or "s85")[:6],
           "pfx": str(ch.get("pfx") or "Grey")[:12], "made": "mto" if ch.get("made") == "mto" else None}
    try:
        out["card"] = int(ch.get("card"))
    except (TypeError, ValueError):
        out["card"] = -1
    return out


def _option_words(book: dict, ch: dict, q: dict) -> str:
    k = q.get("option")
    if k == "tint":
        dn = dict(book["data"]["solidDepths"] + book["data"]["gradDepths"]).get(ch["depth"], "")
        col = {"Green": "Green (Pioneer G15)"}.get(ch["colour"], ch["colour"])
        return f"{q.get('option_name')}: {col} {dn}{' graduated' if ch['depth'].startswith('g') else ''}"
    if k == "photo" and ch["tab"] != "bif":
        return f"{q.get('option_name')}, {ch['pfx']}"
    return q.get("option_name") or "Clear"


def _frame_for(s: dict, fr) -> tuple[dict | None, dict | None, str]:
    """(frame record from the server's own lookup, numbers for the blank estimate, problem words).
    The page's frame numbers are only used for Own frame (typed by staff)."""
    fr = fr if isinstance(fr, dict) else {}
    if fr.get("own"):
        nums = lensoptions.clean_frame({"a": fr.get("a"), "dbl": fr.get("dbl"), "ed": fr.get("ed"), "source": "typed"})
        if not nums or nums.get("dbl") is None:
            return None, None, "Own frame: type the eye size and bridge (ED if known)."
        return {"own": True}, nums, ""
    bc = str(fr.get("barcode") or "").strip()
    if not bc:
        return None, None, "Scan the frame, search by name, or Own frame."
    f = (s.get("frame_cache") or {}).get(bc)
    if not f:
        return None, None, "Scan the frame again."
    nums = lensoptions.clean_frame({"a": f.get("a"), "dbl": f.get("dbl"), "ed": f.get("ed"), "source": "scan"})
    return f, nums, "" if nums and nums.get("dbl") is not None else "The frame record has no eye size or bridge: type it as Own frame."


def _stock_fit(rows: list, pricing: dict, rx: dict, q: dict, typ: str, key: dict, nums: dict | None) -> dict:
    """The Lens options blank check for one stock lens, each eye from its own PD and its own Rx."""
    pds, pd_note = lensoptions.mono_pds(rx, near=(typ == "near"))
    est = lensoptions.blank_estimate(nums, pds, multi=False) if nums else None
    eyes = {s_: e for s_, e in (("R", q.get("right")), ("L", q.get("left"))) if e}
    avail = {s_: lensoptions.stock_blanks(rows, e, q, pricing) for s_, e in eyes.items()}
    pkey = (key["supplier"], key["lens"], "stock", key["material"])
    out = {"est": est, "pd_note": pd_note, "pds": pds, "pkey": pkey, "avail": avail}
    if not est:
        return {**out, "fits": None}
    need = {s_: est["per_eye"].get(s_, est["value"]) for s_ in avail}
    ok, got = lensoptions.blank_fit(pkey, avail, need)
    largest = {s_: max(avail.get(s_, {}).get(pkey) or [], default=None) for s_ in need}
    return {**out, "fits": ok, "got": got, "need": need, "largest": largest}


def _link(cfg: dict, key: dict) -> dict:
    """pickjob.link_for, but a stock lens with no blank chosen yet counts as linked when any of its blank
    sizes is (each size is checked again once the blank is known)."""
    rows = pickjob.links(cfg)
    if key.get("stockgrind") == "stock" and not key.get("blank_mm"):
        k6 = pickjob._key({**key, "blank_mm": ""})[:6]
        for r in rows:
            if pickjob._key(r)[:6] == k6 and str(r.get("lens_barcode") or "").strip():
                got = pickjob.link_for(rows, {**key, "blank_mm": r.get("blank_mm")})
                if got.get("ready"):
                    return got
    return pickjob.link_for(rows, {**key, "blank_mm": key.get("blank_mm") or ""})


def _mm(v) -> str:
    return f"{v:g} mm" if v is not None else ""


def checks(cfg: dict, token, data: dict, rows: list, pricing: dict) -> dict:
    """Screen 5's right side, all from the server: the lens as priced by the sheet, the blank verdict, the
    fixes (PD, other stock lens, made to order - each re-priced by the sheet), heights, staff warnings,
    and whether Make may be pressed (with the one-line reason when not)."""
    s = _need(token)
    book = sheetprice.load()
    data = data if isinstance(data, dict) else {}
    if str(data.get("version") or "") != book["version"]:
        raise _err(409, "Prices were updated. Reload.", reload=True)
    rx = s.get("rx")
    if not rx:
        raise _err(400, "No spectacle Rx saved for this patient. Make this job in Touch.")
    ch = _clean_choice(data.get("choice"))
    if ch["tab"] not in USE:
        return {"stop": "Make this job in Touch.", "can_make": False, "why": "Price only in version 1.", "rx": staff_rx(s)}
    q, typ, kind = _question(rx, ch["tab"])
    if q.get("unavailable"):
        return {"stop": q["unavailable"], "can_make": False, "why": "This lens type doesn't apply to the Rx.", "rx": staff_rx(s)}
    found = _found(rows, pricing, q)
    start = resolve(book, rx, found, ch)                       # what the patient chose, as the sheet prices it
    fix = data.get("fix") if isinstance(data.get("fix"), dict) else {}
    fr_rec, nums, frame_problem = _frame_for(s, data.get("frame"))
    rimless = bool(data.get("rimless"))
    res = {"rx": staff_rx(s), "start_price": start["quote"].get("total"), "fixes": [], "warnings": [], "stop": "",
           "heights_needed": False, "verdict": None, "blank": None, "pd_shift": None, "fix": ""}
    # the fixes always come from what the patient chose, so a fix that is picked can be seen and undone
    cur, applied, start_fit = start, None, None
    if start["state"] == "stock" and nums is not None and not frame_problem:
        start_fit = _stock_fit(rows, pricing, rx, q, typ, start["quote"]["map"]["key"], nums)
        if start_fit["fits"] is False:
            res["fixes"] = _fixes(book, rx, rows, pricing, found, q, typ, start, start_fit, nums, fix)
            applied = next((f for f in res["fixes"] if f["on"]), None)
            if applied and applied["kind"] == "stock":
                j = next((j for j, c in enumerate(sheetprice.page(book, ch["tab"])["cards"]) if applied["idx"] in sheetprice.indices(c)), None)
                cur = resolve(book, rx, found, {**ch, "card": j, "idx": applied["idx"], "made": None})
            elif applied and applied["kind"] == "mto":
                cur = resolve(book, rx, found, {**ch, "made": "mto"})
            res["fix"] = applied["kind"] if applied else ""
    qq = cur["quote"]
    res.update(lens=sheetprice.card_title(book, cur["choice"]), option=_option_words(book, cur["choice"], qq) if qq.get("ok") else "",
               made="Stock" if cur["state"] == "stock" else "Made to order", price=qq.get("total"),
               lens_pair=qq.get("lens_pair"), tint_pair=qq.get("tint_pair"), choice=cur["choice"])
    if cur["state"] == "off":
        res["stop"] = cur["why"] or "Not on the price sheet."
    elif cur["state"] == "plain":
        res["stop"] = "Price only: make this job in Touch."
    elif cur["state"] == "none":
        res["stop"] = "Not made in this prescription. Change lens."
    link = None
    if not res["stop"]:
        key = qq["map"]["key"]
        link = _link(cfg, key)
        if not link.get("ready"):
            res["stop"] = "Not linked to Optomate yet. Make this job in Touch." + (f" ({link['why']})" if link.get("why") and "linked" not in link["why"] else "")
        cat = next((r.get("category") for r in pickjob.links(cfg) if pickjob._key(r)[:6] == pickjob._key({**key, "blank_mm": ""})[:6]), "") or ""
        res["heights_needed"] = cat.lower() in HEIGHT_CATEGORIES or bool(sheetprice.page(book, ch["tab"]).get("heights"))
        if rimless and key["index"] not in RIMLESS:
            res["stop"] = "Rimless frame: 1.53, 1.60 or 1.67 only. Change lens."
    if frame_problem:
        res["verdict"] = {"tone": "none", "head": "Scan the frame.", "detail": frame_problem}
    elif fr_rec and not fr_rec.get("own"):
        res["frame"] = fr_rec
        if fr_rec.get("price") is None:
            res["stop"] = res["stop"] or "This frame has no sell price in Optomate. Fix the frame record or use Own frame."
        if not fr_rec.get("qoh"):
            res["warnings"].append("Optomate shows none on hand.")
    # --- the blank ---
    ref = None
    if not res["stop"] and nums is not None:
        key = qq["map"]["key"]
        if key["stockgrind"] == "grind":
            res["verdict"] = {"tone": "blue", "head": "Made to order, cut to this frame",
                              "detail": "Fix picked: made to order." if applied else "No stock blank to check."}
        else:
            fit = start_fit if cur is start and start_fit else _stock_fit(rows, pricing, rx, q, typ, key, nums)
            est = fit["est"]
            if est:
                res["blank"] = {"per_eye": est["per_eye"], "working": est["working"], "approx": est["approx"],
                                "label": "approximate" if est["approx"] else "estimate", "notes": est["notes"]}
            if fit["fits"] is True:
                b = max(v for v in fit["got"].values() if v)
                ref = {**key, "blank_mm": b}
                res["verdict"] = {"tone": "green", "head": f"Fits a {_mm(b)} stock blank",
                                  "detail": (f"Fix picked: {applied['title']}. " if applied else "")
                                  + f"Blank needed {_mm(est['value'])} ({res['blank']['label']})."}
            elif fit["fits"] is False:
                comes = max((v for v in fit["largest"].values() if v), default=None)
                res["verdict"] = {"tone": "amber", "head": f"Stock blank too small: needs {_mm(est['value'])}, comes {_mm(comes)}",
                                  "detail": f"Blank needed {_mm(est['value'])} ({res['blank']['label']})."}
                if applied and applied["kind"] == "pd":
                    res["pd_shift"] = applied["shift"]
                    ref = {**key, "blank_mm": applied["blank"]}
                    res["verdict"] = {"tone": "green", "head": f"Fits a {_mm(applied['blank'])} stock blank with the wider PD",
                                      "detail": applied["detail"]}
            else:
                res["verdict"] = {"tone": "none", "head": "Blank not checked", "detail": "PD missing on the Rx."}
        if key["stockgrind"] == "grind":
            ref = {**key, "blank_mm": ""}
    if ref and ref.get("blank_mm"):
        # a stock lens is one Optomate lens per blank size: the blank this job needs must be linked too
        lk = _link(cfg, ref)
        if not lk.get("ready"):
            res["stop"] = f"The {_mm(ref['blank_mm'])} blank of this lens isn't linked to Optomate yet. Make this job in Touch."
            ref = None
    res["ref"] = ref
    if ref:
        res["lens_use"] = "N" if (ch["tab"] == "sv" and typ == "near") else USE[ch["tab"]]
        res["tint_barcode"] = qq["map"].get("tint_barcode") or ""
    res["price_changed"] = res["price"] is not None and res["start_price"] is not None and res["price"] != res["start_price"]
    res["can_make"], res["why"] = _can_make(res, data, s)
    res["mode"] = mode_words(cfg, s)
    return res


def _fixes(book, rx, rows, pricing, found, q, typ, cur, fit, nums, fix) -> list[dict]:
    """In this order (Mark: stock before grind): 1 widen the PD (keeps the price), 2 another stock lens that
    fits, 3 made to order, same lens. Each with the NEW pair price, re-priced by the sheet."""
    ch, tab = cur["choice"], cur["choice"]["tab"]
    out = []
    # 1 the PD (single vision, stock, no prism, both monocular PDs as saved: Mark, 9 Nov 2026)
    if tab == "sv" and not lensoptions.rx_has_prism(rx) and not fit["pd_note"]:
        powers = {}
        for side, k in (("R", "right"), ("L", "left")):
            e = q.get(k)
            if e:
                powers[side] = pdshift.horizontal_power(e["sph"], e["cyl"], lensoptions.num((rx.get(k) or {}).get("axis")))
        if set(powers) == set(fit["pds"]) and all(v is not None for v in fit["largest"].values()):
            v = pdshift.find(nums, fit["pds"], powers, fit["largest"])
            if v:
                out.append({"kind": "pd", "title": "Widen the PD", "best": "Keeps the price", "price": cur["quote"]["total"],
                            "detail": v["line"], "shift": {k: x for k, x in v["shift"].items() if x},
                            "blank": max(v["blank"].values()), "on": fix.get("kind") == "pd"})
    # 2 another stock lens that fits (other thicknesses on this tab, the patient's option kept)
    if tab == "sv":
        for j, c in enumerate(sheetprice.page(book, "sv")["cards"]):
            if c["idx"] == ch["idx"]:
                continue
            alt = resolve(book, rx, found, {**ch, "card": j, "idx": c["idx"], "made": None})
            if alt["state"] != "stock":
                continue
            f2 = _stock_fit(rows, pricing, rx, q, typ, alt["quote"]["map"]["key"], nums)
            if f2["fits"]:
                b = max(x for x in f2["got"].values() if x)
                out.append({"kind": "stock", "idx": c["idx"], "title": f"{c['idx']} stock lens",
                            "detail": f"{c.get('product')}, {_mm(b)} blank fits", "price": alt["quote"]["total"],
                            "best": "", "on": fix.get("kind") == "stock" and str(fix.get("idx")) == c["idx"]})
                if len([f for f in out if f["kind"] == "stock"]) >= 2:
                    break
    # 3 made to order, same lens and options
    if tab == "sv":
        m = resolve(book, rx, found, {**ch, "made": "mto"})
        if m["state"] == "mto":
            out.append({"kind": "mto", "title": "Made to order", "detail": f"{sheetprice.card_title(book, m['choice'])}, cut to the frame",
                        "price": m["quote"]["total"], "best": "", "on": fix.get("kind") == "mto"})
    return out


def _can_make(res: dict, data: dict, s: dict) -> tuple[bool, str]:
    if res.get("stop"):
        return False, res["stop"]
    if res["rx"].get("pd_missing"):
        return False, "PD missing on the Rx. Add it in Optomate first."
    fr = data.get("frame") if isinstance(data.get("frame"), dict) else {}
    if not (fr.get("own") or res.get("frame")):
        return False, "Scan the frame, search by name, or Own frame."
    if not res.get("ref"):
        if res.get("verdict") and res["verdict"].get("tone") == "amber":
            return False, "Pick a fix for the blank."
        return False, (res.get("verdict") or {}).get("detail") or "Check the frame first."
    if res["heights_needed"]:
        h = data.get("heights") if isinstance(data.get("heights"), dict) else {}
        try:
            ok = all(10 <= float(h.get(x)) <= 40 for x in ("R", "L"))
        except (TypeError, ValueError):
            ok = False
        if not ok:
            return False, "Type both fitting heights (10 to 40 mm)."
    if res.get("price_changed") and not data.get("told"):
        return False, f"Tick \"Told the patient\": was ${res['start_price']}, now ${res['price']}."
    return True, ""


def mode_words(cfg: dict, s: dict) -> dict:
    """The Make button always says its mode."""
    if "mode" not in s:
        st = pickjob.status(cfg)
        if st.get("write_live") and not st.get("fixture"):
            s["mode"] = {"button": "Make test job (ZZTEST)" if st.get("zztest_only") else "Make job in Optomate", "live": True}
        else:
            s["mode"] = {"button": "Make job (dry run, nothing written)", "live": False}
    return s["mode"]


def _engine_request(s: dict, res: dict, data: dict) -> dict:
    fr = data.get("frame") if isinstance(data.get("frame"), dict) else {}
    h = data.get("heights") if isinstance(data.get("heights"), dict) else {}
    rx = s["rx"]
    return {"patient_id": s["pid"], "rx_id": rx.get("rx_id"), "rx_saved_at": str(rx.get("saved_at") or "")[:19],
            "option": {k: res["ref"].get(k) for k in ("supplier", "lens", "index", "material", "coating", "stockgrind", "blank_mm")},
            "lens_use": res["lens_use"], "hub_pair_price": res["lens_pair"], "sheet_tint_pair": res["tint_pair"],
            "tint_barcode": res.get("tint_barcode") or "",
            "frame": ({"own": True, "a": fr.get("a"), "dbl": fr.get("dbl"), "ed": fr.get("ed")} if fr.get("own")
                      else {"own": False, "barcode": (res.get("frame") or {}).get("barcode")}),
            "heights": {x: h.get(x) for x in ("R", "L")} if res["heights_needed"] else {},
            "second_pair": bool(data.get("second_pair")), "pd_shift": res.get("pd_shift"), "rimless": bool(data.get("rimless"))}


def job(cfg: dict, token, data: dict, rows: list, pricing: dict, user: str, command: str) -> dict:
    """Screen 6 (plan) and 7 (make): the checks again, then the engine. The engine re-reads Optomate fresh."""
    s = _need(token)
    res = checks(cfg, token, data, rows, pricing)
    if not res.get("can_make"):
        return {"ok": False, "refusals": [res.get("why") or "Not ready yet."], "checks": res}
    req = _engine_request(s, res, data)
    eng = pickjob.run_engine(cfg, "plan" if command == "plan" else "make", {**pickjob.clean_request(req, user),
                                                                             "sheet_tint_pair": res["tint_pair"],
                                                                             "rimless": bool(data.get("rimless"))})
    pat = (eng.get("preview") or {}).get("patient")
    if isinstance(pat, dict):
        pat.pop("dob", None)                         # screens 5-7: no DOB (director's section 2)
    eng["sheet"] = {"lens": res["lens"], "option": res["option"], "made": res["made"], "price": res["price"],
                    "lens_pair": res["lens_pair"], "tint_pair": res["tint_pair"]}
    eng["mode_words"] = res["mode"]
    return eng
