"""Pick a lens -> make the job in Optomate (Mark, 8 Nov 2026). HIDDEN until its turn on IDEAS.md.

Mark's words: "staff chooses last 3 patients with Rx and it gives simple best options depending on
their Rx, and opening up option of entering frames there too so that it creates the job thru hub
which would allow checking for best PD and blank size issues, - and should there be issue give
suggestions on next best lens (with preference for stock over grind where possible) and after all
this create simple process / button so it creates the job on optomate, then staff can invoice."

Design: CEC-Optomate-Agent docs/designs/pick-lens-job-design.md. This module BUILDS ON the Lens
options page (hub/lensoptions.py: recent patients, Rx -> options, frame, blank fit) and adds:

  * the lens link table (agent repo pricing/lens_link.csv, read here, cached) so every option says
    whether Optomate knows it ("ready") or not ("make this job in Touch");
  * best options: the two or three to offer first, cheapest that fits first, with one plain reason;
  * next best when the picked lens doesn't fit the frame: a stock lens that fits, then grind;
  * the check screen and Make job: the agent's engine (python -m orders.pickjob plan|make) as a
    subprocess, the same shape as Jobs arrived (hub/jobscan.py). The engine reads Optomate fresh,
    copies the Rx verbatim and honours orders/pickjob_config.json (DRY_RUN, WRITE_LIVE, ZZTEST_ONLY).
    Nothing here writes anything.

HIDDEN: enabled() needs config\\integrations.json  "pick_lens_job": {"show": true}  or the
environment variable CEC_HUB_PICK_LENS=1 (the test copy). Otherwise every route answers 404 and the
#/picklens page says it is not switched on. No tile anywhere until Mark says "show the job button
to staff".

PRIVACY: names in POST bodies and answers only; nothing about a patient reaches hub.log.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from hub import checkin, lensoptions

TIMEOUTS = {"status": 20, "tints": 25, "plan": 60, "make": 90}
NOT_CONNECTED = "Make job isn't connected to Optomate on this computer."
LENS_USE = {"distance": "D", "near": "N", "intermediate": "I", "office": "M", "multifocal": "M", "bifocal": "B"}
HEIGHT_PARTS = {"office", "multifocal", "bifocal"}
BEST_N = 3

_links_cache = {"path": None, "mtime": None, "rows": []}
_lock = threading.RLock()


# ---------------------------------------------------------------------------
# the hidden switch
# ---------------------------------------------------------------------------

def enabled(cfg: dict) -> bool:
    if (os.environ.get("CEC_HUB_PICK_LENS") or "").strip().lower() in ("1", "true", "yes"):
        return True
    return bool(((cfg or {}).get("pick_lens_job") or {}).get("show") is True)


def connected(cfg: dict) -> bool:
    if lensoptions.fixture_mode():
        return True
    d = checkin.agent_dir(cfg)
    return bool(d and (d / "orders" / "pickjob.py").is_file())


# ---------------------------------------------------------------------------
# the lens link table (agent repo, pricing/lens_link.csv) - read, cached by mtime
# ---------------------------------------------------------------------------

def links_path(cfg: dict) -> Path | None:
    d = checkin.agent_dir(cfg)
    return d / "pricing" / "lens_link.csv" if d else None


def links(cfg: dict) -> list[dict]:
    p = links_path(cfg)
    if not p or not p.is_file():
        return []
    with _lock:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return []
        if _links_cache["path"] == str(p) and _links_cache["mtime"] == mtime:
            return _links_cache["rows"]
        with open(p, newline="", encoding="utf-8-sig") as f:
            rows = [dict(r) for r in csv.DictReader(f)]
        _links_cache.update(path=str(p), mtime=mtime, rows=rows)
        return rows


def _idx(v) -> str:
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return ""


def _key(o: dict) -> tuple:
    """(supplier, raw lens name, index, material, coating, stock/grind, blank) - the link table's key."""
    sg = str(o.get("stockgrind") or o.get("type") or "").strip().lower()
    blank = str(o.get("blank_mm") or "").strip()
    if blank:
        try:
            blank = str(int(float(blank)))
        except ValueError:
            pass
    return (str(o.get("supplier") or "").strip(), str(o.get("lens") or o.get("raw_name") or o.get("name") or "").strip(),
            _idx(o.get("index")), str(o.get("material") or "").strip(), str(o.get("coating") or "").strip(),
            sg, blank if sg == "stock" else "")


def link_for(rows: list[dict], o: dict) -> dict:
    """What Optomate knows about this option, in plain words.
    {"ready": bool, "words": str, "barcode", "description", "status", "price_agree", "coat_in"}."""
    key = _key(o)
    by = {}
    for r in rows:
        by.setdefault(_key(r), r)
    r = by.get(key)
    if r is None and key[5] == "stock":
        same = [x for k, x in by.items() if k[:6] == key[:6] and x.get("lens_barcode")]
        if len({x["lens_barcode"] for x in same}) == 1:
            r = same[0]
    if r is None or not str(r.get("lens_barcode") or "").strip() or r.get("status") in ("wrong", "not_stocked"):
        return {"ready": False, "words": "Make this job in Touch", "why": "Not linked to Optomate yet",
                "barcode": "", "description": "", "status": (r or {}).get("status", ""), "price_agree": "", "coat_in": ""}
    out = {"ready": True, "words": "Ready for Optomate", "why": "", "barcode": r["lens_barcode"],
           "description": r.get("lens_description", ""), "status": r.get("status", ""),
           "price_agree": r.get("price_agree", ""), "coat_in": r.get("coat_in", "")}
    if r.get("coat_in") == "not linked":
        out.update(ready=False, words="Make this job in Touch", why="This coating has no Optomate add-on")
    elif r.get("price_agree") == "no":
        out.update(ready=False, words="Make this job in Touch",
                   why=f"Prices don't agree: Optomate ${r.get('optomate_pair')} a pair, Hub ${r.get('hub_pair_price')}")
    elif r.get("price_agree") == "":
        out.update(ready=False, words="Make this job in Touch", why="No Optomate price to check against")
    return out


def option_ref(o: dict) -> dict:
    """The fields the engine needs to find the link row (no price, no patient)."""
    return {"supplier": o.get("supplier"), "lens": o.get("lens") or o.get("raw_name") or o.get("name"),
            "index": o.get("index"), "material": o.get("material"), "coating": o.get("coating"),
            "stockgrind": str(o.get("stockgrind") or o.get("type") or "").lower(), "blank_mm": o.get("blank_mm")}


def _same(a: dict, b: dict) -> bool:
    return _key(option_ref(a))[:6] == _key(option_ref(b))[:6]


# ---------------------------------------------------------------------------
# best options and next best
# ---------------------------------------------------------------------------

def _why(o: dict, rank: int, rec: str, has_frame: bool) -> str:
    bits = []
    if rank == 0:
        bits.append("Cheapest for this Rx")
    if o.get("type") == "Stock":
        if has_frame and o.get("fits") is True:
            bits.append(f"stock blank fits this frame" if not o.get("blank_mm") else f"{o['blank_mm']:g} mm stock blank fits")
        elif not has_frame:
            bits.append("stock blank")
    else:
        bits.append("made to order, cut to the frame")
    if rec and o.get("index") == rec and not o.get("thicker"):
        bits.append("suggested thickness for this power")
    elif o.get("thicker"):
        bits.append(f"thicker than {rec}" if rec else "thicker")
    s = ", ".join(bits)
    return s[:1].upper() + s[1:]


def rank_options(listed: list[dict], rec: str = "", has_frame: bool = False) -> dict:
    """{"best": up to 3, "rest": the others}. Cheapest first among lenses with a sell price that fit
    the frame (or no frame yet); a lens flagged thicker than the suggested index only makes the top
    three when nothing else is left; stock blanks too small for the frame go to the end."""
    priced = [o for o in listed if o.get("sell") is not None]
    pool = priced or list(listed)

    def key(o):
        # thickness before readiness: a lens one step thicker than ideal is a worse offer even when
        # Optomate knows it; a right-thickness lens that must be made in Touch is still worth offering
        return (o.get("fits") is False, bool(o.get("thicker")), not o.get("link", {}).get("ready", True),
                o.get("sell") if o.get("sell") is not None else 1e9, o.get("cost") or 0)
    ordered = sorted(pool, key=key)
    best, seen = [], set()
    for o in ordered:
        k = (o.get("supplier"), o.get("raw_name") or o.get("name"), o.get("index"))
        if k in seen:
            continue
        seen.add(k)
        best.append(o)
        if len(best) == BEST_N:
            break
    rest = [o for o in listed if o not in best]
    for i, o in enumerate(best):
        o["why"] = _why(o, i, rec, has_frame)
    return {"best": best, "rest": rest}


def next_best(listed: list[dict], picked: dict | None, has_frame: bool) -> dict | None:
    """When the picked lens doesn't fit (or isn't ready): the next lens to offer. A stock lens that
    fits first (Mark: prefer stock over grind), then grind; ready for Optomate first within each."""
    if not picked:
        return None
    mine = next((o for o in listed if _same(o, picked)), None)
    problem, need_ready = "", False
    if mine is None:
        problem = "This lens doesn't cover this Rx"
    elif has_frame and mine.get("fits") is False:
        problem = mine.get("blank_line") or "Stock blank too small for this frame"
    elif not (mine.get("link") or {}).get("ready", False):
        problem = (mine.get("link") or {}).get("why") or "Not linked to Optomate yet"
        need_ready = True
    if not problem:
        return {"picked": mine, "problem": "", "suggest": []}
    pool = [o for o in listed if o is not mine and o.get("sell") is not None and o.get("fits") is not False
            and (not need_ready or (o.get("link") or {}).get("ready"))]

    def key(o):
        return (0 if o.get("type") == "Stock" else 1, not o.get("link", {}).get("ready", False),
                bool(o.get("thicker")), o.get("sell") if o.get("sell") is not None else 1e9)
    sug = sorted(pool, key=key)[:3]
    for o in sug:
        o["why"] = ("Stock blank that fits" if o.get("type") == "Stock" else "Made to order, cut to the frame") \
            + (", thicker" if o.get("thicker") else "")
    return {"picked": mine, "problem": problem, "suggest": sug}


# ---------------------------------------------------------------------------
# options: the Lens options answer, decorated
# ---------------------------------------------------------------------------

def options(cfg: dict, pid, rows: list, pricing: dict, sells: dict, frame=None, picked: dict | None = None) -> dict:
    out = lensoptions.options(cfg, pid, rows, pricing, sells, frame)
    if out.get("error"):
        return out
    link_rows = links(cfg)
    has_frame = bool(out.get("frame") and (out["frame"] or {}).get("dbl") is not None)
    for rx in out.get("rxs") or []:
        for tab in rx.get("tabs") or []:
            for part in tab.get("parts") or []:
                a = part.get("answer")
                if not a:
                    continue
                listed = a.get("options") or []
                for o in listed:
                    o["link"] = link_for(link_rows, o)
                    o["ref"] = option_ref(o)
                    o["use"] = LENS_USE.get(part.get("key"), "D")
                ranked = rank_options(listed, a.get("rec_index") or "", has_frame)
                a["best"] = ranked["best"]
                a["rest"] = ranked["rest"]
                a["next_best"] = next_best(listed, picked, has_frame) if picked else None
                a["heights_needed"] = part.get("key") in HEIGHT_PARTS
    out["links_known"] = sum(1 for r in link_rows if r.get("lens_barcode"))
    return out


# ---------------------------------------------------------------------------
# frame by barcode (lens_bridge, read only)
# ---------------------------------------------------------------------------

def frame(cfg: dict, barcode) -> dict:
    barcode = str(barcode or "").strip()[:40]
    if not barcode:
        return {"error": "Scan or type the frame barcode first."}
    data = lensoptions.run_bridge(cfg, "frame", "--barcode", barcode)
    if data.get("error"):
        return {"error": data["error"]}
    f = data.get("frame") or {}
    return {"frame": {"barcode": f.get("barcode"), "model": f.get("model"), "colour": f.get("colour"),
                      "a": f.get("a"), "dbl": f.get("dbl"), "ed": f.get("ed"), "sunglass": bool(f.get("sunglass")),
                      "price": f.get("price"), "qoh": f.get("qoh"), "source": "scan"}}


# ---------------------------------------------------------------------------
# the engine (agent repo, orders/pickjob.py) as a subprocess
# ---------------------------------------------------------------------------

def run_engine(cfg: dict, command: str, request: dict | None = None) -> dict:
    """Never raises. One JSON object on the engine's last stdout line."""
    if not connected(cfg):
        return {"ok": False, "connected": False, "refusals": [NOT_CONNECTED]}
    python = (cfg.get("optomate_agent") or {}).get("python") or sys.executable
    cwd = checkin.agent_dir(cfg)
    try:
        proc = subprocess.run([python, "-m", "orders.pickjob", command, "--json"],
                              input=json.dumps(request or {}, default=str), cwd=str(cwd) if cwd else None,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=TIMEOUTS.get(command, 60))
    except subprocess.TimeoutExpired:
        return {"ok": False, "refusals": ["Optomate took too long - check Touch before trying again."
                                          if command == "make" else "Optomate took too long - try again."]}
    except OSError:
        return {"ok": False, "connected": False, "refusals": ["Couldn't start the Make job helper."]}
    line = next((c.strip() for c in reversed((proc.stdout or "").splitlines()) if c.strip()), "")
    try:
        data = json.loads(line) if line else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {"ok": False, "refusals": ["The Make job helper didn't give a readable answer."
                                          + (" Check Touch: the job may or may not exist." if command == "make" else "")]}
    return data


def clean_request(data: dict, user: str) -> dict | None:
    """Only the fields the engine expects, typed. None when the shape is wrong."""
    if not isinstance(data, dict):
        return None
    no_rx = data.get("no_rx") is True              # Mark, 10 Nov 2026: a flagged job with NO Rx
    try:
        pid = int(data.get("patient_id"))
        rx_id = None if no_rx else int(data.get("rx_id"))
    except (TypeError, ValueError):
        return None
    opt = data.get("option") if isinstance(data.get("option"), dict) else {}
    fr = data.get("frame") if isinstance(data.get("frame"), dict) else {}
    hts = data.get("heights") if isinstance(data.get("heights"), dict) else {}
    return {
        "patient_id": pid, "rx_id": rx_id, "rx_saved_at": str(data.get("rx_saved_at") or "")[:19],
        "option": {k: opt.get(k) for k in ("supplier", "lens", "index", "material", "coating", "stockgrind", "blank_mm")},
        "lens_use": str(data.get("lens_use") or "")[:1].upper(),
        "hub_pair_price": str(data.get("hub_pair_price") if data.get("hub_pair_price") is not None else "")[:12],
        "frame": {"own": bool(fr.get("own")), "barcode": str(fr.get("barcode") or "")[:40],
                  "a": str(fr.get("a") or "")[:6], "dbl": str(fr.get("dbl") or "")[:6], "ed": str(fr.get("ed") or "")[:6]},
        "heights": {s: str(hts.get(s) or "")[:6] for s in ("R", "L")},
        "tint_barcode": str(data.get("tint_barcode") or "")[:40],
        "second_pair": bool(data.get("second_pair")),
        # Mark, 9 Nov 2026: widen the job PD to fit a stock SV blank (the engine re-checks the rule)
        "pd_shift": None if no_rx else _clean_shift(data.get("pd_shift")),
        "no_rx": no_rx, "no_rx_ack": no_rx and data.get("no_rx_ack") is True,
        "user": str(user or "")[:40],
    }


def _clean_shift(v) -> dict | None:
    if not isinstance(v, dict):
        return None
    out = {}
    for side in ("R", "L"):
        try:
            mm = float(v.get(side) or 0)
        except (TypeError, ValueError):
            return None
        if mm < 0 or mm > 3:
            return None
        if mm:
            out[side] = mm
    return out or None


def status(cfg: dict) -> dict:
    d = run_engine(cfg, "status")
    d["enabled"] = enabled(cfg)
    d["connected"] = d.get("connected", True)
    return d


def tints(cfg: dict) -> dict:
    return run_engine(cfg, "tints")


def check(cfg: dict, data: dict, user: str) -> dict:
    req = clean_request(data, user)
    if req is None:
        return {"ok": False, "refusals": ["Pick a patient and a lens first."]}
    return run_engine(cfg, "plan", req)


def make(cfg: dict, data: dict, user: str) -> dict:
    req = clean_request(data, user)
    if req is None:
        return {"ok": False, "refusals": ["Pick a patient and a lens first."], "created": None}
    return run_engine(cfg, "make", req)
