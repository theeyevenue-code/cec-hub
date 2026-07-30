"""Tests for hub/recall.py — the Recalls tile's back end.

Two things matter here: bad input never reaches the subprocess, and no patient
data ever comes back through the Hub (counts + a file path only).
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


# --- PHI must not come back through the Hub ---------------------------------

def test_patient_rows_are_stripped_even_if_the_agent_sends_them(cfg, monkeypatch):
    class P:
        stdout = json.dumps({
            "ok": True, "to_send": 2,
            "rows": [{"first_name": "Jane", "mobile": "0400111222"}],
            "patients": ["someone"], "recipients": ["someone"],
        })
        returncode = 0
    monkeypatch.setattr(recall.subprocess, "run", lambda *a, **k: P())
    out = recall.preview(cfg, "2026-08", "T0")
    for key in ("rows", "patients", "recipients"):
        assert key not in out
    assert out["to_send"] == 2


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
