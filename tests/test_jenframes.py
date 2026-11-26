"""Jen's frames page (Mark, 26 Nov 2026): scan = one line at our cost incl. GST from the cached
catalogue, optional "for", same-day remove (kept, marked removed), month totals, staff guard.
No agent, no Optomate."""
import importlib
import json
from datetime import datetime

import pytest

from hub import jenframes as jf
from hub import stocktake as st

CAT = {"ok": True, "loaded": "2026-11-26T09:00:00", "suppliers": {"MARC": "Marcolin"},
       "frames": [{"b": "20000939", "d": "TOM FORD TF5709 black 54", "s": "MARC", "p": 575.0, "c": 268.0,
                   "q": 1, "st": "C", "k": "f"},
                  {"b": "20000777", "d": "NO COST FRAME", "s": "MARC", "p": 100.0, "c": None,
                   "q": 1, "st": "C", "k": "f"}]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CEC_HUB_JENFRAMES_DIR", str(tmp_path / "jen"))
    monkeypatch.delenv("CEC_HUB_INTEGRATIONS", raising=False)
    st.reset_cache()
    monkeypatch.setattr(st, "run_catalogue", lambda cfg: CAT)
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c, tmp_path / "jen"
    st.reset_cache()


def _scan(c, bc):
    return c.post("/api/jenframes/scan", json={"barcode": bc}).get_json()


def test_scan_adds_frame_at_cost_inc_gst(client):
    c, d = client
    r = _scan(c, " 20000939\r")
    assert r["ok"] and r["line"]["cost_inc"] == 268.0 and r["line"]["found"]
    assert r["line"]["description"].startswith("TOM FORD") and r["same_before"] == 0
    v = c.get("/api/jenframes").get_json()
    assert v["count"] == 1 and v["total"] == 268.0 and v["unpriced"] == 0
    month = datetime.now().strftime("%Y-%m")
    assert (d / f"{month}.json").is_file() and v["months"] == [month]


def test_same_frame_twice_counts_twice_and_says_so(client):
    c, _ = client
    _scan(c, "20000939")
    r = _scan(c, "20000939")
    assert r["same_before"] == 1
    assert c.get("/api/jenframes").get_json()["total"] == 536.0


def test_unknown_and_unpriced_frames_are_kept(client):
    c, _ = client
    r = _scan(c, "99999999")
    assert r["ok"] and not r["line"]["found"] and r["line"]["cost_inc"] is None
    _scan(c, "20000777")
    v = c.get("/api/jenframes").get_json()
    assert v["count"] == 2 and v["unpriced"] == 2 and v["total"] == 0


def test_empty_scan_refused(client):
    c, _ = client
    r = c.post("/api/jenframes/scan", json={"barcode": "  "})
    assert r.status_code == 400


def test_for_note_saved(client):
    c, _ = client
    lid = _scan(c, "20000939")["line"]["id"]
    assert c.post("/api/jenframes/for", json={"id": lid, "text": "Staff <b>"}).get_json()["ok"]
    assert c.get("/api/jenframes").get_json()["lines"][0]["for"] == "Staff b"


def test_remove_same_day_keeps_the_record(client):
    c, d = client
    lid = _scan(c, "20000939")["line"]["id"]
    assert c.post("/api/jenframes/remove", json={"id": lid}).get_json()["ok"]
    v = c.get("/api/jenframes").get_json()
    assert v["count"] == 0 and v["total"] == 0
    saved = json.loads(next(d.glob("*.json")).read_text(encoding="utf-8"))
    assert saved["lines"][0]["removed"]


def test_older_scan_cannot_be_removed(client, monkeypatch):
    c, d = client
    lid = _scan(c, "20000939")["line"]["id"]
    p = next(d.glob("*.json"))
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["lines"][0]["at"] = doc["month"] + "-01T09:00:00" if datetime.now().day != 1 else "2000-01-01T00:00:00"
    p.write_text(json.dumps(doc), encoding="utf-8")
    r = c.post("/api/jenframes/remove", json={"id": lid})
    assert r.status_code == 400 and "today" in r.get_json()["error"]


def test_bad_month_refused(client):
    c, _ = client
    assert c.get("/api/jenframes?month=../x").status_code == 400


@pytest.mark.parametrize("method,url", [("get", "/api/jenframes"), ("post", "/api/jenframes/scan"),
                                        ("post", "/api/jenframes/remove")])
def test_routes_are_staff_only(client, method, url):
    c, _ = client
    r = getattr(c, method)(url, json={} if method == "post" else None,
                           environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert r.status_code == 403 and r.get_json()["staff_needed"] is True
