"""Tests for hub/checkin.py and the Check-in routes - all mocked.

No network and no real engine: subprocess.run is replaced by a fake engine that
answers like `python -m checkin.cli`. Everything lands in tmp_path. Names below
are ZZTEST fakes.
"""

import base64
import json
import logging
import re
import struct
import zlib
from datetime import timedelta

import pytest

from hub import checkin

KEY = "zztest-device-key-0123456789"
H1 = "a" * 64
H2 = "b" * 64


def _png():
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b""))


SIG = "data:image/png;base64," + base64.b64encode(_png()).decode()

QUESTIONS = {"version": "test-v1", "screens": [
    {"id": "details", "title": "Your details", "skippable": True, "prefilled": True, "questions": [
        {"id": "given_name", "label": "Given name", "type": "text", "field": "given_name"}]},
    {"id": "consent", "title": "Consent", "skippable": False, "questions": [
        {"id": "guardian_name", "label": "Parent or guardian's name", "type": "text", "required": True,
         "audience": "child"},
        {"id": "signature", "label": "Sign", "type": "signature", "required": True}]},
]}


class FakeEngine:
    """Stands in for `python -m checkin.cli`. Records argv and env."""

    def __init__(self):
        self.calls = []
        self.env = None
        self.save_result = None
        self.pdf_path = None
        self.duplicates = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        self.env = kw.get("env")
        args = argv[3:]                    # [python, -m, checkin.cli, cmd, ...]
        cmd = args[0]
        out = {"dry_run": True, "fixture": True}
        if cmd == "today":
            out.update(date=checkin._now().date().isoformat(), appointments=[
                {"appointment_id": 1001, "time": "9:00 am", "patient_id": 99001, "given": "Adultone",
                 "surname": "ZZTEST", "is_child": False, "type": "CU", "optometrist_id": 5,
                 "has_exam_today": False, "session_state": None},
                {"appointment_id": 1002, "time": "9:30 am", "patient_id": 99002, "given": "Childone",
                 "surname": "ZZTEST", "is_child": True, "type": "MC", "optometrist_id": 5,
                 "has_exam_today": False, "session_state": None}])
        elif cmd == "patient":
            pid = int(args[2])
            out.update(patient_id=pid, is_child=pid == 99002,
                       details={"given_name": "Childone" if pid == 99002 else "Adultone",
                                "family_name": "ZZTEST", "preferred_name": "",
                                "dob": "01/06/2017" if pid == 99002 else "15/03/1980"})
        elif cmd == "questions":
            aud = args[2] if len(args) > 2 else "adult"
            qs = json.loads(json.dumps(QUESTIONS))
            for s in qs["screens"]:
                s["questions"] = [q for q in s["questions"] if q.get("audience", "both") in ("both", aud)]
            out.update(version="test-v1", questions=qs)
        elif cmd == "lists":
            out.update(sources=["Google"], occupations=["TEACHER"],
                       optometrists=[{"id": 5, "name": "ZZTEST Optom", "code": "Z5"}])
        elif cmd == "search":
            out.update(patients=[{"id": 99001, "given": "Adultone", "surname": "ZZTEST",
                                  "dob": "15/03/1980", "suburb": "CONCORD"}], more=True)
        elif cmd == "plan":
            out.update(hash=H1, can_save=True, blockers=[], warnings=[], patient_changes=[],
                       unchanged=[], duplicates=self.duplicates, is_new_patient=False,
                       exam={"action": "none", "notes": {}}, notes_append="", writes={})
        elif cmd == "save":
            h = args[args.index("--expect-hash") + 1]
            if h != H1:
                out = {"error": "The record changed - check it again", "dry_run": True}
            else:
                out = self.save_result or {"dry_run": True, "would_save": {"hash": H1}, "pdf": "x.pdf"}
        elif cmd == "pdf":
            out.update(pdf=str(self.pdf_path))

        class P:
            stdout = "engine warning line\n" + json.dumps(out) + "\n"
            returncode = 0
        return P()


@pytest.fixture
def world(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    (agent / "checkin").mkdir(parents=True)
    cfg = {"optomate_agent": {"agent_dir": str(agent), "python": "py"},
           "checkin": {"ipad_key": KEY}}
    eng = FakeEngine()
    monkeypatch.setattr(checkin.subprocess, "run", eng)
    checkin._lists_cache.update(at=0.0, dir=None, data=None)
    return {"cfg": cfg, "agent": agent, "eng": eng,
            "sessions": agent / "local-reports" / "checkin" / "sessions"}


@pytest.fixture
def client(world, tmp_path, monkeypatch):
    import importlib
    cfg_path = tmp_path / "integrations.json"
    cfg_path.write_text(json.dumps(world["cfg"]), encoding="utf-8")
    monkeypatch.setenv("CEC_HUB_INTEGRATIONS", str(cfg_path))
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        c.world = world
        c.cfg_path = cfg_path
        yield c


def _send(world, **body):
    return checkin.send(world["cfg"], {"kind": "appointment", "appointment_id": 1001, **body})


def _submit(world, token, **over):
    body = {"answers": {"given_name": "Adultone"}, "skipped_screens": [], "signature_png": SIG}
    body.update(over)
    return checkin.ipad_submit(world["cfg"], token, body)


# --- wrapper -------------------------------------------------------------------

def test_not_connected_without_the_checkin_package(tmp_path):
    cfg = {"optomate_agent": {"agent_dir": str(tmp_path)}}
    assert checkin.today(cfg)["connected"] is False
    assert checkin.run_engine(cfg, "today")["connected"] is False
    assert checkin.today({})["connected"] is False


def test_last_json_line_wins_and_garbage_is_handled(world, monkeypatch):
    assert checkin.run_engine(world["cfg"], "lists")["optometrists"][0]["id"] == 5

    class P:
        stdout = "not json"
    monkeypatch.setattr(checkin.subprocess, "run", lambda *a, **k: P())
    assert "Couldn't read" in checkin.run_engine(world["cfg"], "lists")["error"]


def test_timeout_is_plain_words(world, monkeypatch):
    def boom(*a, **k):
        raise checkin.subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(checkin.subprocess, "run", boom)
    assert "too long" in checkin.run_engine(world["cfg"], "today")["error"]
    assert "Check the patient in Optomate" in checkin.run_engine(world["cfg"], "save")["error"]


def test_engine_env_passes_fixture_and_strips_the_gate(world, monkeypatch):
    monkeypatch.setenv("CHECKIN_FIXTURE", "1")
    monkeypatch.setenv("CHECKIN_DRY_RUN", "false")
    monkeypatch.setenv("CHECKIN_LIVE_WRITES", "true")
    monkeypatch.setenv("CHECKIN_DATA_DIR", "C:\\elsewhere")
    checkin.run_engine(world["cfg"], "today")
    env = world["eng"].env
    assert env["CHECKIN_FIXTURE"] == "1"
    for k in ("CHECKIN_DRY_RUN", "CHECKIN_LIVE_WRITES", "CHECKIN_DATA_DIR"):
        assert k not in env


def test_search_is_sanitised_and_says_more(world):
    out = checkin.search(world["cfg"], "O'Brien) John;")
    argv = world["eng"].calls[-1]
    assert argv[-2:] == ["--q", "O'Brien John"]
    assert out["more"] is True
    assert "two letters" in checkin.search(world["cfg"], " ) ")["error"]


@pytest.mark.parametrize("bad", ["", "../../etc", "A" * 32, "a" * 31, "a" * 33, "g" * 32,
                                 "a" * 31 + "/", "..\\" + "a" * 29])
def test_token_format_and_path_confinement(world, bad):
    assert checkin.session_path(world["cfg"], bad) is None
    assert checkin.load_session(world["cfg"], bad) is None
    assert checkin.detail(world["cfg"], bad)[0] == 404


def test_session_path_stays_in_the_sessions_folder(world):
    p = checkin.session_path(world["cfg"], "a" * 32)
    assert p.parent == world["sessions"].resolve()


# --- sending + the one iPad slot -------------------------------------------------

def test_send_appointment_makes_a_sent_session_with_prefill(world):
    st, out = _send(world)
    assert st == 200 and re.fullmatch(r"[0-9a-f]{32}", out["token"])
    s = checkin.load_session(world["cfg"], out["token"])
    assert s["state"] == "sent" and s["patient_id"] == 99001 and s["appointment_id"] == 1001
    assert s["optometrist_id"] == 5 and s["audience"] == "adult"
    assert s["prefill"]["given_name"] == "Adultone"
    assert checkin.ipad_current(world["cfg"]) == {"token": out["token"], "state": "sent"}


def test_child_appointment_defaults_to_child_form_staff_can_override(world):
    _st, out = checkin.send(world["cfg"], {"kind": "appointment", "appointment_id": 1002})
    assert checkin.load_session(world["cfg"], out["token"])["audience"] == "child"
    _st, out = checkin.send(world["cfg"], {"kind": "appointment", "appointment_id": 1002,
                                           "audience": "adult", "replace": True})
    assert checkin.load_session(world["cfg"], out["token"])["audience"] == "adult"


def test_new_patient_needs_adult_or_child(world):
    assert checkin.send(world["cfg"], {"kind": "new"})[0] == 400
    st, out = checkin.send(world["cfg"], {"kind": "new", "audience": "child"})
    s = checkin.load_session(world["cfg"], out["token"])
    assert st == 200 and s["patient_id"] is None and s["prefill"] == {}


@pytest.mark.parametrize("body", [{"kind": "nope"}, {"kind": "appointment", "appointment_id": "x"},
                                  {"kind": "patient", "patient_id": -3},
                                  {"kind": "new", "audience": "elder"}])
def test_bad_send_requests_are_refused(world, body):
    assert checkin.send(world["cfg"], body)[0] == 400


def test_replace_while_filling_asks_first_then_discards(world):
    _st, first = _send(world)
    checkin.ipad_filling(world["cfg"], first["token"])
    st, busy = checkin.send(world["cfg"], {"kind": "new", "audience": "adult"})
    assert st == 409 and busy["busy"] and busy["state"] == "filling" and busy["name"] == "Adultone ZZTEST"
    assert checkin.load_session(world["cfg"], first["token"])["state"] == "filling"   # untouched
    st, second = checkin.send(world["cfg"], {"kind": "new", "audience": "adult", "replace": True})
    assert st == 200
    assert checkin.load_session(world["cfg"], first["token"]) is None                # gone
    assert checkin.ipad_current(world["cfg"])["token"] == second["token"]
    # the old iPad form can no longer submit
    assert _submit(world, first["token"])[0] == 409


def test_state_transitions_sent_filling_submitted(world):
    _st, out = _send(world)
    tok = out["token"]
    assert checkin.ipad_session(world["cfg"], tok)[1]["questions"]["version"] == "test-v1"
    assert checkin.ipad_filling(world["cfg"], tok)[0] == 200
    assert checkin.load_session(world["cfg"], tok)["state"] == "filling"
    assert _submit(world, tok)[0] == 200
    s = checkin.load_session(world["cfg"], tok)
    assert s["state"] == "submitted" and s["submitted"] and s["questions_version"] == "test-v1"
    assert checkin.ipad_current(world["cfg"]) == {"state": "idle"}
    # a submitted form is not the iPad's any more
    assert checkin.ipad_filling(world["cfg"], tok)[0] == 409
    assert _submit(world, tok)[0] == 409


def test_submit_stores_answers_signature_and_skips(world):
    _st, out = _send(world)
    st, _ = _submit(world, out["token"], answers={"given_name": "Adultone", "medical": ["Asthma"]},
                    skipped_screens=["details"])
    s = checkin.load_session(world["cfg"], out["token"])
    assert st == 200 and s["answers"] == {"given_name": "Adultone", "medical": ["Asthma"]}
    assert s["signature_png"] == SIG and s["skipped_screens"] == ["details"]


@pytest.mark.parametrize("over", [{"signature_png": ""}, {"signature_png": "data:image/png;base64,AAAA"},
                                  {"signature_png": "data:image/png;base64," + base64.b64encode(b"x" * 80).decode()},
                                  {"answers": {"Bad Key": "x"}}, {"answers": {"a": {"nested": 1}}},
                                  {"answers": "nope"}, {"skipped_screens": ["../x"]}])
def test_submit_rejects_bad_input(world, over):
    _st, out = _send(world)
    assert _submit(world, out["token"], **over)[0] == 400
    assert checkin.load_session(world["cfg"], out["token"])["state"] == "sent"


def test_required_guardian_for_a_child(world):
    _st, out = checkin.send(world["cfg"], {"kind": "appointment", "appointment_id": 1002})
    st, err = _submit(world, out["token"])
    assert st == 400 and err["field"] == "guardian_name"
    assert _submit(world, out["token"], answers={"guardian_name": "ZZTEST Parent"})[0] == 200


# --- check screen, overrides, save ---------------------------------------------

def _submitted(world):
    _st, out = _send(world)
    _submit(world, out["token"])
    return out["token"]


def test_detail_runs_the_plan_with_the_bare_session_name(world):
    tok = _submitted(world)
    st, d = checkin.detail(world["cfg"], tok)
    assert st == 200 and d["plan"]["hash"] == H1 and d["optometrists"][0]["id"] == 5
    plan_call = next(c for c in world["eng"].calls if c[3] == "plan")
    assert plan_call[4:] == ["--session", f"{tok}.json"]


def test_overrides_are_validated_and_stored(world):
    tok = _submitted(world)
    cfg = world["cfg"]
    assert checkin.override(cfg, tok, {"action": "reject", "field": "email"})[0] == 200
    assert checkin.override(cfg, tok, {"action": "edit", "field": "suburb", "value": " Concord "})[0] == 200
    assert checkin.override(cfg, tok, {"action": "optometrist", "optometrist_id": 5})[0] == 200
    ov = checkin.load_session(cfg, tok)["staff_overrides"]
    assert ov["rejected_fields"] == ["email"] and ov["edited"] == {"suburb": "Concord"}
    assert checkin.override(cfg, tok, {"action": "accept", "field": "email"})[0] == 200
    assert checkin.override(cfg, tok, {"action": "edit", "field": "suburb", "value": ""})[0] == 200
    ov = checkin.load_session(cfg, tok)["staff_overrides"]
    assert ov["rejected_fields"] == [] and ov["edited"] == {}
    assert checkin.override(cfg, tok, {"action": "drop", "field": "email"})[0] == 400
    assert checkin.override(cfg, tok, {"action": "reject", "field": "../x"})[0] == 400
    assert checkin.override(cfg, tok, {"action": "edit", "field": "email", "value": "x" * 500})[0] == 400
    assert checkin.override(cfg, tok, {"action": "optometrist", "optometrist_id": 77})[0] == 400


def test_use_record_only_one_of_the_duplicates(world):
    tok = _submitted(world)
    world["eng"].duplicates = [{"id": 99001}]
    assert checkin.override(world["cfg"], tok, {"action": "use_record", "patient_id": 12345})[0] == 400
    assert checkin.override(world["cfg"], tok, {"action": "use_record", "patient_id": 99001})[0] == 200
    assert checkin.override(world["cfg"], tok, {"action": "create_new"})[0] == 200
    assert checkin.load_session(world["cfg"], tok)["staff_overrides"]["confirm_new_patient"] is True


def test_save_passes_the_hash_and_staff(world):
    tok = _submitted(world)
    st, out = checkin.save(world["cfg"], tok, H1, "ZZTEST Staff")
    argv = world["eng"].calls[-1]
    assert argv[3:] == ["save", "--session", f"{tok}.json", "--expect-hash", H1, "--staff", "ZZTEST Staff"]
    assert st == 200 and out["test_saved"] is True
    s = checkin.load_session(world["cfg"], tok)                  # dry run: kept for re-checking
    assert s["state"] == "submitted" and s["hub_test_saved"]


def test_save_refuses_a_bad_hash_without_running(world):
    tok = _submitted(world)
    n = len(world["eng"].calls)
    assert checkin.save(world["cfg"], tok, "nothex", "x")[0] == 400
    assert len(world["eng"].calls) == n


def test_changed_record_is_a_409_with_plain_words(world):
    tok = _submitted(world)
    st, out = checkin.save(world["cfg"], tok, H2, "x")
    assert st == 409 and out["changed"] and out["error"] == "The record changed - check it again"


def test_live_saved_deletes_the_session_and_marks_saved(world):
    tok = _submitted(world)
    world["eng"].save_result = {"dry_run": False, "saved": True, "patient_id": 99001, "warnings": []}
    st, out = checkin.save(world["cfg"], tok, H1, "x")
    assert st == 200 and out["saved"] is True
    assert checkin.session_path(world["cfg"], tok).exists() is False      # answers + signature gone
    row = next(a for a in checkin.today(world["cfg"])["appointments"] if a["appointment_id"] == 1001)
    assert row["status"] == "Saved" and row["token"] is None
    marker = (world["agent"] / "local-reports" / "checkin" / "hub-saved.json").read_text()
    assert "Adultone" not in marker and "ZZTEST" not in marker


def test_live_failure_keeps_the_session_and_takes_the_new_patient_id(world):
    st, out = checkin.send(world["cfg"], {"kind": "new", "audience": "adult"})
    tok = out["token"]
    _submit(world, tok)
    world["eng"].save_result = {"dry_run": False, "saved": False, "patient_id": 123456,
                                "error": "Optomate refused the save"}
    st, res = checkin.save(world["cfg"], tok, H1, "x")
    s = checkin.load_session(world["cfg"], tok)
    assert st == 200 and res["error"] and s["state"] == "submitted" and s["patient_id"] == 123456


def test_discard_deletes_the_file(world):
    tok = _submitted(world)
    assert checkin.discard(world["cfg"], tok)[0] == 200
    assert checkin.load_session(world["cfg"], tok) is None
    assert checkin.discard(world["cfg"], tok)[0] == 404


def test_purge_keeps_forms_waiting_to_be_checked(world):
    cfg = world["cfg"]
    old = checkin._iso(checkin._now() - timedelta(hours=30))
    world["sessions"].mkdir(parents=True, exist_ok=True)
    for tok, state in (("1" * 32, "sent"), ("2" * 32, "filling"), ("3" * 32, "submitted")):
        (world["sessions"] / f"{tok}.json").write_text(json.dumps(
            {"token": tok, "state": state, "created": old, "audience": "adult"}), encoding="utf-8")
    assert checkin.purge(cfg) == 2
    assert [p.stem for p in world["sessions"].glob("*.json")] == ["3" * 32]


def test_today_shows_plain_status_words_and_waiting(world):
    _st, out = checkin.send(world["cfg"], {"kind": "new", "audience": "adult"})
    _submit(world, out["token"])
    _send(world)
    t = checkin.today(world["cfg"])
    row = next(a for a in t["appointments"] if a["appointment_id"] == 1001)
    assert row["status"] == "On the iPad" and t["ipad"]["name"] == "Adultone ZZTEST"
    assert [w["token"] for w in t["waiting"]] == [out["token"]]
    assert t["dry_run"] is True and t["ipad_ready"] is True


def test_pdf_path_confinement(world, tmp_path):
    tok = _submitted(world)
    outside = tmp_path / "elsewhere.pdf"
    outside.write_bytes(b"%PDF-1.4")
    world["eng"].pdf_path = outside
    assert checkin.pdf_file(world["cfg"], tok)[0] is None
    inside = world["agent"] / "local-reports" / "checkin" / "dry-run" / "f.pdf"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"%PDF-1.4")
    world["eng"].pdf_path = inside
    assert checkin.pdf_file(world["cfg"], tok)[0] == inside.resolve()
    not_pdf = inside.with_suffix(".json")
    not_pdf.write_text("{}")
    assert checkin.confine_pdf(world["cfg"], str(not_pdf)) is None


def test_device_key_rules(world):
    assert checkin.key_ok(world["cfg"], KEY)
    assert not checkin.key_ok(world["cfg"], KEY + "x")
    assert not checkin.key_ok(world["cfg"], "")
    for k in ("", "short", "CHANGE-ME-to-a-long-random-string"):
        assert checkin.ipad_key({"checkin": {"ipad_key": k}}) == ""


def test_nothing_patient_identifying_is_logged(world, caplog):
    caplog.set_level(logging.DEBUG)
    _st, out = _send(world)
    tok = out["token"]
    checkin.ipad_session(world["cfg"], tok)
    checkin.ipad_filling(world["cfg"], tok)
    _submit(world, tok, answers={"given_name": "Adultone", "medications": "ZZTEST tablet"})
    checkin.override(world["cfg"], tok, {"action": "edit", "field": "email", "value": "zz@example.invalid"})
    checkin.save(world["cfg"], tok, H1, "ZZTEST Staff")
    checkin.discard(world["cfg"], tok)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "99001" in text                                         # the ID is allowed
    for leak in ("Adultone", "ZZTEST", "tablet", "example.invalid", "15/03/1980", tok):
        assert leak not in text


# --- routes -----------------------------------------------------------------------

def test_ipad_routes_need_the_key_in_a_header(client):
    assert client.get("/api/checkin/ipad/current").status_code == 401
    assert client.get(f"/api/checkin/ipad/current?key={KEY}").status_code == 401   # not the query
    r = client.get("/api/checkin/ipad/current", headers={"X-Checkin-Key": KEY})
    assert r.status_code == 200 and r.get_json() == {"state": "idle"}
    assert r.headers["Cache-Control"] == "no-store"
    tok = "c" * 32
    for method, path in (("get", f"/api/checkin/ipad/session/{tok}"),
                         ("post", f"/api/checkin/ipad/session/{tok}/filling"),
                         ("post", f"/api/checkin/ipad/session/{tok}/submit")):
        assert getattr(client, method)(path).status_code == 401


def test_ipad_routes_say_not_set_up_without_a_key(client):
    cfg = json.loads(client.cfg_path.read_text())
    cfg["checkin"]["ipad_key"] = "CHANGE-ME-to-a-long-random-string"
    client.cfg_path.write_text(json.dumps(cfg))
    r = client.get("/api/checkin/ipad/current", headers={"X-Checkin-Key": "CHANGE-ME-to-a-long-random-string"})
    assert r.status_code == 503 and r.get_json()["setup"] is False


def test_ipad_page_is_no_store_and_has_no_way_into_the_hub(client):
    r = client.get("/checkin/ipad")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert "href=\"#" not in html and "<a " not in html
    srcs = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert srcs and all(s.startswith("/checkin/ipad/") for s in srcs)
    for name in ("ipad.js", "ipad.css"):
        r = client.get(f"/checkin/ipad/{name}")
        assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    js = client.get("/checkin/ipad/ipad.js").get_data(as_text=True)
    for used in ("localStorage.", "sessionStorage.", "indexedDB", "document.cookie", "setItem("):
        assert used not in js
    assert "/api/checkin/ipad" in js
    assert re.search(r'fetch\("/api/(?!checkin/ipad)', js) is None
    assert client.get("/checkin/ipad/../app.py").status_code == 404
    assert client.get("/checkin/ipad/index.html").status_code == 404


def test_full_flow_over_http(client):
    w = client.world
    r = client.post("/api/checkin/send", json={"kind": "appointment", "appointment_id": 1001})
    tok = r.get_json()["token"]
    hk = {"X-Checkin-Key": KEY}
    assert client.get("/api/checkin/ipad/current", headers=hk).get_json()["token"] == tok
    form = client.get(f"/api/checkin/ipad/session/{tok}", headers=hk).get_json()
    assert form["name"] == "Adultone" and form["questions"]["screens"]
    assert client.post(f"/api/checkin/ipad/session/{tok}/filling", headers=hk, json={}).status_code == 200
    r = client.post(f"/api/checkin/ipad/session/{tok}/submit", headers=hk,
                    json={"answers": {"given_name": "Adultone"}, "skipped_screens": [], "signature_png": SIG})
    assert r.status_code == 200
    d = client.get(f"/api/checkin/session/{tok}").get_json()
    assert d["plan"]["hash"] == H1
    r = client.post(f"/api/checkin/session/{tok}/save", json={"hash": d["plan"]["hash"]})
    assert r.status_code == 200 and r.get_json()["dry_run"] is True
    assert client.post(f"/api/checkin/session/{tok}/save", json={"hash": H2}).status_code == 409
    assert client.get("/checkin/pdf/" + "0" * 32).status_code == 404
    r = client.post("/api/checkin/search", json={"q": "ZZTEST Adultone"})
    assert r.status_code == 200 and r.get_json()["more"] is True
    assert client.post("/api/checkin/search", json={"q": ""}).status_code == 400
    assert client.post(f"/api/checkin/session/{tok}/discard", json={}).status_code == 200
    assert not list(w["sessions"].glob("*.json"))
