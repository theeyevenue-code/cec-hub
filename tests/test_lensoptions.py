"""Lens options page - synthetic data only (ZZTEST people, made-up Rx).
The SQL function and the bridge subprocess are faked; nothing touches Optomate."""

import importlib
import json
import logging
from decimal import Decimal

import pytest

from hub import lens_bridge as lb
from hub import lensoptions as lo

RTF = ("{\\rtf1\\ansi\\ansicpg1252\\deff0\\nouicompat{\\fonttbl{\\f0\\fnil\\fcharset0 Segoe UI;}}"
       " \\n {\\*\\generator Riched20 10.0.26100}\\viewkind4\\uc1  \\n \\pard\\f0\\fs20\\lang1033 "
       "%sWarranty for this prescription is only applicable to glasses made at Concord Eye Care \\par \\n "
       "\\par \\n Please note this prescription is valid for 1 year\\par \\n }")
OLD_PLAIN = ("%s \\n  \\n *** Warranty for this prescription is only applicable to glasses made "
             "at Concord Eye Care *** Valid for 1 year")


# --- comment: RTF + footer stripped -------------------------------------------------

def test_rtf_and_footer_stripped_to_nothing():
    assert lb.clean_comment(RTF % "") == ""
    assert lb.clean_comment(OLD_PLAIN % "") == ""


def test_rtf_keeps_the_optoms_words():
    assert lb.clean_comment(RTF % "SVN\\par ") == "SVN"
    assert lb.clean_comment(OLD_PLAIN % "Occ MF") == "Occ MF"


def test_rtf_escapes_decoded():
    assert lb.strip_rtf("{\\rtf1 caf\\'e9 \\{x\\}\\par}") == "café {x}"


# --- comment -> lens type ----------------------------------------------------------

@pytest.mark.parametrize("words,typ", [
    ("SV", "distance"), ("SVN", "near"), ("SV near", "near"), ("near", "near"),
    ("reading", "near"), ("SVD", "distance"), ("driving", "distance"),
    ("MF", "multifocal"), ("progressives", "multifocal"), ("PAL", "multifocal"),
    ("bifocals", "bifocal"), ("BF", "bifocal"), ("Occ MF", "office"), ("office", "office"),
    ("computer", "office"), ("SV computer", "intermediate"), ("intermediate SV", "intermediate"),
])
def test_keywords_with_add(words, typ):
    d = lb.detect_type(RTF % (words + "\\par "), "", has_add=True)
    assert d["type"] == typ
    assert d["source"] == "comment"


def test_practice_comment_used_when_rx_comment_has_nothing():
    d = lb.detect_type(RTF % "", "Bifocals", has_add=True)
    assert d["type"] == "bifocal" and d["source"] == "practice_comment"


def test_no_keyword_no_add_is_distance_quietly():
    d = lb.detect_type(RTF % "", "", has_add=False)
    assert d["types"] == ["distance"] and d["note"] == ""


def test_no_keyword_with_add_is_multifocal_and_says_so():
    d = lb.detect_type(RTF % "", "", has_add=True)
    assert d["types"] == ["multifocal"]
    assert "Lens type not written on the Rx" in d["note"]
    assert d["source"] == "none"


def test_two_types_named_both_kept_in_order():
    d = lb.detect_type(RTF % "SV dist + SV near\\par ", "", has_add=True)
    assert d["types"] == ["distance", "near"]
    assert "more than one lens" in d["note"]


def test_near_without_add_is_the_reading_pair_as_written():
    d = lb.detect_type(RTF % "SVN\\par ", "", has_add=False)
    assert d["types"] == ["near"] and d["as_written"]
    assert "reading pair as written" in d["note"]


def test_multifocal_written_but_no_add_falls_back_with_a_note():
    d = lb.detect_type(RTF % "MF\\par ", "", has_add=False)
    assert d["types"] == ["distance"]
    assert "no add on the Rx" in d["note"]


def test_special_lens_named_is_flagged():
    d = lb.detect_type(RTF % "MiyoSmart\\par ", "", has_add=False)
    assert d["special"].lower() == "miyosmart"
    assert "particular lens" in d["note"]


# --- Rx strings --------------------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ("+2.25", Decimal("2.25")), ("-0.50", Decimal("-0.50")), ("0.00", Decimal("0")),
    ("PLANO", Decimal("0")), ("pl", Decimal("0")), ("DS", Decimal("0")),
    ("−1.75", Decimal("-1.75")), ("+1,50", Decimal("1.50")), ("+2.00D", Decimal("2.00")),
    ("", None), (None, None), ("BALANCE", None), ("abc", None),
])
def test_parse_power(raw, want):
    assert lb.parse_power(raw) == want


def test_parse_eye_keeps_strings_verbatim_and_drops_zero_fields():
    e = lb.parse_eye(["+1.00", "0.00", "90", "+2.00", "", "31.5", "", "0.00", "IN", "1.00", "DOWN"])
    assert e == {"sph": "+1.00", "cyl": None, "axis": None, "add": "+2.00", "intadd": None,
                 "pd": "31.5", "near_pd": None, "prism": "1.00 DOWN"}


# --- bridge commands with a fake sql -------------------------------------------------

def _eye(sph, cyl="", axis="", add="", intadd="", pd="", near_pd=""):
    return [sph, cyl, axis, add, intadd, pd, near_pd, "", "", "", ""]


def test_recent_one_line_per_patient_newest_first_capped():
    rows = [["7", "2026-11-03 15:00:00", "Ann", "ZZTEST"],
            ["7", "2026-11-03 14:00:00", "Ann", "ZZTEST"],
            ["8", "2026-11-03 13:30:00", "Bob", "ZZTEST"],
            ["9", "2026-11-03 11:05:00", "Cy", "ZZTEST"],
            ["10", "2026-11-03 09:00:00", "Di", "ZZTEST"]]
    seen = []
    out = lb.cmd_recent("2026-11-03", 3, sql=lambda q: seen.append(q) or rows)
    assert [p["id"] for p in out["patients"]] == [7, 8, 9]
    assert out["patients"][0]["saved"] == "3:00pm"
    assert out["more"] is True
    q = seen[0]
    assert "SPECTACLE_RX" in q and "NOLOCK" in q and "'20261103'" in q and "'20261104'" in q
    assert not any(w in q.upper() for w in ("UPDATE ", "INSERT ", "DELETE "))


def test_recent_rejects_a_bad_date():
    with pytest.raises(lb.BridgeError):
        lb.cmd_recent("2026-13-45; drop", 3, sql=lambda q: [])


def test_search_pairs_first_then_wide_net_and_escapes():
    calls = []

    def sql(q):
        calls.append(q)
        if "AND p.GIVEN" in q or "AND (p.SURNAME" in q and "OR (p.SURNAME" in q:
            return [["1", "Ann", "ZZTEST", "1970-01-01", "2026-11-03"]]
        return [["1", "Ann", "ZZTEST", "1970-01-01", "2026-11-03"],
                ["2", "Zztest", "O'Ann", "1980-01-01", ""]]
    out = lb.cmd_search("O'Ann zztest", sql=sql)
    assert [p["id"] for p in out["patients"]] == [1, 2]
    assert "O''Ann" in calls[0]
    assert "EXISTS (SELECT 1 FROM SPECTACLE_RX" in calls[0]
    assert out["patients"][0]["last_rx"] == "2026-11-03"


def test_rx_returns_rows_of_the_latest_day_and_the_last_frame():
    rx_rows = [
        ["21", "2026-11-03 15:00:00", "2026-11-03", "MJ", "0", "0", RTF % "SVN\\par ", "",
         *_eye("+1.00", add="+2.00", pd="31"), *_eye("+1.25", add="+2.00", pd="31")],
        ["20", "2026-11-03 14:59:00", "2026-11-03", "MJ", "0", "0", RTF % "SVD\\par ", "",
         *_eye("+1.00", add="+2.00", pd="31"), *_eye("+1.25", add="+2.00", pd="31")],
        ["19", "2025-01-01 10:00:00", "2025-01-01", "MJ", "0", "0", "", "",
         *_eye("+0.50"), *_eye("+0.50")],
    ]

    def sql(q):
        if q.startswith("SELECT p.ID"):
            return [["5", "Ann", "ZZTEST", "1960-01-01"]]
        if "SPECTACLE_ORDERS" in q:
            return [["2025-06-01", "5219", "0.0", "0.0", "0", "0", "57.0"]]
        return rx_rows
    out = lb.cmd_rx(5, sql=sql)
    assert [r["rx_id"] for r in out["rxs"]] == [21, 20]
    assert out["rxs"][0]["lens_type"]["type"] == "near"
    assert out["rxs"][1]["lens_type"]["type"] == "distance"
    assert out["rxs"][0]["right"]["sph"] == "+1.00"
    assert out["frame"] == {"a": 52.0, "dbl": 19.0, "ed": 57.0, "order_date": "2025-06-01"}


def test_rx_one_eye_only():
    rows = [["1", "2026-11-03 10:00:00", "2026-11-03", "MJ", "1", "0", "", "",
             *_eye("-1.00"), *_eye("-9.00")]]
    out = lb.build_rx(rows)
    assert out[0]["left"] is None and out[0]["right"]["sph"] == "-1.00"


@pytest.mark.parametrize("size,want", [("5219", (52, 19)), ("5419.0", (54, 19)), ("52-19", (52, 19)),
                                       ("52", (52, None)), ("0", (None, None)), ("", (None, None))])
def test_split_frame_size(size, want):
    assert lb.split_size(size) == want


def test_bridge_main_refuses_unknown_commands(capsys):
    lb.main(["rx", "--pid", "1; drop"])
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["error"] == "Bad command."


# --- the Hub side: questions, blank estimate ----------------------------------------

def _rx(right, left, typ="multifocal", as_written=False, source="none"):
    return {"right": right, "left": left,
            "lens_type": {"type": typ, "types": [typ], "as_written": as_written, "source": source,
                          "note": "", "words": [], "text": "", "practice_text": ""}}


def E(sph, cyl=None, add=None, intadd=None, pd=None, near_pd=None, prism=None):
    return {"sph": sph, "cyl": cyl, "axis": "90" if cyl else None, "add": add, "intadd": intadd,
            "pd": pd, "near_pd": near_pd, "prism": prism}


def test_near_is_sphere_plus_add_with_working():
    q = lo.mode_question(_rx(E("-0.75", "-0.75", "+2.25"), E("-1.00", None, "+2.25")), "near")
    assert q["right"] == {"sph": 1.5, "cyl": -0.75} and q["left"] == {"sph": 1.25, "cyl": 0.0}
    assert q["working"] == ["R -0.75 + add +2.25 = +1.50", "L -1.00 + add +2.25 = +1.25"]


def test_intermediate_never_made_up():
    q = lo.mode_question(_rx(E("+1.00", add="+2.00"), E("+1.00", add="+2.00")), "intermediate")
    assert q["unavailable"].startswith("Intermediate add not on the Rx")
    q = lo.mode_question(_rx(E("+1.00", add="+2.00", intadd="+1.00"), E("+1.00", add="+2.00", intadd="+1.00")),
                         "intermediate")
    assert q["right"]["sph"] == 2.0


def test_multifocal_takes_the_higher_add_and_says_so():
    q = lo.mode_question(_rx(E("+1.00", add="+2.00"), E("+1.00", add="+2.25")), "multifocal")
    assert q["add"] == 2.25 and "Adds differ" in q["notes"][0]


def test_no_add_means_no_multifocal():
    q = lo.mode_question(_rx(E("-2.00"), E("-2.00"), "distance"), "multifocal")
    assert "No add" in q["unavailable"]


def test_reading_pair_as_written_has_no_distance():
    rx = _rx(E("+2.50"), E("+2.50"), "near", as_written=True)
    assert lo.mode_question(rx, "near", "near")["right"] == {"sph": 2.5, "cyl": 0.0}
    assert "reading pair" in lo.mode_question(rx, "distance", "near")["unavailable"]


def test_flags_prism_balance_and_differing_adds():
    flags = lo.rx_flags({"right": E("BALANCE", add="+2.00"), "left": E("+1.00", add="+2.50", prism="1.00 IN")})
    assert any("balance lens" in f for f in flags)
    assert any(f.startswith("Prism left 1.00 IN: check the lens can take it") for f in flags)
    assert any("adds differ" in f for f in flags)


def test_mono_pds_halves_a_binocular_and_notes_near():
    pds, note = lo.mono_pds({"right": E("+1", pd="63"), "left": E("+1")})
    assert pds == {"R": 31.5, "L": 31.5} and "taken as 31.5" in note
    pds, note = lo.mono_pds({"right": E("+1", pd="32", near_pd="30"), "left": E("+1", pd="31")}, near=True)
    assert pds == {"R": 30.0, "L": 31.0} and "Near PD not on the Rx" in note


def test_blank_estimate_formula_per_eye():
    # ED 57 + (A 52 + DBL 19 - 2 x 31) = 9 + 2 = 68 ; left PD 32 -> 7 -> 66
    est = lo.blank_estimate({"a": 52, "dbl": 19, "ed": 57}, {"R": 31, "L": 32})
    assert est["per_eye"] == {"R": 68, "L": 66} and est["value"] == 68
    assert est["working"][0] == "R: 57 + (52 + 19 - 2 x 31 = 9) + 2 = 68mm"
    assert not est["approx"]
    assert any("vertical centre" in n for n in est["notes"])


def test_blank_estimate_without_ed_is_approximate():
    est = lo.blank_estimate({"a": 50, "dbl": 18, "ed": None}, {"R": 30, "L": 30}, multi=True)
    assert est["value"] == 50 + 2 + 8 + 2 and est["approx"]
    assert any("Approximate" in n for n in est["notes"])
    assert any("fitting height" in n for n in est["notes"])


def test_blank_estimate_needs_bridge_and_pd():
    assert lo.blank_estimate({"a": 52, "dbl": None, "ed": 57}, {"R": 31}) is None
    assert lo.blank_estimate({"a": 52, "dbl": 19, "ed": 57}, {}) is None


def test_clean_frame_range_checks():
    assert lo.clean_frame({"a": "52", "dbl": "19", "ed": "300"}) == {
        "a": 52.0, "dbl": 19.0, "ed": None, "source": "typed", "order_date": ""}
    assert lo.clean_frame({"a": "abc"}) is None


# --- the endpoint -----------------------------------------------------------------

LENS_CSV = (
    "supplier,brand,lens,category,index,type,blank_mm,sph_min,sph_max,cyl_max,add_min,add_max,price\n"
    "Synchrony,Synchrony,FSV Clear 1.50,Single vision,1.50,stock,65,-4.00,+0.00,-2.00,,,10.00\n"
    "Synchrony,Synchrony,FSV Clear 1.50,Single vision,1.50,stock,75,-4.00,+0.00,-2.00,,,10.00\n"
    "Synchrony,Synchrony,FSV Clear 1.50,Single vision,1.50,stock,65,+0.25,+4.00,-2.00,,,10.00\n"
    "Synchrony,Synchrony,SV Grind 1.50,Single vision,1.50,grind,,-10.00,+8.00,-4.00,,,40.00\n"
    "Synchrony,Synchrony,SV Grind 1.60,Single vision,1.60,grind,,-10.00,+8.00,-4.00,,,50.00\n"
    "Synchrony,Synchrony,Progressive Ultra HDV 1.50,Progressive,1.50,grind,,-8.00,+6.00,-4.00,0.75,3.50,45.00\n"
)


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.eyes = (_eye("+1.00", add="+2.00", pd="31"), _eye("+1.25", add="+2.00", pd="32"))

    def __call__(self, cfg, *args):
        self.calls.append(args)
        if args[0] == "recent":
            return {"date": "2026-11-03", "more": False, "fixture": False,
                    "patients": [{"id": 77, "given": "Ann", "surname": "ZZTEST", "saved": "3:00pm"}]}
        if args[0] == "search":
            return {"patients": [{"id": 77, "given": "Ann", "surname": "ZZTEST", "dob": "1960-01-01",
                                  "last_rx": "2026-11-03"}], "more": False}
        if args[0] == "rx":
            right, left = (lb.parse_eye(e) for e in self.eyes)
            return {"patient": {"id": 77, "given": "Ann", "surname": "ZZTEST", "dob": "1960-01-01"},
                    "frame": {"a": 52.0, "dbl": 19.0, "ed": 57.0, "order_date": "2025-06-01"},
                    "rxs": [{"rx_id": 1, "saved_at": "2026-11-03 15:00:00", "saved": "3:00pm",
                             "saved_date": "2026-11-03", "rx_date": "2026-11-03", "optom": "MJ",
                             "right_only": False, "left_only": False, "right": right, "left": left,
                             "lens_type": lb.detect_type(RTF % "", "", bool(right["add"] or left["add"]),
                                                         False)}]}
        return {"error": "Bad command."}


@pytest.fixture
def client(tmp_path, monkeypatch):
    lenses_dir = tmp_path / "lenses"
    lenses_dir.mkdir()
    (lenses_dir / "test.csv").write_text(LENS_CSV, encoding="utf-8")
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "lens_filter.json").write_text('{"keep_only": {}, "preferred": {"match": [], "exclude": []}}',
                                          encoding="utf-8")
    (cfg / "integrations.json").write_text(json.dumps({"optomate_agent": {"agent_dir": str(tmp_path)}}),
                                           encoding="utf-8")
    monkeypatch.setenv("CEC_HUB_LENSES_DIR", str(lenses_dir))
    monkeypatch.setenv("CEC_HUB_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("CEC_HUB_INTEGRATIONS", raising=False)
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    fake = FakeBridge()
    monkeypatch.setattr(lo, "run_bridge", fake)
    with app_module.app.test_client() as c:
        c.fake = fake
        yield c


def test_recent_endpoint_no_store(client):
    r = client.get("/api/lensoptions/recent")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert r.get_json()["patients"] == [{"id": 77, "name": "ZZTEST, Ann", "saved": "3:00pm"}]


def test_search_endpoint(client):
    r = client.post("/api/lensoptions/search", json={"q": "zztest ann"})
    assert r.get_json()["patients"][0]["dob"] == "01/01/1960"
    assert client.post("/api/lensoptions/search", json={"q": "a"}).status_code == 400


def test_rx_endpoint_all_five_tabs_multifocal_preselected(client, caplog):
    caplog.set_level(logging.INFO)
    r = client.post("/api/lensoptions/rx", json={"patient_id": 77})
    d = r.get_json()
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    rx = d["rxs"][0]
    assert [t["key"] for t in rx["tabs"]] == ["distance", "near", "office", "multifocal", "bifocal"]
    assert rx["default_tab"] == "multifocal"
    assert "Lens type not written" in rx["type_note"]
    tabs = {t["key"]: t for t in rx["tabs"]}
    mf = tabs["multifocal"]["parts"][0]
    assert mf["answer"]["best"]["name"] == "Progressive Ultra HDV"
    assert mf["answer"]["best"]["category"] == "Multifocal"
    # frame from the last order -> blank estimate fed to the finder
    near = tabs["near"]["parts"][0]
    assert near["working"][0] == "R +1.00 + add +2.00 = +3.00"
    assert near["blank"]["value"] == 68
    # plus Rx: the stock lens only comes 65 mm for plus powers, the frame needs 68
    others = {o["name"]: o for o in near["answer"]["others"]}
    stock = others["FSV Clear"]
    assert stock["fits"] is False and stock["blank_mm"] == 65
    assert stock["blank_line"] == ("Needs 68 mm - this lens only comes 65 mm for this Rx. No stock "
                                   "blank big enough for this Rx at this index: grind (or a smaller frame)")
    assert near["answer"]["best"]["type"] == "Grind"
    assert near["answer"]["best"]["blank_line"].startswith("Made to order")
    assert "too small for this frame" in near["answer"]["blank_note"]
    office = tabs["office"]["parts"]
    assert office[0]["unavailable"].startswith("Intermediate add not on the Rx")
    assert d["frame"]["source"] == "order"
    assert "ZZTEST" not in caplog.text and "Ann" not in caplog.text


def test_rx_endpoint_typed_frame_wins(client):
    d = client.post("/api/lensoptions/rx", json={"patient_id": 77,
                                                  "frame": {"a": "48", "dbl": "18"}}).get_json()
    assert d["frame"]["source"] == "typed"
    near = {t["key"]: t for t in d["rxs"][0]["tabs"]}["near"]["parts"][0]
    assert near["blank"]["approx"] is True
    assert near["blank"]["value"] == 48 + 2 + (48 + 18 - 62) + 2   # right eye, PD 31


def test_rx_endpoint_bad_patient(client):
    assert client.post("/api/lensoptions/rx", json={"patient_id": "x"}).status_code == 400


def test_page_route_and_tile_ship():
    js = open("static/app.js", encoding="utf-8").read()
    assert "renderLensOptions" in js and "#\\/lensoptions" in js
    tiles = json.load(open("config/tiles.example.json", encoding="utf-8"))["tiles"]
    assert any(t["link"] == "#/lensoptions" for t in tiles)


def test_minus_rx_fits_the_bigger_stock_blank(client):
    client.fake.eyes = (_eye("-2.00", pd="31"), _eye("-2.25", pd="32"))
    d = client.post("/api/lensoptions/rx", json={"patient_id": 77}).get_json()
    rx = d["rxs"][0]
    assert rx["default_tab"] == "distance"
    part = {t["key"]: t for t in rx["tabs"]}["distance"]["parts"][0]
    best = part["answer"]["best"]
    assert best["name"] == "FSV Clear" and best["type"] == "Stock"
    assert best["fits"] is True and best["blank_mm"] == 75     # 65 too small for 68
    assert best["blank_line"] == "Fits a 75 mm stock blank"
    assert best["blanks"] == [65.0, 75.0]


def test_no_frame_says_which_blanks_exist(client):
    client.fake.eyes = (_eye("-2.00", pd="31"), _eye("-2.25", pd="32"))
    d = client.post("/api/lensoptions/rx", json={"patient_id": 77, "frame": {"a": ""}}).get_json()
    part = {t["key"]: t for t in d["rxs"][0]["tabs"]}["distance"]["parts"][0]
    assert part["blank"] is None
    assert part["answer"]["best"]["blank_line"] == "Stock blank 65 mm / 75 mm for this Rx"


def test_needs_bigger_blank_points_to_the_index_that_has_it():
    avail = {"R": {("S", "A 1.67", "stock", ""): [65.0], ("S", "A 1.60", "stock", ""): [65.0, 70.0]},
             "L": {("S", "A 1.67", "stock", ""): [65.0], ("S", "A 1.60", "stock", ""): [70.0]}}
    ok, line = lo.blank_line({"type": "stock"}, ("S", "A 1.67", "stock", ""), avail,
                             {"R": 68, "L": 67}, ["1.60 stock"])
    assert not ok
    assert line == "Needs 68 mm - this lens only comes 65 mm for this Rx. Big enough stock: 1.60 stock"
    ok, line = lo.blank_line({"type": "stock"}, ("S", "A 1.60", "stock", ""), avail, {"R": 68, "L": 67}, [])
    assert ok and line == "Fits a 70 mm stock blank"


def test_blank_says_needed_size_and_minimum_stock_size(client):
    # Mark, 3 Nov 2026: "needs 68mm" AND "minimum 75mm blank" (the stock size to ask for)
    client.fake.eyes = (_eye("-2.00", pd="31"), _eye("-2.25", pd="32"))
    d = client.post("/api/lensoptions/rx", json={"patient_id": 77}).get_json()
    blank = {t["key"]: t for t in d["rxs"][0]["tabs"]}["distance"]["parts"][0]["blank"]
    assert blank["value"] == 68 and blank["min_stock"] == 75.0
    assert blank["stock_sizes"] == [65.0, 75.0]
    js = open("static/app.js", encoding="utf-8").read()
    assert "Minimum</span>" in js and "b.min_stock" in js