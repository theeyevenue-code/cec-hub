"""Jobs arrived page (hub/jobscan.py + routes). The agent engine is faked: no subprocess
reaches Optomate or Twilio here."""

import importlib
import json
import logging
import subprocess

import pytest

from hub import jobscan


class Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


@pytest.fixture
def agent(tmp_path):
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "jobscan.py").write_text("# fake", encoding="utf-8")
    return {"optomate_agent": {"agent_dir": str(tmp_path), "python": "py.exe"}}


class _Calls(list):
    """Stands in for subprocess.run: records each call, answers with noise + one JSON line."""
    reply = {"ok": True, "lines": []}
    raise_exc = None
    stdout = None

    def __call__(self, cmd, **kw):
        self.append((cmd, kw))
        if self.raise_exc:
            raise self.raise_exc
        return Proc(self.stdout if self.stdout is not None else "noise\n" + json.dumps(self.reply))


@pytest.fixture
def calls(monkeypatch):
    seen = _Calls()
    monkeypatch.setattr(jobscan.subprocess, "run", seen)
    return seen


# --- helpers ------------------------------------------------------------------

def test_clean_ids_dedupes_strips_zeros_and_splits_bad():
    good, bad = jobscan.clean_ids(["35144", "035144", " 35145 ", "abc<x>", "", None, "1234567890"])
    assert good == ["35144", "35145"] and bad == ["abcx", "1234567890"]
    assert jobscan.clean_ids("35144") == (["35144"], [])
    assert jobscan.clean_ids(None) == ([], [])


def test_clean_ids_keeps_glasses_contacts_type():
    # contacts orders share numbers with glasses jobs (agent quirk #87): the type a user
    # picked travels as G/C and is never dropped or merged with the bare number
    good, bad = jobscan.clean_ids(["G035144", "c5100", "CL5100", "5100", "X5100", "G", "C:7"])
    assert good == ["G35144", "C5100", "5100", "C7"] and bad == ["X5100", "G"]


def test_mode_passed_and_sanitised(agent, calls):
    calls.reply = {"ok": True, "lines": []}
    jobscan.lookup(agent, ["C5100"], "contacts")
    jobscan.lookup(agent, ["5100"], "rm -rf")
    calls.reply = {"ok": True, "counts": {}, "results": []}
    jobscan.confirm(agent, ["G5100", "C5101"], 2, "contacts")
    assert calls[0][0][-4:] == ["--ids", "C5100", "--mode", "contacts"]
    assert calls[1][0][-2:] == ["--mode", "glasses"]
    assert calls[2][0][-4:] == ["--ids", "G5100,C5101", "--mode", "contacts"]


def test_not_connected_without_agent_engine(tmp_path):
    out = jobscan.lookup({"optomate_agent": {"agent_dir": str(tmp_path)}}, ["1"])
    assert out["ok"] is False and out["connected"] is False
    assert jobscan.status({}).get("connected") is False


def test_lookup_runs_the_engine_with_validated_ids_only(agent, calls):
    calls.reply = {"ok": True, "lines": [{"id": 35144, "state": "ok"}]}
    out = jobscan.lookup(agent, ["35144", "x;rm", "35144"])
    cmd, kw = calls[0]
    assert cmd == ["py.exe", "-m", "orders.jobscan", "lookup", "--json", "--ids", "35144",
                   "--mode", "glasses"]
    assert kw["cwd"] == agent["optomate_agent"]["agent_dir"]
    assert [l["state"] for l in out["lines"]] == ["ok", "bad"]


def test_lookup_of_only_rubbish_never_runs_the_engine(agent, calls):
    out = jobscan.lookup(agent, ["hello"])
    assert calls == [] and out["lines"] == [{"id": "hello", "state": "bad", "reason": "Not a job number"}]
    assert jobscan.lookup(agent, [])["ok"] is False


def test_confirm_refuses_when_count_differs_from_dialog(agent, calls):
    out = jobscan.confirm(agent, ["1", "2"], 3)
    assert out["ok"] is False and "Nothing changed" in out["error"] and calls == []
    assert jobscan.confirm(agent, [], 0)["ok"] is False


def test_confirm_passes_ids(agent, calls):
    calls.reply = {"ok": True, "nothing_changed": True, "counts": {"would": 2}, "results": []}
    out = jobscan.confirm(agent, ["1", "2"], "2")
    assert calls[0][0][3:] == ["confirm", "--json", "--ids", "1,2", "--mode", "glasses"]
    assert out["nothing_changed"]
    assert calls[0][1]["timeout"] == jobscan.TIMEOUTS["confirm"]


def test_confirm_timeout_says_rescan(agent, calls):
    calls.raise_exc = subprocess.TimeoutExpired("x", 1)
    out = jobscan.confirm(agent, ["1"], 1)
    assert out["timeout"] and "Scan the cards again" in out["error"]


def test_unreadable_answer(agent, calls):
    calls.stdout = "Traceback...\n"
    assert "readable" in jobscan.lookup(agent, ["1"])["error"]
    assert "Scan the cards again" in jobscan.confirm(agent, ["1"], 1)["error"]


def test_engine_failure_to_start(agent, calls):
    calls.raise_exc = OSError("no python")
    assert jobscan.status(agent)["connected"] is False


# --- routes -------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch, agent):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "integrations.json").write_text(json.dumps(agent), encoding="utf-8")
    monkeypatch.setenv("CEC_HUB_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("CEC_HUB_INTEGRATIONS", raising=False)
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    fake = _Calls()
    monkeypatch.setattr(jobscan.subprocess, "run", fake)
    with app_module.app.test_client() as c:
        c.fake = fake
        yield c


def test_routes_are_no_store(client):
    client.fake.reply = {"ok": True, "gates": {"dry_run": True}, "mode_words": "TEST MODE"}
    r = client.get("/api/jobscan/status")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    r = client.post("/api/jobscan/lookup", json={"ids": ["90001"]})
    assert r.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("method,url", [("get", "/api/jobscan/status"),
                                        ("post", "/api/jobscan/lookup"),
                                        ("post", "/api/jobscan/confirm")])
def test_routes_are_staff_only(client, method, url):
    r = getattr(client, method)(url, json={"ids": ["1"], "expected": 1},
                                environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert r.status_code == 403 and r.get_json()["staff_needed"] is True
    r = getattr(client, method)(url, json={"ids": ["1"], "expected": 1},
                                headers={"X-Checkin-Key": "k"})
    assert r.status_code == 403
    assert client.fake == []


def test_confirm_route_logs_counts_never_names(client, caplog):
    client.fake.reply = {"ok": True, "nothing_changed": True, "texted": 0,
                         "counts": {"done": 0, "would": 1, "skipped": 0, "failed": 0},
                         "results": [{"id": 90001, "name": "ZZTEST, Alex", "message": "Hi Alex"}]}
    with caplog.at_level(logging.INFO):
        r = client.post("/api/jobscan/confirm", json={"ids": ["90001"], "expected": 1})
    assert r.status_code == 200 and r.get_json()["results"][0]["name"] == "ZZTEST, Alex"
    assert "Jobs arrived: confirm TEST MODE - done 0, would 1" in caplog.text
    assert "ZZTEST" not in caplog.text and "Alex" not in caplog.text


def test_confirm_route_mismatch_is_400(client):
    r = client.post("/api/jobscan/confirm", json={"ids": ["1", "2"], "expected": 1})
    assert r.status_code == 400 and client.fake == []


def test_tile_and_route_wired():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    tiles = json.loads((root / "config" / "tiles.example.json").read_text(encoding="utf-8"))
    assert any(t["link"] == "#/jobs-arrived" for t in tiles["tiles"])
    js = (root / "static" / "app.js").read_text(encoding="utf-8")
    assert "#\\/jobs-arrived$/, fn: renderJobsArrived" in js
    assert "/api/jobscan/confirm" in js and "expected: ids.length" in js
