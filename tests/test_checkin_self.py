"""Self check-in on the iPad + the automatic save (Mark, 13 Oct 2026, option A).

Same fake engine as test_checkin.py, plus the engine's `match` command. Names
are ZZTEST fakes.
"""

import json

import pytest

from hub import checkin
from tests.test_checkin import H1, KEY, FakeEngine


class SelfEngine(FakeEngine):
    """FakeEngine + `match`: 'zztest' + 15/03/1980 (or mobile 0400 000 001) = the
    9:00 booking; 'twin' = two patients; anything else = no match."""

    def __init__(self):
        super().__init__()
        self.plan_extra = {}

    def __call__(self, argv, **kw):
        args = argv[3:]
        if args[0] == "match":
            a = dict(zip(args[1::2], args[2::2]))
            sur = a.get("--surname", "").lower()
            ok = sur == "zztest" and (a.get("--dob") == "15/03/1980"
                                      or a.get("--mobile", "").endswith("400000001"))
            if sur == "twin":
                out = {"matches": [{"patient_id": 99001, "appointment_id": 1001},
                                   {"patient_id": 99002, "appointment_id": 1002}], "count": 2}
            elif sur == "same":
                out = {"matches": [{"patient_id": 99001, "appointment_id": 1001},
                                   {"patient_id": 99001, "appointment_id": 1001}], "count": 2}
            elif sur == "crowd":
                out = {"matches": [{"patient_id": 1, "appointment_id": 1}] * 3, "count": 4}
            elif ok:
                out = {"matches": [{"patient_id": 99001, "appointment_id": 1001}], "count": 1}
            else:
                out = {"matches": [], "count": 0}
            out.update(dry_run=True)

            class P:
                stdout = json.dumps(out) + "\n"
                returncode = 0
            self.calls.append(argv)
            return P()
        res = super().__call__(argv, **kw)
        if args[0] == "plan" and self.plan_extra:
            body = json.loads(res.stdout.splitlines()[-1])
            body.update(self.plan_extra)

            class P2:
                stdout = json.dumps(body) + "\n"
                returncode = 0
            return P2()
        return res


@pytest.fixture
def w(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    (agent / "checkin").mkdir(parents=True)
    cfg = {"optomate_agent": {"agent_dir": str(agent), "python": "py"},
           "checkin": {"ipad_key": KEY}}                      # both features default ON
    checkin._self_fails.clear()
    eng = SelfEngine()
    monkeypatch.setattr(checkin.subprocess, "run", eng)
    monkeypatch.setattr(checkin, "_run_later", lambda fn, *a: fn(*a))   # save at once
    checkin._lists_cache.update(at=0.0, dir=None, data=None)
    return {"cfg": cfg, "eng": eng}


def _self(w, **body):
    return checkin.ipad_self(w["cfg"], body)


def _fill(w, token, answers=None):
    return checkin.ipad_submit(w["cfg"], token,
                               {"answers": answers or {"given_name": "Adultone"}, "skipped_screens": []})


# ---- the look-up -------------------------------------------------------------

def test_idle_ipad_says_self_checkin_is_on(w):
    assert checkin.ipad_current(w["cfg"]) == {"state": "idle", "self": True}
    w["cfg"]["checkin"]["self_checkin"] = False
    assert checkin.ipad_current(w["cfg"]) == {"state": "idle"}
    assert _self(w, surname="zztest", dob="15/03/1980")[0] == 403


def test_surname_and_birth_date_open_the_form(w):
    st, out = _self(w, surname="ZZTEST", dob="15/03/1980")
    assert st == 200 and out["ok"] and len(out["token"]) == 32
    s = checkin.load_session(w["cfg"], out["token"])
    assert s["appointment_id"] == 1001 and s["patient_id"] == 99001 and s["via"] == "self"
    assert checkin.ipad_current(w["cfg"])["token"] == out["token"]
    assert set(out) == {"ok", "token"}                    # no name, no DOB back


def test_mobile_route(w):
    st, out = _self(w, surname="zztest", mobile="0400 000 001")
    assert st == 200 and out["ok"]
    assert ["--mobile", "0400000001"] == w["eng"].calls[0][-2:]


def test_no_match_says_nothing_about_who(w):
    st, out = _self(w, surname="zztest", dob="01/01/1990")
    assert st == 200 and out == {"found": False, "many": False}


def test_family_on_one_mobile_picks_by_first_name(w):
    st, out = _self(w, surname="twin", mobile="0400 000 001")
    assert st == 200 and [c["name"] for c in out["choose"]] == ["Adultone", "Childone"]
    assert set(out) == {"choose", "pick"}                       # first names only
    assert checkin.ipad_current(w["cfg"])["state"] == "idle"     # nothing opened yet
    st, opened = checkin.ipad_pick(w["cfg"], {"pick": out["pick"], "n": 1})
    assert st == 200 and opened["ok"]
    assert checkin.load_session(w["cfg"], opened["token"])["appointment_id"] == 1002
    # the choice is single-use
    assert checkin.ipad_pick(w["cfg"], {"pick": out["pick"], "n": 0})[1]["expired"] is True


def test_family_pick_expires_and_rejects_bad_input(w, monkeypatch):
    out = _self(w, surname="twin", mobile="0400 000 001")[1]
    assert checkin.ipad_pick(w["cfg"], {"pick": out["pick"], "n": 5})[0] == 400
    out = _self(w, surname="twin", mobile="0400 000 001")[1]
    t0 = checkin.time.time()
    monkeypatch.setattr(checkin.time, "time", lambda: t0 + checkin.PICK_TTL_S + 1)
    assert checkin.ipad_pick(w["cfg"], {"pick": out["pick"], "n": 0})[1]["expired"] is True
    assert checkin.ipad_pick(w["cfg"], {"pick": "nope", "n": 0})[1]["expired"] is True


def test_same_first_name_or_too_many_go_to_reception(w):
    assert _self(w, surname="same", mobile="0400 000 001")[1] == {"found": False, "many": True}
    assert _self(w, surname="crowd", mobile="0400 000 001")[1] == {"found": False, "many": True}


def test_family_pick_never_replaces_a_form_on_the_ipad(w):
    out = _self(w, surname="twin", mobile="0400 000 001")[1]
    checkin.send(w["cfg"], {"kind": "new", "audience": "adult"})
    assert checkin.ipad_pick(w["cfg"], {"pick": out["pick"], "n": 0})[0] == 409


def test_guessing_is_slowed_down(w):
    for _ in range(checkin.SELF_TRIES):
        _self(w, surname="zztest", dob="01/01/1990")
    st, out = _self(w, surname="zztest", dob="15/03/1980")
    assert st == 429 and out["error"] == checkin.SEE_RECEPTION


def test_bad_input_is_refused_before_the_engine(w):
    assert _self(w, surname="z", dob="15/03/1980")[1]["field"] == "surname"
    assert _self(w, surname="zztest", dob="1980-03-15")[1]["field"] == "dob"
    assert _self(w, surname="zztest")[1]["field"] == "mobile"
    assert _self(w, surname="zztest", mobile="0400")[1]["field"] == "mobile"
    assert w["eng"].calls == []


def test_busy_ipad_is_never_replaced(w):
    first = checkin.send(w["cfg"], {"kind": "appointment", "appointment_id": 1002})[1]
    st, out = _self(w, surname="zztest", dob="15/03/1980")
    assert st == 409 and out["error"] == checkin.SEE_RECEPTION
    assert checkin.ipad_current(w["cfg"])["token"] == first["token"]


def test_already_checked_in_today(w):
    tok = _self(w, surname="zztest", dob="15/03/1980")[1]["token"]
    w["eng"].plan_extra = {"patient_changes": [{"field": "phone_mobile", "accepted": True}]}
    _fill(w, tok)
    assert _self(w, surname="zztest", dob="15/03/1980")[1] == {"done": True}


# ---- the automatic save ----------------------------------------------------

def test_nothing_to_check_saves_itself_as_a_test(w):
    tok = _self(w, surname="zztest", dob="15/03/1980")[1]["token"]
    assert _fill(w, tok)[0] == 200
    s = checkin.load_session(w["cfg"], tok)
    assert s["auto"]["result"] == "test_saved" and s.get("hub_test_saved")
    save = [c for c in w["eng"].calls if c[3] == "save"][0]
    assert save[save.index("--staff") + 1] == checkin.AUTO_STAFF
    assert save[save.index("--expect-hash") + 1] == H1
    row = next(a for a in checkin.today(w["cfg"])["appointments"] if a["appointment_id"] == 1001)
    assert row["status"] == "Saved (test)" and row["self"] is True


@pytest.mark.parametrize("extra,why", [
    ({"patient_changes": [{"field": "phone_mobile", "accepted": True}]}, "Mobile changed"),
    ({"patient_changes": [{"field": "email", "accepted": True}]}, "Email changed"),
    ({"patient_changes": [{"field": "medicare_number", "accepted": True}]}, "Medicare changed"),
    ({"patient_changes": [{"field": "dob", "accepted": True}]}, "Date of birth changed"),
    ({"patient_changes": [{"field": "family_name", "accepted": True}]}, "Name changed"),
    ({"is_new_patient": True}, "New patient"),
    ({"exam": {"action": "exists", "notes": {}}}, "Exam already open"),
    ({"can_save": False, "blockers": ["Pick the optometrist"]}, "Needs a staff decision"),
    ({"warnings": ["The form was filled on 12/10/2026, not today"]}, "Something to look at"),
])
def test_anything_risky_waits_for_a_person(w, extra, why):
    w["eng"].plan_extra = extra
    tok = _self(w, surname="zztest", dob="15/03/1980")[1]["token"]
    _fill(w, tok)
    s = checkin.load_session(w["cfg"], tok)
    assert why in s["check_reasons"] and "auto" not in s
    assert not [c for c in w["eng"].calls if c[3] == "save"]
    row = next(a for a in checkin.today(w["cfg"])["appointments"] if a["appointment_id"] == 1001)
    assert row["status"] == "Ready to check" and why in row["check_reasons"]


def test_address_change_alone_still_saves_itself(w):
    w["eng"].plan_extra = {"patient_changes": [{"field": "address", "accepted": True}]}
    tok = _self(w, surname="zztest", dob="15/03/1980")[1]["token"]
    _fill(w, tok)
    assert checkin.load_session(w["cfg"], tok)["auto"]["result"] == "test_saved"


def test_auto_save_off_leaves_every_form_for_staff(w):
    w["cfg"]["checkin"]["auto_save"] = False
    tok = _self(w, surname="zztest", dob="15/03/1980")[1]["token"]
    _fill(w, tok)
    s = checkin.load_session(w["cfg"], tok)
    assert "auto" not in s and "check_reasons" not in s


def test_live_auto_save_deletes_the_form_and_marks_saved(w):
    w["eng"].save_result = {"dry_run": False, "saved": True, "patient_id": 99001}
    tok = _self(w, surname="zztest", dob="15/03/1980")[1]["token"]
    _fill(w, tok)
    assert checkin.load_session(w["cfg"], tok) is None
    row = next(a for a in checkin.today(w["cfg"])["appointments"] if a["appointment_id"] == 1001)
    assert row["status"] == "Saved"


def test_staff_sent_forms_save_themselves_too(w):
    tok = checkin.send(w["cfg"], {"kind": "appointment", "appointment_id": 1001})[1]["token"]
    _fill(w, tok)
    assert checkin.load_session(w["cfg"], tok)["auto"]["result"] == "test_saved"


def test_new_patient_form_is_never_auto_saved(w):
    tok = checkin.send(w["cfg"], {"kind": "new", "audience": "adult"})[1]["token"]
    _fill(w, tok, {"given_name": "Newone"})
    s = checkin.load_session(w["cfg"], tok)
    assert "auto" not in s and not [c for c in w["eng"].calls if c[3] == "plan"]


# ---- route ---------------------------------------------------------------

def test_route_needs_the_device_key(tmp_path, monkeypatch, w):
    import importlib
    p = tmp_path / "integrations.json"
    p.write_text(json.dumps(w["cfg"]), encoding="utf-8")
    monkeypatch.setenv("CEC_HUB_INTEGRATIONS", str(p))
    import app as app_module
    importlib.reload(app_module)
    with app_module.app.test_client() as c:
        assert c.post("/api/checkin/ipad/self", json={"surname": "zztest", "dob": "15/03/1980"}).status_code == 401
        r = c.post("/api/checkin/ipad/self", json={"surname": "zztest", "dob": "15/03/1980"},
                   headers={"X-Checkin-Key": KEY})
        assert r.status_code == 200 and r.get_json()["ok"]
        assert "no-store" in r.headers.get("Cache-Control", "")
