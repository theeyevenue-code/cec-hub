"""Who got what: input validation, the subprocess call shape and the API routes.

No real agent, no Optomate: subprocess.run is replaced by a fake that records the call.
"""
import json
import subprocess
import types

import pytest

from hub import contactlog


def _agent(tmp_path):
    (tmp_path / "recall").mkdir(exist_ok=True)
    (tmp_path / "recall" / "contact_log.py").write_text("# fake", encoding="utf-8")
    return {"optomate_agent": {"agent_dir": str(tmp_path), "python": "py-fake"},
            "review_bot": {"sent_log": str(tmp_path / "bot" / "sent_log.json")}}


@pytest.fixture
def fake_run(monkeypatch):
    calls = []
    reply = {"stdout": json.dumps({"ok": True, "results": [], "more": False})}

    def run(cmd, **kw):
        calls.append({"cmd": cmd, **kw})
        return types.SimpleNamespace(stdout="noise line\n" + reply["stdout"] + "\n", stderr="", returncode=0)
    monkeypatch.setattr(contactlog.subprocess, "run", run)
    return calls, reply


def test_clean_query_keeps_name_characters_only():
    assert contactlog.clean_query("O'Brien (Sean); drop") == "O'Brien Sean drop"
    assert contactlog.clean_query("Smith-Jones  Ann") == "Smith-Jones Ann"
    assert len(contactlog.clean_query("a" * 200)) == 60


def test_search_call_shape(tmp_path, fake_run):
    calls, _ = fake_run
    out = contactlog.search(_agent(tmp_path), "Smith John")
    assert out["connected"] is True and out["ok"] is True
    c = calls[0]
    assert c["cmd"] == ["py-fake", "-m", "recall.contact_log", "--q", "Smith John", "--json"]
    assert c["cwd"] == str(tmp_path) and c["timeout"] == contactlog.TIMEOUT_S


def test_search_too_short_never_calls_agent(tmp_path, fake_run):
    calls, _ = fake_run
    assert contactlog.search(_agent(tmp_path), "a ()")["results"] == []
    assert calls == []


def test_timeline_call_shape_passes_review_log(tmp_path, fake_run):
    calls, _ = fake_run
    cfg = _agent(tmp_path)
    contactlog.timeline(cfg, "42")
    assert calls[0]["cmd"] == ["py-fake", "-m", "recall.contact_log", "--pid", "42",
                               "--review-log", cfg["review_bot"]["sent_log"], "--json"]


def test_timeline_refuses_bad_pid(tmp_path, fake_run):
    calls, _ = fake_run
    for bad in ("abc", "-1", "0", None, "1; rm", "99999999999"):
        assert "error" in contactlog.timeline(_agent(tmp_path), bad)
    assert calls == []


def test_not_connected_without_agent(tmp_path):
    cfg = {"optomate_agent": {"agent_dir": str(tmp_path)}}
    assert contactlog.search(cfg, "Smith")["connected"] is False
    assert contactlog.timeline({}, 5)["connected"] is False


def test_never_raises_on_timeout_or_garbage(tmp_path, monkeypatch, fake_run):
    calls, reply = fake_run
    reply["stdout"] = "not json"
    assert "error" in contactlog.timeline(_agent(tmp_path), 5)

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(contactlog.subprocess, "run", slow)
    assert "too long" in contactlog.search(_agent(tmp_path), "Smith")["error"]

    def missing(cmd, **kw):
        raise OSError("no python")
    monkeypatch.setattr(contactlog.subprocess, "run", missing)
    assert contactlog.search(_agent(tmp_path), "Smith")["connected"] is False


def test_routes_are_post_no_store_and_log_no_names(hub_client, monkeypatch, caplog):
    seen = {}

    def fake_search(cfg, q):
        seen["q"] = q
        return {"connected": True, "ok": True, "results": [{"id": 1, "name": "Testy Person"}], "more": False}

    def fake_timeline(cfg, pid):
        seen["pid"] = pid
        return {"connected": True, "ok": True, "summary": "0 messages in the last 12 months",
                "events": [], "sources": {}, "counts": {}}
    monkeypatch.setattr(contactlog, "search", fake_search)
    monkeypatch.setattr(contactlog, "timeline", fake_timeline)
    with caplog.at_level("INFO"):
        r = hub_client.post("/api/contactlog/search", json={"q": "Person Testy"})
        assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
        assert r.get_json()["results"][0]["id"] == 1
        r = hub_client.post("/api/contactlog/timeline", json={"pid": 1})
        assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert seen == {"q": "Person Testy", "pid": 1}
    assert "Testy" not in caplog.text
    # GET is never answered (the static catch-all makes it a 404 rather than 405)
    assert hub_client.get("/api/contactlog/search?q=Person").status_code in (404, 405)
