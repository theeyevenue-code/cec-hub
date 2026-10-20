"""Frame stocktake — the scan page (#/stocktake).

Mark, 20 Oct 2026 (TEST MODE): staff scan every frame on the shelves; Claude reconciles the
count against Optomate later and nothing in Optomate changes from this page.

What it keeps: one JSON file per counting session in data\\stocktake\\ (git-ignored): barcodes,
areas ("Wall 1", "Drawer 3"), +1/-1 events and staff first names. No patient data exists here.
The agent reads these files:  python -m inventory.stocktake reconcile

Frame details (description, supplier, Sell, Optomate stock at MJ) come from the agent's READ-ONLY
catalogue command, run once and cached so each scan is instant:
    python -m inventory.stocktake catalogue --json
The Hub has no Optomate client by design (see hub/medicare.py). If the agent isn't reachable,
scans are still saved and the row says "Optomate not connected".
"""

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TIMEOUT_S = 90
CACHE_TTL_S = 30 * 60
SESSION_RE = re.compile(r"^\d{8}-\d{4}-[0-9a-f]{4}$")
BARCODE_STRIP = re.compile(r"[^A-Z0-9\-]")
TEXT_STRIP = re.compile(r"[\x00-\x1f<>]")
MAX_SCANS = 20000

_lock = threading.Lock()
_cache = {"at": 0.0, "frames": {}, "suppliers": {}, "loaded": None, "error": None}


def data_dir() -> Path:
    return Path(os.getenv("CEC_HUB_STOCKTAKE_DIR", str(BASE_DIR / "data" / "stocktake")))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def norm_barcode(s) -> str:
    return BARCODE_STRIP.sub("", str(s or "").strip().upper())[:30]


def clean_text(s, n: int = 60) -> str:
    return re.sub(r"\s+", " ", TEXT_STRIP.sub("", str(s or ""))).strip()[:n]


def clean_area(s) -> str:
    return clean_text(s, 40) or "No area"


def _who(name) -> str:
    return clean_text(name, 30) or "someone"


# --- Catalogue (agent, read-only, cached) ---------------------------------------------------

def _agent_dir(cfg: dict):
    d = ((cfg or {}).get("optomate_agent") or {}).get("agent_dir")
    return Path(d) if d else None


def run_catalogue(cfg: dict) -> dict:
    """-> the agent's catalogue JSON, or {"ok": False, "error": ...}. Never raises."""
    dpath = _agent_dir(cfg)
    if dpath is None or not (dpath / "inventory" / "stocktake.py").is_file():
        return {"ok": False, "error": "Optomate not connected on this computer."}
    python = ((cfg or {}).get("optomate_agent") or {}).get("python") or sys.executable
    try:
        proc = subprocess.run([python, "-m", "inventory.stocktake", "catalogue", "--json"],
                              cwd=str(dpath), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Optomate took too long - try Refresh."}
    except OSError:
        return {"ok": False, "error": "Couldn't reach Optomate from this computer."}
    for line in reversed((proc.stdout or "").splitlines()):
        if line.strip():
            try:
                data = json.loads(line)
            except ValueError:
                break
            return data if isinstance(data, dict) else {"ok": False, "error": "Unreadable reply."}
    return {"ok": False, "error": "Couldn't read Optomate's frame list."}


def get_catalogue(cfg: dict, force: bool = False, loader=None) -> dict:
    """The cached catalogue; reloads after 30 minutes or on Refresh."""
    with _lock:
        ttl = CACHE_TTL_S if _cache["frames"] else 60      # a failed load is retried after a minute
        if _cache["at"] and time.time() - _cache["at"] < ttl and not force:
            return _cache
    data = (loader or run_catalogue)(cfg)
    with _lock:
        if data.get("ok"):
            _cache.update(at=time.time(), loaded=data.get("loaded"), error=None,
                          suppliers=data.get("suppliers") or {},
                          frames={norm_barcode(f.get("b")): f for f in data.get("frames") or []
                                  if f.get("b")})
        else:
            _cache["error"] = data.get("error") or "Optomate not connected."
            _cache["at"] = time.time() - (CACHE_TTL_S - 60 if _cache["frames"] else 0)
        return _cache


def reset_cache() -> None:
    with _lock:
        _cache.update(at=0.0, frames={}, suppliers={}, loaded=None, error=None)


def lookup(cat: dict, barcode: str):
    b = norm_barcode(barcode)
    for v in (b, "0" + b if b.isdigit() and len(b) == 12 else None,
              b[1:] if b.isdigit() and len(b) == 13 and b.startswith("0") else None):
        if v and v in cat["frames"]:
            return cat["frames"][v]
    return None


def frame_info(cat: dict, barcode: str) -> dict:
    """What the row shows. state: ok | unknown (amber) | offline (grey)."""
    f = lookup(cat, barcode)
    if f:
        return {"state": "ok", "description": f.get("d", ""), "supplier": f.get("s", ""),
                "sell": f.get("p"), "qoh": f.get("q"), "status": f.get("st", ""),
                "kind": "sunglass" if f.get("k") == "s" else "frame"}
    if not cat["frames"]:
        return {"state": "offline", "description": "Optomate not connected - scan saved"}
    return {"state": "unknown", "description": "Not in Optomate"}


# --- Sessions -------------------------------------------------------------------------------

def _path(sid: str) -> Path:
    if not SESSION_RE.match(sid or ""):
        raise ValueError("bad session")
    return data_dir() / f"{sid}.json"


def _read(sid: str) -> dict:
    p = _path(sid)
    if not p.is_file():
        raise FileNotFoundError(sid)
    return json.loads(p.read_text(encoding="utf-8"))


def _write(s: dict) -> None:
    s["updated"] = _now()
    p = _path(s["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _totals(s: dict) -> dict:
    counts = {}
    for e in s.get("scans", []):
        counts[e["barcode"]] = counts.get(e["barcode"], 0) + int(e.get("delta", 1))
    return {"frames": sum(max(0, c) for c in counts.values()),
            "barcodes": sum(1 for c in counts.values() if c > 0),
            "unlabelled": sum(int(u.get("qty", 1)) for u in s.get("unlabelled", []))}


def list_sessions() -> dict:
    out = []
    d = data_dir()
    if d.is_dir():
        for p in d.glob("*.json"):
            try:
                s = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(s, dict) and SESSION_RE.match(str(s.get("id", ""))):
                out.append({"id": s["id"], "name": s.get("name", ""), "created": s.get("created"),
                            "updated": s.get("updated"), **_totals(s)})
    out.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return {"sessions": out}


def create_session(name, by) -> dict:
    now = datetime.now()
    sid = f"{now:%Y%m%d-%H%M}-{secrets.token_hex(2)}"
    s = {"version": 1, "id": sid, "name": clean_text(name, 60) or f"Stocktake {now:%d %b %Y}",
         "branch": "MJ", "created": _now(), "updated": _now(), "created_by": _who(by),
         "scans": [], "undone": [], "unlabelled": []}
    with _lock:
        _write(s)
    return {"ok": True, "id": sid}


def session_view(sid: str, cat: dict) -> dict:
    """Rows newest first (one per barcode), area summary, unlabelled list, totals."""
    s = _read(sid)
    rows, areas = {}, {}
    for e in s.get("scans", []):
        r = rows.setdefault(e["barcode"], {"barcode": e["barcode"], "count": 0, "areas": [], "last": 0})
        r["count"] += int(e.get("delta", 1))
        r["last"] = e["n"]
        if e["area"] not in r["areas"]:
            r["areas"].append(e["area"])
        a = areas.setdefault(e["area"], {"area": e["area"], "frames": 0, "last": None})
        a["frames"] += int(e.get("delta", 1))
        a["last"] = e["t"]
    out_rows = []
    for r in sorted(rows.values(), key=lambda x: -x["last"]):
        r["count"] = max(0, r["count"])
        out_rows.append({**r, **frame_info(cat, r["barcode"])})
    for u in s.get("unlabelled", []):
        a = areas.setdefault(u["area"], {"area": u["area"], "frames": 0, "last": None})
        a["frames"] += int(u.get("qty", 1))
        a["last"] = max(a["last"] or "", u["t"])
    return {"id": s["id"], "name": s.get("name", ""), "created": s.get("created"),
            "updated": s.get("updated"), "rows": out_rows,
            "areas": sorted(areas.values(), key=lambda a: a["last"] or "", reverse=True),
            "unlabelled": s.get("unlabelled", []), "totals": _totals(s),
            "catalogue": {"loaded": cat.get("loaded"), "error": cat.get("error"),
                          "frames": len(cat["frames"])},
            "can_undo": bool(s.get("scans"))}


def _add_event(sid: str, barcode, area, delta: int, how: str, by) -> dict:
    b = norm_barcode(barcode)
    if not b:
        return {"error": "That scan was empty - try again."}
    with _lock:
        s = _read(sid)
        if len(s["scans"]) >= MAX_SCANS:
            return {"error": "This session is full - start a new one."}
        if delta < 0:
            have = sum(int(e.get("delta", 1)) for e in s["scans"] if e["barcode"] == b)
            if have <= 0:
                return {"error": "That frame is already at 0."}
        n = (s["scans"][-1]["n"] + 1) if s["scans"] else 1
        s["scans"].append({"n": n, "t": _now(), "barcode": b, "area": clean_area(area),
                           "delta": delta, "by": _who(by), "how": how})
        _write(s)
        count = sum(int(e.get("delta", 1)) for e in s["scans"] if e["barcode"] == b)
    return {"ok": True, "barcode": b, "count": max(0, count), "totals": _totals(s)}


def scan(sid, barcode, area, by, get_cat) -> dict:
    """The scan is saved FIRST; the frame details are looked up after (get_cat may be slow once)."""
    r = _add_event(sid, barcode, area, 1, "scan", by)
    return {**r, **frame_info(get_cat(), r["barcode"])} if r.get("ok") else r


def adjust(sid, barcode, area, delta, by) -> dict:
    if delta not in (1, -1):
        return {"error": "Only +1 or -1."}
    return _add_event(sid, barcode, area, delta, "plus" if delta > 0 else "minus", by)


def undo(sid, by) -> dict:
    with _lock:
        s = _read(sid)
        if not s["scans"]:
            return {"error": "Nothing to undo."}
        e = s["scans"].pop()
        s.setdefault("undone", []).append({**e, "undone_at": _now(), "undone_by": _who(by)})
        _write(s)
    return {"ok": True, "barcode": e["barcode"], "totals": _totals(s)}


def add_unlabelled(sid, data: dict, by) -> dict:
    d = data or {}
    entry = {k: clean_text(d.get(k), 60) for k in ("supplier", "model", "colour", "size")}
    if not entry["model"] and not entry["supplier"]:
        return {"error": "Type at least the supplier or the model."}
    try:
        qty = int(d.get("qty") or 1)
    except (TypeError, ValueError):
        return {"error": "Qty must be a number."}
    if not 1 <= qty <= 99:
        return {"error": "Qty must be 1 to 99."}
    with _lock:
        s = _read(sid)
        entry.update(id=f"u{len(s['unlabelled']) + 1}-{secrets.token_hex(2)}", t=_now(),
                     area=clean_area(d.get("area")), qty=qty, by=_who(by))
        s["unlabelled"].append(entry)
        _write(s)
    return {"ok": True, "entry": entry, "totals": _totals(s)}


def remove_unlabelled(sid, uid, by) -> dict:
    with _lock:
        s = _read(sid)
        keep = [u for u in s["unlabelled"] if u.get("id") != uid]
        if len(keep) == len(s["unlabelled"]):
            return {"error": "Already removed."}
        gone = [u for u in s["unlabelled"] if u.get("id") == uid]
        s["unlabelled"] = keep
        s.setdefault("undone", []).extend({**g, "undone_at": _now(), "undone_by": _who(by)} for g in gone)
        _write(s)
    return {"ok": True, "totals": _totals(s)}
