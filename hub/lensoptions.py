"""Lens options page - click a patient, see the lenses for their saved Rx.

Mark, 3 Nov 2026: staff who don't know optics pick one of the last three
patients whose spectacle Rx was saved today (or search by name), and the page
shows the best spectacle lens for that Rx, the alternatives and the prices,
in plain words.

  hub/lens_bridge.py    reads Optomate (SQL, read only) in a subprocess
  this module           turns the saved Rx into Lens Finder questions, one per
                        lens type, and trims the answers for the page
  hub/lenses.py         the existing Lens Finder (find_options) - unchanged

WHICH LENS TYPE. The optom writes it in the Rx comment (SV, SVN, near,
computer, MF, bifocal, office ...); lens_bridge.detect_type reads it. Every
type the Rx can make gets a tab; the one written leads. Nothing written and an
add on the Rx -> multifocal leads, with a line asking for the type to be
written. Never a silent guess.

NUMBERS. The Rx strings are shown verbatim. The lookup reads them as numbers;
the only figures worked out here are a reading / computer pair (sphere + add,
or + INT add, per eye) and they are always shown with their working
("R -0.75 + add +2.25 = +1.50"). Prices and ranges are the Lens Finder's own.

BLANK SIZE. No frame has been chosen at this point, so the blank is not
checked; the page says so and the stock blank sizes are shown.

PRIVACY. Names travel in POST bodies and answers, never in a URL or hub.log.
Every answer is served no-store.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from hub import checkin, lenses

BRIDGE = Path(__file__).resolve().with_name("lens_bridge.py")
HUB_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_S = 45
NOT_CONNECTED = "Lens options isn't connected to Optomate on this computer."

TYPE_LABELS = {
    "multifocal": "Multifocal",
    "near": "Reading",
    "office": "Office",
    "intermediate": "Computer",
    "distance": "Distance",
    "bifocal": "Bifocal",
}
TYPE_LONG = {
    "multifocal": "Multifocal (distance to reading in one lens)",
    "near": "Reading glasses (single vision)",
    "office": "Office lens (desk and computer)",
    "intermediate": "Computer glasses (single vision)",
    "distance": "Distance glasses (single vision)",
    "bifocal": "Bifocal",
}
CATALOG_KIND = {
    "distance": "Single vision", "near": "Single vision", "intermediate": "Single vision",
    "office": "Occupational", "multifocal": "Progressive", "bifocal": "Bifocal",
}
# Staff-facing category words (Mark, 22 Sep 2026: product names, not tiers).
CATEGORY_WORD = {"Single vision": "Single vision", "Progressive": "Multifocal",
                 "Occupational": "Office", "Bifocal": "Bifocal", "Anti-fatigue": "Digital"}
SHOW = 5


def fixture_mode() -> bool:
    return (os.environ.get("LENS_OPTIONS_FIXTURE", "") or "").strip().lower() in ("1", "true", "yes")


def connected(cfg: dict) -> bool:
    if fixture_mode():
        return True
    d = checkin.agent_dir(cfg)
    return bool(d and d.is_dir())


def run_bridge(cfg: dict, *args: str) -> dict:
    """Run hub/lens_bridge.py and return its JSON answer. Validated arguments
    only. Never raises."""
    if not connected(cfg):
        return {"connected": False, "error": NOT_CONNECTED}
    python = (cfg.get("optomate_agent") or {}).get("python") or sys.executable
    try:
        proc = subprocess.run([python, str(BRIDGE), *args], cwd=str(HUB_ROOT),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"error": "Optomate took too long - try again."}
    except OSError:
        return {"connected": False, "error": "Couldn't start the Lens options helper."}
    line = next((c.strip() for c in reversed((proc.stdout or "").splitlines()) if c.strip()), "")
    try:
        data = json.loads(line) if line else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {"error": "The Lens options helper didn't give a readable answer."}
    return data


# ---------------------------------------------------------------------------
# Bridge calls
# ---------------------------------------------------------------------------

def recent(cfg: dict) -> dict:
    """The last three patients whose spectacle Rx was saved today. A test copy
    can point at a past day with CEC_HUB_LENS_OPTIONS_DATE (environment only)."""
    args = ["recent", "--n", "3"]
    day = (os.environ.get("CEC_HUB_LENS_OPTIONS_DATE") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        args += ["--date", day]
    data = run_bridge(cfg, *args)
    if data.get("error"):
        return data
    return {"date": str(data.get("date") or ""), "more": bool(data.get("more")),
            "test_day": len(args) > 3,
            "fixture": bool(data.get("fixture")),
            "patients": [{"id": int(p["id"]), "name": _name(p), "saved": str(p.get("saved") or "")}
                         for p in data.get("patients") or [] if isinstance(p, dict) and p.get("id")]}


def search(cfg: dict, q) -> dict:
    q = str(q or "").strip()[:80]
    if len(q) < 2:
        return {"error": "Type the surname and first name."}
    data = run_bridge(cfg, "search", "--q", q)
    if data.get("error"):
        return data
    return {"more": bool(data.get("more")),
            "patients": [{"id": int(p["id"]), "name": _name(p), "dob": _dmy(p.get("dob")),
                          "last_rx": _dmy(p.get("last_rx"))}
                         for p in data.get("patients") or [] if isinstance(p, dict) and p.get("id")]}


def _name(p: dict) -> str:
    g, s = str(p.get("given") or "").strip(), str(p.get("surname") or "").strip()
    return f"{s.upper()}, {g}" if s and g else (s or g or "Patient")


def _dmy(iso) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(iso or "")[:10])
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""


# ---------------------------------------------------------------------------
# Rx -> Lens Finder questions
# ---------------------------------------------------------------------------

def num(raw) -> Decimal | None:
    from hub.lens_bridge import parse_power
    return parse_power(raw)


def fmt_power(d: Decimal | None) -> str:
    if d is None:
        return ""
    return "0.00" if d == 0 else f"{d:+.2f}"


def _eye_numbers(eye: dict | None) -> dict | None:
    """{sph, cyl, add, intadd} as Decimals for the lookup, or None when the eye
    has no usable sphere (balance lens, blank, unreadable, not on the Rx)."""
    if not eye:
        return None
    sph = num(eye.get("sph"))
    if sph is None:
        return None
    return {"sph": sph, "cyl": num(eye.get("cyl")) or Decimal("0"),
            "add": num(eye.get("add")), "intadd": num(eye.get("intadd"))}


def rx_flags(rx: dict) -> list[str]:
    """Plain-words warnings for the bench. Never a clinical judgement."""
    flags = []
    if rx.get("right_only") or rx.get("left_only"):
        flags.append(f"This Rx is for the {'right' if rx.get('right_only') else 'left'} eye only.")
    for side, key in (("Right", "right"), ("Left", "left")):
        eye = rx.get(key)
        if not eye:
            continue
        raw = str(eye.get("sph") or "").strip()
        if raw.upper().startswith("BAL"):
            flags.append(f"{side} is a balance lens - lenses below are matched on the other eye.")
        elif not raw:
            flags.append(f"{side} sphere is blank on the Rx - check the Rx.")
        elif num(raw) is None:
            flags.append(f"{side} sphere reads \"{raw}\" - check the Rx.")
        if eye.get("prism"):
            flags.append(f"Prism {side.lower()} {eye['prism']}: check the lens can take it.")
    adds = {e.get("add") for e in (rx.get("right"), rx.get("left")) if e}
    if len(adds) > 1 and any(adds) and not (rx.get("right_only") or rx.get("left_only")):
        flags.append("The adds differ between the eyes - check the Rx.")
    return flags


def mode_question(rx: dict, typ: str, written_as: str = "") -> dict:
    """One lens type -> the Lens Finder question, or why it can't be asked.
    {"right": {sph, cyl}|None, "left": ..., "add": float|None, "kind",
     "working": [lines], "notes": [lines], "unavailable": "" | why}"""
    r, l = _eye_numbers(rx.get("right")), _eye_numbers(rx.get("left"))
    eyes = [("R", r), ("L", l)]
    out = {"right": None, "left": None, "add": None, "kind": CATALOG_KIND[typ],
           "working": [], "notes": [], "unavailable": ""}
    if not r and not l:
        out["unavailable"] = "No sphere to look up on this Rx - check the Rx in Optomate."
        return out

    def pair(e):
        return {"sph": float(e["sph"]), "cyl": float(e["cyl"])} if e else None

    # written_as: this Rx has no add and its comment says it IS the reading
    # (or computer) pair, so its powers are used as written for that type only.
    if written_as:
        if typ == written_as:
            out["right"], out["left"] = pair(r), pair(l)
            out["notes"].append("Powers as written: this Rx is saved as the "
                                + ("reading" if typ == "near" else "computer") + " pair.")
        else:
            out["unavailable"] = ("This Rx is saved as the "
                                  + ("reading" if written_as == "near" else "computer")
                                  + " pair (no add), so it has no powers for this lens.")
        return out
    if typ == "distance":
        out["right"], out["left"] = pair(r), pair(l)
        return out
    if typ in ("near", "intermediate"):
        key, word = ("add", "add") if typ == "near" else ("intadd", "INT add")
        if not any(e and e[key] for _, e in eyes):
            out["unavailable"] = ("Intermediate add not on the Rx - ask the optom for the "
                                  "computer powers." if typ == "intermediate"
                                  else "No add on the Rx, so there are no reading powers to work out.")
            return out
        for side, e in eyes:
            if not e:
                continue
            if e[key]:
                total = e["sph"] + e[key]
                out["working"].append(f"{side} {fmt_power(e['sph'])} + {word} {fmt_power(e[key])} "
                                      f"= {fmt_power(total)}")
                e2 = {"sph": total, "cyl": e["cyl"]}
            else:
                out["notes"].append(f"{side} has no {word} on the Rx - its sphere is used as written.")
                e2 = e
            out["right" if side == "R" else "left"] = pair(e2)
        out["notes"].append("Cyl and axis as on the Rx.")
        return out
    # multifocal / office / bifocal: the distance Rx plus the add
    adds = [e["add"] for _, e in eyes if e and e["add"]]
    if not adds:
        out["unavailable"] = "No add on the Rx, so this lens type doesn't apply."
        return out
    out["right"], out["left"] = pair(r), pair(l)
    out["add"] = float(max(adds))
    if len(set(adds)) > 1:
        out["notes"].append(f"Adds differ - checked at the higher, {fmt_power(max(adds))}.")
    return out


# ---------------------------------------------------------------------------
# Lens Finder answers, trimmed for the page
# ---------------------------------------------------------------------------

def sell_index(products: list) -> dict:
    """(supplier, name, index, type) -> {coating: sell price per pair, "": any}.
    Same key as the Lens Finder page's sellIndex()."""
    idx = {}
    for p in products or []:
        by = {c.get("coating"): c.get("cec_price") for c in p.get("coatings") or []
              if c.get("cec_price") is not None}
        by[""] = p.get("cec_price")
        idx[(p.get("supplier"), p.get("name"), _fmt_index(p.get("index")), p.get("type"))] = by
    return idx


def _fmt_index(v) -> str:
    return f"{float(v):.2f}" if v is not None else ""


def _short(name) -> str:
    return re.sub(r"\s+1\.\d\d$", "", str(name or "")).strip()


def _lens_out(o: dict, sells: dict, best_price) -> dict:
    by = sells.get((o.get("supplier"), o.get("name"), _fmt_index(o.get("index")), o.get("type"))) or {}
    sell = by.get(o.get("coating"), by.get(""))
    cat = lenses.normalise_category(o.get("category", ""))
    name = _short(o.get("name"))
    if cat == "Single vision":
        name = re.sub(r"\s*single vision\s*", " ", name, flags=re.I).strip() or name
    price = o.get("price_now")
    return {
        "index": _fmt_index(o.get("index")), "category": CATEGORY_WORD.get(cat, cat),
        "name": name, "supplier": str(o.get("supplier") or ""),
        "type": "Stock" if o.get("type") == "stock" else "Grind",
        "coating": str(o.get("coating") or ""), "code": str(o.get("code") or ""),
        "blank_mm": o.get("blank_mm"), "cost": price, "basis": o.get("basis") or "",
        "sell": sell,
        "dearer_by": (round(price - best_price, 2)
                      if price is not None and best_price is not None else None),
        "thicker": bool(o.get("under_index")),
        "warnings": [w for w in (o.get("warnings") or []) if "no longer ordered" not in w][:4],
    }


def _pkey(o: dict) -> tuple:
    """One lens as ordered, whatever its coating or power band."""
    return (o.get("supplier"), o.get("name"), o.get("type"), o.get("material") or "")


def stock_blanks(rows: list, eye: dict | None, q: dict, pricing: dict) -> dict:
    """{lens: sorted stock blank sizes (mm) that take THIS eye's own Rx}, from
    the price files' own per-row ranges (a plus band is often 65 mm only, a
    minus band 70/75 mm; it differs by index and brand)."""
    if not eye:
        return {}
    res = lenses.find_options(rows, eye["sph"], eye["cyl"], None, add=q["add"],
                              kind=q["kind"], pricing=pricing)
    out = {}
    for o in res.get("options") or []:
        if o.get("type") == "stock" and o.get("blank_mm") is not None:
            out.setdefault(_pkey(o), set()).add(float(o["blank_mm"]))
    return {k: sorted(v) for k, v in out.items()}


def _mm(v) -> str:
    return f"{v:g} mm"


def blank_fit(key, avail: dict, need: dict) -> tuple[bool, dict]:
    """Per eye: the smallest stock blank of this lens that is at least the
    blank the frame needs. -> (fits both eyes, {"R": mm|None, ...})."""
    got = {}
    for side, req in need.items():
        sizes = avail.get(side, {}).get(key) or []
        got[side] = next((b for b in sizes if b >= req - 1e-9), None)
    return all(v is not None for v in got.values()), got


def blank_line(o: dict, key, avail: dict, need: dict | None, fits_at: list) -> tuple[bool, str]:
    """One plain verdict about the blank for one lens option."""
    if o.get("type") != "stock":
        return True, "Made to order - cut to the frame, no stock blank needed"
    sizes = {s: avail.get(s, {}).get(key) or [] for s in avail}
    if not need:
        both = sorted({b for v in sizes.values() for b in v})
        return True, ("Stock blank " + " / ".join(_mm(b) for b in both) + " for this Rx"
                      if both else "Stock")
    ok, got = blank_fit(key, avail, need)
    if ok:
        vals = sorted(set(got.values()))
        if len(vals) == 1:
            return True, f"Fits a {_mm(vals[0])} stock blank"
        return True, "Fits stock: " + ", ".join(f"{s} {_mm(v)}" for s, v in got.items())
    req = max(need.values())
    have = max((b for v in sizes.values() for b in v), default=None)
    line = f"Needs {_mm(req)}" + (f" - this lens only comes {_mm(have)} for this Rx" if have else "")
    if fits_at:
        line += f". Big enough stock: {', '.join(fits_at)}"
    else:
        line += ". No stock blank big enough for this Rx at this index: grind (or a smaller frame)"
    return False, line


def summarise(result: dict, sells: dict, avail: dict | None = None,
              need: dict | None = None) -> dict:
    """The engine's answer cut to what the page shows: one line per lens, each
    with its blank verdict; the cheapest that FITS leads (engine order, with
    stock lenses too small for the frame moved behind the ones that fit)."""
    avail = avail or {}
    options = [o for o in result.get("options") or [] if o.get("orderable", True)
               and not o.get("hard_under") and o.get("standard_coating", True)
               and o.get("plain", True)]
    lines, seen = [], set()
    for o in options:
        key = _pkey(o)
        if key in seen:
            continue
        seen.add(key)
        lines.append((o, key))
    # which indices have a stock lens big enough (for the "needs 70 mm" lines)
    fit_idx = sorted({_fmt_index(o.get("index")) for o, k in lines
                      if o.get("type") == "stock" and need and blank_fit(k, avail, need)[0]})
    verdicts = []
    for o, key in lines:
        ok, line = blank_line(o, key, avail, need, [f"{i} stock" for i in fit_idx])
        verdicts.append((ok, o, key, line))
    ordered = [v for v in verdicts if v[0]] + [v for v in verdicts if not v[0]]
    best = next((v for v in ordered if v[0] and v[1].get("price_now") is not None), None)
    best_price = best[1].get("price_now") if best else None

    def out(v):
        ok, o, key, line = v
        x = _lens_out(o, sells, best_price)
        x["fits"], x["blank_line"] = ok, line
        sizes = sorted({b for side in avail.values() for b in side.get(key) or []})
        if sizes and o.get("type") == "stock":
            x["blanks"] = sizes
            got = blank_fit(key, avail, need)[1] if need else {}
            picked = [b for b in got.values() if b]
            # the blank to order: the one that fits the frame, else the biggest there is
            x["blank_mm"] = max(picked) if ok and picked else (sizes[-1] if not ok else sizes[0])
        return x
    rest = [out(v) for v in ordered if v is not best]
    engine_best = next((o for o in result.get("options") or [] if o.get("best")), None)
    note = ""
    if best and engine_best is not None and _pkey(engine_best) != best[2]:
        note = ("The usual first choice is too small for this frame (blank estimate), "
                "so the next lens that fits leads.")
    if need and best is None and verdicts:
        note = "No lens we order fits this frame from stock - ask about a grind or a smaller frame."
    return {
        "best": out(best) if best else None,
        "others": rest[:SHOW], "more": max(0, len(rest) - SHOW),
        "blank_note": note,
        "headline": result.get("headline") or "", "verdict": result.get("verdict") or "",
        "rec_index": _fmt_index(result.get("rec_index")),
        "rx_display": (result.get("rx") or {}).get("display", ""),
        "rx_left_display": (result.get("rx_left") or {}).get("display", ""),
    }


def run_mode(rows: list, pricing: dict, sells: dict, q: dict,
             est: dict | None = None) -> dict | None:
    """Both eyes through the Lens Finder (a lens is an option only when both
    eyes' powers are in its range), then each eye's own stock blanks against
    the blank that eye needs for the frame."""
    r, l = q["right"], q["left"]
    first, other = (r, l) if r else (l, None)
    if not first:
        return None
    res = lenses.find_options(rows, first["sph"], first["cyl"], None, add=q["add"],
                              kind=q["kind"], pricing=pricing, left=other)
    avail = {s: stock_blanks(rows, e, q, pricing) for s, e in (("R", r), ("L", l)) if e}
    need = None
    if est:
        need = {s: est["per_eye"].get(s, est["value"]) for s in avail}
    return summarise(res, sells, avail, need)


# ---------------------------------------------------------------------------
# Blank size estimate from the frame (Mark, 3 Nov 2026)
# ---------------------------------------------------------------------------

def _f(v, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi else None


def clean_frame(frame) -> dict | None:
    """Frame numbers typed by staff or read from the last order, range-checked.
    None when there is no usable eye size."""
    frame = frame if isinstance(frame, dict) else {}
    a = _f(frame.get("a"), 30, 75)
    if a is None:
        return None
    return {"a": a, "dbl": _f(frame.get("dbl"), 10, 30), "ed": _f(frame.get("ed"), 30, 90),
            "source": str(frame.get("source") or "typed")[:10],
            "order_date": str(frame.get("order_date") or "")[:10]}


def mono_pds(rx: dict, near: bool = False) -> tuple[dict, str]:
    """{"R": mm, "L": mm} monocular PDs from the saved Rx, and a note.
    Near PD for a reading pair when saved, else the distance PD (said so). A
    single figure over 40 is a binocular PD and is halved per eye (said so)."""
    out, notes = {}, []
    used_dist = False
    for side, key in (("R", "right"), ("L", "left")):
        e = rx.get(key) or {}
        v = num(e.get("near_pd")) if near else None
        if near and v is None:
            used_dist = True
        if v is None:
            v = num(e.get("pd"))
        if v and v > 0:
            out[side] = float(v)
    if len(out) == 1:
        (side, v), = out.items()
        if v > 40:
            out = {"R": v / 2, "L": v / 2}
            notes.append(f"One PD of {v:g} on the Rx - taken as {v / 2:g} each eye.")
    if near and used_dist and out:
        notes.append("Near PD not on the Rx - distance PD used, so the reading blank "
                     "may need to be a little bigger.")
    return out, " ".join(notes)


def _eye_order(items):
    return sorted(items, key=lambda kv: kv[0] != "R")


def blank_estimate(frame: dict | None, pds: dict, multi: bool = False) -> dict | None:
    """The smallest blank the job needs, per eye, by the Lens Finder's own rule
    (lenses.min_blank_from_frame: ED, or eye size + 2 with no ED, + the
    decentration, + 2mm spare), fed each eye's monocular PD. The larger eye
    decides. Always an estimate: the fitting height is unknown, so the optical
    centre is taken at the frame's vertical centre."""
    if not frame or frame.get("dbl") is None or not pds:
        return None
    per_eye, working = {}, []
    a, dbl, ed = frame["a"], frame["dbl"], frame.get("ed")
    base = ed if ed else a + 2
    for side, pd in _eye_order(pds.items()):
        mb = lenses.min_blank_from_frame({"a": a, "dbl": dbl, "ed": ed, "pd": pd})
        if mb is None:
            continue
        per_eye[side] = mb
        dec = max(a + dbl - 2 * pd, 0)
        working.append(f"{side}: {base:g} + ({a:g} + {dbl:g} - 2 x {pd:g} = {dec:g}) + 2 "
                       f"= {mb}mm")
    if not per_eye:
        return None
    notes = ["Height not known - optical centre taken at the frame's vertical centre."]
    if not ed:
        notes.insert(0, "Approximate (no frame diagonal): eye size + 2 used in place of ED.")
    if multi:
        notes.append("The fitting height can push the blank bigger.")
    inputs = (f"A {a:g} · DBL {dbl:g} · ED " + (f"{ed:g}" if ed else "not known")
              + " · PD " + " / ".join(f"{s} {v:g}" for s, v in _eye_order(pds.items())))
    return {"value": max(per_eye.values()), "per_eye": per_eye, "inputs": inputs,
            "working": working, "notes": notes, "approx": not ed}


# ---------------------------------------------------------------------------
# Tabs: every lens type, the one written on the Rx pre-selected
# ---------------------------------------------------------------------------

TABS = ["distance", "near", "office", "multifocal", "bifocal"]
TAB_LABELS = {"distance": "Distance (SV)", "near": "Near (SV)", "office": "Office / intermediate",
              "multifocal": "Multifocal", "bifocal": "Bifocal"}
TAB_PARTS = {"distance": ["distance"], "near": ["near"], "office": ["intermediate", "office"],
             "multifocal": ["multifocal"], "bifocal": ["bifocal"]}
TAB_OF_TYPE = {"intermediate": "office"}
MULTI = {"office", "multifocal", "bifocal"}


def default_tab(rx: dict) -> str:
    t = (rx.get("lens_type") or {}).get("type") or "distance"
    return TAB_OF_TYPE.get(t, t)


def options_for_rx(rx: dict, rows: list, pricing: dict, sells: dict,
                   frame: dict | None = None) -> dict:
    lt = rx.get("lens_type") or {}
    written_as = lt.get("type") if lt.get("as_written") else ""
    written = ({TAB_OF_TYPE.get(t, t) for t in lt.get("types") or []}
               if lt.get("source", "none") != "none" else set())
    frame = clean_frame(frame)
    tabs = []
    for tab in TABS:
        parts = []
        for typ in TAB_PARTS[tab]:
            q = mode_question(rx, typ, written_as)
            part = {"key": typ, "title": TYPE_LONG[typ], "working": q["working"],
                    "notes": q["notes"], "unavailable": q["unavailable"], "answer": None,
                    "blank": None}
            if not q["unavailable"]:
                pds, pd_note = mono_pds(rx, near=(typ == "near"))
                est = blank_estimate(frame, pds, multi=typ in MULTI)
                if est and pd_note:
                    est["notes"].insert(0, pd_note)
                part["blank"] = est
                part["answer"] = run_mode(rows, pricing, sells, q, est)
            parts.append(part)
        tabs.append({"key": tab, "label": TAB_LABELS[tab], "written": tab in written,
                     "parts": parts})
    return {
        "rx_id": rx.get("rx_id"), "saved": rx.get("saved"), "saved_date": _dmy(rx.get("saved_date")),
        "rx_date": _dmy(rx.get("rx_date")), "optom": str(rx.get("optom") or ""),
        "right": rx.get("right"), "left": rx.get("left"),
        "type_note": lt.get("note") or "", "type_words": lt.get("words") or [],
        "type_source": lt.get("source") or "none", "default_tab": default_tab(rx),
        "comment": lt.get("text") or "", "practice_comment": lt.get("practice_text") or "",
        "flags": rx_flags(rx), "tabs": tabs,
        "blank_note": ("" if frame and frame.get("dbl") else
                       "No frame size yet, so the blank isn't checked - type the frame's eye "
                       "size and bridge to estimate it."),
    }


def options(cfg: dict, pid, rows: list, pricing: dict, sells: dict, frame=None) -> dict:
    """frame: the numbers staff typed ({"a", "dbl", "ed"}); None = use the
    frame on the patient's last spectacle order, if any."""
    try:
        pid = int(pid)
        if pid <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return {"error": "Pick a patient first."}
    data = run_bridge(cfg, "rx", "--pid", str(pid))
    if data.get("error"):
        return data
    p = data.get("patient") or {}
    rxs = [r for r in data.get("rxs") or [] if isinstance(r, dict)]
    if isinstance(frame, dict) and frame:
        used = clean_frame({**frame, "source": "typed"})
    else:
        last = data.get("frame") if isinstance(data.get("frame"), dict) else None
        used = clean_frame({**last, "source": "order"}) if last else None
    out = {"patient": {"id": pid, "name": _name(p), "dob": _dmy(p.get("dob"))},
           "fixture": bool(data.get("fixture")), "rxs": [],
           "frame": ({**used, "order_date": _dmy(used.get("order_date"))} if used else None)}
    if not rxs:
        out["empty"] = "No spectacle Rx saved for this patient in Optomate."
        return out
    out["rxs"] = [options_for_rx(r, rows, pricing, sells, used) for r in rxs]
    if len(rxs) > 1:
        out["several"] = (f"{len(rxs)} spectacle Rx were saved on {out['rxs'][0]['saved_date']} - "
                          "pick the one being made.")
    return out
