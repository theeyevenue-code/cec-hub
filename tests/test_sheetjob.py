"""iPad price sheet steps 2-3 (hub/sheetprice.py, hub/sheetjob.py, /api/sheet/*). Synthetic price books, a fake
read-only bridge and a fake engine: nothing touches Optomate. Fake ZZTEST people only."""
import datetime as dt
import importlib
import json

import pytest

from hub import lens_bridge as lb
from hub import lensoptions as lo
from hub import pickjob as pk
from hub import sheetjob as sj
from hub import sheetprice as sp
from tests.test_lensoptions import RTF, _eye as _eye0


def _eye(*a, prism=None, **kw):
    e = _eye0(*a, **kw)
    if prism:
        e[7:11] = list(prism)
    return e

# price books named exactly as the sheet map expects (ranges made up for the tests)
LENS_CSV = (
    "supplier,brand,lens,category,index,material,type,blank_mm,sph_min,sph_max,cyl_max,add_min,add_max,price,coating\n"
    "ZEISS,ZEISS,ClearView FSV 1.50,Single vision,1.50,Clear,stock,70,-4.00,+4.00,-2.00,,,10.00,DuraVision Platinum UV\n"
    "ZEISS,ZEISS,ClearView FSV 1.60,Single vision,1.60,Clear,stock,70,-6.00,+4.00,-2.00,,,12.00,DuraVision Platinum UV\n"
    "ZEISS,ZEISS,ClearView FSV 1.60,Single vision,1.60,Clear,stock,75,-6.00,+4.00,-2.00,,,12.00,DuraVision Platinum UV\n"
    "ZEISS,ZEISS,ClearView FSV BluePro 1.50,Single vision,1.50,Clear BluePro,stock,70,-4.00,+2.00,-2.00,,,14.00,DuraVision Platinum UV\n"
    "ZEISS,ZEISS,ClearView Single Vision 1.50,Single vision,1.50,Clear,grind,,-10.00,+8.00,-4.00,,,30.00,DuraVision Plus Platinum UV\n"
    "ZEISS,ZEISS,ClearView Single Vision 1.50,Single vision,1.50,Clear BluePro,grind,,-10.00,+8.00,-4.00,,,32.00,DuraVision Plus Platinum UV\n"
    "ZEISS,ZEISS,ClearView Single Vision 1.60,Single vision,1.60,Clear,grind,,-12.00,+8.00,-4.00,,,35.00,DuraVision Plus Platinum UV\n"
    "ZEISS,ZEISS,SmartLife Digital 1.60,Anti-fatigue,1.60,Clear,grind,,-10.00,+8.00,-4.00,,,60.00,DuraVision Plus Platinum UV\n"
    "Synchrony,Synchrony,FSV Clear 1.50,Single vision,1.50,Clear,stock,70,-4.00,+4.00,-2.00,,,9.00,HMC+\n"
    "Synchrony,Synchrony,FSV Clear 1.56,Single vision,1.56,Clear,stock,65,+0.25,+4.00,-2.00,,,9.00,HMC+\n"
    "Synchrony,Synchrony,Progressive Ultra HDV 1.50,Progressive,1.50,Clear,grind,,-8.00,+6.00,-4.00,0.75,3.50,45.00,HMC+\n"
)
HEAD = ("supplier,lens,index,material,coating,stockgrind,blank_mm,category,pb_codes,orderable,lens_barcode,lens_description,"
        "lens_sellinc,coating_addon_barcode,addon_sellinc,coat_in,optomate_pair,hub_pair_price,hub_price_note,price_agree,"
        "suggest_method,candidates,status,checked_by,checked_on\n")
LINK_CSV = HEAD + (
    "ZEISS,ClearView FSV 1.50,1.50,Clear,DuraVision Platinum UV,stock,70,Single vision,,yes,20001989,SV Stock 1.50 70,100.00,,,lens,200.00,200.00,,yes,name,,unchecked,,\n"
    "ZEISS,ClearView FSV 1.60,1.60,Clear,DuraVision Platinum UV,stock,70,Single vision,,yes,20001993,SV Stock 1.60 70,125.00,,,lens,250.00,250.00,,yes,name,,unchecked,,\n"
    "ZEISS,ClearView FSV 1.60,1.60,Clear,DuraVision Platinum UV,stock,75,Single vision,,yes,20001994,SV Stock 1.60 75,125.00,,,lens,250.00,250.00,,yes,name,,unchecked,,\n"
    "ZEISS,ClearView FSV BluePro 1.50,1.50,Clear BluePro,DuraVision Platinum UV,stock,70,Single vision,,yes,20001991,SV Blue 1.50 70,125.00,20002018,0.00,addon,250.00,250.00,,yes,name,,unchecked,,\n"
    "ZEISS,ClearView Single Vision 1.50,1.50,Clear,DuraVision Plus Platinum UV,grind,,Single vision,,yes,20001357,SV Grind 1.50,150.00,20002018,0.00,addon,300.00,300.00,,yes,name,,unchecked,,\n"
    "ZEISS,ClearView Single Vision 1.50,1.50,Clear BluePro,DuraVision Plus Platinum UV,grind,,Single vision,,yes,20001363,SV Grind Blue 1.50,175.00,20002018,0.00,addon,350.00,350.00,,yes,name,,unchecked,,\n"
    "ZEISS,ClearView Single Vision 1.60,1.60,Clear,DuraVision Plus Platinum UV,grind,,Single vision,,yes,20001352,SV Grind 1.60,175.00,20002018,0.00,addon,350.00,350.00,,yes,name,,unchecked,,\n"
    "ZEISS,SmartLife Digital 1.60,1.60,Clear,DuraVision Plus Platinum UV,grind,,Anti-fatigue,,yes,20001600,Digital 1.60,225.00,20002018,0.00,addon,450.00,450.00,,yes,name,,unchecked,,\n"
    "Synchrony,FSV Clear 1.50,1.50,Clear,HMC+,stock,70,Single vision,,yes,20001311,Synchrony FSV 1.50,70.00,20001325,0.00,addon,140.00,140.00,,yes,name,,unchecked,,\n"
    "Synchrony,Progressive Ultra HDV 1.50,1.50,Clear,HMC+,grind,,Progressive,,yes,20001267,Ultra HDV 1.50,210.00,20001325,0.00,addon,420.00,420.00,,yes,code,,unchecked,,\n"
)

OLD = {9011: "2023-03-12", 9012: "2025-06-01"}       # saved Rx dates that are not today
PEOPLE = {
    9003: ("Mia", "ZZTEST", (_eye("-2.75", "-0.75", "180", pd="30"), _eye("-3.00", "-0.50", "10", pd="30")), ""),
    9005: ("Jo", "ZZTEST-HIGH", (_eye("-6.50", "-0.50", "180", pd="30"), _eye("-6.25", "-0.25", "170", pd="30")), ""),
    9001: ("Alex", "ZZTEST", (_eye("+1.00", "-0.50", "90", add="+2.00", pd="31.5"), _eye("+1.25", add="+2.00", pd="32")), ""),
    9007: ("Lee", "ZZTEST-DIGITAL", (_eye("-1.50", "-0.25", "180", add="+1.00", pd="31"), _eye("-1.75", add="+1.00", pd="31")), ""),
    9008: ("Ola", "ZZTEST-DIGI60", (_eye("-1.50", add="+0.60", pd="31"), _eye("-1.75", add="+0.60", pd="31")), ""),
    9009: ("Ray", "ZZTEST-DIGIDIFF", (_eye("-1.50", add="+0.75", pd="31"), _eye("-1.75", add="+1.00", pd="31")), ""),
    9010: ("Noa", "ZZTEST-NORX", (), ""),
    9011: ("Uri", "ZZTEST-OLDRX", (_eye("-2.00", "-0.50", "90", pd="31"), _eye("-2.25", pd="31")), ""),
    9012: ("Viv", "ZZTEST-RECENT", (_eye("-1.00", pd="31"), _eye("-1.25", pd="31")), ""),
    9013: ("Pat", "SAMPLE", (_eye("-1.00", pd="31"), _eye("-1.25", pd="31")), ""),
    9004: ("Priya", "ZZTEST-PRISM", (_eye("-1.00", pd="32", prism=("1.00", "IN", "", "")), _eye("-1.25", pd="32")), ""),
}
FRAMES = {"ZZFRAME5219": {"barcode": "ZZFRAME5219", "model": "ZZTEST Metal", "colour": "Gunmetal", "a": 52.0, "dbl": 19.0,
                          "ed": 57.0, "price": 179.0, "qoh": 1, "brand": "ZZ"},
          "ZZFRAME5319": {"barcode": "ZZFRAME5319", "model": "ZZTEST Panto", "colour": "Crystal", "a": 53.0, "dbl": 19.0,
                          "ed": 58.0, "price": 199.0, "qoh": 0, "brand": "ZZ"}}


class Bridge:
    def __call__(self, cfg, *args):
        if args[0] == "recent":
            return {"date": dt.date.today().isoformat(), "more": False, "fixture": True,
                    "patients": [{"id": pid, "given": g, "surname": s, "saved": "9:15am", "optom": "MJ"} for pid, (g, s, e, _) in PEOPLE.items()
                                 if e and pid not in OLD][:3]}
        if args[0] == "search":
            hits = [(pid, g, sn) for pid, (g, sn, _, _) in PEOPLE.items() if g.lower() in args[2].lower()] or [(9004, "Priya", "ZZTEST-PRISM")]
            return {"patients": [{"id": pid, "given": g, "surname": sn, "dob": "1970-06-30", "last_rx": "2026-11-10"} for pid, g, sn in hits], "more": False}
        if args[0] == "rx":
            pid = int(args[2])
            g, s, eyes, comment = PEOPLE[pid]
            saved = OLD.get(pid, dt.date.today().isoformat())
            right, left = (lb.parse_eye(e) for e in eyes) if eyes else (None, None)
            return {"patient": {"id": pid, "given": g, "surname": s, "dob": "1970-01-01"}, "frame": None,
                    "rxs": [] if not eyes else [{"rx_id": pid - 8500, "saved_at": f"{saved} 09:15:00", "saved": "9:15am", "saved_date": saved,
                             "rx_date": saved, "optom": "NK" if pid in OLD else "MJ", "right_only": False, "left_only": False, "right": right, "left": left,
                             "lens_type": lb.detect_type(RTF % comment, "", bool(right["add"] or left["add"]), False)}]}  # noqa: E501
        if args[0] == "frame":
            f = FRAMES.get(args[2])
            return {"frame": f} if f else {"error": "That frame barcode isn't in Optomate."}
        if args[0] == "frames":
            return {"frames": list(FRAMES.values()), "more": False}
        return {"error": "Bad command."}


@pytest.fixture
def c(tmp_path, monkeypatch):
    lenses_dir = tmp_path / "lenses"
    lenses_dir.mkdir()
    (lenses_dir / "test.csv").write_text(LENS_CSV, encoding="utf-8")
    agent = tmp_path / "agent"
    (agent / "pricing").mkdir(parents=True)
    (agent / "orders").mkdir()
    (agent / "orders" / "pickjob.py").write_text("", encoding="utf-8")
    (agent / "pricing" / "lens_link.csv").write_text(LINK_CSV, encoding="utf-8")
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "lens_filter.json").write_text('{"keep_only": {}, "preferred": {"match": [], "exclude": []}}', encoding="utf-8")
    (cfg / "integrations.json").write_text(json.dumps({"optomate_agent": {"agent_dir": str(agent)},
                                                       "checkin": {"staff_code": "2468"}, "pick_lens_job": {"show": True}}),
                                           encoding="utf-8")
    monkeypatch.setenv("CEC_HUB_LENSES_DIR", str(lenses_dir))
    monkeypatch.setenv("CEC_HUB_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("CEC_HUB_INTEGRATIONS", raising=False)
    monkeypatch.delenv("LENS_OPTIONS_FIXTURE", raising=False)
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    monkeypatch.setattr(lo, "run_bridge", Bridge())
    sent = []

    def engine(cfg_, command, request=None):
        sent.append((command, request))
        if command == "status":
            return {"dry_run": True, "write_live": False, "zztest_only": True, "fixture": True}
        return {"ok": True, "refusals": [], "warnings": [], "preview": {"patient": {"id": request["patient_id"], "name": "ZZTEST, Mia", "dob": "01/01/1970"}},
                "created": None, "why": "fixture"}
    monkeypatch.setattr(pk, "run_engine", engine)
    sj._sessions.clear()
    with app_module.app.test_client() as client:
        client.environ_base["REMOTE_ADDR"] = "10.0.0.9"       # the iPad, not the server itself
        client.sent = sent
        yield client
    sj._sessions.clear()


def unlock(c):
    """This iPad unlocked once (the Hub's own staff cookie), then Staff opens the list - no code each time."""
    if not c.get_cookie("cec_checkin_staff"):
        r = c.post("/api/checkin/staff/unlock", json={"code": "2468"})
        assert r.status_code == 200, r.get_json()
    r = c.post("/api/sheet/start")
    assert r.status_code == 200, r.get_json()


def pick(c, who="Mia"):
    unlock(c)
    pats = c.get("/api/sheet/patients").get_json()["patients"]
    h = next((p["h"] for p in pats if who in p["name"]), None)
    if h is None:
        h = c.post("/api/sheet/search", json={"q": "zztest " + who}).get_json()["patients"][0]["h"]
    r = c.post("/api/sheet/pick", json={"h": h})
    assert r.status_code == 200 and r.get_json() == {"ok": True}, r.get_json()


def body(**kw):
    b = {"version": sp.load()["version"], "choice": {"tab": "sv", "card": 0, "idx": "1.50", "opt": "clear"},
         "frame": {"barcode": "ZZFRAME5219"}}
    b.update(kw)
    return b


# ---- the one price file ------------------------------------------------------------------------
def test_every_sheet_choice_prices_and_maps_to_one_row_or_price_only():
    book = sp.load()
    n = 0
    for ch in sp.every_choice(book):
        q = sp.quote(book, ch)
        n += 1
        if not q["ok"]:
            assert q["why"]                                   # e.g. light-reactive not at 1.74 on Essential
            continue
        assert q["total"] == q["lens_pair"] + q["tint_pair"]
        m = q["map"]
        if m["ok"]:
            assert set(m["key"]) == {"supplier", "lens", "index", "material", "coating", "stockgrind"}
            assert "{idx}" not in m["key"]["lens"]
            if ch["opt"] == "tint":
                assert m["tint_barcode"]
        else:
            assert m["why"]
    assert n > 900


def test_sheet_prices_are_the_sheets_own_and_tints_split_the_way_optomate_charges():
    book = sp.load()
    q = sp.quote(book, {"tab": "sv", "card": 0, "idx": "1.50", "opt": "tint", "colour": "Black", "depth": "s85"})
    assert (q["total"], q["lens_pair"], q["tint_pair"], q["made"]) == (350, 300, 50, "mto")
    assert q["map"]["tint_barcode"] == "20002054" and q["map"]["key"]["stockgrind"] == "grind"
    g = sp.quote(book, {"tab": "sv", "card": 0, "idx": "1.50", "opt": "tint", "colour": "Green", "depth": "g9040"})
    assert (g["total"], g["tint_pair"], g["map"]["tint_barcode"]) == (370, 70, "20002199")
    # a fix is RE-PRICED by the sheet's rules, never by adding a difference: blue-light 1.67 already includes made to order
    b67 = sp.quote(book, {"tab": "sv", "card": 2, "idx": "1.67", "opt": "blue", "made": "mto"})
    assert b67["total"] == 500
    assert sp.quote(book, {"tab": "sv", "card": 0, "idx": "1.50", "opt": "clear", "made": "mto"})["total"] == 300
    assert sp.quote(book, {"tab": "mf", "card": 1, "idx": "1.53", "opt": "clear"})["total"] == 610
    off = sp.quote(book, {"tab": "mf", "card": 0, "idx": "1.74", "opt": "photo"})
    assert not off["ok"] and "1.74" in off["why"]
    for tab in ("sun", "myo"):
        assert not sp.quote(book, {"tab": tab, "card": 0, "idx": "", "opt": "clear"})["map"]["ok"]


def test_the_page_carries_the_price_file_and_its_version_and_no_demo_data():
    import price_sheet.build as build
    html = build.build()
    assert '"version": "%s"' % sp.load()["version"] in html
    for banned in ("ZZTEST", "staff=demo", "Ray-Ban", "PATS"):
        assert banned not in html
    assert "/api/sheet/checks" in html


# ---- the lock ----------------------------------------------------------------------------------
def test_no_code_each_time_but_only_an_unlocked_ipad(c):
    # Mark, 10 Nov 2026: "Take out test codes I don't think I'll give it to patients ever"
    r = c.post("/api/sheet/start")                                     # a browser never unlocked: refused
    assert r.status_code == 403 and r.get_json()["staff_needed"]
    assert c.get("/api/sheet/patients").status_code == 403
    assert c.post("/api/checkin/staff/unlock", json={"code": "1111"}).status_code == 403
    assert c.post("/api/sheet/start", headers={"X-Checkin-Key": "x"}).status_code == 403   # never the check-in key
    unlock(c)
    r = c.get("/api/sheet/patients")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    p = r.get_json()["patients"][0]
    assert set(p) == {"h", "name", "time", "optom", "type", "two", "stop"} and "id" not in p     # no patient number, no Rx


def test_picking_ties_the_ipad_and_locks_everything_else(c):
    pick(c, "Mia")
    assert c.get("/api/sheet/patients").status_code == 403
    assert c.post("/api/sheet/search", json={"q": "zztest"}).status_code == 403
    r = c.get("/api/lensoptions/recent")                                  # every other staff route
    assert r.status_code == 403 and r.get_json()["sheet_locked"]
    assert c.post("/api/sheet/next").status_code == 200
    assert c.get("/api/sheet/patient").status_code == 401


def test_the_server_uses_its_own_patient_not_the_pages(c):
    pick(c, "Mia")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    r = c.post("/api/sheet/plan", json=body(patient_id=9005, choice={"tab": "sv", "card": 0, "idx": "1.50", "opt": "clear"}))
    cmd, req = c.sent[-1]
    assert cmd == "plan" and req["patient_id"] == 9003 and req["rx_id"] == 503
    assert "dob" not in json.dumps(r.get_json()["preview"]["patient"])


def test_list_stays_open_until_idle_and_reopens_without_a_code(c, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(sj, "_now", lambda: t[0])
    unlock(c)
    t[0] += 200                                                        # no 2-minute window any more
    assert c.get("/api/sheet/patients").status_code == 200
    t[0] += sj.IDLE_S + 1
    assert c.get("/api/sheet/patients").status_code == 401           # 5 minutes untouched: cleared
    assert c.post("/api/sheet/start").status_code == 200             # Staff again: straight back to the list
    assert c.get("/api/sheet/patients").status_code == 200


def test_next_patient_then_staff_needs_no_code(c):
    pick(c, "Mia")
    assert c.post("/api/sheet/next").status_code == 200
    assert c.post("/api/sheet/start").status_code == 200
    assert c.get("/api/sheet/patients").status_code == 200


# ---- screen 3: the patient's view ------------------------------------------------------------------
def test_sheet_answer_has_first_name_and_marks_only(c):
    pick(c, "Mia")
    d = c.get("/api/sheet/patient").get_json()
    text = json.dumps(d)
    assert d["first"] == "Mia" and d["tab"] == "sv"
    for leak in ("ZZTEST", "-2.75", "-3.00", "1970", "MJ", "PD", "prism", "Check the Rx"):
        assert leak not in text
    sv = d["tabs"]["sv"]["cards"]
    assert sv[0]["1.50"]["state"] == "stock" and sv[0]["1.50"]["price"] == 200
    assert sv[0]["1.50"]["opts"]["blue"] == "stock" and sv[0]["1.50"]["opts"]["tint"] == "mto"


def test_high_minus_shows_the_made_to_order_price_up_front(c):
    pick(c, "Jo")
    sv = c.get("/api/sheet/patient").get_json()["tabs"]["sv"]
    assert sv["cards"][0]["1.50"]["state"] == "mto" and sv["cards"][0]["1.50"]["price"] == 300
    assert sv["cards"][1]["1.60"]["state"] == "mto"            # -6.50 is past the 1.60 stock band in these books
    assert sv["cards"][2]["1.67"]["state"] == "none"           # no 1.67 in these test books
    assert "1.50" in sv["thicker"] or sv["rec"] == ""


def test_prism_means_no_stock_lens_and_no_pd_change(c):
    pick(c, "Priya")
    sv = c.get("/api/sheet/patient").get_json()["tabs"]["sv"]
    assert sv["cards"][0]["1.50"]["state"] == "mto"
    r = c.post("/api/sheet/checks", json=body(frame={"barcode": "ZZFRAME5319"})).get_json()
    assert r["made"] == "Made to order" and not any(f["kind"] == "pd" for f in r["fixes"])
    assert any("Prism on the Rx" in w for w in r["rx"]["warnings"])


def test_presbyope_multifocal_marks_and_no_add_means_ask_us(c):
    pick(c, "Alex")
    tabs = c.get("/api/sheet/patient").get_json()["tabs"]
    assert tabs["mf"]["cards"][0]["1.50"]["state"] == "mto"
    pick_low = None
    c.post("/api/sheet/next")
    pick(c, "Mia")
    tabs = c.get("/api/sheet/patient").get_json()["tabs"]
    assert tabs["mf"]["plain"] and tabs["mf"]["note"] == "Ask us about these lenses."
    assert pick_low is None


# ---- screen 5: checks and fixes, all from the server -------------------------------------------------
def test_checks_fit_and_make_mode_words(c):
    pick(c, "Mia")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    r = c.post("/api/sheet/checks", json=body()).get_json()
    assert r["verdict"]["tone"] == "green" and r["verdict"]["head"] == "Fits a 70 mm stock blank"
    assert r["can_make"] and r["mode"]["button"] == "Make job (dry run, nothing written)"
    assert r["ref"]["blank_mm"] == 70.0 and r["price"] == 200


def test_too_small_offers_pd_then_stock_then_made_to_order_each_repriced(c):
    pick(c, "Mia")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5319"})       # needs 72 mm: 1.50 comes 70
    r = c.post("/api/sheet/checks", json=body(frame={"barcode": "ZZFRAME5319"})).get_json()
    assert r["verdict"]["tone"] == "amber" and not r["can_make"] and r["why"] == "Pick a fix for the blank."
    kinds = [f["kind"] for f in r["fixes"]]
    assert kinds[0] == "pd" and kinds.index("stock") < kinds.index("mto")
    prices = {f["kind"] + f.get("idx", ""): f["price"] for f in r["fixes"]}
    assert prices == {"pd": 200, "stock1.60": 250, "mto": 300}
    assert "Optomate shows none on hand." in r["warnings"]
    pd = c.post("/api/sheet/checks", json=body(frame={"barcode": "ZZFRAME5319"}, fix={"kind": "pd"})).get_json()
    assert pd["can_make"] and pd["pd_shift"] == {"R": 1.0, "L": 1.0} and pd["price"] == 200
    up = c.post("/api/sheet/checks", json=body(frame={"barcode": "ZZFRAME5319"}, fix={"kind": "stock", "idx": "1.60"})).get_json()
    assert up["price"] == 250 and not up["can_make"] and "Told the patient" in up["why"]
    assert any(f["on"] for f in up["fixes"])                                # the picked fix still shows
    told = c.post("/api/sheet/checks", json=body(frame={"barcode": "ZZFRAME5319"}, fix={"kind": "stock", "idx": "1.60"}, told=True)).get_json()
    assert told["can_make"] and told["ref"]["index"] == "1.60"


def test_blue_option_kept_when_the_fix_reprices(c):
    pick(c, "Mia")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5319"})
    ch = {"tab": "sv", "card": 0, "idx": "1.50", "opt": "blue"}
    r = c.post("/api/sheet/checks", json=body(frame={"barcode": "ZZFRAME5319"}, choice=ch, fix={"kind": "mto"})).get_json()
    assert r["price"] == 350 and r["option"] == "Blue-light filter" and r["made"] == "Made to order"


def test_rimless_only_153_160_167(c):
    pick(c, "Mia")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    r = c.post("/api/sheet/checks", json=body(rimless=True)).get_json()
    assert not r["can_make"] and "Rimless frame: 1.53, 1.60 or 1.67 only" in r["stop"]
    ok = c.post("/api/sheet/checks", json=body(rimless=True, choice={"tab": "sv", "card": 1, "idx": "1.60", "opt": "clear"})).get_json()
    assert ok["can_make"], ok["why"]


def test_heights_needed_for_digital_by_its_category(c):
    pick(c, "Lee")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    ch = {"tab": "af", "card": 1, "idx": "1.60", "opt": "clear"}
    r = c.post("/api/sheet/checks", json=body(choice=ch)).get_json()
    assert r["heights_needed"] and not r["can_make"] and "heights" in r["why"]
    ok = c.post("/api/sheet/checks", json=body(choice=ch, heights={"R": "20", "L": "19.5"})).get_json()
    assert ok["can_make"] and ok["lens_use"] == "M"


def test_stale_page_prices_refused(c):
    pick(c, "Mia")
    r = c.post("/api/sheet/checks", json=body(version="old"))
    assert r.status_code == 409 and r.get_json()["error"] == "Prices were updated. Reload."


def test_engine_gets_the_servers_prices_and_tint(c):
    pick(c, "Mia")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    ch = {"tab": "sv", "card": 0, "idx": "1.50", "opt": "tint", "colour": "Brown", "depth": "g75"}
    c.post("/api/sheet/plan", json=body(choice=ch, hub_pair_price="1"))
    cmd, req = c.sent[-1]
    assert req["hub_pair_price"] == "300" and req["sheet_tint_pair"] == 70 and req["tint_barcode"] == "20002083"
    assert req["option"]["stockgrind"] == "grind" and req["option"]["lens"] == "ClearView Single Vision 1.50"


def test_not_linked_lens_is_make_in_touch(c):
    pick(c, "Alex")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    r = c.post("/api/sheet/checks", json=body(choice={"tab": "office", "card": 0, "idx": "1.50", "opt": "clear"})).get_json()
    assert not r["can_make"] and ("Touch" in r["stop"] or "Not made" in r["stop"])


# ---- frame search by name (read-only bridge) ----------------------------------------------------
def test_frame_name_search_is_a_select_with_safe_words_only(monkeypatch):
    q = lb.frames_search_query(["black", "52"])
    assert q.startswith("SELECT TOP 13 ") and "LIKE N'%black%'" in q and "LIKE N'%52%'" in q
    for bad in ("x'; DROP", "50%", "a_b"):
        with pytest.raises(lb.BridgeError):
            lb.cmd_frames(bad, sql=lambda q: [])
    monkeypatch.setenv("LENS_OPTIONS_FIXTURE", "1")
    got = lb.cmd_frames("zztest round")
    assert [f["barcode"] for f in got["frames"]] == ["ZZFRAME5420"] and got["frames"][0]["qoh"] == 0


def test_lens_options_pd_shift_stops_for_prism():
    assert lo._shift_ctx({"kind": "Single vision", "pd_exact": True, "prism": True}, {"frame": {"a": 52}}) is None


# ---- the small Synchrony single vision tab (Mark, 10 Nov 2026: the sheet as a portal for any lens) ----
def test_synchrony_sv_prices_are_copied_per_card_never_worked_out():
    book = sp.load()
    q = lambda **kw: sp.quote(book, {"tab": "syn", "opt": "clear", **kw})   # noqa: E731
    assert q(card=0, idx="1.50")["total"] == 140 and q(card=0, idx="1.50", made="mto")["total"] == 200
    assert [c["idx"] for c in sp.page(book, "syn")["cards"]] == ["1.50", "1.56"]    # from 1.60: ZEISS (Mark, 10 Nov)
    assert q(card=1, idx="1.56")["total"] == 140 and not q(card=1, idx="1.56", made="mto")["ok"]   # 1.56: stock only
    k = q(card=0, idx="1.50", made="mto")["map"]["key"]
    assert (k["supplier"], k["lens"], k["coating"], k["stockgrind"]) == ("Synchrony", "Single Vision Grind 1.50", "HMC+", "grind")
    assert q(card=0, idx="1.50")["map"]["key"]["lens"] == "FSV Clear 1.50"


def test_synchrony_sv_tab_is_small_and_last():
    pages = sp.load()["data"]["pages"]
    assert pages[-1]["key"] == "syn" and pages[-1]["small"] is True
    assert [p["key"] for p in pages[:-1]] == ["sv", "mf", "af", "sun", "myo", "office", "bif"]


def test_synchrony_sv_marks_and_job(c):
    pick(c, "Mia")
    syn = c.get("/api/sheet/patient").get_json()["tabs"]["syn"]
    assert syn["cards"][0]["1.50"]["state"] == "stock" and syn["cards"][1]["1.56"]["state"] == "none"
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    r = c.post("/api/sheet/checks", json=body(choice={"tab": "syn", "card": 0, "idx": "1.50", "opt": "clear"})).get_json()
    assert r["can_make"], r["why"]
    assert r["price"] == 140 and r["ref"]["lens"] == "FSV Clear 1.50" and r["lens_use"] == "D"
    c.post("/api/sheet/plan", json=body(choice={"tab": "syn", "card": 0, "idx": "1.50", "opt": "clear"}))
    cmd, req = c.sent[-1]
    assert cmd == "plan" and req["hub_pair_price"] == "140" and req["option"]["supplier"] == "Synchrony"



# ---- SmartLife Digital add (Mark, 10 Nov 2026: +0.50 to +1.25 by 0.25 steps) -----------------------------
DIGI = {"tab": "af", "card": 1, "idx": "1.60", "opt": "clear"}


@pytest.mark.parametrize("who,ok", [("Lee", True), ("Ola", False), ("Ray", False), ("Mia", False), ("Alex", False)])
def test_digital_needs_an_rx_add_of_050_to_125_same_both_eyes(c, who, ok):
    pick(c, who)                       # Lee +1.00 / Ola +0.60 / Ray +0.75 and +1.00 / Mia no add / Alex +2.00
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    b = body(choice=DIGI, heights={"R": "20", "L": "20"})
    r = c.post("/api/sheet/checks", json=b).get_json()
    assert r["can_make"] is ok, r.get("why")
    if not ok:
        assert r["stop"] == sj.DIGITAL_STOP
        for route in ("/api/sheet/plan", "/api/sheet/make"):
            n = len(c.sent)
            j = c.post(route, json=b).get_json()
            assert j["ok"] is False and j["refusals"] == [sj.DIGITAL_STOP]
            assert [x for x in c.sent[n:] if x[0] in ("plan", "make")] == []


def test_digital_add_rule_leaves_the_patient_sheet_alone(c):
    pick(c, "Ola")
    af = c.get("/api/sheet/patient").get_json()["tabs"]["af"]
    assert af["cards"][1]["1.60"]["state"] == "mto"



# ---- no saved prescription (Mark, 10 Nov 2026: "a pathway where this will work without a saved prescription") ----
def _search_pick(c, words):
    unlock(c)
    return c.post("/api/sheet/pick", json={"h": c.post("/api/sheet/search", json={"q": words}).get_json()["patients"][0]["h"]}).get_json()


def test_an_older_rx_needs_staff_to_confirm_and_is_flagged_over_two_years(c):
    r = _search_pick(c, "zztest uri")
    conf = r["confirm"]
    assert conf["date"] == "12 Mar 2023" and conf["optom"] == "NK" and conf["old"] is True
    assert c.get("/api/sheet/patient").status_code == 403                  # nothing shows until staff choose
    assert c.post("/api/sheet/pick", json={"h": conf["use"]}).get_json() == {"ok": True}
    d = c.get("/api/sheet/patient").get_json()
    assert not d.get("no_rx") and d["tabs"]["sv"]["cards"][0]["1.50"]["state"] == "stock"
    c.post("/api/sheet/next")
    recent = _search_pick(c, "zztest viv")["confirm"]
    assert recent["old"] is False and recent["date"] == "1 Jun 2025"


def test_no_rx_job_needs_the_tick_carries_no_rx_and_is_flagged(c):
    r = _search_pick(c, "zztest noa")
    assert r == {"ok": True}
    d = c.get("/api/sheet/patient").get_json()
    assert d["no_rx"] and d["tabs"] == {} and d["first"] == "Noa"
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    ch = {"tab": "sv", "card": 1, "idx": "1.60", "opt": "clear"}
    k = c.post("/api/sheet/checks", json=body(choice=ch)).get_json()
    assert k["no_rx"] and not k["can_make"] and k["why"] == sj.NO_RX_WHY
    assert k["verdict"]["head"] == "Not checked - no Rx" and "not checked" in k["made"] and k["price"] == 250
    assert k["ref"]["blank_mm"] == 70.0 and "VisuStore picks the blank" in k["verdict"]["detail"]   # the 70 mm item
    assert k["rx"]["right"] == "No saved Rx" and k["frame"]["barcode"] == "ZZFRAME5219"
    n = len(c.sent)
    j = c.post("/api/sheet/make", json=body(choice=ch)).get_json()                 # no tick: refused, engine not asked
    assert j["ok"] is False and j["refusals"] == [sj.NO_RX_WHY] and not [x for x in c.sent[n:] if x[0] == "make"]
    ok = c.post("/api/sheet/checks", json=body(choice=ch, no_rx_ok=True)).get_json()
    assert ok["can_make"], ok["why"]
    j = c.post("/api/sheet/make", json=body(choice=ch, no_rx_ok=True)).get_json()
    cmd, req = c.sent[-1]
    assert cmd == "make" and req["no_rx"] is True and req["no_rx_ack"] is True and req["rx_id"] is None
    assert req["pd_shift"] is None and req["hub_pair_price"] == "250" and j["no_rx"] is True


def test_skipping_an_older_rx_is_price_only(c):
    conf = _search_pick(c, "zztest uri")["confirm"]
    c.post("/api/sheet/pick", json={"h": conf["skip"]})
    assert c.get("/api/sheet/patient").get_json()["no_rx"] is True


def test_the_choice_is_kept_for_the_patient_today_and_comes_back(c):
    _search_pick(c, "zztest noa")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    ch = {"tab": "sv", "card": 1, "idx": "1.60", "opt": "tint", "colour": "Brown", "depth": "g75"}
    c.post("/api/sheet/checks", json=body(choice=ch, rimless=True))
    c.post("/api/sheet/next")
    _search_pick(c, "zztest noa")                                            # reopened later the same day
    res = c.get("/api/sheet/patient").get_json()["resume"]
    assert res["choice"] == {"tab": "sv", "card": 1, "idx": "1.60", "opt": "tint", "colour": "Brown", "depth": "g75", "pfx": "Grey"}
    assert res["frame"]["barcode"] == "ZZFRAME5219" and res["rimless"] is True
    sj._kept.clear()
    c.post("/api/sheet/next")
    _search_pick(c, "zztest noa")
    assert c.get("/api/sheet/patient").get_json()["resume"] is None


def test_page_has_the_old_rx_question_and_resume():
    import price_sheet.build as build
    html = build.build()
    assert "Use this Rx" in html and "Over 2 years old" in html and "resumeChoice" in html
    assert "NO RX on this job" in html and "norxok" in html
    assert "Next, ask us to scan your frame." in html


# ---- real frames for testing (Mark, 10 Nov 2026: "the frame check needs to be built so i can test it out") ----
def test_frame_search_matches_brand_words_with_spaces_and_hyphens_ignored():
    q = lb.frames_search_query(["ray-ban"])
    assert "REPLACE(REPLACE(REPLACE(ISNULL(f.BRAND_IDENTIFIER,'')" in q and "LIKE N'%rayban%'" in q


def test_a_real_barcode_with_a_space_is_looked_up(monkeypatch):
    seen = []
    row = ["RB 3447V", "344V", "gun metal", "5021", "0", "0", "LUX", "2", "207.0000", "1", "RAYBAN"]
    got = lb.cmd_frame("RB 3447V", sql=lambda q: seen.append(q) or [row])
    assert got["frame"]["barcode"] == "RB 3447V" and got["frame"]["ed"] is None and got["frame"]["brand"] == "RAYBAN"
    assert "N'RB 3447V'" in seen[0]


def test_fixture_copy_reads_real_frames_only_when_switched_on(monkeypatch):
    monkeypatch.setenv("LENS_OPTIONS_FIXTURE", "1")
    calls = []
    monkeypatch.setattr(lb, "sqlq", lambda q: calls.append(q) or [])
    monkeypatch.delenv("CEC_HUB_REAL_FRAMES", raising=False)
    lb.cmd_frames("zztest")
    assert calls == []                                              # plain fixture copy: test frames only
    monkeypatch.setenv("CEC_HUB_REAL_FRAMES", "1")
    got = lb.cmd_frames("zztest")
    assert len(calls) == 1 and calls[0].startswith("SELECT")         # read only
    assert all(f["brand"] == "ZZ TEST FRAME" for f in got["frames"])  # test frames labelled


def test_no_ed_on_the_frame_is_estimated_from_eye_size_and_said_so(c, monkeypatch):
    FRAMES["RB 3447V"] = {"barcode": "RB 3447V", "model": "344V", "colour": "gun metal", "a": 50.0, "dbl": 21.0, "ed": None,
                          "price": 207.0, "qoh": 1, "brand": "RAYBAN"}
    try:
        pick(c, "Mia")
        c.post("/api/sheet/frame", json={"barcode": "RB 3447V"})
        r = c.post("/api/sheet/checks", json=body(frame={"barcode": "RB 3447V"})).get_json()
        assert r["blank"]["approx"] and "ED estimated from eye size" in r["blank"]["ed_note"] and r["verdict"]["tone"] == "green"
        t = c.post("/api/sheet/checks", json=body(frame={"barcode": "RB 3447V", "ed_typed": "60"})).get_json()
        assert not t["blank"]["approx"] and t["blank"]["ed_note"] == "ED typed by staff."
    finally:
        FRAMES.pop("RB 3447V", None)



# ---- gate C: the Hub refuses Make for anyone not ZZTEST while zztest_only is on (default ON) ----------------
def test_hub_refuses_a_job_for_anyone_not_zztest(c):
    pick(c, "Pat")                                                     # surname SAMPLE
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    n = len(c.sent)
    for route in ("/api/sheet/plan", "/api/sheet/make"):
        j = c.post(route, json=body()).get_json()
        assert j["ok"] is False and j["refusals"] == ["Test mode: jobs can only be made for the ZZTEST test patient for now."]
    assert [x for x in c.sent[n:] if x[0] in ("plan", "make")] == []   # the engine is never asked
    assert sj.zztest_only({}) is True and sj.zztest_only({"pick_lens_job": {"zztest_only": False}}) is False


def test_hub_lets_a_zztest_patient_through(c):
    pick(c, "Mia")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    c.post("/api/sheet/make", json=body())
    assert c.sent[-1][0] == "make" and c.sent[-1][1]["patient_id"] == 9003



# ---- one Optomate item per stock lens: the 70 mm one (Mark, 10 Nov 2026: "just choose 70mm one") ----------
def test_a_stock_job_orders_the_70mm_item_even_when_the_frame_needs_75(c):
    pick(c, "Mia")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5319"})                       # needs 72 mm
    ch = {"tab": "sv", "card": 1, "idx": "1.60", "opt": "clear"}
    r = c.post("/api/sheet/checks", json=body(choice=ch, frame={"barcode": "ZZFRAME5319"})).get_json()
    assert r["verdict"]["head"] == "Fits a 75 mm stock blank"                           # the check on screen stays
    assert r["ref"]["blank_mm"] == 70.0 and r["can_make"]                               # the item ordered is the 70 mm one
    c.post("/api/sheet/plan", json=body(choice=ch, frame={"barcode": "ZZFRAME5319"}))
    assert c.sent[-1][1]["option"]["blank_mm"] == 70.0


def test_a_lens_with_no_70mm_item_uses_its_standard_stock_item(monkeypatch):
    rows = [{"supplier": "Synchrony", "lens": "FSV Clear 1.56", "index": "1.56", "material": "Clear", "coating": "HMC+",
             "stockgrind": "stock", "blank_mm": b, "lens_barcode": "20001305"} for b in ("65", "73")]
    monkeypatch.setattr(pk, "links", lambda cfg: rows)
    key = {"supplier": "Synchrony", "lens": "FSV Clear 1.56", "index": "1.56", "material": "Clear", "coating": "HMC+", "stockgrind": "stock"}
    assert sj._item_blank({}, key) == 73.0                                           # closest to 70; same barcode either way
    assert sj._item_blank({}, {**key, "stockgrind": "grind"}) == ""


# ---- first live ZZTEST job (10 Nov 2026): the chosen option reaches the engine, or the job stops ----------------
def test_photofusion_maps_to_its_own_lens_item_and_colour_note_never_a_tint():
    book = sp.load()
    st_ = sp.quote(book, {"tab": "sv", "card": 1, "idx": "1.60", "opt": "photo", "pfx": "Grey"})
    assert st_["map"]["key"]["material"] == "PhotoFusionX" and st_["map"]["key"]["stockgrind"] == "stock"
    assert st_["map"]["tint_barcode"] == "" and st_["map"]["pfx_colour"] == "Grey"
    g = sp.quote(book, {"tab": "sv", "card": 1, "idx": "1.60", "opt": "photo", "pfx": "Brown"})
    assert g["map"]["key"]["stockgrind"] == "grind" and g["map"]["key"]["material"] == "PhotoFusionX"
    assert g["map"]["tint_barcode"] == "" and g["map"]["pfx_colour"] == "Brown"


def test_an_unlinked_photofusion_stock_lens_stops_it_never_goes_as_clear(c):
    pick(c, "Mia")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    ch = {"tab": "sv", "card": 0, "idx": "1.50", "opt": "photo", "pfx": "Grey"}
    r = c.post("/api/sheet/checks", json=body(choice=ch)).get_json()
    assert not r["can_make"] and r["stop"]                                   # stops; never falls back to clear
    n = len(c.sent)
    j = c.post("/api/sheet/make", json=body(choice=ch)).get_json()
    assert j["ok"] is False and [x for x in c.sent[n:] if x[0] == "make"] == []


def test_the_engine_gets_the_option_the_page_chose(c):
    pick(c, "Mia")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5219"})
    ch = {"tab": "sv", "card": 0, "idx": "1.50", "opt": "blue"}
    c.post("/api/sheet/plan", json=body(choice=ch))
    req = c.sent[-1][1]
    assert req["option"]["material"] == "Clear BluePro" and req["hub_pair_price"] == "250"


def test_page_keeps_the_plain_sheet_choice_through_staff_and_shows_it_on_check():
    import price_sheet.build as build
    html = build.build()
    assert "st.lastChoice" in html and "staffbtn2" in html and "Sheet choice" in html and 'id="resumed"' in html


def test_a_widened_pd_shows_as_its_own_row_on_the_job(c):
    pick(c, "Mia")
    c.post("/api/sheet/frame", json={"barcode": "ZZFRAME5319"})
    r = c.post("/api/sheet/checks", json=body(frame={"barcode": "ZZFRAME5319"}, fix={"kind": "pd"})).get_json()
    assert r["pd_job"] == {"to": {"R": 31.0, "L": 31.0}, "from": {"R": 30.0, "L": 30.0}}
    plain = c.post("/api/sheet/checks", json=body()).get_json()
    assert "pd_job" not in plain
