"""Tests for hub/checkin.py and the Check-in routes - all mocked.

No network and no real engine: subprocess.run is replaced by a fake engine that
answers like `python -m checkin.cli`. Everything lands in tmp_path. Names below
are ZZTEST fakes.
"""

import base64
import json
import logging
import os
import re
import struct
import zlib
from datetime import timedelta

import pytest

from hub import checkin

KEY = "zztest-device-key-0123456789"
STAFF_CODE = "zztest-staff-code"
LAN = {"REMOTE_ADDR": "192.168.1.50"}          # a front-desk PC, not the server
H1 = "a" * 64
H2 = "b" * 64


def _png():
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b""))


# An old iPad page (before 9 Oct 2026) might still send a signature: it is ignored.
OLD_SIG = "data:image/png;base64," + base64.b64encode(_png()).decode()

QUESTIONS = {"version": "test-v1", "screens": [
    {"id": "details", "title": "Your details", "skippable": True, "prefilled": True, "questions": [
        {"id": "given_name", "label": "Given name", "type": "text", "field": "given_name"},
        {"id": "occupation", "label": "Occupation", "type": "picklist", "list": "occupations",
         "field": "occupation", "audience": "adult", "new_patient_only": True}]},
    {"id": "consent", "title": "Consent", "skippable": False, "questions": [
        {"id": "guardian_name", "label": "Parent or guardian's name", "type": "text", "required": True,
         "audience": "child"},
        {"id": "consent_statement", "type": "info", "text": "I confirm these details are correct."}]},
]}


class FakeEngine:
    """Stands in for `python -m checkin.cli`. Records argv and env."""

    def __init__(self):
        self.calls = []
        self.env = None
        self.save_result = None
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
                 "has_exam_today": False, "session_state": None,
                 "booking_reason": "Eye Exam - Existing Patient"},
                {"appointment_id": 1002, "time": "9:30 am", "patient_id": 99002, "given": "Childone",
                 "surname": "ZZTEST", "is_child": True, "type": "MC", "optometrist_id": 5,
                 "has_exam_today": False, "session_state": None, "booking_reason": ""}])
        elif cmd == "patient":
            pid = int(args[2])
            out.update(patient_id=pid, is_child=pid == 99002,
                       last_exam="2024-10-02" if pid == 99001 else "",
                       details={"given_name": "Childone" if pid == 99002 else "Adultone",
                                "family_name": "ZZTEST", "preferred_name": "",
                                "dob": "01/06/2017" if pid == 99002 else "15/03/1980"})
        elif cmd == "questions":
            aud = args[2] if len(args) > 2 else "adult"
            returning = "--returning" in args
            qs = json.loads(json.dumps(QUESTIONS))
            for s in qs["screens"]:
                s["questions"] = [q for q in s["questions"] if q.get("audience", "both") in ("both", aud)
                                  and not (returning and q.get("new_patient_only"))]
            out.update(version="test-v1", questions=qs)
        elif cmd == "lists":
            out.update(sources=["Google"], occupations=["TEACHER"],
                       optometrists=[{"id": 5, "name": "ZZTEST Optom", "code": "Z5"}])
        elif cmd == "search":
            out.update(patients=[{"id": 99001, "given": "Adultone", "surname": "ZZTEST",
                                  "dob": "15/03/1980", "is_child": False, "suburb": "CONCORD"},
                                 {"id": 99002, "given": "Childone", "surname": "ZZTEST",
                                  "dob": "01/06/2017", "is_child": True, "suburb": "CONCORD"}],
                       more=True)
        elif cmd == "plan":
            out.update(hash=H1, can_save=True, blockers=[], warnings=[], patient_changes=[],
                       unchanged=[], duplicates=self.duplicates, is_new_patient=False,
                       exam={"action": "none", "notes": {}}, notes_append="", writes={})
        elif cmd == "save":
            h = args[args.index("--expect-hash") + 1]
            if h != H1:
                out = {"error": "The record changed - check it again", "dry_run": True}
            else:
                out = self.save_result or {"dry_run": True, "would_save": {"hash": H1}}

        class P:
            stdout = "engine warning line\n" + json.dumps(out) + "\n"
            returncode = 0
        return P()


@pytest.fixture
def world(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    (agent / "checkin").mkdir(parents=True)
    cfg = {"optomate_agent": {"agent_dir": str(agent), "python": "py"},
           "checkin": {"ipad_key": KEY, "staff_code": STAFF_CODE}}
    checkin._unlock_fails.clear()
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
    body = {"answers": {"given_name": "Adultone"}, "skipped_screens": []}
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


def test_booking_reason_goes_to_the_session_prefill_not_the_staff_list(world):
    _st, out = _send(world)
    s = checkin.load_session(world["cfg"], out["token"])
    assert s["prefill"]["booking_reason"] == "Eye Exam - Existing Patient"
    assert checkin.ipad_session(world["cfg"], out["token"])[1]["prefill"]["booking_reason"] == \
        "Eye Exam - Existing Patient"
    rows = checkin.today(world["cfg"])["appointments"]
    assert all("booking_reason" not in r for r in rows)
    # no booking comment -> no key; a patient picked by search has no appointment
    _st, out = checkin.send(world["cfg"], {"kind": "appointment", "appointment_id": 1002, "replace": True})
    assert "booking_reason" not in checkin.load_session(world["cfg"], out["token"])["prefill"]
    _st, out = checkin.send(world["cfg"], {"kind": "patient", "patient_id": 99001, "replace": True})
    assert "booking_reason" not in checkin.load_session(world["cfg"], out["token"])["prefill"]


def test_returning_patient_gets_the_returning_set(world):
    """v5: returning = a previous exam here (engine `patient` last_exam), not
    merely a record on file."""
    def ids(form):
        return {q["id"] for sc in form["questions"]["screens"] for q in sc["questions"]}
    _st, out = _send(world)                                          # 99001, examined before
    s = checkin.load_session(world["cfg"], out["token"])
    assert s["returning"] is True and s["last_exam"] == "2024-10-02"
    form = checkin.ipad_session(world["cfg"], out["token"])[1]
    assert "occupation" not in ids(form) and "given_name" in ids(form)
    q_call = [c for c in world["eng"].calls if c[3] == "questions"][-1]
    assert q_call[4:] == ["--audience", "adult", "--returning"]
    # on file but never examined (99002): the new-patient set
    _st, out = checkin.send(world["cfg"], {"kind": "patient", "patient_id": 99002, "replace": True})
    assert checkin.load_session(world["cfg"], out["token"])["returning"] is False
    checkin.ipad_session(world["cfg"], out["token"])
    assert [c for c in world["eng"].calls if c[3] == "questions"][-1][4:] == ["--audience", "child"]
    _st, out = checkin.send(world["cfg"], {"kind": "new", "audience": "adult", "replace": True})
    form = checkin.ipad_session(world["cfg"], out["token"])[1]
    assert "occupation" in ids(form)
    assert [c for c in world["eng"].calls if c[3] == "questions"][-1][4:] == ["--audience", "adult"]


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
    # ...but sending it again is "received", not "gone" (Codex #11)
    assert _submit(world, tok) == (200, {"ok": True, "already": True})


def test_submit_stores_answers_and_skips_and_no_signature(world):
    """Mark, 9 Oct 2026: no signature. A form sends without one; a signature an old
    iPad page still sends is not kept."""
    _st, out = _send(world)
    assert "signature_png" not in checkin.load_session(world["cfg"], out["token"])
    st, _ = _submit(world, out["token"], answers={"given_name": "Adultone", "medical": ["Asthma"]},
                    skipped_screens=["details"], signature_png=OLD_SIG)
    s = checkin.load_session(world["cfg"], out["token"])
    assert st == 200 and s["answers"] == {"given_name": "Adultone", "medical": ["Asthma"]}
    assert "signature_png" not in s and s["skipped_screens"] == ["details"]
    assert OLD_SIG not in checkin.session_path(world["cfg"], out["token"]).read_text()


@pytest.mark.parametrize("over", [{"answers": {"Bad Key": "x"}}, {"answers": {"a": {"nested": 1}}},
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
    assert checkin.session_path(world["cfg"], tok).exists() is False      # answers gone
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


def test_purge_unsubmitted_after_two_days_keeps_forms_waiting_to_be_checked(world):
    cfg = world["cfg"]
    old = checkin._iso(checkin._now() - timedelta(days=2, hours=1))
    recent = checkin._iso(checkin._now() - timedelta(hours=30))
    world["sessions"].mkdir(parents=True, exist_ok=True)
    for tok, state, made in (("1" * 32, "sent", old), ("2" * 32, "filling", old),
                             ("4" * 32, "paused", old), ("3" * 32, "submitted", old),
                             ("5" * 32, "filling", recent)):
        (world["sessions"] / f"{tok}.json").write_text(json.dumps(
            {"token": tok, "state": state, "created": made, "audience": "adult"}), encoding="utf-8")
    assert checkin.purge(cfg) == 3
    assert sorted(p.stem for p in world["sessions"].glob("*.json")) == ["3" * 32, "5" * 32]


def test_today_shows_plain_status_words_and_waiting(world):
    _st, out = checkin.send(world["cfg"], {"kind": "new", "audience": "adult"})
    _submit(world, out["token"])
    _send(world)
    t = checkin.today(world["cfg"])
    row = next(a for a in t["appointments"] if a["appointment_id"] == 1001)
    assert row["status"] == "On the iPad" and t["ipad"]["name"] == "Adultone ZZTEST"
    assert [w["token"] for w in t["waiting"]] == [out["token"]]
    assert t["dry_run"] is True and t["ipad_ready"] is True


def test_no_signed_pdf_any_more(client):
    """No signed PDF (Mark, 9 Oct 2026): the route and the engine call are gone."""
    tok = _submitted(client.world)
    assert client.get("/checkin/pdf/" + tok).status_code == 404
    assert not hasattr(checkin, "pdf_file")
    assert all(c[3] != "pdf" for c in client.world["eng"].calls)
    js = open(os.path.join(os.path.dirname(__file__), "..", "static", "app.js"), encoding="utf-8").read()
    assert "/checkin/pdf/" not in js and "Signed form" not in js
    ipad = open(os.path.join(os.path.dirname(__file__), "..", "ipad", "ipad.js"), encoding="utf-8").read()
    for gone in ("canvas", "signature_png", "clear-sig", "Sign with your finger"):
        assert gone not in ipad, gone
    assert '"Send"' in ipad and " send" in ipad

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
    # Codex #9: pinch-zoom allowed
    assert "user-scalable=no" not in html and "maximum-scale" not in html
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


def test_ipad_v2_layout_contract(client):
    """v2 (Mark, 5 Oct 2026): bands with equal tiles in 4 columns (2 when narrow),
    None tile clears the others, follow-ups inline, the details skip button, and
    no side-bar boxes anywhere on the iPad."""
    js = client.get("/checkin/ipad/ipad.js").get_data(as_text=True)
    css = client.get("/checkin/ipad/ipad.css").get_data(as_text=True)
    for used in ("none_value", "follow_up", "skip_button", 'class="reveal"', "revealNew",
                 "Tap all that apply", "Tap one", 'data-act="skip"'):
        assert used in js, used
    assert "repeat(4, minmax(0, 1fr))" in css and "repeat(2, minmax(0, 1fr))" in css
    assert "grid-template-columns: 250px minmax(0, 1fr)" in css
    assert ".t.none { border-style: dashed; }" in css
    assert "border-left" not in css
    for size in re.findall(r"font-size:\s*(\d+)px", css):
        assert int(size) >= 14, size


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
                    json={"answers": {"given_name": "Adultone"}, "skipped_screens": []})
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


# --- Codex review, 9 Oct 2026 ------------------------------------------------------

STAFF_ROUTES = [("get", "/api/checkin/today", None), ("post", "/api/checkin/search", {"q": "ZZTEST"}),
                ("post", "/api/checkin/send", {"kind": "new", "audience": "adult"}),
                ("get", "/api/checkin/session/" + "c" * 32, None),
                ("post", "/api/checkin/session/" + "c" * 32 + "/override", {"action": "accept", "field": "email"}),
                ("post", "/api/checkin/session/" + "c" * 32 + "/save", {"hash": H1}),
                ("post", "/api/checkin/session/" + "c" * 32 + "/discard", {}),
                ("post", "/api/checkin/session/" + "c" * 32 + "/resume", {})]


def _call(client, method, path, body, **kw):
    if method == "get":
        return client.get(path, **kw)
    return client.post(path, json=body, **kw)


@pytest.mark.parametrize("method,path,body", STAFF_ROUTES)
def test_staff_routes_refuse_the_ipad_key_even_on_the_server(client, method, path, body):
    """Codex #1: the device key opens the iPad routes ONLY. A request carrying it
    is refused on every staff route - even from the server itself, even with a
    valid staff cookie."""
    client.set_cookie(checkin.STAFF_COOKIE, checkin.staff_cookie_value(client.world["cfg"]))
    r = _call(client, method, path, body, headers={"X-Checkin-Key": KEY})
    assert r.status_code == 403 and r.get_json()["ipad"] is True
    assert r.headers["Cache-Control"] == "no-store"
    assert "ZZTEST" not in r.get_data(as_text=True)


@pytest.mark.parametrize("method,path,body", STAFF_ROUTES)
def test_staff_routes_need_the_staff_unlock_off_the_server(client, method, path, body):
    r = _call(client, method, path, body, environ_base=LAN)
    assert r.status_code == 403 and r.get_json()["staff_needed"] is True
    assert "ZZTEST" not in r.get_data(as_text=True)


def test_unlock_with_the_staff_code_then_staff_routes_work(client):
    assert client.get("/api/checkin/today", environ_base=LAN).status_code == 403
    r = client.post("/api/checkin/staff/unlock", json={"code": "wrong-code"}, environ_base=LAN)
    assert r.status_code == 403 and "isn't right" in r.get_json()["error"]
    # the iPad cannot unlock itself, even with the right code
    r = client.post("/api/checkin/staff/unlock", json={"code": STAFF_CODE}, environ_base=LAN,
                    headers={"X-Checkin-Key": KEY})
    assert r.status_code == 403
    r = client.post("/api/checkin/staff/unlock", json={"code": STAFF_CODE}, environ_base=LAN)
    assert r.status_code == 200
    cookie = r.headers["Set-Cookie"]
    assert checkin.STAFF_COOKIE in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert STAFF_CODE not in cookie
    assert client.get("/api/checkin/today", environ_base=LAN).status_code == 200
    # changing the code locks every computer out again
    cfg = json.loads(client.cfg_path.read_text())
    cfg["checkin"]["staff_code"] = "zztest-new-code"
    client.cfg_path.write_text(json.dumps(cfg))
    assert client.get("/api/checkin/today", environ_base=LAN).status_code == 403


def test_unlock_locks_out_after_five_wrong_codes(client):
    for _ in range(5):
        client.post("/api/checkin/staff/unlock", json={"code": "nope-nope"}, environ_base=LAN)
    r = client.post("/api/checkin/staff/unlock", json={"code": STAFF_CODE}, environ_base=LAN)
    assert r.status_code == 403 and "Too many" in r.get_json()["error"]


def test_no_staff_code_means_only_the_server_can_use_checkin(client):
    cfg = json.loads(client.cfg_path.read_text())
    for code in ("", "short", "CHANGE-ME-staff-code"):
        cfg["checkin"]["staff_code"] = code
        client.cfg_path.write_text(json.dumps(cfg))
        r = client.get("/api/checkin/today", environ_base=LAN)
        assert r.status_code == 403 and r.get_json()["code_set"] is False
        assert client.post("/api/checkin/staff/unlock", json={"code": code},
                           environ_base=LAN).status_code == 403
    assert client.get("/api/checkin/today").status_code == 200        # the server itself


def test_ipad_key_still_opens_the_ipad_routes_from_the_lan(client):
    r = client.get("/api/checkin/ipad/current", headers={"X-Checkin-Key": KEY}, environ_base=LAN)
    assert r.status_code == 200 and r.get_json() == {"state": "idle"}


def _filling(world):
    _st, out = _send(world)
    tok = out["token"]
    checkin.ipad_session(world["cfg"], tok)
    checkin.ipad_filling(world["cfg"], tok)
    return tok


def test_inactivity_pause_keeps_answers_on_the_server_and_only_staff_resume(world):
    """Codex #4: the lock sends the answers so far as a draft and pauses the
    form. The iPad sees 'paused' with no token and cannot reopen it; reception
    resumes it and the iPad gets the answers back at the same screen."""
    cfg = world["cfg"]
    tok = _filling(world)
    body = {"answers": {"given_name": "Adultone", "symptoms": ["Blurry vision"]},
            "skipped_screens": ["details"], "idx": 2}
    assert checkin.ipad_draft(cfg, tok, body)[0] == 200
    st, out = checkin.ipad_draft(cfg, tok, {"pause": True})
    assert st == 200 and out["state"] == "paused"
    s = checkin.load_session(cfg, tok)
    assert s["state"] == "paused" and s["draft"]["answers"]["symptoms"] == ["Blurry vision"]
    assert "signature_png" not in s["draft"] and "signature_png" not in s
    assert checkin.ipad_current(cfg) == {"state": "paused"}           # no token
    assert checkin.ipad_session(cfg, tok)[0] == 409                    # cannot reopen it
    assert checkin.ipad_filling(cfg, tok)[0] == 409
    assert _submit(world, tok)[0] == 409
    # a second pause (the iPad retrying) changes nothing
    assert checkin.ipad_draft(cfg, tok, {"pause": True, "answers": {}, "skipped_screens": [],
                                         "idx": 0})[1]["state"] == "paused"
    assert checkin.load_session(cfg, tok)["draft"]["idx"] == 2
    # the staff list shows it, and the slot is still taken
    t = checkin.today(cfg)
    assert t["ipad"]["state"] == "paused" and t["ipad"]["status"] == "Paused"
    row = next(a for a in t["appointments"] if a["appointment_id"] == 1001)
    assert row["status"] == "Paused" and row["token"] == tok
    assert checkin.send(cfg, {"kind": "new", "audience": "adult"})[0] == 409
    # reception resumes
    assert checkin.resume(cfg, tok) == (200, {"ok": True, "state": "sent"})
    assert checkin.ipad_current(cfg) == {"token": tok, "state": "sent"}
    st, form = checkin.ipad_session(cfg, tok)
    assert st == 200 and form["draft"] == {"answers": body["answers"], "skipped": ["details"], "idx": 2}
    assert _submit(world, tok)[0] == 200
    assert "draft" not in checkin.load_session(cfg, tok)
    assert checkin.resume(cfg, tok)[0] == 409                          # not paused any more


def test_draft_input_is_checked(world):
    cfg = world["cfg"]
    tok = _filling(world)
    for bad in ({"answers": {"Bad Key": "x"}, "skipped_screens": [], "idx": 0},
                {"answers": {}, "skipped_screens": ["../x"], "idx": 0},
                {"answers": {}, "skipped_screens": [], "idx": 99},
                {"answers": {}, "skipped_screens": [], "idx": "2"}):
        assert checkin.ipad_draft(cfg, tok, bad)[0] == 400
    assert checkin.ipad_draft(cfg, "f" * 32, {"pause": True})[0] == 409


def test_draft_route_needs_the_key(client):
    tok = "c" * 32
    assert client.post(f"/api/checkin/ipad/session/{tok}/draft", json={"pause": True}).status_code == 401
    r = client.post(f"/api/checkin/ipad/session/{tok}/draft", json={"pause": True},
                    headers={"X-Checkin-Key": KEY})
    assert r.status_code == 409 and r.get_json() == {"gone": True}


def test_submit_retry_is_received_not_gone(world):
    """Codex #11: the server saved the form, the answer never reached the iPad,
    the iPad sends again: 'received', even after staff checked or discarded it.
    A token never submitted is still 'gone'."""
    cfg = world["cfg"]
    tok = _filling(world)
    assert _submit(world, tok) == (200, {"ok": True})
    assert _submit(world, tok) == (200, {"ok": True, "already": True})
    checkin.discard(cfg, tok)
    assert _submit(world, tok) == (200, {"ok": True, "already": True})
    receipts = (world["agent"] / "local-reports" / "checkin" / "hub-received.json").read_text()
    assert tok not in receipts                                         # hashes only
    other = _filling(world)
    checkin.discard(cfg, other)                                        # cancelled, never submitted
    assert _submit(world, other) == (409, {"gone": True})
    assert _submit(world, "f" * 32) == (409, {"gone": True})


def test_search_and_today_take_child_or_adult_from_the_engine(world):
    """Codex #3: one age rule (the engine's is_child, under 16). The Hub passes it
    through and never works out an age itself."""
    out = checkin.search(world["cfg"], "ZZTEST")
    assert {p["id"]: p["is_child"] for p in out["patients"]} == {99001: False, 99002: True}
    t = checkin.today(world["cfg"])
    assert {a["patient_id"]: a["audience"] for a in t["appointments"]} == {99001: "adult", 99002: "child"}


def test_staff_page_has_no_age_sum_of_its_own():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text(encoding="utf-8")
    ci = js[js.index("/* --- Check-in (iPad registration form)"):]
    assert "ciIsChild" not in ci and "getFullYear" not in ci and "age < " not in ci
    assert 'p.is_child ? "child" : "adult"' in ci


def test_ipad_v3_contract(client):
    """Codex #4/#6/#9/#10/#11 on the iPad page."""
    js = client.get("/checkin/ipad/ipad.js").get_data(as_text=True)
    css = client.get("/checkin/ipad/ipad.css").get_data(as_text=True)
    html = client.get("/checkin/ipad").get_data(as_text=True)
    for used in ("Still there?", "Please return the iPad to reception.", "WARN_MS = 3 * 60 * 1000",
                 "2 * 60 * 1000", "/draft", "pause: true", "Thank you, received.", "S.welcome",
                 "roomForBar", "nonesOf", 'class="skipsmall"'):
        assert used in js, used
    assert 'id="still"' in html
    assert "grid-auto-rows: 1fr" in css and ".skipsmall" in css and ".skipbig" not in css
    # never a link out of the iPad page - the policy address is plain text
    assert "<a " not in js and "href" not in js and "window.open" not in js
    assert "location.href" not in js and "location.assign" not in js


def test_ipad_v5_flow_chart_contract(client):
    """v5 (Mark, 9 Oct 2026): module screens open from taps, the booking reason
    or age (same rule as the engine's schema.screen_order); answers on a module
    screen that is no longer open are never sent; no question asked twice."""
    js = client.get("/checkin/ipad/ipad.js").get_data(as_text=True)
    for used in ("function order()", "function firedBy(", "booking_reason", "age_min",
                 "not_if_answered", "order().forEach", "S.modules"):
        assert used in js, used
    assert "S.screens[S.idx]" not in js            # the shown order, never the raw list


def test_check_screen_shows_last_exam_here():
    import pathlib
    app_js = (pathlib.Path(checkin.__file__).resolve().parent.parent / "static" / "app.js").read_text(encoding="utf-8")
    assert "Last exam here: <strong>" in app_js and "ciVisitLine(plan)" in app_js
    assert "Extra questions: " in app_js
