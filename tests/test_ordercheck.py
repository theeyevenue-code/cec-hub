"""Order-check proxy: input validation + graceful degradation + result parsing.
The engine subprocess is stubbed — no real speccheck / Optomate is touched."""

import json

import pytest

from hub import ordercheck


def test_blank_order():
    r = ordercheck.check({}, "")
    assert r["connected"] is True and "order number" in r["error"]


def test_non_numeric_order_rejected():
    r = ordercheck.check({}, "34813; rm -rf /")
    assert r["connected"] is True and "doesn't look like" in r["error"]


def test_not_configured_is_not_connected():
    r = ordercheck.check({}, "34813")
    assert r["connected"] is False and "connected" in r["message"].lower()


def test_engine_dir_missing_is_not_connected(tmp_path):
    cfg = {"optomate_agent": {"speccheck_dir": str(tmp_path)}}  # no speccheck/ inside
    r = ordercheck.check(cfg, "34813")
    assert r["connected"] is False and "not deployed" in r["message"].lower()


def _cfg_with_engine(tmp_path):
    (tmp_path / "speccheck").mkdir()
    return {"optomate_agent": {"speccheck_dir": str(tmp_path), "python": "py"}}


def test_valid_result_parsed(tmp_path, monkeypatch):
    cfg = _cfg_with_engine(tmp_path)
    verdict = {"order": "34813", "verdict": "ISSUE",
               "checks": {"expiry": {"status": "ISSUE", "reason": "expired"}}}

    class P:
        returncode = 1
        stdout = json.dumps(verdict)
        stderr = ""
    monkeypatch.setattr(ordercheck.subprocess, "run", lambda *a, **k: P())
    r = ordercheck.check(cfg, "34813")
    assert r["connected"] is True and r["result"]["verdict"] == "ISSUE"


def test_engine_crash_reports_error(tmp_path, monkeypatch):
    cfg = _cfg_with_engine(tmp_path)

    class P:
        returncode = 3            # not a valid speccheck exit
        stdout = ""
        stderr = "boom"
    monkeypatch.setattr(ordercheck.subprocess, "run", lambda *a, **k: P())
    r = ordercheck.check(cfg, "34813")
    assert r["connected"] is True and "boom" in r["error"]


def test_timeout(tmp_path, monkeypatch):
    cfg = _cfg_with_engine(tmp_path)

    def boom(*a, **k):
        raise ordercheck.subprocess.TimeoutExpired(cmd="speccheck", timeout=25)
    monkeypatch.setattr(ordercheck.subprocess, "run", boom)
    r = ordercheck.check(cfg, "34813")
    assert r["connected"] is True and "timed out" in r["error"]


def test_unreadable_output(tmp_path, monkeypatch):
    cfg = _cfg_with_engine(tmp_path)

    class P:
        returncode = 0
        stdout = "not json"
        stderr = ""
    monkeypatch.setattr(ordercheck.subprocess, "run", lambda *a, **k: P())
    r = ordercheck.check(cfg, "34813")
    assert r["connected"] is True and "unreadable" in r["error"]
