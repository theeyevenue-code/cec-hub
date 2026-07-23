"""Credits watch-list: amount capture + ageing / overdue-chase logic.

Pure-function and file-round-trip tests — no app reload, no network.
"""

import json
from datetime import date, timedelta

import pytest

from hub import lists


# --- _parse_amount -----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("12.50", 12.50),
    ("$1,234.50", 1234.50),
    (12.5, 12.50),
    ("  89 ", 89.0),
    ("", None),
    (None, None),
    ("abc", None),
    ("0", None),        # zero / negative aren't real credits
    ("-5", 5.0),        # stray sign stripped -> treated as 5 (amount is magnitude)
])
def test_parse_amount(raw, expected):
    assert lists._parse_amount(raw) == expected


# --- ageing / overdue --------------------------------------------------------

def test_days_since_counts_whole_days():
    assert lists._days_since(str(date.today() - timedelta(days=10))) == 10
    assert lists._days_since("not-a-date") is None


def test_annotate_fresh_open_is_not_overdue():
    c = lists._annotate({"added": str(date.today() - timedelta(days=5)), "status": "open"})
    assert c["days_open"] == 5
    assert c["overdue"] is False


def test_annotate_old_open_is_overdue():
    c = lists._annotate({"added": str(date.today() - timedelta(days=40)), "status": "open"})
    assert c["overdue"] is True


def test_annotate_arrived_is_never_overdue():
    # 'arrived' just needs a tick-off — it's not something to chase the supplier for.
    c = lists._annotate({"added": str(date.today() - timedelta(days=99)), "status": "arrived"})
    assert c["overdue"] is False


# --- add + list round-trip ---------------------------------------------------

def _cfg(tmp_path):
    return {"optomate_agent": {"logs_dir": str(tmp_path)}}


def test_add_stores_amount(tmp_path):
    cfg = _cfg(tmp_path)
    ok, err = lists.credits_mutate(cfg, "add",
                                   {"text": "Mr J Smith scratched lens", "supplier": "HOY",
                                    "amount": "$149.95"}, who="Karen")
    assert ok, err
    items = lists.credits_list(cfg)
    assert len(items) == 1
    assert items[0]["amount"] == 149.95
    assert items[0]["status"] == "open"


def test_add_without_amount_is_allowed(tmp_path):
    cfg = _cfg(tmp_path)
    ok, _ = lists.credits_mutate(cfg, "add",
                                 {"text": "frame return", "supplier": "SAFILO"}, who="Angie")
    assert ok
    assert lists.credits_list(cfg)[0]["amount"] is None


def test_list_sorts_overdue_first(tmp_path):
    cfg = _cfg(tmp_path)
    path = tmp_path / "credits-watch.json"
    path.write_text(json.dumps({"credits": [
        {"id": "c1", "text": "fresh", "supplier": "HOY", "status": "open",
         "added": str(date.today() - timedelta(days=2))},
        {"id": "c2", "text": "stale", "supplier": "HOY", "status": "open",
         "added": str(date.today() - timedelta(days=45))},
    ]}), encoding="utf-8")
    items = lists.credits_list(cfg)
    assert [c["id"] for c in items] == ["c2", "c1"]   # overdue chase first
    assert items[0]["overdue"] is True
