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
              steps="Mark to review the item.",
              review={"kind": "suggest", "confidence": "High",
                      "why": "We claimed 10910 on 2025-05-27 (36-month rule)", "check": "",
                      "age": 55, "minutes": 30,
                      "suggestions": [{"item": "10914", "name": "Progressive disorder", "confidence": "High",
                                       "basis": "Finding noted", "history_only": False,
                                       "claim_note": "progressive disorder: drusen",
                                       "snippets": [{"section": "Posterior", "text": "Small drusen OU"}]}]})

AGENT_OUT = {"ok": True, "generated_at": "2026-10-13 09:00", "last_report_pull": "2026-10-13 08:55",
             "days_since_pull": 0, "pull_overdue": False,
             "unreported_claims": {"count": 2, "oldest_days": 9},
             "summary": {"do_now": {"n": 1, "amount": 34.0}, "review": {"n": 1, "amount": 67.85}},
             "items": [ITEM, REVIEW],
             "pin_set": True,
             "today": [{"time": "09:00", "patient_label": "Ruby H",
                        "lines": [{"text": "Comprehensive limit used: ask Mark which item applies.",
                                   "sub": "10910 on 15 Aug 2025 (36-month rule)"}]}]}


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


seen_input = []


@pytest.fixture
def calls(monkeypatch):
    seen = []
    seen_input.clear()

    def fake_run(argv, **kw):
        seen.append(argv)
        seen_input.append(kw.get("input"))
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


# --- Mark's item review: visible only to Mark ----------------------------------------
def test_is_mark_case_insensitive():
    assert M.is_mark("Mark") and M.is_mark(" mark ") and M.is_mark("MARK")
    assert not M.is_mark("Karen") and not M.is_mark("Marky") and not M.is_mark("")


def test_karen_sees_review_lines_but_never_snippets(agent, calls):
    """Codex #5: Karen's 'awaiting Mark' tile must lead somewhere - the review lines show
    in her Awaiting Mark group as 'Mark to review the item', with nothing from the notes."""
    out = M.worklist(agent, fixture=True, mark_view=False)
    assert "--review" not in calls[-1]
    assert [i["key"] for i in out["items"]] == ["0123456789ab", "abcdefabcdef"]
    rv = out["items"][1]
    assert rv["review"] == {"kind": "suggest"} and rv["steps"] == "Mark to review the item."
    text = json.dumps(out)
    for leak in ("drusen", "Posterior", "We claimed", "progressive disorder"):
        assert leak not in text
    assert out["summary"]["review"]["n"] == 1 and out["is_mark"] is False


def test_mark_gets_cards_with_snippets(agent, calls):
    out = M.worklist(agent, fixture=True, mark_view=True)
    assert "--review" in calls[-1]
    card = [i for i in out["items"] if i["action_group"] == "review"][0]
    s = card["review"]["suggestions"][0]
    assert s["snippets"][0] == {"section": "Posterior", "text": "Small drusen OU"}
    assert s["basis"] == "Finding noted" and s["claim_note"] == "progressive disorder: drusen"
    assert "confidence" not in s                                   # Codex #4: no confidence words
    assert card["review"]["why"].startswith("We claimed 10910") and card["review"]["kind"] == "suggest"


def test_short_booking_review_offers_10916_and_it_can_be_accepted(agent, calls):
    raw = dict(REVIEW["review"], kind="check_short",
               check="Booked 15 min: a brief visit fits 10916, not 10910/10913/10914",
               suggestions=[{"item": "10916", "name": "Brief initial attendance", "claim_note": "",
                             "snippets": []}])
    r = M.clean_review(raw, True)
    assert r["kind"] == "check_short" and [s["item"] for s in r["suggestions"]] == ["10916"]
    assert M.mark(agent, "0123456789ab", "accepted_10916", "Mark", pin="2468")["ok"]
    assert calls[0][3:7] == ["--mark", "0123456789ab", "--status", "accepted_10916"]


def test_no_evidence_rows_lead_with_not_eligible():
    js = open("static/app.js", encoding="utf-8").read()
    assert 'r.kind === "check"' in js and "Another item…" in js
    assert "Only if the full notes from that day show new symptoms or a progressive condition." in js
    i = js.index('if (r.kind === "check") {')
    block = js[i:js.index("} else {", i)]
    assert block.index('"Not eligible", false') < block.index("Another item")   # primary first
    assert "accept(s, true)" in block                                          # alternatives secondary


def test_review_whitelist_trims_and_drops_odd_items():
    raw = dict(REVIEW["review"], suggestions=[
        {"item": "10918", "name": "x", "snippets": []},
        {"item": "10913", "name": "n", "basis": "Very sure", "claim_note": "x; <script>",
         "snippets": [{"section": "Complaint", "text": "x" * 500}] * 4}])
    r = M.clean_review(raw, True)
    assert [s["item"] for s in r["suggestions"]] == ["10913"]
    assert len(r["suggestions"][0]["snippets"]) == 2 and len(r["suggestions"][0]["snippets"][0]["text"]) == 140
    assert r["suggestions"][0]["basis"] == "" and r["suggestions"][0]["claim_note"] == ""
    assert M.clean_review(raw, False) == {"kind": "suggest"}


def test_today_lines_pass_through_cleaned(agent, calls):
    t = M.worklist(agent, fixture=True)["today"]
    assert t[0]["time"] == "09:00" and t[0]["patient_label"] == "Ruby H"
    assert t[0]["lines"][0] == {"text": "Comprehensive limit used: ask Mark which item applies.",
                                "sub": "10910 on 15 Aug 2025 (36-month rule)"}
    assert M.clean_today([{"time": "9", "lines": ["old plain line"]}])[0]["lines"] == [
        {"text": "old plain line", "sub": ""}]


def test_today_error_passes_through(agent, monkeypatch):
    """Codex #9: a failed bookings read is shown, not silently dropped."""
    body = dict(AGENT_OUT, today=[], today_error="Couldn't read today's bookings.")
    monkeypatch.setattr(M.subprocess, "run", lambda a, **k: FakeProc(json.dumps(body)))
    out = M.worklist(agent)
    assert out["today"] == [] and out["today_error"] == "Couldn't read today's bookings."


def test_pin_set_flag_passes_through(agent, monkeypatch):
    for flag in (True, False):
        body = dict(AGENT_OUT, pin_set=flag)
        monkeypatch.setattr(M.subprocess, "run", lambda a, b=body, **k: FakeProc(json.dumps(b)))
        assert M.worklist(agent, mark_view=True)["pin_set"] is flag


def test_accept_and_not_eligible_are_mark_only_with_a_pin(agent, calls):
    assert M.mark(agent, "0123456789ab", "accepted_10914", "Karen", pin="2468")["error"] == "Only Mark can decide the item."
    assert M.mark(agent, "0123456789ab", "not_eligible", "Angie", pin="2468")["ok"] is False
    assert M.mark(agent, "0123456789ab", "accepted_10918", "Mark", pin="2468")["ok"] is False
    assert M.mark(agent, "0123456789ab", "accepted_10914", "Mark")["error"] == "Enter your 4-digit PIN."
    assert M.mark(agent, "0123456789ab", "accepted_10914", "Mark", pin="12a4")["ok"] is False
    assert M.mark(agent, "0123456789ab", "open", "Karen", pin="2468")["ok"] is False   # a PIN'd undo is Mark's
    assert calls == []
    out = M.mark(agent, "0123456789ab", "accepted_10914", "mark", pin="2468",
                 claim_note="progressive disorder: drusen")
    assert out["ok"]
    argv = calls[0]
    assert argv[3:7] == ["--mark", "0123456789ab", "--status", "accepted_10914"]
    assert argv[argv.index("--claim-note") + 1] == "progressive disorder: drusen"
    assert "--pin-stdin" in argv and "2468" not in argv                 # PIN never on the command line
    assert seen_input[0] == "2468\n"


def test_mark_undo_of_a_decision_passes_the_pin(agent, calls):
    assert M.mark(agent, "0123456789ab", "open", "Mark", pin="2468")["ok"]
    assert "--pin-stdin" in calls[0] and seen_input[0] == "2468\n"


def test_staff_ticks_need_no_pin_and_resubmitted_is_allowed(agent, calls):
    assert M.mark(agent, "0123456789ab", "resubmitted", "Karen")["ok"]
    assert "--pin-stdin" not in calls[0] and seen_input[0] == ""


def test_agent_pin_errors_reach_the_screen(agent, monkeypatch):
    monkeypatch.setattr(M.subprocess, "run", lambda a, **k: FakeProc(
        '{"ok": false, "error": "Wrong PIN. 4 tries left.", "pin": true}'))
    assert M.mark(agent, "0123456789ab", "not_eligible", "Mark", pin="1111")["error"] == "Wrong PIN. 4 tries left."


def test_endpoint_review_needs_mark_cookie(client, calls):
    client.set_cookie("hub_staff", "Karen")
    body = client.get("/api/medicare").get_json()
    assert all(i["review"] in (None, {"kind": "suggest"}) for i in body["items"])
    assert "drusen" not in json.dumps(body)
    client.set_cookie("hub_staff", "Mark")
    r = client.get("/api/medicare")
    assert r.headers["Cache-Control"] == "no-store"
    assert any(i["action_group"] == "review" for i in r.get_json()["items"])
    client.set_cookie("hub_staff", "Karen")
    assert client.post("/api/medicare/mark", json={"key": "0123456789ab", "status": "accepted_10914",
                                                   "pin": "2468"}).status_code == 400


def test_endpoint_passes_pin_on_stdin_and_never_logs_it(client, calls, caplog):
    client.set_cookie("hub_staff", "Mark")
    with caplog.at_level("INFO"):
        r = client.post("/api/medicare/mark", json={"key": "0123456789ab", "status": "not_eligible",
                                                    "pin": "2468"})
    assert r.status_code == 200
    assert seen_input[-1] == "2468\n" and "2468" not in calls[-1]
    assert "2468" not in caplog.text


def test_page_wording_follows_the_codex_review():
    js = open("static/app.js", encoding="utf-8").read()
    for want in ("Pull reports in Optomate first", "awaiting Mark", "other decision", "item review",
                 "Confirm eligibility against the full notes from that day.", "Mark's review",
                 "Mark: set your PIN at the server (one-off)", "Resubmitted",
                 "Today's billing checks unavailable - check in Optomate", "Possible item + evidence",
                 "Check eligibility"):
        assert want in js, want
    for gone in ("Only you see this", "only you see this", "Automatic", "mc-conf-Weak",
                 "You're confirming the notes"):
        assert gone not in js, gone
    css = open("static/style.css", encoding="utf-8").read()
    mc = css[css.index("/* --- Medicare rejections"):]
    assert "border-left" not in mc                                     # no side-bar accent lines
    import re
    sizes = [int(x) for x in re.findall(r"font-size:\s*(\d+)px", mc)]
    assert sizes and min(sizes) >= 14
