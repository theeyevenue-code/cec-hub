"""hub.medicare + /api/medicare. The agent subprocess is mocked; FICTIONAL data only."""
import importlib
import json
import subprocess

import pytest

from hub import medicare as M

ITEM = {"key": "0123456789ab", "service_date": "2026-05-01", "deadline": "2027-05-01",
        "days_left": 200, "patient_label": "Sam K", "item": "10918", "amount": 34.0,
        "code": "374", "meaning": "Old card issue number used", "action_group": "fix_now",
        "steps": "Resubmit.", "fact": "Record now has issue no. 2", "claim": "Z1001",
        "flag": "", "status": None}

AGENT_OUT = {"ok": True, "generated_at": "2026-10-13 09:00", "last_report_pull": "2026-10-13 08:55",
             "days_since_pull": 0, "pull_overdue": False,
             "unreported_claims": {"count": 2, "oldest_days": 9},
             "summary": {"fix_now": {"n": 1, "amount": 34.0}}, "items": [ITEM]}


class FakeProc:
    def __init__(self, out):
        self.stdout = out
        self.returncode = 0


@pytest.fixture
def agent(tmp_path):
    d = tmp_path / "agent"
    (d / "medicare").mkdir(parents=True)
    (d / "medicare" / "rejections.py").write_text("# stub", encoding="utf-8")
    return {"optomate_agent": {"agent_dir": str(d), "python": "python"}}


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)
        if "--mark" in argv:
            return FakeProc('{"ok": true, "status": "done"}\n')
        return FakeProc("noise line\n" + json.dumps(AGENT_OUT) + "\n")
    monkeypatch.setattr(M.subprocess, "run", fake_run)
    return seen


def test_worklist_reads_last_json_line_with_fixed_args(agent, calls):
    out = M.worklist(agent, fixture=True)
    assert out["connected"] and out["items"][0]["patient_label"] == "Sam K"
    assert calls[0][1:] == ["-m", "medicare.rejections", "--json", "--fixture"]
    assert out["summary"]["check"] == {"n": 0, "amount": 0.0}
    assert out["unreported_claims"] == {"count": 2, "oldest_days": 9}


def test_unknown_fields_never_reach_the_page(agent, monkeypatch):
    leaky = dict(ITEM, medicare_number="1234567890", _name="Sam Kerr", dob="1990-01-01")
    body = dict(AGENT_OUT, items=[leaky], patients_full=["x"])
    monkeypatch.setattr(M.subprocess, "run", lambda a, **k: FakeProc(json.dumps(body)))
    text = json.dumps(M.worklist(agent))
    for bad in ("1234567890", "Sam Kerr", "1990-01-01", "patients_full"):
        assert bad not in text


def test_bad_items_dropped(agent, monkeypatch):
    body = dict(AGENT_OUT, items=[dict(ITEM, key="../../etc"), dict(ITEM, action_group="nope"), ITEM])
    monkeypatch.setattr(M.subprocess, "run", lambda a, **k: FakeProc(json.dumps(body)))
    assert len(M.worklist(agent)["items"]) == 1


def test_not_connected_and_not_set_up(tmp_path):
    assert M.worklist({})["connected"] is False
    cfg = {"optomate_agent": {"agent_dir": str(tmp_path)}}
    assert "isn't set up" in M.worklist(cfg)["message"]


def test_agent_error_and_timeout_degrade(agent, monkeypatch):
    monkeypatch.setattr(M.subprocess, "run",
                        lambda a, **k: FakeProc('{"ok": false, "error": "Couldn\'t read Optomate (x)."}'))
    assert "Couldn't read Optomate" in M.worklist(agent)["error"]

    def slow(a, **k):
        raise subprocess.TimeoutExpired(a, 60)
    monkeypatch.setattr(M.subprocess, "run", slow)
    assert "too long" in M.worklist(agent)["error"]
    monkeypatch.setattr(M.subprocess, "run", lambda a, **k: FakeProc("garbage"))
    assert M.worklist(agent)["error"]


def test_mark_validates_and_passes_fixed_args(agent, calls):
    assert M.mark(agent, "bad", "done", "Karen")["ok"] is False
    assert M.mark(agent, "0123456789ab", "delete", "Karen")["ok"] is False
    assert M.mark(agent, "0123456789ab", "done", "")["error"].startswith("Pick your name")
    assert calls == []
    assert M.mark(agent, "0123456789AB", "done", "Karen; rm -rf")["ok"]
    assert calls[0][3:] == ["--mark", "0123456789ab", "--status", "done", "--by", "Karen rm -rf"]


# --- endpoints ------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch, agent, calls):
    cfg = tmp_path / "integrations.json"
    cfg.write_text(json.dumps(agent), encoding="utf-8")
    monkeypatch.setenv("CEC_HUB_INTEGRATIONS", str(cfg))
    monkeypatch.setenv("CEC_HUB_MEDICARE_FIXTURE", "1")
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_get_is_no_store(client, calls):
    r = client.get("/api/medicare")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert r.get_json()["items"][0]["key"] == "0123456789ab"
    assert "--fixture" in calls[0]


def test_post_mark_uses_staff_cookie(client, calls):
    client.set_cookie("hub_staff", "Karen")
    r = client.post("/api/medicare/mark", json={"key": "0123456789ab", "status": "written_off"})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert calls[-1][calls[-1].index("--by") + 1] == "Karen"


def test_post_mark_without_name_is_refused(client, calls):
    r = client.post("/api/medicare/mark", json={"key": "0123456789ab", "status": "done"})
    assert r.status_code == 400 and not calls


def test_page_route_and_function_exist():
    js = open("static/app.js", encoding="utf-8").read()
    assert "#\\/medicare$/, fn: renderMedicare" in js and "async function renderMedicare" in js
