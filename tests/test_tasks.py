"""Smart to-do list: due dates + recurrence + roll-forward.

Pure-function tests for the recurrence maths, plus file-round-trip tests for
add/toggle/delete through the real JSON store (redirected to tmp_path)."""

from datetime import date, timedelta

import pytest

from hub import lists


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the tasks store at a tmp folder."""
    monkeypatch.setattr(lists, "HUB_DATA", tmp_path)
    return tmp_path


TODAY = date(2026, 7, 24)          # a Friday


# --- pure helpers -----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("2026-07-24", "2026-07-24"),
    ("2026-07-24T09:00", "2026-07-24"),
    ("", None), (None, None), ("not-a-date", None), ("2026-13-01", None),
])
def test_parse_due(raw, expected):
    assert lists._parse_due(raw) == expected


@pytest.mark.parametrize("payload,ok", [
    ({"kind": "daily"}, True),
    ({"kind": "weekdays"}, True),
    ({"kind": "weekly"}, True),
    ({"kind": "every", "n": 3}, True),
    ({"kind": "every"}, False),        # missing n
    ({"kind": "every", "n": 0}, False),
    ({"kind": "monthly"}, False),      # unsupported in v1
    ("daily", False), (None, False),
])
def test_repeat_from_payload(payload, ok):
    assert (lists._repeat_from_payload(payload) is not None) == ok


def test_advance_daily():
    assert lists._advance({"kind": "daily"}, TODAY) == TODAY + timedelta(days=1)


def test_advance_every_n():
    assert lists._advance({"kind": "every", "n": 3}, TODAY) == TODAY + timedelta(days=3)


def test_advance_weekly_keeps_weekday():
    nxt = lists._advance({"kind": "weekly"}, TODAY)
    assert nxt == TODAY + timedelta(days=7) and nxt.weekday() == TODAY.weekday()


def test_advance_weekdays_skips_weekend():
    friday = date(2026, 7, 24)
    assert lists._advance({"kind": "weekdays"}, friday) == date(2026, 7, 27)  # Mon


# --- materialize (idempotent generation) ------------------------------------

def test_materialize_creates_one_occurrence_when_due():
    series = [{"id": "s1", "text": "Open till", "by": "Karen",
               "repeat": {"kind": "daily"}, "next_due": TODAY.isoformat(),
               "active": True, "added": TODAY.isoformat()}]
    items = []
    assert lists._materialize(items, series, TODAY) is True
    assert len(items) == 1 and items[0]["series_id"] == "s1"
    assert items[0]["due"] == TODAY.isoformat()


def test_materialize_not_yet_due():
    series = [{"id": "s1", "text": "x", "by": "K", "repeat": {"kind": "daily"},
               "next_due": (TODAY + timedelta(days=2)).isoformat(),
               "active": True, "added": TODAY.isoformat()}]
    items = []
    assert lists._materialize(items, series, TODAY) is False
    assert items == []


def test_materialize_idempotent_and_single_rolling():
    """A daily task missed for days stays ONE open item, never duplicated."""
    series = [{"id": "s1", "text": "x", "by": "K", "repeat": {"kind": "daily"},
               "next_due": TODAY.isoformat(), "active": True,
               "added": TODAY.isoformat()}]
    items = []
    lists._materialize(items, series, TODAY)
    # later days, still not done -> no new occurrence
    lists._materialize(items, series, TODAY + timedelta(days=1))
    lists._materialize(items, series, TODAY + timedelta(days=3))
    assert len([t for t in items if not t["done"]]) == 1


def test_materialize_skips_inactive_series():
    series = [{"id": "s1", "text": "x", "by": "K", "repeat": {"kind": "daily"},
               "next_due": TODAY.isoformat(), "active": False,
               "added": TODAY.isoformat()}]
    items = []
    assert lists._materialize(items, series, TODAY) is False


# --- annotate ---------------------------------------------------------------

def test_annotate_overdue():
    t = {"due": (TODAY - timedelta(days=1)).isoformat(), "done": False}
    assert lists._annotate_task(t, TODAY)["overdue"] is True


def test_annotate_done_is_never_overdue():
    t = {"due": (TODAY - timedelta(days=5)).isoformat(), "done": True}
    assert lists._annotate_task(t, TODAY)["overdue"] is False


# --- file round-trip through the real store ---------------------------------

def test_add_oneoff_with_due(store):
    ok, err = lists.tasks_mutate("add", {"text": "Call lab", "due": "2026-08-01"}, "Mark")
    assert ok, err
    tasks = lists.tasks_list()
    assert len(tasks) == 1
    assert tasks[0]["text"] == "Call lab" and tasks[0]["due"] == "2026-08-01"
    assert tasks[0]["series_id"] is None


def test_add_repeating_creates_series_and_occurrence(store):
    ok, _ = lists.tasks_mutate("add", {"text": "Open till",
                                       "repeat": {"kind": "daily"}}, "Karen")
    assert ok
    tasks = lists.tasks_list()
    open_tasks = [t for t in tasks if not t["done"]]
    assert len(open_tasks) == 1
    assert open_tasks[0]["series_id"] is not None


def test_toggle_repeating_advances_and_does_not_immediately_regenerate(store):
    lists.tasks_mutate("add", {"text": "Open till", "repeat": {"kind": "daily"}}, "Karen")
    occ = lists.tasks_list()[0]
    ok, _ = lists.tasks_mutate("toggle", {"id": occ["id"]}, "Karen")
    assert ok
    tasks = lists.tasks_list()
    # the ticked one is done; NO new open occurrence today (next due is tomorrow)
    assert any(t["done"] and t["id"] == occ["id"] for t in tasks)
    assert not [t for t in tasks if not t["done"]]


def test_delete_repeating_occurrence_stops_the_series(store):
    lists.tasks_mutate("add", {"text": "x", "repeat": {"kind": "daily"}}, "K")
    occ = lists.tasks_list()[0]
    lists.tasks_mutate("delete", {"id": occ["id"]}, "K")
    # series deactivated -> nothing regenerates
    assert lists.tasks_list() == []


def test_edit_updates_text(store):
    lists.tasks_mutate("add", {"text": "old"}, "K")
    tid = lists.tasks_list()[0]["id"]
    lists.tasks_mutate("edit", {"id": tid, "text": "new"}, "K")
    assert lists.tasks_list()[0]["text"] == "new"


def test_backward_compat_old_format(store):
    # a pre-smart tasks.json (simple records, no series key) still loads + ticks
    (store / "tasks.json").write_text(
        '{"tasks": [{"id": "t1", "text": "legacy", "by": "Mark",'
        ' "added": "2026-07-01", "done": false}]}', encoding="utf-8")
    tasks = lists.tasks_list()
    assert len(tasks) == 1 and tasks[0]["text"] == "legacy"
    ok, _ = lists.tasks_mutate("toggle", {"id": "t1"}, "Mark")
    assert ok and lists.tasks_list()[0]["done"] is True
