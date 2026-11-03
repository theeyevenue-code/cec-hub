"""Frame stocktake page: sessions on disk, scan / +1 / -1 / undo, unlabelled frames, the cached
catalogue lookup (amber 'not in Optomate', UPC/EAN variants, offline). No agent, no Optomate."""
import importlib
import json

import pytest

from hub import stocktake as st

CAT = {"ok": True, "loaded": "2026-10-21T09:00:00",
       "suppliers": {"VMD": "Visual Merchandising", "GIE": "Green Infinity"},
       "frames": [{"b": "20000001", "d": "VMD VM100 BLK 52-18", "s": "VMD", "p": 249.0, "q": 1, "st": "C", "k": "f"},
                  {"b": "0123456789012", "d": "VMD VM700", "s": "VMD", "p": 199.0, "q": 0, "st": "O", "k": "f"},
                  {"b": "S0000009", "d": "Sunglass", "s": "VMD", "p": 99.0, "q": 1, "st": "C", "k": "s"}]}


@pytest.fixture
def st_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CEC_HUB_STOCKTAKE_DIR", str(tmp_path / "stocktake"))
    monkeypatch.delenv("CEC_HUB_INTEGRATIONS", raising=False)
    st.reset_cache()
    monkeypatch.setattr(st, "run_catalogue", lambda cfg: CAT)
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c, tmp_path / "stocktake"
    st.reset_cache()


def _new(c, name="Frames Oct"):
    r = c.post("/api/stocktake/sessions", json={"name": name})
    assert r.status_code == 200
    return r.get_json()["id"]


def test_create_and_list_sessions(st_client):
    c, d = st_client
    sid = _new(c)
    assert st.SESSION_RE.match(sid)
    saved = json.loads((d / f"{sid}.json").read_text(encoding="utf-8"))
    assert saved["name"] == "Frames Oct" and saved["scans"] == [] and saved["branch"] == "MJ"
    lst = c.get("/api/stocktake/sessions").get_json()["sessions"]
    assert lst[0]["id"] == sid and lst[0]["frames"] == 0
    assert c.get("/api/stocktake/sessions").headers["Cache-Control"] == "no-store"


def test_scan_counts_and_shows_optomate(st_client):
    c, d = st_client
    sid = _new(c)
    r = c.post(f"/api/stocktake/session/{sid}/scan", json={"barcode": " 20000001\r", "area": "Wall 1"}).get_json()
    assert r["ok"] and r["count"] == 1 and r["state"] == "ok"
    assert r["description"].startswith("VMD VM100") and r["qoh"] == 1 and r["sell"] == 249.0
    r = c.post(f"/api/stocktake/session/{sid}/scan", json={"barcode": "20000001", "area": "Wall 2"}).get_json()
    assert r["count"] == 2 and r["totals"]["frames"] == 2
    v = c.get(f"/api/stocktake/session/{sid}").get_json()
    assert v["rows"][0]["count"] == 2 and v["rows"][0]["areas"] == ["Wall 1", "Wall 2"]
    assert {a["area"] for a in v["areas"]} == {"Wall 1", "Wall 2"}


def test_unknown_barcode_is_amber_and_upc_finds_ean(st_client):
    c, _ = st_client
    sid = _new(c)
    r = c.post(f"/api/stocktake/session/{sid}/scan", json={"barcode": "NOPE<script>", "area": "W"}).get_json()
    assert r["state"] == "unknown" and r["barcode"] == "NOPESCRIPT"
    r = c.post(f"/api/stocktake/session/{sid}/scan", json={"barcode": "123456789012", "area": "W"}).get_json()
    assert r["state"] == "ok" and r["status"] == "O"
    r = c.post(f"/api/stocktake/session/{sid}/scan", json={"barcode": "S0000009", "area": "W"}).get_json()
    assert r["kind"] == "sunglass"


def test_empty_scan_refused(st_client):
    c, _ = st_client
    sid = _new(c)
    r = c.post(f"/api/stocktake/session/{sid}/scan", json={"barcode": "  ", "area": "W"})
    assert r.status_code == 400


def test_plus_minus_and_undo(st_client):
    c, d = st_client
    sid = _new(c)
    c.post(f"/api/stocktake/session/{sid}/scan", json={"barcode": "20000001", "area": "W"})
    r = c.post(f"/api/stocktake/session/{sid}/adjust", json={"barcode": "20000001", "area": "W", "delta": 1}).get_json()
    assert r["count"] == 2
    r = c.post(f"/api/stocktake/session/{sid}/adjust", json={"barcode": "20000001", "area": "W", "delta": -1}).get_json()
    assert r["count"] == 1
    assert c.post(f"/api/stocktake/session/{sid}/adjust", json={"barcode": "20000001", "delta": 5}).status_code == 400
    r = c.post(f"/api/stocktake/session/{sid}/undo", json={}).get_json()
    assert r["ok"] and r["totals"]["frames"] == 2                   # the -1 is undone
    c.post(f"/api/stocktake/session/{sid}/undo", json={})
    c.post(f"/api/stocktake/session/{sid}/undo", json={})
    assert c.post(f"/api/stocktake/session/{sid}/undo", json={}).status_code == 400
    assert c.post(f"/api/stocktake/session/{sid}/adjust",
                  json={"barcode": "20000001", "delta": -1}).status_code == 400   # never below 0
    saved = json.loads((d / f"{sid}.json").read_text(encoding="utf-8"))
    assert saved["scans"] == [] and len(saved["undone"]) == 3


def test_unlabelled_add_and_remove(st_client):
    c, d = st_client
    sid = _new(c)
    assert c.post(f"/api/stocktake/session/{sid}/unlabelled", json={"qty": 1}).status_code == 400
    assert c.post(f"/api/stocktake/session/{sid}/unlabelled",
                  json={"model": "X", "qty": "200"}).status_code == 400
    r = c.post(f"/api/stocktake/session/{sid}/unlabelled",
               json={"supplier": "GIE", "model": "Verona Aria", "colour": "C2", "size": "50-18",
                     "qty": "2", "area": "Drawer 3"}).get_json()
    assert r["ok"] and r["totals"]["unlabelled"] == 2
    uid = r["entry"]["id"]
    v = c.get(f"/api/stocktake/session/{sid}").get_json()
    assert v["unlabelled"][0]["model"] == "Verona Aria"
    assert c.post(f"/api/stocktake/session/{sid}/unlabelled-remove", json={"id": uid}).get_json()["ok"]
    saved = json.loads((d / f"{sid}.json").read_text(encoding="utf-8"))
    assert saved["unlabelled"] == []


def test_bad_or_missing_session_is_404(st_client):
    c, _ = st_client
    assert c.get("/api/stocktake/session/../../etc").status_code == 404
    assert c.get("/api/stocktake/session/20261021-0900-abcd").status_code == 404
    assert c.post("/api/stocktake/session/20261021-0900-abcd/scan", json={"barcode": "1"}).status_code == 404


def test_offline_catalogue_still_saves_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("CEC_HUB_STOCKTAKE_DIR", str(tmp_path))
    st.reset_cache()
    calls = []
    monkeypatch.setattr(st, "run_catalogue", lambda cfg: calls.append(1) or {"ok": False, "error": "Optomate not connected."})
    sid = st.create_session("x", "ZZ")["id"]
    r = st.scan(sid, "20000001", "W", "ZZ", lambda: st.get_catalogue({}))
    assert r["ok"] and r["state"] == "offline"
    st.get_catalogue({})
    assert len(calls) == 1                                   # a failed load is not retried at once
    st.reset_cache()


def test_suppliers_and_refresh(st_client):
    c, _ = st_client
    s = c.get("/api/stocktake/suppliers").get_json()["suppliers"]
    assert {"id": "GIE", "name": "Green Infinity"} in s
    r = c.post("/api/stocktake/refresh", json={}).get_json()
    assert r["ok"] and r["frames"] == 3


def test_run_catalogue_not_connected(tmp_path):
    assert st.run_catalogue({"optomate_agent": {"agent_dir": str(tmp_path)}})["ok"] is False
    assert st.run_catalogue({})["ok"] is False


def test_stocktake_tile_in_template():
    tiles = json.loads(open("config/tiles.example.json", encoding="utf-8").read())["tiles"]
    assert any(t["id"] == "stocktake" and t["link"] == "#/stocktake" for t in tiles)
