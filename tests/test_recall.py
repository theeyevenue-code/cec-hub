"""Tests for hub/recall.py — the Recalls tile's back end.

What matters here: bad input never reaches a subprocess or the filesystem, all
failure modes come back as friendly messages, and only strictly-patterned
filenames are ever served. (The batch rows DO pass through — Mark wants the full
list on screen — that's by design, same posture as the Payment follow-ups tile.)
"""

import json

import pytest

from hub import recall


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "recall").mkdir()
    (tmp_path / "local-reports").mkdir()
    return {"optomate_agent": {"agent_dir": str(tmp_path), "python": "py"}}


# --- input validation -------------------------------------------------------

@pytest.mark.parametrize("month", ["", "nope", "2026-13", "2026-00", "2026-1",
                                   "2026-01-01", "../../etc"])
def test_bad_month_is_rejected_without_running_anything(cfg, month, monkeypatch):
    monkeypatch.setattr(recall.subprocess, "run",
                        lambda *a, **k: pytest.fail("should not have run"))
    out = recall.preview(cfg, month, "T0")
    assert out["error"]


@pytest.mark.parametrize("touch", ["", "T1", "DROP TABLE", "t-4; rm -rf /"])
def test_bad_touch_is_rejected_without_running_anything(cfg, touch, monkeypatch):
    monkeypatch.setattr(recall.subprocess, "run",
                        lambda *a, **k: pytest.fail("should not have run"))
    out = recall.preview(cfg, "2026-08", touch)
    assert out["error"]


def test_valid_touches_are_accepted_case_insensitively(cfg, monkeypatch):
    seen = {}

    class P:
        stdout = json.dumps({"ok": True, "to_send": 3})
        returncode = 0

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return P()

    monkeypatch.setattr(recall.subprocess, "run", fake_run)
    out = recall.preview(cfg, "2026-08", "t-4")
    assert out["to_send"] == 3
    assert "T-4" in seen["argv"]


# --- not-connected paths ----------------------------------------------------

def test_missing_agent_dir_says_not_connected():
    out = recall.preview({}, "2026-08", "T0")
    assert out["connected"] is False


def test_agent_dir_without_recall_engine_says_not_connected(tmp_path):
    cfg = {"optomate_agent": {"agent_dir": str(tmp_path)}}
    out = recall.preview(cfg, "2026-08", "T0")
    assert out["connected"] is False


# --- subprocess failure modes ----------------------------------------------

def test_timeout_is_a_friendly_message(cfg, monkeypatch):
    def boom(*a, **k):
        raise recall.subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(recall.subprocess, "run", boom)
    out = recall.preview(cfg, "2026-08", "T0")
    assert "too long" in out["error"]


def test_unparseable_output_is_handled(cfg, monkeypatch):
    class P:
        stdout = "not json at all"
        returncode = 0
    monkeypatch.setattr(recall.subprocess, "run", lambda *a, **k: P())
    out = recall.preview(cfg, "2026-08", "T0")
    assert out["error"]


def test_empty_output_is_handled(cfg, monkeypatch):
    class P:
        stdout = "\n  \n"
        returncode = 0
    monkeypatch.setattr(recall.subprocess, "run", lambda *a, **k: P())
    out = recall.preview(cfg, "2026-08", "T0")
    assert out["error"]


def test_agent_reported_error_is_passed_through(cfg, monkeypatch):
    class P:
        stdout = json.dumps({"error": "unknown touch"})
        returncode = 2
    monkeypatch.setattr(recall.subprocess, "run", lambda *a, **k: P())
    out = recall.preview(cfg, "2026-08", "T0")
    assert out["error"] == "unknown touch"


def test_json_on_last_line_wins(cfg, monkeypatch):
    """The agent may print warnings before the JSON line."""
    class P:
        stdout = "some warning\n" + json.dumps({"ok": True, "to_send": 9}) + "\n"
        returncode = 0
    monkeypatch.setattr(recall.subprocess, "run", lambda *a, **k: P())
    assert recall.preview(cfg, "2026-08", "T0")["to_send"] == 9


# --- the full batch passes through (Mark wants the list up-front) -----------

def test_patient_rows_pass_through_for_the_full_list(cfg, monkeypatch):
    class P:
        stdout = json.dumps({
            "ok": True, "to_send": 2,
            "rows": [{"pid": 1, "name": "Jane Doe", "mobile": "0400111222",
                      "deselected": False}],
        })
        returncode = 0
    monkeypatch.setattr(recall.subprocess, "run", lambda *a, **k: P())
    out = recall.preview(cfg, "2026-08", "T0")
    assert out["rows"][0]["name"] == "Jane Doe"
    assert out["to_send"] == 2


# --- deselection ------------------------------------------------------------

def test_set_deselected_writes_the_agent_file(cfg):
    out = recall.set_deselected(cfg, "2026-08", "T-4", [3, 1, 2, 2])
    assert out == {"ok": True, "deselected": 3}
    f = (recall.agent_dir(cfg) / "local-reports"
         / "recall-deselect-202608-Tminus4.json")
    saved = json.loads(f.read_text(encoding="utf-8"))
    assert saved["pids"] == [1, 2, 3]          # deduped + sorted
    assert saved["month"] == "2026-08" and saved["touch"] == "T-4"


def test_set_deselected_empty_list_clears(cfg):
    recall.set_deselected(cfg, "2026-08", "T0", [5])
    out = recall.set_deselected(cfg, "2026-08", "T0", [])
    assert out["deselected"] == 0
    f = recall.agent_dir(cfg) / "local-reports" / "recall-deselect-202608-T0.json"
    assert json.loads(f.read_text(encoding="utf-8"))["pids"] == []


@pytest.mark.parametrize("month,touch,pids", [
    ("nope", "T0", [1]),
    ("2026-08", "TX", [1]),
    ("2026-08", "T0", ["abc"]),
])
def test_set_deselected_rejects_bad_input(cfg, month, touch, pids):
    assert recall.set_deselected(cfg, month, touch, pids).get("error")


def test_set_deselected_not_connected_without_agent():
    assert recall.set_deselected({}, "2026-08", "T0", [1]).get("error")


# --- preview_file path safety ----------------------------------------------

def test_preview_file_returns_none_when_missing(cfg):
    assert recall.preview_file(cfg, "2026-08", "T0") is None


def test_preview_file_finds_the_generated_file(cfg):
    d = recall.agent_dir(cfg) / "local-reports"
    (d / "recall-sms-preview-202608-T0.html").write_text("<h1>x</h1>", encoding="utf-8")
    got = recall.preview_file(cfg, "2026-08", "T0")
    assert got is not None and got.name == "recall-sms-preview-202608-T0.html"


def test_preview_file_encodes_touch_safely(cfg):
    d = recall.agent_dir(cfg) / "local-reports"
    (d / "recall-sms-preview-202608-Tminus4.html").write_text("x", encoding="utf-8")
    assert recall.preview_file(cfg, "2026-08", "T-4") is not None
    (d / "recall-sms-preview-202608-Tplus6.html").write_text("x", encoding="utf-8")
    assert recall.preview_file(cfg, "2026-08", "T+6") is not None


@pytest.mark.parametrize("bad", ["../../windows/win", "2026-08/../..", ""])
def test_preview_file_rejects_path_tricks(cfg, bad):
    assert recall.preview_file(cfg, bad, "T0") is None


# --- Angie's chase sheet through the Hub -------------------------------------

def test_latest_chase_sheet_picks_the_newest(cfg):
    d = recall.agent_dir(cfg) / "local-reports"
    (d / "recall-call-sheet-chase-20260730.html").write_text("old", encoding="utf-8")
    (d / "recall-call-sheet-chase-20260731.html").write_text("new", encoding="utf-8")
    (d / "recall-call-sheet-chase-XXXX.html").write_text("junk", encoding="utf-8")
    (d / "unrelated.html").write_text("no", encoding="utf-8")
    got = recall.latest_chase_sheet(cfg)
    assert got.name == "recall-call-sheet-chase-20260731.html"


def test_latest_chase_sheet_none_when_absent(cfg):
    assert recall.latest_chase_sheet(cfg) is None
    assert recall.latest_chase_sheet({}) is None


def test_refresh_chase_sheet_parses_counts(cfg, monkeypatch):
    d = recall.agent_dir(cfg) / "local-reports"

    class P:
        stdout = "Phone recall sheet — x\n  patients to call: 98  households: 94\n"
        returncode = 0

    def fake_run(argv, **kw):
        (d / "recall-call-sheet-chase-20260731.html").write_text("x", encoding="utf-8")
        assert "recall.call_sheet" in argv[2]
        return P()

    monkeypatch.setattr(recall.subprocess, "run", fake_run)
    out = recall.refresh_chase_sheet(cfg)
    assert out["ok"] and out["patients"] == 98 and out["households"] == 94


def test_refresh_chase_sheet_errors_when_no_file_appears(cfg, monkeypatch):
    class P:
        stdout = "boom"
        returncode = 1
    monkeypatch.setattr(recall.subprocess, "run", lambda *a, **k: P())
    out = recall.refresh_chase_sheet(cfg)
    assert out.get("error")
