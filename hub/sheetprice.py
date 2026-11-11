"""The iPad price sheet's prices, on the server (Mark, 10 Nov 2026, steps 2-3).

ONE price file: price_sheet/prices.json. price_sheet/build.py inlines it into static/price-sheet.html; this
module reads the SAME file, so the price a job is checked against comes from the server, never the page
(director's rule 2). The sheet map (price_sheet/sheet_map.json) says which price-book row and which
Optomate tint item each choice is.

Every price here is COPIED from the file. The only arithmetic is the sheet's own adding-up (lens + option
+ made to order), exactly as the page's total does it. A fix (made to order, another stock lens) is
RE-PRICED by these same rules from the new choice, never by adding a difference.

A "choice" (dict, from the page or built here):
    tab     sv | mf | af | sun | myo | office | bif
    card    card number on that tab (0-based)
    idx     thickness, e.g. "1.60" ("1.53" = the rimless other index where the card has one)
    opt     option key: clear | blue | photo | tint | polar
    colour  tint colour (Black, Grey, Brown, Green)        depth  s85 s75 s60 s25 s12 g9040 g75 g25
    pfx     PhotoFusion X colour (Grey ... Burgundy)
    made    "stock" or "mto" (single vision only: made to order asked for, e.g. a fix)
No patient data ever enters this module.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "price_sheet"
PRICES = BASE / "prices.json"
MAP = BASE / "sheet_map.json"
JOB_TABS = ("sv", "mf", "af", "office", "bif", "syn")
SV_TABS = ("sv", "syn")       # single vision: a stock lens, or made to order (syn = the small Synchrony tab)

_cache: dict = {}
_lock = threading.Lock()


def _read(path: Path) -> tuple[dict, str, float]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()[:12], path.stat().st_mtime


def load(prices: Path | None = None, sheet_map: Path | None = None) -> dict:
    """{"data", "version", "map"}; cached by mtime. version = the same hash build.py puts in the page."""
    p, m = prices or PRICES, sheet_map or MAP
    with _lock:
        key = (str(p), str(m))
        try:
            mt = (p.stat().st_mtime, m.stat().st_mtime)
        except OSError:
            raise FileNotFoundError("The price file isn't on this computer.")
        hit = _cache.get(key)
        if hit and hit["mtime"] == mt:
            return hit
        data, version, _ = _read(p)
        smap, _, _ = _read(m)
        sets = data.get("optionSets") or {}
        for pg in data.get("pages") or []:
            for c in pg.get("cards") or []:
                if isinstance(c.get("opts"), str):
                    c["opts"] = sets.get(c["opts"]) or []
        out = {"data": data, "version": version, "map": smap, "mtime": mt}
        _cache[key] = out
        return out


# ---------------------------------------------------------------------------
# the sheet's own rules (a copy of the page's priceNow, plus made to order on request)
# ---------------------------------------------------------------------------

def page(book: dict, tab: str) -> dict | None:
    return next((pg for pg in book["data"]["pages"] if pg.get("key") == tab), None)


def card(book: dict, tab: str, i) -> dict | None:
    pg = page(book, tab)
    try:
        i = int(i)
    except (TypeError, ValueError):
        return None
    cards = (pg or {}).get("cards") or []
    return cards[i] if 0 <= i < len(cards) else None


def indices(c: dict) -> list[str]:
    """Thicknesses this card is sold in, in sheet order (the rimless other index last)."""
    if c.get("thick"):
        out = [t["idx"] for t in c["thick"]]
        if c.get("other"):
            out.append(c["other"]["idx"])
        return out
    if c.get("grid"):
        return [g[0] for g in c["grid"]]
    return [c["idx"]] if c.get("idx") else [""]


def options(book: dict, c: dict, idx: str) -> list[dict]:
    """The option list the page shows for this card at this thickness (MF light-reactive off at 1.74 on Essential)."""
    if c.get("opts"):
        return list(c["opts"])
    if c.get("mf"):
        out = []
        for o in book["data"]["optionSets"]["MF"]:
            o = dict(o)
            if o.get("offIdx"):
                o["off"] = idx == o["offIdx"] and not c.get("photo174")
            out.append(o)
        return out
    return []


def _base(c: dict, idx: str):
    if c.get("thick"):
        if c.get("other") and idx == c["other"]["idx"]:
            return c["other"]["price"]
        t = next((t for t in c["thick"] if t["idx"] == idx), None)
        return t["price"] if t else None
    if c.get("grid"):
        g = next((g for g in c["grid"] if g[0] == idx), None)
        return g[3] if g else None          # anti-reflective column (sun: price only)
    return c.get("price")


def _is_grad(depth) -> bool:
    return str(depth or "").startswith("g")


def price(book: dict, ch: dict) -> dict:
    """The sheet's pair price for this choice, split the way Optomate charges it.
    -> {"ok", "why", "total", "lens_pair", "tint_pair", "made", "idx", "option"}; money as whole dollars (ints)."""
    tab = str(ch.get("tab") or "")
    c = card(book, tab, ch.get("card"))
    if c is None:
        return {"ok": False, "why": "That lens isn't on the price sheet."}
    idx = str(ch.get("idx") or (indices(c)[0]))
    if idx not in indices(c):
        return {"ok": False, "why": f"Not made in {idx} on the price sheet."}
    base = _base(c, idx)
    if base is None:
        return {"ok": False, "why": f"No sheet price at {idx}."}
    opts = options(book, c, idx)
    k = str(ch.get("opt") or "clear")
    o = next((x for x in opts if x.get("k") == k), None) if opts else None
    if opts and o is None:
        return {"ok": False, "why": "That option isn't on the price sheet for this lens."}
    if o and o.get("off"):
        return {"ok": False, "why": o.get("offwhy") or "Not made at this thickness."}
    grind = c.get("grind") or 0
    pfx = str(ch.get("pfx") or "Grey")
    lens, tint = base, 0
    made = "mto"
    if tab == "syn":
        # Synchrony single vision (Mark, 10 Nov 2026): each card carries its own made-to-order price, copied
        made = "mto" if str(ch.get("made")) == "mto" else "stock"
        if made == "mto":
            if c.get("mtoPrice") is None:
                return {"ok": False, "why": f"No made-to-order {idx} on the price sheet."}
            lens = c["mtoPrice"]
    elif tab == "sv":
        made = "mto" if (str(ch.get("made")) == "mto" or (o and o.get("mto")) or
                         (o and o.get("k") == "photo" and o.get("photoStock") and pfx != "Grey")) else "stock"
        if o and o["k"] == "tint":
            # one sheet price = the lens made to order + the tint (Mark, 9 Nov 2026: "$100 + tint $50")
            lens, tint = base + grind, (o["grad"] if _is_grad(ch.get("depth")) else o["add"]) - grind
        elif o and o.get("incl"):
            lens = base + o["add"]                       # "Includes made to order"
        else:
            lens = base + (o["add"] if o else 0) + (grind if made == "mto" else 0)
    elif o and o.get("price") is not None:
        lens = o["price"]                                # bifocal: each lens type is its own price
    elif o and o["k"] == "tint":
        tint = o["grad"] if _is_grad(ch.get("depth")) else o["add"]
    elif o:
        lens = base + o["add"]
    if tab in ("sun", "myo"):
        # price only in version 1: the total as the page shows it, nothing to make
        extra = 0
        if c.get("gradAdd") and c.get("tintPick") and _is_grad(ch.get("depth")):
            extra += c["gradAdd"]
        if ch.get("mirror") and c.get("mirror"):
            extra += c["mirror"]
        if c.get("grid") and str(ch.get("coat")) == "hc":
            g = next((g for g in c["grid"] if g[0] == idx), None)
            if g and g[2] is not None:
                lens = g[2]
        lens += extra
    return {"ok": True, "why": "", "total": lens + tint, "lens_pair": lens, "tint_pair": tint, "made": made,
            "idx": idx, "option": (o or {}).get("k", "clear"), "option_name": (o or {}).get("n", "")}


# ---------------------------------------------------------------------------
# the sheet map: choice -> price-book row (the lens link key) and tint item
# ---------------------------------------------------------------------------

def _pick(v, k):
    return v.get(k) if isinstance(v, dict) else v


def pricebook(book: dict, ch: dict, pr: dict | None = None) -> dict:
    """-> {"ok", "why", "key": {supplier, lens, index, material, coating, stockgrind}, "use",
            "tint_barcode", "tint_kind"}. ok False = PRICE ONLY (the reason in plain words)."""
    pr = pr or price(book, ch)
    if not pr.get("ok"):
        return {"ok": False, "why": pr.get("why") or "Not on the price sheet."}
    tab = str(ch.get("tab"))
    tm = (book["map"].get("tabs") or {}).get(tab) or {}
    if tm.get("price_only"):
        return {"ok": False, "why": tm["price_only"]}
    idx, k = pr["idx"], pr["option"]
    out = {"ok": True, "why": "", "use": tm.get("use", "D"), "tint_barcode": "", "tint_kind": ""}
    if tm.get("stock"):                                  # single vision tabs: a stock row and a made-to-order row
        side = tm["mto" if pr["made"] == "mto" else "stock"]
        lens = _pick(side["lens"], k)
        if isinstance(lens, dict):
            lens = lens.get(idx)
        if pr["made"] == "stock" and k == "tint":
            return {"ok": False, "why": "A tinted single vision lens is made to order."}
        mat = (side.get("material") or {}).get(k)
        if not lens or not mat:
            return {"ok": False, "why": "No price-book lens for this choice."}
        key = {"supplier": side["supplier"], "lens": lens.replace("{idx}", idx), "index": idx, "material": mat,
               "coating": _pick(side["coating"], k), "stockgrind": "stock" if pr["made"] == "stock" else "grind"}
    else:
        try:
            i = int(ch.get("card"))
        except (TypeError, ValueError):
            return {"ok": False, "why": "That lens isn't on the price sheet."}
        cm = tm.get("card") if tm.get("cards") == "all" else ((tm.get("cards") or [])[i] if i < len(tm.get("cards") or []) else None)
        if not cm:
            return {"ok": False, "why": "No price-book lens for this card."}
        mat = (cm.get("material") or {}).get(k)
        coat = _pick(cm.get("coating"), k)
        if not mat or not coat:
            return {"ok": False, "why": "No price-book lens for this option."}
        if k == "photo" and cm.get("photo_colours") and str(ch.get("pfx") or "Grey") not in cm["photo_colours"]:
            return {"ok": False, "why": cm.get("photo_note") or "That colour can't go on the job."}
        key = {"supplier": cm["supplier"], "lens": cm["lens"].replace("{idx}", idx), "index": idx, "material": mat,
               "coating": coat, "stockgrind": "grind"}
    out["key"] = key
    if k == "tint":
        bc = (book["map"].get("tints") or {}).get(f"{ch.get('colour') or 'Grey'}|{ch.get('depth') or 's85'}")
        if not bc:
            return {"ok": False, "why": "That tint colour and depth isn't mapped to an Optomate tint."}
        out.update(tint_barcode=bc, tint_kind="graduated" if _is_grad(ch.get("depth")) else "solid")
    elif k == "photo" and key["material"] == "PhotoFusionX":
        # as staff key it in Touch (real jobs, Oct-Nov 2026): the PhotoFusion X LENS item, no tint line; the colour
        # goes in the job comment. (10 Nov 2026: the $0 Photofusion colour tint items are not how jobs are keyed.)
        out["pfx_colour"] = str(ch.get("pfx") or "Grey")
    return out


def quote(book: dict, ch: dict) -> dict:
    """price + pricebook in one answer, for the Hub routes and the audit."""
    pr = price(book, ch)
    pb = pricebook(book, ch, pr)
    return {**pr, "map": pb, "version": book["version"]}


# ---------------------------------------------------------------------------
# every choice on the sheet (the audit walks this)
# ---------------------------------------------------------------------------

def every_choice(book: dict):
    data = book["data"]
    colours = [c["k"] for c in data["tintColours"]]
    depths = [d[0] for d in data["solidDepths"] + data["gradDepths"]]
    pfxs = [p["k"] for p in data["pfxColours"]]
    for pg in data["pages"]:
        tab = pg["key"]
        for i, c in enumerate(pg["cards"]):
            for idx in indices(c):
                opts = options(book, c, idx) or [{"k": "clear"}]
                for o in opts:
                    k = o["k"]
                    mades = ["stock", "mto"] if tab in SV_TABS else [None]
                    for made in mades:
                        base = {"tab": tab, "card": i, "idx": idx, "opt": k, "made": made}
                        if k == "tint":
                            for col in colours:
                                for d in depths:
                                    yield {**base, "colour": col, "depth": d}
                        elif k == "photo" and tab != "bif":
                            for p in pfxs:
                                yield {**base, "pfx": p}
                        else:
                            yield base


def card_title(book: dict, ch: dict) -> str:
    """Product words for staff screens and the audit (staff use product names)."""
    c = card(book, ch.get("tab"), ch.get("card")) or {}
    words = c.get("product") or c.get("tier") or ""
    if c.get("thick") or c.get("grid"):
        words = f"{words} {ch.get('idx')}"
    return words
