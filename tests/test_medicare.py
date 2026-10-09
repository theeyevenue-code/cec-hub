"""hub.medicare + /api/medicare. The agent subprocess is mocked; FICTIONAL data only."""
import importlib
import json
import subprocess

import pytest

from hub import medicare as M

ITEM = {"key": "0123456789ab", "service_date": "2026-05-01", "deadline": "2027-05-01",
        "days_left": 200, "patient_label": "Sam K", "item": "10918", "amount": 34.0,
        "code": "374", "meaning": "Old card issue number used", "action_group": "do_now",
        "steps": "Resubmit.", "fact": "Record now has issue no. 2", "claim": "Z1001",
        "flag": "", "status": None}

REVIEW = dict(ITEM, key="abcdefabcdef", item="10910", code="160", action_group="review",
              review={"confidence": "High", "why": "We claimed 10910 on 2025-05-27 (36-month rule)",
                      "age": 55, "minutes": 30,
                      "suggestions": [{"item": "10914", "name": "Progressive disorder", "confidence": "High",
                                       "history_only": False,
                                       "snippets": [{"section": "Posterior", "text": "Small drusen OU"}]}]})

AGENT_OUT = {"ok": True, "generated_at": "2026-10-13 09:00", "last_report_pull": "2026-10-13 08:55",
             "days_since_pull": 0, "pull_overdue": False,
             "unreported_claims": {"count": 2, "oldest_days": 9},
             "summary": {"do_now": {"n": 1, "amount": 34.0}, "review": {"n": 1, "amount": 67.85}},
             "items": [ITEM, REVIEW],
             "today": [{"time": "09:00", "patient_label": "Ruby H",
                        "lines": ["Comprehensive used 15 Aug 2025 (10910, 36-month rule): bill 10913/10914 if notes support, else 10918 or private."]}]}


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
    assert out["summary"]["mark"] == {"n": 0, "amount": 0.0}
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
    assert len(M.worklist(agent)["items"]) == 1     # the review card is Mark-only


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


# --- Mark's item review: visible only to Mark ----------------------------------------
def test_is_mark_case_insensitive():
    assert M.is_mark("Mark") and M.is_mark(" mark ") and M.is_mark("MARK")
    assert not M.is_mark("Karen") and not M.is_mark("Marky") and not M.is_mark("")


def test_karen_never_gets_review_cards_or_snippets(agent, calls):
    out = M.worklist(agent, fixture=True, mark_view=False)
    assert "--review" not in calls[-1]
    assert [i["key"] for i in out["items"]] == ["0123456789ab"]
    assert "drusen" not in json.dumps(out) and out["summary"]["review"]["n"] == 1
    assert out["is_mark"] is False


def test_mark_gets_cards_with_snippets(agent, calls):
    out = M.worklist(agent, fixture=True, mark_view=True)
    assert "--review" in calls[-1]
    card = [i for i in out["items"] if i["action_group"] == "review"][0]
    assert card["review"]["suggestions"][0]["snippets"][0] == {"section": "Posterior", "text": "Small drusen OU"}
    assert card["review"]["why"].startswith("We claimed 10910")


def test_review_whitelist_trims_and_drops_odd_items():
    raw = dict(REVIEW["review"], suggestions=[
        {"item": "10918", "name": "x", "snippets": []},
        {"item": "10913", "name": "n", "confidence": "High",
         "snippets": [{"section": "Complaint", "text": "x" * 500}] * 4}])
    r = M.clean_review(raw, True)
    assert [s["item"] for s in r["suggestions"]] == ["10913"]
    assert len(r["suggestions"][0]["snippets"]) == 2 and len(r["suggestions"][0]["snippets"][0]["text"]) == 140
    assert M.clean_review(raw, False) == {"confidence": "High"}


def test_today_lines_pass_through_cleaned(agent, calls):
    t = M.worklist(agent, fixture=True)["today"]
    assert t[0]["time"] == "09:00" and t[0]["patient_label"] == "Ruby H" and "10910" in t[0]["lines"][0]


def test_accept_and_not_eligible_are_mark_only(agent, calls):
    assert M.mark(agent, "0123456789ab", "accepted_10914", "Karen")["error"] == "Only Mark can decide the item."
    assert M.mark(agent, "0123456789ab", "not_eligible", "Angie")["ok"] is False
    assert M.mark(agent, "0123456789ab", "accepted_10918", "Mark")["ok"] is False
    assert calls == []
    assert M.mark(agent, "0123456789ab", "accepted_10914", "mark")["ok"]
    assert calls[0][3:7] == ["--mark", "0123456789ab", "--status", "accepted_10914"]


def test_endpoint_review_needs_mark_cookie(client, calls):
    client.set_cookie("hub_staff", "Karen")
    body = client.get("/api/medicare").get_json()
    assert all(i["action_group"] != "review" for i in body["items"])
    client.set_cookie("hub_staff", "Mark")
    r = client.get("/api/medicare")
    assert r.headers["Cache-Control"] == "no-store"
    assert any(i["action_group"] == "review" for i in r.get_json()["items"])
    client.set_cookie("hub_staff", "Karen")
    assert client.post("/api/medicare/mark", json={"key": "0123456789ab", "status": "accepted_10914"}).status_code == 400
