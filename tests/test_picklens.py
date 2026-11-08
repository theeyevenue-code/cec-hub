"""Pick a lens -> make the job (hub/pickjob.py + routes + lens_bridge frame). Synthetic data only:
the engine subprocess and the Optomate bridge are faked; nothing touches Optomate."""
import importlib
import json
import subprocess

import pytest

from hub import lens_bridge as lb
from hub import lensoptions as lo
from hub import pickjob as pk
from tests.test_lensoptions import FakeBridge, LENS_CSV

LINK_CSV = (
    "supplier,lens,index,material,coating,stockgrind,blank_mm,category,pb_codes,orderable,lens_barcode,lens_description,"
    "lens_sellinc,coating_addon_barcode,addon_sellinc,coat_in,optomate_pair,hub_pair_price,hub_price_note,price_agree,"
    "suggest_method,candidates,status,checked_by,checked_on\n"
    "Synchrony,FSV Clear 1.50,1.50,,,stock,65,Single vision,,yes,20001311,Synchrony FSV 1.50 HMC+,70.00,,,lens,140.00,140.00,,yes,name,,correct,Mark,2026-11-08\n"
    "Synchrony,SV Grind 1.50,1.50,,,grind,,Single vision,,yes,20001185,Synchrony SV Grind 1.50 HMC+,100.00,,,lens,200.00,200.00,,yes,name,,unchecked,,\n"
    "Synchrony,SV Grind 1.60,1.60,,,grind,,Single vision,,yes,20001186,Synchrony SV Grind 1.60 HMC+,120.00,,,lens,240.00,250.00,,no,name,,unchecked,,\n"
    "Synchrony,Progressive Ultra HDV 1.50,1.50,,,grind,,Progressive,,yes,20001267,Synchrony Progressive Ultra HDV 1.50,210.00,,,lens,420.00,420.00,,yes,code,,unchecked,,\n"
    "ZEISS,Chrome Thing 1.60,1.60,Clear,DuraVision Plus Chrome UV,grind,,Progressive,,yes,20009999,ZEISS x,300.00,,,not linked,600.00,600.00,,yes,name,,unchecked,,\n"
    "ZEISS,Wrong Thing 1.60,1.60,Clear,DuraVision Plus Platinum UV,grind,,Progressive,,yes,20009998,ZEISS y,300.00,20002018,0.00,addon,600.00,600.00,,yes,name,,wrong,Mark,2026-11-08\n"
)


def _opt(**kw):
    o = {"supplier": "Synchrony", "raw_name": "FSV Clear 1.50", "name": "FSV Clear", "index": "1.50", "material": "",
         "coating": "", "type": "Stock", "blank_mm": 65, "sell": 140.0, "cost": 10.0, "fits": None, "thicker": False}
    o.update(kw)
    return o


def _rows():
    import csv
    import io
    return [dict(r) for r in csv.DictReader(io.StringIO(LINK_CSV))]


# --- the hidden switch -------------------------------------------------------------------

def test_enabled_needs_the_flag_or_the_env(monkeypatch):
    monkeypatch.delenv("CEC_HUB_PICK_LENS", raising=False)
    assert not pk.enabled({})
    assert not pk.enabled({"pick_lens_job": {"show": False}})
    assert pk.enabled({"pick_lens_job": {"show": True}})
    monkeypatch.setenv("CEC_HUB_PICK_LENS", "1")
    assert pk.enabled({})


# --- link table ----------------------------------------------------------------------------

def test_link_for_ready_touch_price_and_coat():
    rows = _rows()
    ok = pk.link_for(rows, _opt())
    assert ok["ready"] and ok["barcode"] == "20001311" and ok["words"] == "Ready for Optomate"
    # another blank of the same stock lens is still that lens
    assert pk.link_for(rows, _opt(blank_mm=70))["barcode"] == "20001311"
    no = pk.link_for(rows, _opt(raw_name="SV Grind 1.60", index="1.60", type="Grind", blank_mm=None))
    assert not no["ready"] and "Prices don't agree" in no["why"] and "$240.00" in no["why"] and "$250.00" in no["why"]
    coat = pk.link_for(rows, _opt(supplier="ZEISS", raw_name="Chrome Thing 1.60", index="1.60", material="Clear", coating="DuraVision Plus Chrome UV", type="Grind"))
    assert not coat["ready"] and "no Optomate add-on" in coat["why"]
    wrong = pk.link_for(rows, _opt(supplier="ZEISS", raw_name="Wrong Thing 1.60", index="1.60", material="Clear", coating="DuraVision Plus Platinum UV", type="Grind"))
    assert not wrong["ready"] and wrong["words"] == "Make this job in Touch"
    assert not pk.link_for(rows, _opt(raw_name="Nothing"))["ready"]


def test_option_ref_carries_the_key_only():
    ref = pk.option_ref(_opt())
    assert ref == {"supplier": "Synchrony", "lens": "FSV Clear 1.50", "index": "1.50", "material": "",
                   "coating": "", "stockgrind": "stock", "blank_mm": 65}
    assert "sell" not in ref


# --- best options and next best ------------------------------------------------------------

def test_rank_options_cheapest_that_fits_first_thicker_and_too_small_later():
    listed = [_opt(raw_name="A", sell=300.0, fits=True, link={"ready": True}),
              _opt(raw_name="B", sell=140.0, fits=False, link={"ready": True}),          # too small for the frame
              _opt(raw_name="C", sell=200.0, fits=True, link={"ready": True}),
              _opt(raw_name="D", sell=150.0, fits=True, thicker=True, link={"ready": True}),
              _opt(raw_name="E", sell=250.0, type="Grind", fits=True, link={"ready": False})]
    r = pk.rank_options(listed, rec="1.60", has_frame=True)
    assert [o["raw_name"] for o in r["best"]] == ["C", "A", "E"]
    assert [o["raw_name"] for o in r["rest"]] == ["B", "D"]
    assert r["best"][0]["why"].startswith("Cheapest for this Rx")
    assert "made to order" in r["best"][2]["why"].lower()


def test_rank_options_without_a_frame_says_stock_blank():
    r = pk.rank_options([_opt(raw_name="A", sell=140.0)], rec="1.50", has_frame=False)
    assert r["best"][0]["why"] == "Cheapest for this Rx, stock blank, suggested thickness for this power"


def test_next_best_prefers_a_stock_lens_that_fits_then_grind():
    picked = _opt(raw_name="P", type="Stock", fits=False, blank_line="Needs 70 mm - this lens only comes 65 mm", link={"ready": True})
    listed = [picked,
              _opt(raw_name="G", type="Grind", sell=200.0, fits=True, link={"ready": True}),
              _opt(raw_name="S", type="Stock", sell=220.0, fits=True, link={"ready": True}),
              _opt(raw_name="T", type="Stock", sell=100.0, fits=False, link={"ready": True}),
              _opt(raw_name="U", type="Stock", sell=90.0, fits=True, link={"ready": False})]
    nb = pk.next_best(listed, picked, has_frame=True)
    assert nb["problem"].startswith("Needs 70 mm")
    assert [o["raw_name"] for o in nb["suggest"]] == ["S", "U", "G"]      # stock that fits (ready first), then grind
    assert nb["suggest"][0]["why"] == "Stock blank that fits" and nb["suggest"][2]["why"].startswith("Made to order")


def test_next_best_for_a_lens_optomate_does_not_know_offers_ready_ones_only():
    picked = _opt(raw_name="P", fits=True, link={"ready": False, "why": "Not linked to Optomate yet"})
    listed = [picked, _opt(raw_name="A", sell=300.0, fits=True, link={"ready": True}),
              _opt(raw_name="B", sell=100.0, fits=True, link={"ready": False})]
    nb = pk.next_best(listed, picked, True)
    assert nb["problem"] == "Not linked to Optomate yet" and [o["raw_name"] for o in nb["suggest"]] == ["A"]


def test_next_best_is_quiet_when_the_pick_fits():
    picked = _opt(raw_name="P", fits=True, link={"ready": True})
    nb = pk.next_best([picked, _opt(raw_name="G", type="Grind")], picked, True)
    assert nb["problem"] == "" and nb["suggest"] == []
    assert pk.next_best([], None, True) is None
    assert pk.next_best([_opt(raw_name="X")], _opt(raw_name="Gone"), True)["problem"] == "This lens doesn't cover this Rx"


# --- the engine subprocess -----------------------------------------------------------------

class Seen:
    def __init__(self, answer):
        self.answer, self.calls, self.raise_exc = answer, [], None

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if self.raise_exc:
            raise self.raise_exc
        return subprocess.CompletedProcess(args, 0, stdout="noise\n" + json.dumps(self.answer) + "\n", stderr="")


@pytest.fixture
def agent(tmp_path):
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "pickjob.py").write_text("# engine", encoding="utf-8")
    (tmp_path / "pricing").mkdir()
    (tmp_path / "pricing" / "lens_link.csv").write_text(LINK_CSV, encoding="utf-8")
    return {"optomate_agent": {"agent_dir": str(tmp_path)}, "pick_lens_job": {"show": True}}


def test_run_engine_passes_the_request_on_stdin_and_reads_the_last_line(agent, monkeypatch):
    seen = Seen({"ok": True, "body": {"PATIENT_ID": 9003}})
    monkeypatch.setattr(pk.subprocess, "run", seen)
    out = pk.run_engine(agent, "plan", {"patient_id": 9003})
    assert out["ok"] and out["body"]["PATIENT_ID"] == 9003
    args, kw = seen.calls[0]
    assert args[1:] == ["-m", "orders.pickjob", "plan", "--json"]
    assert json.loads(kw["input"]) == {"patient_id": 9003} and kw["cwd"] == agent["optomate_agent"]["agent_dir"]


def test_run_engine_timeouts_and_rubbish_say_check_touch(agent, monkeypatch):
    seen = Seen({})
    seen.raise_exc = subprocess.TimeoutExpired("x", 1)
    monkeypatch.setattr(pk.subprocess, "run", seen)
    assert "check Touch" in pk.run_engine(agent, "make", {})["refusals"][0]
    seen.raise_exc = None
    seen.answer = None
    monkeypatch.setattr(pk.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="not json", stderr=""))
    assert "may or may not exist" in pk.run_engine(agent, "make", {})["refusals"][0]


def test_run_engine_not_connected_without_the_engine(tmp_path, monkeypatch):
    monkeypatch.delenv("LENS_OPTIONS_FIXTURE", raising=False)
    out = pk.run_engine({"optomate_agent": {"agent_dir": str(tmp_path)}}, "status")
    assert out["connected"] is False


def test_clean_request_types_everything_and_drops_the_rest():
    r = pk.clean_request({"patient_id": "9003", "rx_id": 503, "rx_saved_at": "2026-11-08 09:15:00 extra", "lens_use": "distance",
                          "option": {"supplier": "Synchrony", "lens": "FSV Clear 1.50", "index": "1.50", "material": "",
                                     "coating": "", "stockgrind": "stock", "blank_mm": 65, "sell": 140},
                          "hub_pair_price": 140.0, "frame": {"barcode": "ZZFRAME5219", "own": 0},
                          "heights": {"R": 21, "L": "", "X": 1}, "tint_barcode": None, "second_pair": "yes",
                          "evil": "x"}, "Karen")
    assert r["patient_id"] == 9003 and r["rx_id"] == 503 and r["rx_saved_at"] == "2026-11-08 09:15:00"
    assert r["lens_use"] == "D" and r["hub_pair_price"] == "140.0" and "sell" not in r["option"]
    assert r["frame"] == {"own": False, "barcode": "ZZFRAME5219", "a": "", "dbl": "", "ed": ""}
    assert r["heights"] == {"R": "21", "L": ""} and r["tint_barcode"] == "" and r["second_pair"] is True
    assert r["user"] == "Karen" and "evil" not in r
    assert pk.clean_request({"patient_id": "x"}, "") is None


# --- lens_bridge: a frame by barcode (read only) -----------------------------------------------

def test_frame_by_barcode_row_is_read_and_sized():
    row = ["827", "Ray-Ban RB5154", "Black", "5121.0", "0", "0", "LUX", "2", "299.0000", "2"]
    f = lb.build_frame_record(row)
    assert f == {"barcode": "827", "model": "Ray-Ban RB5154", "colour": "Black", "a": 51.0, "dbl": 21.0, "ed": None,
                 "sunglass": False, "price": 299.0, "qoh": 2}
    assert lb.build_frame_record(["x", "", "", "0", "0", "0", "", "3", "", "0"])["sunglass"] is True
    assert lb.build_frame_record(None) is None


def test_cmd_frame_validates_and_refuses_unknown(monkeypatch):
    monkeypatch.delenv("LENS_OPTIONS_FIXTURE", raising=False)
    with pytest.raises(lb.BridgeError):
        lb.cmd_frame("'; DROP TABLE", sql=lambda q: [])
    with pytest.raises(lb.BridgeError):
        lb.cmd_frame("NOPE", sql=lambda q: [])
    seen = []
    out = lb.cmd_frame("ZZ-1", sql=lambda q: seen.append(q) or [["ZZ-1", "M", "C", "5218", "0", "57.0", "MIS", "2", "179.0000", "1"]])
    assert out["frame"]["a"] == 52.0 and out["frame"]["ed"] == 57.0 and "N'ZZ-1'" in seen[0]
    monkeypatch.setenv("LENS_OPTIONS_FIXTURE", "1")
    assert lb.cmd_frame("zzframe5219")["frame"]["price"] == 179.0


# --- routes ---------------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    lenses_dir = tmp_path / "lenses"
    lenses_dir.mkdir()
    (lenses_dir / "test.csv").write_text(LENS_CSV, encoding="utf-8")
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "lens_filter.json").write_text('{"keep_only": {}, "preferred": {"match": [], "exclude": []}}', encoding="utf-8")
    agent_dir = tmp_path / "agent"
    (agent_dir / "orders").mkdir(parents=True)
    (agent_dir / "orders" / "pickjob.py").write_text("# engine", encoding="utf-8")
    (agent_dir / "pricing").mkdir()
    (agent_dir / "pricing" / "lens_link.csv").write_text(LINK_CSV, encoding="utf-8")
    (cfg / "integrations.json").write_text(json.dumps({"optomate_agent": {"agent_dir": str(agent_dir)},
                                                       "pick_lens_job": {"show": True}}), encoding="utf-8")
    monkeypatch.setenv("CEC_HUB_LENSES_DIR", str(lenses_dir))
    monkeypatch.setenv("CEC_HUB_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("CEC_HUB_INTEGRATIONS", raising=False)
    monkeypatch.delenv("CEC_HUB_PICK_LENS", raising=False)
    monkeypatch.delenv("LENS_OPTIONS_FIXTURE", raising=False)
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    fake = FakeBridge()
    monkeypatch.setattr(lo, "run_bridge", fake)
    engine = Seen({"ok": True, "refusals": [], "preview": {}, "body": {"PATIENT_ID": 77}, "mode": {"dry_run": True}, "created": None})
    monkeypatch.setattr(pk.subprocess, "run", engine)
    with app_module.app.test_client() as c:
        c.fake, c.engine, c.cfg_dir = fake, engine, cfg
        yield c


def test_routes_are_404_until_switched_on(client):
    (client.cfg_dir / "integrations.json").write_text(json.dumps({"optomate_agent": {"agent_dir": "C:\\nowhere"}}), encoding="utf-8")
    for url, method in (("/api/picklens/status", "get"), ("/api/picklens/options", "post"), ("/api/picklens/frame", "post"),
                        ("/api/picklens/check", "post"), ("/api/picklens/make", "post"), ("/api/picklens/tints", "get")):
        r = getattr(client, method)(url, json={})
        assert r.status_code == 404 and r.get_json()["enabled"] is False, url
    assert client.engine.calls == []


def test_status_runs_the_engine_and_says_enabled(client):
    client.engine.answer = {"dry_run": True, "write_live": False, "zztest_only": True, "link_rows": 6, "linked": 5}
    d = client.get("/api/picklens/status").get_json()
    assert d["enabled"] and d["dry_run"] and d["linked"] == 5
    assert client.engine.calls[0][0][1:] == ["-m", "orders.pickjob", "status", "--json"]


def test_options_route_decorates_with_links_best_and_next_best(client):
    d = client.post("/api/picklens/options", json={"patient_id": 77}).get_json()
    assert d["patient"]["name"] == "ZZTEST, Ann"
    tab = next(t for t in d["rxs"][0]["tabs"] if t["key"] == "distance")
    a = tab["parts"][0]["answer"]
    assert a["best"] and all("link" in o and "ref" in o and o["use"] == "D" for o in a["best"] + a["rest"])
    assert a["best"][0]["why"].startswith("Cheapest")
    ready = [o for o in a["best"] + a["rest"] if o["link"]["ready"]]
    assert ready and ready[0]["link"]["barcode"].startswith("2000")
    assert a["next_best"] is None and a["heights_needed"] is False
    mf = next(t for t in d["rxs"][0]["tabs"] if t["key"] == "multifocal")
    assert mf["parts"][0]["answer"]["heights_needed"] is True
    assert mf["parts"][0]["answer"]["best"][0]["use"] == "M"
    # with the picked lens named, next_best is answered
    picked = a["best"][0]["ref"]
    d2 = client.post("/api/picklens/options", json={"patient_id": 77, "frame": {"a": 52, "dbl": 19, "ed": 57}, "picked": picked}).get_json()
    nb = next(t for t in d2["rxs"][0]["tabs"] if t["key"] == "distance")["parts"][0]["answer"]["next_best"]
    assert nb is not None and "problem" in nb and "suggest" in nb


def test_frame_route_uses_the_bridge(client):
    client.fake.frame_answer = {"frame": {"barcode": "ZZ", "model": "M", "colour": "C", "a": 52.0, "dbl": 19.0, "ed": 57.0,
                                           "sunglass": False, "price": 179.0, "qoh": 1}}
    orig = client.fake.__class__.__call__

    def call(self, cfg, *args):
        if args[0] == "frame":
            return self.frame_answer
        return orig(self, cfg, *args)
    client.fake.__class__.__call__ = call
    try:
        d = client.post("/api/picklens/frame", json={"barcode": "ZZ"}).get_json()
        assert d["frame"]["a"] == 52.0 and d["frame"]["source"] == "scan" and d["frame"]["price"] == 179.0
        assert client.post("/api/picklens/frame", json={"barcode": ""}).status_code == 400
    finally:
        client.fake.__class__.__call__ = orig


def test_check_and_make_pass_a_cleaned_request_with_the_staff_name(client):
    client.set_cookie("hub_staff", "Karen")
    body = {"patient_id": 77, "rx_id": 1, "rx_saved_at": "2026-11-03 15:00:00", "lens_use": "D",
            "option": {"supplier": "Synchrony", "lens": "FSV Clear 1.50", "index": "1.50", "material": "Clear", "coating": "HMC+",
                       "stockgrind": "stock", "blank_mm": 65},
            "hub_pair_price": 140, "frame": {"barcode": "ZZFRAME5219"}, "heights": {}, "tint_barcode": "", "evil": 1}
    d = client.post("/api/picklens/check", json=body).get_json()
    assert d["ok"]
    args, kw = client.engine.calls[-1]
    sent = json.loads(kw["input"])
    assert args[3] == "plan" and sent["user"] == "Karen" and "evil" not in sent and sent["hub_pair_price"] == "140"
    d = client.post("/api/picklens/make", json=body).get_json()
    assert client.engine.calls[-1][0][3] == "make" and d["created"] is None
    bad = client.post("/api/picklens/make", json={"patient_id": "x"}).get_json()
    assert bad["refusals"] == ["Pick a patient and a lens first."] and client.engine.calls[-1][0][3] == "make"


def test_lens_options_page_still_answers_as_before(client):
    """The existing page keeps working unchanged (Mark's one-change-at-a-time rule)."""
    d = client.post("/api/lensoptions/rx", json={"patient_id": 77}).get_json()
    assert d["patient"]["name"] == "ZZTEST, Ann" and d["rxs"]
    o = d["rxs"][0]["tabs"][0]["parts"][0]["answer"]["options"][0]
    assert "link" not in o and "raw_name" in o and "material" in o


def test_page_ships_the_hidden_route_and_files_but_no_tile():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    assert "/picklens.js" in html and "/picklens.css" in html
    js = (root / "static" / "app.js").read_text(encoding="utf-8")
    assert "#\\/picklens$" in js
    tiles = json.loads((root / "config" / "tiles.example.json").read_text(encoding="utf-8-sig"))
    assert "picklens" not in json.dumps(tiles)
    pj = (root / "static" / "picklens.js").read_text(encoding="utf-8")
    for tier in ("Essential", "Everyday", "Signature"):
        assert tier not in pj                     # staff pages use product names, never tier names
