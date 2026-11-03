"""The assistant's spend ledger: A$ per SYDNEY calendar month, one file outside
every repo, shared by every chat box (Codex 2026-11-03-hub-assistant #4 and the
accepted response, MODIFY 3).

  * Month = the calendar month in Australia/Sydney (AEST/AEDT), not the
    server's clock and not UTC. The console's own limit resets at 00:00 UTC;
    it is a looser backstop, never the cap.
  * The cap comes from config (cap_aud), never from this file.
  * Before each call the worst case is RESERVED under a cross-process file
    lock (two Hub processes, or the home box and the Lens Finder at once, can
    never both slip under the cap). After the call the reservation is swapped
    for the reported usage, charged to the month the call STARTED in. A call
    that fails or never settles keeps its worst case: no refund, no retry.
  * Missing or unreadable file = refuse (fail closed). Only init() creates it,
    and never over an existing one.
  * The old single-total format (version 1, the Lens Finder trial ledger) is
    migrated IN PLACE on first read: its spend is carried into the month it
    belongs to (its own "month" field, else the current Sydney month - the
    conservative choice for a total with no dates).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_UP
from pathlib import Path

CENT4 = Decimal("0.0001")
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")


class LedgerError(Exception):
    pass


class BudgetRefused(Exception):
    pass


# ---------------------------------------------------------------------------
# Sydney time. zoneinfo needs the tzdata package on Windows, which this server
# does not have; the NSW rule is fixed in law, so it is written out here and
# zoneinfo is used only when it is available (the two are tested against
# each other when it is).
# ---------------------------------------------------------------------------

def _first_sunday(year: int, month: int) -> _dt.date:
    d = _dt.date(year, month, 1)
    return d + _dt.timedelta(days=(6 - d.weekday()) % 7)


def _sydney_offset(utc: _dt.datetime) -> _dt.timedelta:
    """AEDT (+11) from 02:00 AEST on the first Sunday in October to 03:00 AEDT
    on the first Sunday in April; AEST (+10) otherwise."""
    y = utc.year
    start = _dt.datetime.combine(_first_sunday(y, 10), _dt.time(2)) - _dt.timedelta(hours=10)
    end = _dt.datetime.combine(_first_sunday(y, 4), _dt.time(3)) - _dt.timedelta(hours=11)
    naive = utc.replace(tzinfo=None)
    dst = naive >= start or naive < end
    return _dt.timedelta(hours=11 if dst else 10)


def sydney_now(now: _dt.datetime | None = None) -> _dt.datetime:
    """Sydney wall-clock time (naive). `now` may be aware (any zone) or naive
    UTC; tests pass it."""
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(_dt.timezone.utc)
    return now.replace(tzinfo=None) + _sydney_offset(now.replace(tzinfo=None))


def month_key(now: _dt.datetime | None = None) -> str:
    return sydney_now(now).strftime("%Y-%m")


def reset_words(now: _dt.datetime | None = None) -> str:
    """'1 December' - the day the next Sydney month's budget starts."""
    d = sydney_now(now).date()
    nxt = _dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return f"1 {MONTH_NAMES[nxt.month - 1]}"


# ---------------------------------------------------------------------------
# Cross-process lock: a sidecar .lock file, locked with msvcrt on Windows and
# fcntl elsewhere, plus a thread lock for this process.
# ---------------------------------------------------------------------------

class FileLock:
    _threads = threading.Lock()

    def __init__(self, path: Path, timeout: float = 10.0):
        self.path = Path(str(path) + ".lock")
        self.timeout = timeout
        self._f = None

    def __enter__(self):
        if not FileLock._threads.acquire(timeout=self.timeout):
            raise LedgerError("Chat spend ledger is busy - try again.")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._f = open(self.path, "a+b")
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    self._lock()
                    return self
                except OSError:
                    if time.monotonic() > deadline:
                        raise LedgerError("Chat spend ledger is busy - try again.")
                    time.sleep(0.02)
        except BaseException:
            self._close()
            FileLock._threads.release()
            raise

    def _lock(self):
        if os.name == "nt":
            import msvcrt
            self._f.seek(0)
            msvcrt.locking(self._f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self._f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _close(self):
        if self._f is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    self._f.seek(0)
                    msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self._f.close()
            self._f = None

    def __exit__(self, *exc):
        self._close()
        FileLock._threads.release()
        return False


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

def practice_day_end(start: _dt.date, weekdays, n: int) -> _dt.date:
    """The nth practice day counting `start` as day 1 when it is one."""
    days = {int(d) for d in weekdays} or {0, 1, 2, 3, 4, 5}
    d, count = start, 0
    while True:
        if d.weekday() in days:
            count += 1
            if count >= n:
                return d
        d += _dt.timedelta(days=1)


def _d4(x: Decimal) -> str:
    return str(Decimal(x).quantize(CENT4, rounding=ROUND_UP))


@dataclass(frozen=True)
class Reservation:
    month: str
    worst: Decimal


class Ledger:
    VERSION = 2

    def __init__(self, path, cap: Decimal, trial_days: int = 0, weekdays=(0, 1, 2, 3, 4, 5)):
        self.path = Path(path)
        self.cap = Decimal(cap)
        self.trial_days = int(trial_days or 0)
        self.weekdays = list(weekdays or (0, 1, 2, 3, 4, 5))

    # --- file ---------------------------------------------------------------

    @staticmethod
    def fresh() -> dict:
        return {"version": 2, "months": {}, "earlier_aud": "0.0000", "first_use": "",
                "trial_end": "", "created": _dt.datetime.now().isoformat(timespec="seconds")}

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise LedgerError("Chat spend ledger is missing - chat stays off until Mark sets it up.")
        except (OSError, ValueError):
            raise LedgerError("Chat spend ledger can't be read - chat stays off.")
        if not isinstance(data, dict):
            raise LedgerError("Chat spend ledger can't be read - chat stays off.")
        return data

    @staticmethod
    def migrate(data: dict, now=None) -> tuple[dict, bool]:
        """(version-2 data, changed). Version 1 = the trial ledger: one
        spent_aud total, optionally a "month" it belongs to and a total_aud of
        earlier months. Its spend is carried forward, never dropped."""
        if data.get("version") == 2:
            return data, False
        if data.get("version") != 1:
            raise LedgerError("Chat spend ledger can't be read - chat stays off.")
        try:
            spent = Decimal(str(data["spent_aud"]))
            earlier = Decimal(str(data.get("total_aud") or "0"))
            calls = int(data.get("calls") or 0)
            assert spent >= 0 and earlier >= 0 and calls >= 0
        except Exception:
            raise LedgerError("Chat spend ledger can't be read - chat stays off.")
        month = str(data.get("month") or "") or month_key(now)
        out = {"version": 2,
               "months": {month: {"spent_aud": _d4(spent), "calls": calls}},
               "earlier_aud": _d4(earlier),
               "first_use": data.get("first_use") or "",
               "trial_end": data.get("trial_end") or "",
               "created": data.get("created") or "",
               "migrated_from_v1": _dt.datetime.now().isoformat(timespec="seconds")}
        return out, True

    @staticmethod
    def validate(data: dict) -> dict:
        try:
            assert data.get("version") == 2 and isinstance(data.get("months"), dict)
            for k, v in data["months"].items():
                _dt.datetime.strptime(k, "%Y-%m")
                assert Decimal(str(v["spent_aud"])) >= 0 and int(v["calls"]) >= 0
            assert Decimal(str(data.get("earlier_aud") or "0")) >= 0
            for k in ("first_use", "trial_end"):
                if data.get(k):
                    _dt.date.fromisoformat(data[k])
        except Exception:
            raise LedgerError("Chat spend ledger can't be read - chat stays off.")
        return data

    def _write(self, data: dict) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def read(self, now=None) -> dict:
        """The ledger, migrated in place (under the lock) if it is still the
        old format. Raises LedgerError when missing or unreadable."""
        data = self._load()
        if data.get("version") != 2:
            with FileLock(self.path):
                data, changed = self.migrate(self._load(), now)
                self.validate(data)
                if changed:
                    self._write(data)
            return data
        return self.validate(data)

    def init(self) -> bool:
        """Admin step only. Never overwrites an existing ledger."""
        with FileLock(self.path):
            if self.path.exists():
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write(self.fresh())
            return True

    # --- money --------------------------------------------------------------

    @staticmethod
    def spent_in(data: dict, month: str) -> Decimal:
        m = data["months"].get(month)
        return Decimal(str(m["spent_aud"])) if m else Decimal(0)

    def trial_over(self, data: dict, now=None) -> bool:
        end = data.get("trial_end")
        return bool(end) and sydney_now(now).date() > _dt.date.fromisoformat(end)

    def reserve(self, worst: Decimal, now=None) -> Reservation:
        """Add the worst case BEFORE the call (a crash mid-call still counts
        it). Refuses at the cap, at trial end, or on any ledger problem."""
        worst = Decimal(worst)
        with FileLock(self.path):
            data, _ = self.migrate(self._load(), now)
            self.validate(data)
            if self.trial_over(data, now):
                raise BudgetRefused("trial_over")
            month = month_key(now)
            spent = self.spent_in(data, month)
            if spent + worst > self.cap:
                raise BudgetRefused("cap")
            if not data.get("first_use"):
                today = sydney_now(now).date()
                data["first_use"] = today.isoformat()
                if self.trial_days > 0:
                    data["trial_end"] = practice_day_end(today, self.weekdays,
                                                         self.trial_days).isoformat()
            m = data["months"].setdefault(month, {"spent_aud": "0.0000", "calls": 0})
            m["spent_aud"] = _d4(spent + worst)
            m["calls"] = int(m["calls"]) + 1
            self._write(data)
            return Reservation(month, worst)

    def settle(self, res: Reservation, actual: Decimal | None) -> None:
        """Swap the reservation for the reported cost, in the month the call
        started. actual None (failed / unknown) keeps the whole reservation."""
        if actual is None:
            return
        with FileLock(self.path):
            data, _ = self.migrate(self._load())
            self.validate(data)
            m = data["months"].setdefault(res.month, {"spent_aud": "0.0000", "calls": 0})
            spent = Decimal(str(m["spent_aud"])) - res.worst + Decimal(actual)
            m["spent_aud"] = _d4(max(spent, Decimal(0)))
            self._write(data)
