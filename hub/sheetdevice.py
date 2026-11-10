"""Known staff iPads for the price sheet (Mark, 11 Nov 2026: "No codes the iPad is purely for staff patients don't touch").

No code is ever typed on the iPad. Instead the iPad is enrolled ONCE from the server:
  1. On the server (this computer only) open /sheet-device - it makes a one-time setup link (10 minutes, one use).
  2. Open that link on the staff iPad. It sets an HttpOnly cookie holding a random device token and goes to the sheet.
  3. From then on the iPad opens the patient list with no prompt. Every other device (guest Wi-Fi, a patient's phone)
     gets "Staff iPad only" and no code pad.

Only a SHA-256 of each device token is kept, in CONFIG_DIR/sheet_devices.json (per machine, never committed).
Remove a device on /sheet-device (server only) to lock it out again. The cookie is re-set on each use, so an iPad in
daily use never reaches the browser's 400-day limit.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import date
from pathlib import Path

COOKIE = "cec_sheet_device"
COOKIE_DAYS = 400
SETUP_S = 10 * 60
PATH: Path | None = None              # set by app.py: CONFIG_DIR / "sheet_devices.json"

_lock = threading.Lock()
_setups: dict[str, float] = {}        # one-time setup token -> expiry (memory only: a restart voids unused links)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load() -> list[dict]:
    if not PATH or not PATH.exists():
        return []
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [x for x in d.get("devices", []) if isinstance(x, dict) and x.get("hash")]


def _save(devs: list[dict]) -> None:
    if not PATH:
        raise RuntimeError("sheetdevice.PATH not set")
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"_note": "Staff iPads allowed on the price sheet. Hashes only. Manage at /sheet-device "
                                        "on the server.", "devices": devs}, indent=1), encoding="utf-8")
    tmp.replace(PATH)


def new_setup() -> str:
    tok = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        for k in [k for k, t in _setups.items() if t < now]:
            del _setups[k]
        _setups[tok] = now + SETUP_S
    return tok


def enrol(setup_token: str, name: str = "Staff iPad") -> str | None:
    """Use a setup token (once). Returns the new device token, or None if unknown / used / expired."""
    with _lock:
        exp = _setups.pop(str(setup_token or ""), None)
        if not exp or exp < time.time():
            return None
        tok = secrets.token_urlsafe(32)
        devs = _load()
        devs.append({"hash": _hash(tok), "name": (name or "Staff iPad")[:40], "added": date.today().isoformat()})
        _save(devs)
    return tok


def ok(token) -> bool:
    if not token:
        return False
    h = _hash(str(token))
    return any(hmac.compare_digest(h, d["hash"]) for d in _load())


def devices() -> list[dict]:
    return [{"id": d["hash"][:10], "name": d.get("name", ""), "added": d.get("added", "")} for d in _load()]


def remove(dev_id: str) -> bool:
    with _lock:
        devs = _load()
        keep = [d for d in devs if d["hash"][:10] != dev_id]
        if len(keep) == len(devs):
            return False
        _save(keep)
    return True
