"""Today's patients (hub/recent_rx.py + GET /api/lenses/recent). Fictional data only.

What matters: the subprocess only ever gets fixed, validated arguments; every
failure comes back as a friendly message; nothing but the whitelisted fields
reaches the page; and the one worked-out number (readers = sphere + add) is
right and always carries its working.
"""

import importlib
import json

import pytest

from hub import recent_rx as R


def eye(sph, cyl=None, axis=None, add=None, pd=None, intadd=None, prism=None):
    return {"sph": sph, "cyl": cyl, "axis": axis, "add": add, "intadd": intadd,
            "pd": pd, "prism": prism}


def script(right, left, **kw):
    return {"label": "Sam K", "time": "11:42am", "optom": "MJ", "status": "script",
            "right": right, "left": left, "flags": [], **kw}


# --- readers arithmetic + its working -------------------------------------------
def test_readers_is_sphere_plus_add_with_working_shown():
    m = R.modes(script(eye("+1.00", "-0.50", "90", "+2.00"), eye("+1.25", add="+2.00")))
    r = m["readers"]
    assert r["line"] == "R +1.00 + add +2.00 = +3.00 · L +1.25 + add +2.00 = +3.25"
    assert r["right"] == {"sph": 3.0, "cyl": -0.5}      # cyl unchanged
    assert r["left"] == {"sph": 3.25, "cyl": 0.0}
    assert "Cyl and axis" in r["cyl_note"]


def test_readers_from_minus_and_plano_eyes():
    m = R.modes(script(eye("-2.75", add="+2.50"), eye("0.00", add="+2.50")))
    assert m["readers"]["line"] == "R -2.75 + add +2.50 = -0.25 · L 0.00 + add +2.50 = +2.50"
    assert m["readers"]["right"]["sph"] == -0.25


def test_readers_crossing_to_zero_reads_as_plano():
    m = R.modes(script(eye("-2.00", add="+2.00"), eye("-1.75", add="+2.00")))
    assert "R -2.00 + add +2.00 = 0.00" in m["readers"]["line"]


def test_plano_word_counts_as_zero():
    m = R.modes(script(eye("PLANO", add="+1.50"), eye("+0.50", add="+1.50")))
    assert m["readers"]["line"].startswith("R 0.00 + add +1.50 = +1.50")


def test_eye_without_its_own_add_is_left_as_written_and_said_so():
    m = R.modes(script(eye("+1.00", add="+2.00"), eye("+1.50")))
    assert "L +1.50 (no add on the script for this eye)" in m["readers"]["line"]
    assert m["readers"]["left"]["sph"] == 1.5
    assert m["readers"]["notes"]


def test_no_add_means_distance_only():
    m = R.modes(script(eye("-1.00", "-0.25", "180"), eye("-1.25")))
    assert set(m) == {"distance"}
    assert m["distance"]["line"] == "R -1.00 / -0.25 · L -1.25"
    assert m["distance"]["kind"] == "Single vision"


def test_multifocal_passes_add_and_kind_and_flags_differing_adds():
    m = R.modes(script(eye("+1.00", add="+2.00"), eye("+1.00", add="+2.25")))["multifocal"]
    assert m["kind"] == "Progressive" and m["add"] == 2.25
    assert m["right"] == {"sph": 1.0, "cyl": 0.0}     # distance powers, not readers
    assert m["notes"] and "+2.25" in m["notes"][0]


def test_balance_or_blank_eye_is_not_checked():
    m = R.modes(script(eye("BALANCE"), eye("-3.00")))
    assert m["distance"]["right"] is None and m["distance"]["left"] == {"sph": -3.0, "cyl": 0.0}


# --- PD ------------------------------------------------------------------------
def test_binocular_pd_is_r_plus_l_with_working():
    assert R.binocular_pd(script(eye("+1", pd="31.5"), eye("+1", pd="32"))) == \
        {"value": 63.5, "line": "PD 31.5 + 32 = 63.5"}
    assert R.binocular_pd(script(eye("+1", pd="31"), eye("+1", pd="31.0"))) == \
        {"value": 62.0, "line": "PD 31 + 31 = 62"}
    assert R.binocular_pd(script(eye("+1", pd="31"), eye("+1"))) is None


# --- enrich: whitelist ----------------------------------------------------------
def test_enrich_keeps_only_whitelisted_fields():
    raw = script(eye("+1.00", add="+2.00", pd="31"), eye("+1.00", add="+2.00", pd="31"),
                 patient_id=99, dob="2001-01-01",
                 flags=[{"code": "prism", "text": "Prism R 1.00 IN"}])
    raw["right"]["surname"] = "Kerrigan"
    out = R.enrich(raw)
    blob = json.dumps(out)
    assert "99" not in blob and "2001" not in blob and "Kerrigan" not in blob
    assert out["flags"] == ["Prism R 1.00 IN"]
    assert out["has_add"] and out["checkable"] and out["pd"]["value"] == 62.0


def test_enrich_no_script():
    out = R.enrich({"label": "Jo P", "time": "9:00am", "optom": "NP", "status": "no_script"})
    assert out["status"] == "no_script" and "modes" not in out


# --- the subprocess --------------------------------------------------------------
@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "pulls").mkdir()
    (tmp_path / "pulls" / "recent_rx.py").write_text("# stub", encoding="utf-8")
    return {"optomate_agent": {"agent_dir": str(tmp_path), "python": "py"}}


class Proc:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0


def ok_line(**extra):
    return json.dumps({"ok": True, "date": "2026-10-03", "more": False, "patients": [
        script(eye("+1.00", add="+2.00"), eye("+1.25", add="+2.00")),
        {"label": "Jo P", "time": "9:00am", "optom": "NP", "status": "no_script"}], **extra})


def test_recent_runs_fixed_args(cfg, monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return Proc("noise\n" + ok_line() + "\n")
    monkeypatch.setattr(R.subprocess, "run", fake)
    out = R.recent(cfg)
    assert seen["argv"] == ["py", "-m", "pulls.recent_rx", "--json", "--n", "3"]
    assert seen["kw"]["timeout"] == R.TIMEOUT_S and seen["kw"]["cwd"] == cfg["optomate_agent"]["agent_dir"]
    assert out["connected"] and len(out["patients"]) == 2
    assert out["patients"][0]["modes"]["readers"]["right"]["sph"] == 3.0


def test_recent_passes_a_valid_date_and_fixture_only(cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(R.subprocess, "run",
                        lambda argv, **kw: seen.append(argv) or Proc(ok_line()))
    R.recent(cfg, 3, "2026-10-03", True)
    R.recent(cfg, 3, "2026-10-03; rm -rf /", False)
    R.recent(cfg, "lots", "", False)
    assert seen[0][-3:] == ["--date", "2026-10-03", "--fixture"]
    assert "--date" not in seen[1]
    assert seen[2][-2:] == ["--n", "3"]


def test_recent_not_deployed_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: pytest.fail("ran"))
    assert R.recent({"optomate_agent": {"agent_dir": str(tmp_path)}})["connected"] is False
    assert R.recent({})["connected"] is False


@pytest.mark.parametrize("behaviour,expect", [
    ("timeout", "too long"), ("oserror", None), ("garbage", "Couldn't read"),
    ("empty", "Couldn't read"), ("notok", "Couldn't read Optomate (x)."),
])
def test_recent_failures_are_friendly(cfg, monkeypatch, behaviour, expect):
    def fake(argv, **kw):
        if behaviour == "timeout":
            raise R.subprocess.TimeoutExpired(argv, 15)
        if behaviour == "oserror":
            raise OSError("nope")
        return Proc({"garbage": "{not json", "empty": "",
                     "notok": json.dumps({"ok": False, "error": "Couldn't read Optomate (x)."})}[behaviour])
    monkeypatch.setattr(R.subprocess, "run", fake)
    out = R.recent(cfg)
    if behaviour == "oserror":
        assert out["connected"] is False
    else:
        assert expect in out["error"]


# --- the endpoint ----------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch, cfg):
    p = tmp_path / "integrations.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("CEC_HUB_INTEGRATIONS", str(p))
    monkeypatch.setenv("CEC_HUB_RECENT_RX_DATE", "2026-10-03")
    monkeypatch.delenv("CEC_HUB_RECENT_RX_FIXTURE", raising=False)
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_endpoint_is_no_store_and_uses_the_test_date(client, monkeypatch):
    seen = []
    monkeypatch.setattr(R.subprocess, "run",
                        lambda argv, **kw: seen.append(argv) or Proc(ok_line()))
    res = client.get("/api/lenses/recent?date=2020-01-01&n=50")   # URL args ignored
    assert res.status_code == 200
    assert res.headers["Cache-Control"] == "no-store"
    assert seen[0][-4:] == ["--n", "3", "--date", "2026-10-03"]
    body = res.get_json()
    assert body["patients"][1]["status"] == "no_script"


def test_endpoint_degrades_when_agent_missing(client, monkeypatch, cfg):
    import shutil
    shutil.rmtree(cfg["optomate_agent"]["agent_dir"] + "/pulls")
    res = client.get("/api/lenses/recent")
    assert res.status_code == 200 and res.get_json()["connected"] is False
