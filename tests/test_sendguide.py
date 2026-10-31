"""Send guide tile - synthetic data only. Optomate and Gmail are mocked: the
bridge subprocess is replaced by a fake, and the bridge's own Gmail code is
driven with a fake service. No test touches C:\\CEC\\runtime, Optomate or Gmail."""

import base64
import hashlib
import importlib
import json
import logging
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from hub import guide_bridge, guide_search, sendguide

PDF_A = b"%PDF-1.7 synthetic guide A " + b"x" * 200
PDF_B = b"%PDF-1.7 synthetic guide B " + b"y" * 200


def _yrs_ago(years: int, days: int = 0) -> str:
    t = date.today()
    try:
        d = t.replace(year=t.year - years)
    except ValueError:                       # 29 Feb
        d = t.replace(year=t.year - years, day=28)
    return (d - timedelta(days=days)).isoformat()


PATIENTS = {
    1001: {"patient_id": 1001, "given": "ZZADULT", "surname": "ZZTEST", "nickname": "",
           "dob": _yrs_ago(40), "email": "adult@example.invalid", "noemail": False, "bademail": False},
    1002: {"patient_id": 1002, "given": "Kiddo Middle", "surname": "Zztest", "nickname": "",
           "dob": _yrs_ago(10), "email": "family@example.invalid", "noemail": False, "bademail": False},
    1003: {"patient_id": 1003, "given": "Sibling", "surname": "Zztest", "nickname": "Sib",
           "dob": _yrs_ago(8), "email": "family@example.invalid", "noemail": False, "bademail": False},
    1004: {"patient_id": 1004, "given": "Noemail", "surname": "Zztest", "nickname": "",
           "dob": _yrs_ago(50), "email": "noemail@example.invalid", "noemail": True, "bademail": False},
    1005: {"patient_id": 1005, "given": "Bademail", "surname": "Zztest", "nickname": "",
           "dob": _yrs_ago(50), "email": "bad@example.invalid", "noemail": False, "bademail": True},
    1006: {"patient_id": 1006, "given": "Blank", "surname": "Zztest", "nickname": "",
           "dob": _yrs_ago(50), "email": "", "noemail": False, "bademail": False},
    1007: {"patient_id": 1007, "given": "Nodob", "surname": "Zztest", "nickname": "",
           "dob": "", "email": "nodob@example.invalid", "noemail": False, "bademail": False},
    1008: {"patient_id": 1008, "given": "Seventeen", "surname": "Zztest", "nickname": "",
           "dob": _yrs_ago(16, days=-1), "email": "teen@example.invalid", "noemail": False, "bademail": False},
    1009: {"patient_id": 1009, "given": "Eighteen", "surname": "Zztest", "nickname": "",
           "dob": _yrs_ago(16), "email": "adult16@example.invalid", "noemail": False, "bademail": False},
}


class FakeBridge:
    """Stands in for hub/guide_bridge.py run in the agent folder."""

    def __init__(self, live=False):
        self.live = live
        self.delivered = []
        self.deliver_reply = None

    def __call__(self, cfg, *args, stdin=None):
        cmd = args[0]
        if cmd == "mode":
            return {"live": self.live, "fixture": False}
        if cmd == "patient":
            p = PATIENTS.get(int(args[2]))
            return {"patient": dict(p), "live": self.live} if p else {"error": "That patient isn't in Optomate."}
        if cmd == "search":
            return {"patients": [], "more": False}
        if cmd == "deliver":
            spec = json.loads(stdin)
            self.delivered.append(spec)
            if self.deliver_reply is not None:
                return self.deliver_reply
            return {"mode": "live" if self.live else "draft", "id": f"d{len(self.delivered)}"}
        return {"error": "Bad command."}


@pytest.fixture
def world(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    for d in ("optomate", "gmailer", "checkin"):
        (agent / d).mkdir(parents=True)
    gdir = tmp_path / "guides"
    gdir.mkdir()
    (gdir / "a.pdf").write_bytes(PDF_A)
    (gdir / "b.pdf").write_bytes(PDF_B)
    manifest = tmp_path / "guides.manifest.json"
    manifest.write_text(json.dumps({"guides": [
        {"id": "guide-a", "title": "Test Guide A", "version": "rev 1", "stage": "COLLECTING",
         "language": "en", "path": "a.pdf", "email_filename": "Concord Eyecare - Test Guide A.pdf",
         "sha256": hashlib.sha256(PDF_A).hexdigest(), "approval_ref": "test", "classification": "care",
         "active": True},
        {"id": "guide-b", "title": "Test Guide B", "version": "rev 2", "stage": "DECIDING",
         "language": "en", "path": "b.pdf", "email_filename": "Concord Eyecare - Test Guide B.pdf",
         "sha256": hashlib.sha256(PDF_B).hexdigest(), "approval_ref": "test", "classification": "decision",
         "active": True},
        {"id": "flyer", "title": "Referral Flyer", "version": "", "stage": "MARKETING",
         "language": "en", "path": "a.pdf", "email_filename": "Flyer.pdf",
         "sha256": hashlib.sha256(PDF_A).hexdigest(), "approval_ref": "test",
         "classification": "marketing", "active": True},
    ]}), encoding="utf-8")
    hist = tmp_path / "runtime" / "history.sqlite3"
    cfg = {"optomate_agent": {"agent_dir": str(agent)},
           "send_guide": {"guides_dir": str(gdir), "history_db": str(hist)}}
    cfg_path = tmp_path / "integrations.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("CEC_HUB_GUIDE_MANIFEST", str(manifest))
    monkeypatch.setenv("CEC_HUB_INTEGRATIONS", str(cfg_path))
    fake = FakeBridge()
    monkeypatch.setattr(sendguide, "run_bridge", fake)
    return {"cfg": cfg, "fake": fake, "gdir": gdir, "hist": hist, "tmp": tmp_path}


@pytest.fixture
def client(world):
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _send(world, pid, ids, key="k" * 20, staff="Tester"):
    return sendguide.send(world["cfg"], {"idempotency_key": key, "patient_id": pid, "guide_ids": ids}, staff)


# --- manifest: fail closed ------------------------------------------------------

def test_manifest_hash_mismatch_refuses_the_guide(world):
    (world["gdir"] / "a.pdf").write_bytes(PDF_A + b" edited")
    page = {g["id"]: g for g in sendguide.guides_for_page(world["cfg"])}
    assert page["guide-a"]["ok"] is False and "changed" in page["guide-a"]["problem"]
    assert page["guide-b"]["ok"] is True
    status, r = sendguide.review(world["cfg"], {"patient_id": 1001, "guide_ids": ["guide-a", "guide-b"]})
    assert r["can_send"] is False and any("changed" in b for b in r["blocks"])
    status, out = _send(world, 1001, ["guide-a", "guide-b"])
    assert status == 409 and world["fake"].delivered == []


def test_missing_file_and_marketing_and_unknown_ids_are_refused(world):
    (world["gdir"] / "b.pdf").unlink()
    _, r = sendguide.review(world["cfg"], {"patient_id": 1001, "guide_ids": ["guide-b", "flyer", "nope"]})
    probs = {g["id"]: g["problem"] for g in r["guides"]}
    assert "missing" in probs["guide-b"]
    assert "print-only" in probs["flyer"]
    assert "approved list" in probs["nope"]
    assert r["can_send"] is False


def test_marketing_entry_never_listed_as_sendable(world):
    page = {g["id"]: g for g in sendguide.guides_for_page(world["cfg"])}
    assert page["flyer"]["ok"] is False


def test_attached_bytes_are_hash_checked_again_in_the_bridge():
    spec = {"to": "a@example.invalid", "subject": "S", "body": "B",
            "attachments": [{"filename": "x.pdf", "sha256": hashlib.sha256(b"one").hexdigest(),
                             "data_b64": base64.b64encode(b"two").decode()}]}
    with pytest.raises(guide_bridge.BridgeError):
        guide_bridge.build_message(spec)


def test_shipped_manifest_is_valid_and_care_only():
    data = json.loads((sendguide.DEFAULT_MANIFEST).read_text(encoding="utf-8"))
    ids = [g["id"] for g in data["guides"]]
    assert len(ids) == len(set(ids))
    for g in data["guides"]:
        assert g["classification"] in sendguide.SEND_CLASSES
        assert sendguide.HEX64_RE.match(g["sha256"])
        assert "Pack" not in g["path"] and "Flyer" not in g["path"]


# --- no email ---------------------------------------------------------------------

@pytest.mark.parametrize("pid,word", [(1004, "no email"), (1005, "not working"),
                                      (1006, "No email in Optomate"), (1007, "date of birth")])
def test_contact_flags_block_the_send(world, pid, word):
    _, r = sendguide.review(world["cfg"], {"patient_id": pid, "guide_ids": ["guide-a"]})
    assert r["can_send"] is False
    assert any(word in b for b in r["blocks"])
    status, _ = _send(world, pid, ["guide-a"])
    assert status == 409 and world["fake"].delivered == []


def test_noemail_patient_address_is_not_shown(world):
    _, r = sendguide.review(world["cfg"], {"patient_id": 1004, "guide_ids": ["guide-a"]})
    assert r["to"]["email"] == ""


# --- child vs adult ---------------------------------------------------------------

def test_adult_greeted_by_first_name_and_own_email(world):
    _, r = sendguide.review(world["cfg"], {"patient_id": 1001, "guide_ids": ["guide-a"]})
    assert r["to"]["label"] == "Patient's own email" and r["patient"]["minor"] is False
    _send(world, 1001, ["guide-a"])
    body = world["fake"].delivered[0]["body"]
    assert body.startswith("Hello Zzadult,\n")
    assert world["fake"].delivered[0]["to"] == "adult@example.invalid"


def test_child_never_greeted_parent_label_and_child_named(world):
    _, r = sendguide.review(world["cfg"], {"patient_id": 1002, "guide_ids": ["guide-a"]})
    assert r["to"]["label"] == "Parent/guardian's email" and r["patient"]["minor"] is True
    _send(world, 1002, ["guide-a"])
    body = world["fake"].delivered[0]["body"]
    assert body.startswith("Hello,\n")
    assert "Hello Kiddo" not in body
    assert "Please find attached the Test Guide A for Kiddo." in body


def test_preferred_name_and_age_boundary(world):
    t = date.today()
    assert sendguide.compose(PATIENTS[1003], [{"title": "G"}], t)["body"].count("the G for Sib.") == 1
    assert sendguide.compose(PATIENTS[1008], [{"title": "G"}], t)["minor"] is True     # 15 (16 tomorrow)
    assert sendguide.compose(PATIENTS[1009], [{"title": "G"}], t)["minor"] is False    # 16 today


def test_subject_neutral_and_body_only_names_guides(world):
    _send(world, 1001, ["guide-a", "guide-b"])
    spec = world["fake"].delivered[0]
    assert spec["subject"] == "Your guide from Concord Eyecare"
    low = spec["body"].lower()
    for word in ("myopia", "ortho", "diagnos", "prescription", "unsubscribe"):
        assert word not in low
    assert "- Test Guide A\n- Test Guide B" in spec["body"]
    assert "Please find attached the following guides:\n\n- Test Guide A\n- Test Guide B" in spec["body"]
    assert "attached as PDF" not in spec["body"]
    assert spec["body"].rstrip().endswith("concordeyecare.com.au")
    assert "Concord Eyecare\n161 Concord Rd" in spec["body"]
    assert [a["filename"] for a in spec["attachments"]] == [
        "Concord Eyecare - Test Guide A.pdf", "Concord Eyecare - Test Guide B.pdf"]


# --- repeat warning ---------------------------------------------------------------

def test_repeat_warning_same_patient_same_guide_within_30_days(world):
    assert _send(world, 1002, ["guide-a"], key="a" * 20)[1]["ok"]
    _, r = sendguide.review(world["cfg"], {"patient_id": 1002, "guide_ids": ["guide-a", "guide-b"]})
    assert [x["guide_id"] for x in r["repeats"]] == ["guide-a"]
    assert r["repeats"][0]["outcome"] == "draft"
    assert r["can_send"] is True                      # a warning, not a block
    # a sibling on the same family email is a different patient: no warning
    _, r2 = sendguide.review(world["cfg"], {"patient_id": 1003, "guide_ids": ["guide-a"]})
    assert r2["repeats"] == []


def test_repeat_warning_expires_after_30_days(world):
    _send(world, 1001, ["guide-a"], key="b" * 20)
    old = (datetime.now().astimezone() - timedelta(days=31)).isoformat(timespec="seconds")
    with sqlite3.connect(world["hist"]) as con:
        con.execute("UPDATE sends SET created = ?", (old,))
    _, r = sendguide.review(world["cfg"], {"patient_id": 1001, "guide_ids": ["guide-a"]})
    assert r["repeats"] == []


def test_failed_send_is_not_a_repeat(world):
    world["fake"].deliver_reply = {"error": "Gmail said no.", "error_code": "HttpError"}
    status, out = _send(world, 1001, ["guide-a"], key="c" * 20)
    assert out["ok"] is False and out["outcome"] == "failed"
    _, r = sendguide.review(world["cfg"], {"patient_id": 1001, "guide_ids": ["guide-a"]})
    assert r["repeats"] == []


# --- idempotency ------------------------------------------------------------------

def test_double_submit_makes_one_draft(world):
    s1, r1 = _send(world, 1001, ["guide-b", "guide-a"], key="d" * 20)
    s2, r2 = _send(world, 1001, ["guide-a", "guide-b"], key="d" * 20)
    assert r1["ok"] and r2["ok"] and r2.get("repeat_submit") is True
    assert r1["op_id"] == r2["op_id"]
    assert len(world["fake"].delivered) == 1
    with sqlite3.connect(world["hist"]) as con:
        assert con.execute("SELECT COUNT(*) FROM sends").fetchone()[0] == 1


def test_same_key_for_a_different_patient_is_refused(world):
    _send(world, 1001, ["guide-a"], key="e" * 20)
    status, out = _send(world, 1002, ["guide-a"], key="e" * 20)
    assert status == 409 and len(world["fake"].delivered) == 1


def test_timeout_is_recorded_unknown_and_not_retried(world):
    world["fake"].deliver_reply = {"error": sendguide.UNKNOWN_WORDS, "timeout": True}
    _, out = _send(world, 1001, ["guide-a"], key="f" * 20)
    assert out["outcome"] == "unknown" and "Drafts" in out["message"]
    _, again = _send(world, 1001, ["guide-a"], key="f" * 20)
    assert again["outcome"] == "unknown" and len(world["fake"].delivered) == 1
    _, r = sendguide.review(world["cfg"], {"patient_id": 1001, "guide_ids": ["guide-a"]})
    assert r["repeats"][0]["outcome"] == "unknown"


def test_history_written_before_gmail_is_called(world):
    seen = {}

    def deliver_check(cfg, *args, stdin=None):
        if args[0] == "deliver":
            with sqlite3.connect(world["hist"]) as con:
                seen["row"] = con.execute("SELECT outcome FROM sends").fetchone()
            return {"mode": "draft", "id": "x"}
        return FakeBridge()(cfg, *args, stdin=stdin)

    sendguide.run_bridge = deliver_check
    _send(world, 1001, ["guide-a"], key="g" * 20)
    assert seen["row"] == ("sending",)


# --- draft-mode gate --------------------------------------------------------------

@pytest.mark.parametrize("env,live", [
    ({}, False),
    ({"GUIDE_DRAFT_MODE": "false"}, False),
    ({"GUIDE_LIVE_SEND": "true"}, False),
    ({"GUIDE_DRAFT_MODE": "true", "GUIDE_LIVE_SEND": "true"}, False),
    ({"GUIDE_DRAFT_MODE": "false", "GUIDE_LIVE_SEND": "yes"}, False),
    ({"GUIDE_DRAFT_MODE": "FALSE", "GUIDE_LIVE_SEND": "TRUE"}, True),
    ({"GUIDE_DRAFT_MODE": "false", "GUIDE_LIVE_SEND": "true"}, True),
])
def test_live_needs_both_keys(env, live):
    assert guide_bridge.live_send_enabled(env) is live


class FakeGmail:
    def __init__(self):
        self.drafts_made, self.sent = [], []
        outer = self

        class _Call:
            def __init__(self, fn):
                self.fn = fn

            def execute(self):
                return self.fn()

        class _Drafts:
            def create(self, userId, body):
                return _Call(lambda: (outer.drafts_made.append(body), {"id": "draft1"})[1])

        class _Messages:
            def send(self, userId, body):
                return _Call(lambda: (outer.sent.append(body), {"id": "msg1"})[1])

        class _Users:
            def drafts(self):
                return _Drafts()

            def messages(self):
                return _Messages()

        self._users = _Users()

    def users(self):
        return self._users


def _spec():
    return {"op_id": "op1", "to": "adult@example.invalid", "subject": "Your guide from Concord Eyecare",
            "body": "Hello,\n", "attachments": [{"filename": "Concord Eyecare - Test Guide A.pdf",
                                                  "sha256": hashlib.sha256(PDF_A).hexdigest(),
                                                  "data_b64": base64.b64encode(PDF_A).decode()}]}


def test_bridge_drafts_unless_both_keys():
    for env in ({}, {"GUIDE_DRAFT_MODE": "false"}, {"GUIDE_LIVE_SEND": "true"}):
        g = FakeGmail()
        out = guide_bridge.deliver(_spec(), service=g, env=env)
        assert out == {"mode": "draft", "id": "draft1"} and g.sent == [] and len(g.drafts_made) == 1
    g = FakeGmail()
    out = guide_bridge.deliver(_spec(), service=g,
                               env={"GUIDE_DRAFT_MODE": "false", "GUIDE_LIVE_SEND": "true"})
    assert out == {"mode": "live", "id": "msg1"} and g.drafts_made == []


def test_bridge_fixture_never_touches_gmail():
    g = FakeGmail()
    out = guide_bridge.deliver(_spec(), service=g, env={"CHECKIN_FIXTURE": "1", "GUIDE_DRAFT_MODE": "false",
                                                         "GUIDE_LIVE_SEND": "true"})
    assert out["mode"] == "draft" and out["fixture"] and g.drafts_made == [] and g.sent == []


def test_hub_never_hands_the_gate_to_the_bridge(monkeypatch):
    monkeypatch.setenv("GUIDE_DRAFT_MODE", "false")
    monkeypatch.setenv("GUIDE_LIVE_SEND", "true")
    env = sendguide._bridge_env()
    assert "GUIDE_DRAFT_MODE" not in env and "GUIDE_LIVE_SEND" not in env


def test_bridge_message_has_pdf_and_rejects_header_tricks():
    msg = guide_bridge.build_message(_spec())
    parts = [p for p in msg.iter_attachments()]
    assert parts[0].get_content_type() == "application/pdf"
    assert parts[0].get_content() == PDF_A
    for bad in ("a@b.c\nBcc: x@y.z", "a@b.c, x@y.z", "not-an-email"):
        with pytest.raises(guide_bridge.BridgeError):
            guide_bridge.build_message({**_spec(), "to": bad})


# --- search convention ------------------------------------------------------------

def test_search_two_words_exact_pairs_first_then_wide_net():
    calls = []
    exact = [{"ID": 2, "SURNAME": "XU", "GIVEN": "XU"}]
    broad = [{"ID": 1, "SURNAME": "XU", "GIVEN": "Anna"}, {"ID": 2, "SURNAME": "XU", "GIVEN": "XU"},
             {"ID": 3, "SURNAME": "XUE", "GIVEN": "Bo"}]

    def fetch(filt, top, spec):
        calls.append((filt, top, spec[0]))
        return exact if spec[0] == "exact" else broad

    rows, more = guide_search.search(fetch, "Xu Xu", limit=12)
    assert [r["ID"] for r in rows] == [2, 1, 3]            # exact hit first, no duplicate
    assert more is False
    assert calls[0][2] == "exact" and "contains(SURNAME,'Xu') and contains(GIVEN,'Xu')" in calls[0][0]
    assert calls[0][1] == 13                                # one more than shown


def test_search_either_order_and_more_flag():
    rows_all = [{"ID": i, "SURNAME": "SMITH", "GIVEN": f"John{i}"} for i in range(20)]

    def fetch(filt, top, spec):
        return guide_search.fixture_match(rows_all, spec)[:top]

    a, more_a = guide_search.search(fetch, "John Smith", limit=5)
    b, more_b = guide_search.search(fetch, "Smith John", limit=5)
    assert [r["ID"] for r in a] == [r["ID"] for r in b] and len(a) == 5
    assert more_a is True and more_b is True


def test_search_sanitises_and_escapes():
    assert guide_search.terms("O'Brien (Jo)") == ["O'Brien", "Jo"]
    assert "contains(SURNAME,'O''Brien')" in guide_search.filter_exact(["O'Brien", "Jo"])
    assert guide_search.terms("S m") == []
    assert guide_search.filter_exact(["one"]) is None


def test_fixture_match_orders_surname_given():
    rows = [{"ID": 1, "SURNAME": "Zed", "GIVEN": "Amy"}, {"ID": 2, "SURNAME": "Abe", "GIVEN": "Zoe"},
            {"ID": 3, "SURNAME": "Abe", "GIVEN": "Amy"}]
    out = guide_search.fixture_match(rows, ("broad", ["e"]))
    assert [r["ID"] for r in out] == [3, 2, 1]


# --- storage + privacy ------------------------------------------------------------

def test_history_refused_inside_the_hub_repo(world):
    cfg = {**world["cfg"], "send_guide": {**world["cfg"]["send_guide"],
                                          "history_db": str(sendguide.BASE_DIR / "data" / "h.sqlite3")}}
    status, out = sendguide.send(cfg, {"idempotency_key": "h" * 20, "patient_id": 1001,
                                       "guide_ids": ["guide-a"]}, "T")
    assert status == 500 and "outside" in out["error"]
    assert world["fake"].delivered == []


def test_history_refused_in_sync_folder(world, tmp_path):
    cfg = {**world["cfg"], "send_guide": {**world["cfg"]["send_guide"],
                                          "history_db": str(tmp_path / "OneDrive" / "h.sqlite3")}}
    with pytest.raises(RuntimeError):
        sendguide.History(cfg)


def test_no_names_or_emails_in_the_log(world, caplog):
    caplog.set_level(logging.DEBUG)
    _send(world, 1002, ["guide-a"], key="i" * 20)
    text = caplog.text.lower()
    for leak in ("family@example.invalid", "kiddo", "zztest", "test guide"):
        assert leak not in text


def test_routes_are_post_and_no_store(client, world):
    r = client.get("/api/sendguide/setup")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert [g["id"] for g in r.get_json()["guides"]] == ["guide-a", "guide-b", "flyer"]
    assert client.get("/api/sendguide/search?q=Smith").status_code in (404, 405)   # POST only
    r = client.post("/api/sendguide/review", json={"patient_id": 1002, "guide_ids": ["guide-a"]})
    assert r.headers["Cache-Control"] == "no-store" and r.get_json()["to"]["label"] == "Parent/guardian's email"
    r = client.post("/api/sendguide/send", json={"idempotency_key": "j" * 20, "patient_id": 1002,
                                                 "guide_ids": ["guide-a"]})
    assert r.get_json()["ok"] is True and r.get_json()["outcome"] == "draft"
    r = client.post("/api/sendguide/send", json={"idempotency_key": "j" * 20, "patient_id": 1002,
                                                 "guide_ids": ["guide-a"]})
    assert r.get_json()["repeat_submit"] is True and len(world["fake"].delivered) == 1


def test_staff_guard_applies(client, world):
    r = client.post("/api/sendguide/review", json={"patient_id": 1001, "guide_ids": []},
                    headers={"X-Checkin-Key": "anything"})
    assert r.status_code == 403
    r = client.get("/api/sendguide/setup", environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert r.status_code == 403


# --- manifest upkeep: one-line guide update, other languages --------------------

def test_one_line_update_swaps_file_version_and_hash(world, tmp_path):
    from hub import guide_manifest
    new = tmp_path / "CEC-Test-Guide-A-rev8.pdf"
    new.write_bytes(PDF_A + b" rev 8")
    entry = guide_manifest.update(world["cfg"], "guide-a", new, "rev 8, Nov 2026", "Mark approved rev 8 (test)")
    assert entry["sha256"] == hashlib.sha256(PDF_A + b" rev 8").hexdigest()
    assert (world["gdir"] / new.name).read_bytes() == PDF_A + b" rev 8"
    page = {g["id"]: g for g in sendguide.guides_for_page(world["cfg"])}
    assert page["guide-a"]["ok"] and page["guide-a"]["version"] == "rev 8, Nov 2026"
    assert page["guide-b"]["ok"]                              # nothing else touched
    with pytest.raises(SystemExit):
        guide_manifest.update(world["cfg"], "guide-a", new, "rev 9", "  ")   # approval required


def test_chinese_guide_entry_is_accepted_and_labelled(world):
    m = json.loads(open(sendguide.manifest_path(world["cfg"]), encoding="utf-8").read())
    zh = dict(m["guides"][0], id="guide-a-zh", title="Test Guide A (Chinese)", language="zh",
              email_filename="Concord Eyecare - Test Guide A (Chinese).pdf")
    bad = dict(m["guides"][0], id="guide-bad-lang", language="Chinese")
    m["guides"] += [zh, bad]
    sendguide.manifest_path(world["cfg"]).write_text(json.dumps(m), encoding="utf-8")
    page = {g["id"]: g for g in sendguide.guides_for_page(world["cfg"])}
    assert page["guide-a-zh"]["ok"] and page["guide-a-zh"]["language_name"] == "Chinese"
    assert "guide-bad-lang" not in page                       # malformed language: dropped
    _, r = sendguide.review(world["cfg"], {"patient_id": 1001, "guide_ids": ["guide-a", "guide-a-zh"]})
    assert r["can_send"] and [g["language"] for g in r["guides"]] == ["en", "zh"]
