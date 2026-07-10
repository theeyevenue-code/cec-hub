"""Lens catalogue + best-option finder tests.

Unit tests hit hub.lenses directly; endpoint tests use the tmp-folder
fixture from conftest. No real lens data, no network.
"""

import io

from tests.conftest import SAMPLE_LENSES_CSV
from hub import lenses


def _sample():
    parsed, errors = lenses.parse_csv_text(SAMPLE_LENSES_CSV, "hoya.csv")
    assert errors == []
    return parsed


# --- Parsing -----------------------------------------------------------------

def test_parse_sample_rows():
    parsed = _sample()
    assert len(parsed) == 5
    nulux = parsed[0]
    assert nulux["brand"] == "Hoya"
    assert nulux["name"] == "Nulux 1.50"
    assert nulux["type"] == "stock"
    assert nulux["blank_mm"] == 70
    assert nulux["sph_min"] == -4.0 and nulux["sph_max"] == 4.0
    assert nulux["cyl_max"] == 2.0        # stored as a magnitude
    assert nulux["price"] == 18.50
    grind = parsed[-1]
    assert grind["type"] == "grind" and grind["blank_mm"] is None


def test_parse_header_aliases_units_and_range_column():
    text = ("Brand,Name,Diameter,Sphere Range,Max Cyl,Cost\n"
            "Hoya,Nulux 1.60,65mm,+6.00 to -8.00,-2.00,$32.00\n")
    parsed, errors = lenses.parse_csv_text(text, "x.csv")
    assert errors == []
    lens = parsed[0]
    assert lens["blank_mm"] == 65
    assert lens["sph_min"] == -8.0 and lens["sph_max"] == 6.0
    assert lens["price"] == 32.0
    assert lens["type"] == "stock"        # guessed: it has a blank size


def test_parse_type_guessed_grind_without_blank():
    text = "lens,sph_min,sph_max\nSV Lab,-10.00,+8.00\n"
    parsed, _ = lenses.parse_csv_text(text, "x.csv")
    assert parsed[0]["type"] == "grind"


def test_parse_collects_row_errors_and_keeps_good_rows():
    text = ("lens,sph_min,sph_max,price\n"
            "Good Lens,-2.00,+2.00,10.00\n"
            "No Range Lens,,,12.00\n"
            ",-1.00,+1.00,9.00\n")
    parsed, errors = lenses.parse_csv_text(text, "x.csv")
    assert len(parsed) == 1 and parsed[0]["name"] == "Good Lens"
    assert len(errors) == 2
    assert "sphere range" in errors[0]
    assert "no lens name" in errors[1]


def test_parse_rejects_file_without_name_column():
    parsed, errors = lenses.parse_csv_text("sph_min,sph_max\n-1,+1\n", "x.csv")
    assert parsed == []
    assert "no 'lens'" in errors[0]


def test_load_catalog_missing_folder_is_calm(tmp_path):
    cat = lenses.load_catalog(tmp_path / "nope")
    assert cat["lenses"] == [] and "No lens files loaded yet" in cat["message"]


def test_load_catalog_ignores_underscore_files(tmp_path):
    (tmp_path / "hoya.csv").write_text(SAMPLE_LENSES_CSV, encoding="utf-8")
    (tmp_path / "_template.csv").write_text(
        "lens,sph_min,sph_max\nEXAMPLE,-1,+1\n", encoding="utf-8")
    cat = lenses.load_catalog(tmp_path)
    assert [f["filename"] for f in cat["files"]] == ["hoya.csv"]
    assert all(l["name"] != "EXAMPLE" for l in cat["lenses"])


# --- Matching ----------------------------------------------------------------

def test_find_cheapest_stock_beats_grind():
    result = lenses.find_options(_sample(), sph=-3.0, cyl=-1.0)
    options = result["options"]
    assert options[0]["name"] == "Nulux 1.50" and options[0]["best"] is True
    prices = [o["price"] for o in options]
    assert prices == sorted(prices)
    assert options[1]["dearer_by"] == round(options[1]["price"] - 18.50, 2)
    assert "saves $26.50" in result["verdict"]


def test_find_higher_index_stock_beats_grind_when_150_runs_out():
    # -5.00 is past Nulux 1.50 but inside Stellify 1.50's range.
    result = lenses.find_options(_sample(), sph=-5.0, cyl=-0.5)
    assert result["options"][0]["name"] == "Stellify 1.50"
    assert "saves $24.00" in result["verdict"]


def test_find_grind_only_job():
    result = lenses.find_options(_sample(), sph=-9.0)
    assert [o["name"] for o in result["options"]] == ["SV Grind 1.50"]
    assert result["verdict"].startswith("No stock lens covers")


def test_find_grind_cheaper_than_stock_verdict():
    rows = ("lens,type,blank_mm,sph_min,sph_max,price\n"
            "Dear Stock,stock,70,-4,+4,50.00\n"
            "Cheap Grind,grind,,-10,+8,30.00\n")
    parsed, _ = lenses.parse_csv_text(rows, "x.csv")
    result = lenses.find_options(parsed, sph=-2.0)
    assert result["options"][0]["name"] == "Cheap Grind"
    assert result["verdict"].startswith("Grinding is actually cheaper")


def test_find_nothing_fits():
    result = lenses.find_options(_sample(), sph=-15.0)
    assert result["options"] == []
    assert "Nothing in the catalogue" in result["verdict"]


def test_find_transposes_plus_cyl():
    result = lenses.find_options(_sample(), sph=-2.0, cyl=1.0)
    assert result["rx"]["transposed"] is True
    assert result["rx"]["sph"] == -1.0 and result["rx"]["cyl"] == -1.0


def test_find_blank_size_rules_out_small_blanks():
    result = lenses.find_options(_sample(), sph=-3.0, min_blank=72)
    names = [o["name"] for o in result["options"]]
    assert "Stellify 1.50" in names          # 75mm blank
    assert "SV Grind 1.50" in names          # made to size
    assert "Nulux 1.50" not in names         # only 70mm
    miss = next(m for m in result["misses"] if m["name"] == "Nulux 1.50")
    assert any("70mm blank is smaller" in r for r in miss["reasons"])


def test_find_warns_when_limits_missing_instead_of_assuming():
    rows = ("lens,type,blank_mm,sph_min,sph_max,price\n"
            "No Cyl Info,stock,70,-4,+4,20.00\n"
            "No Blank Info,stock,,-4,+4,22.00\n")
    parsed, _ = lenses.parse_csv_text(rows, "x.csv")
    result = lenses.find_options(parsed, sph=-2.0, cyl=-1.0, min_blank=68)
    by_name = {o["name"]: o for o in result["options"]}
    assert any("cyl limit isn't in the file" in w
               for w in by_name["No Cyl Info"]["warnings"])
    assert any("blank size isn't in the file" in w
               for w in by_name["No Blank Info"]["warnings"])


def test_find_combined_power_limit():
    rows = ("lens,type,blank_mm,sph_min,sph_max,cyl_max,combined_max,price\n"
            "Tight Combined,stock,70,-6.00,+6.00,-4.00,6.00,20.00\n")
    parsed, _ = lenses.parse_csv_text(rows, "x.csv")
    ok = lenses.find_options(parsed, sph=-4.0, cyl=-1.0)
    assert len(ok["options"]) == 1
    too_much = lenses.find_options(parsed, sph=-5.0, cyl=-2.0)  # -7 combined
    assert too_much["options"] == []
    assert any("combined" in r for r in too_much["misses"][0]["reasons"])


# --- Endpoints -----------------------------------------------------------------

def test_api_catalog(hub_client_lenses):
    client, _ = hub_client_lenses
    data = client.get("/api/lenses").get_json()
    assert data["message"] == ""
    assert [f["filename"] for f in data["files"]] == ["hoya.csv"]
    assert len(data["lenses"]) == 5


def test_api_find_best_option(hub_client_lenses):
    client, _ = hub_client_lenses
    res = client.get("/api/lenses/find?sph=-3.00&cyl=-1.00")
    data = res.get_json()
    assert res.status_code == 200
    assert data["options"][0]["name"] == "Nulux 1.50"
    assert "Best value" in data["verdict"] or "saves" in data["verdict"]


def test_api_find_requires_sphere(hub_client_lenses):
    client, _ = hub_client_lenses
    res = client.get("/api/lenses/find")
    assert res.status_code == 400
    assert "sphere" in res.get_json()["error"].lower()


def test_api_find_rejects_silly_blank(hub_client_lenses):
    client, _ = hub_client_lenses
    res = client.get("/api/lenses/find?sph=-2.00&blank=200")
    assert res.status_code == 400


def test_api_upload_new_file(hub_client_lenses):
    client, lenses_dir = hub_client_lenses
    csv_bytes = ("lens,type,blank_mm,sph_min,sph_max,price\n"
                 "Shamir SV 1.50,stock,70,-6.00,+6.00,15.00\n").encode()
    res = client.post("/api/lenses/upload", data={
        "file": (io.BytesIO(csv_bytes), "Shamir Guide.CSV"),
        "name": "Shamir 2026!",
    }, content_type="multipart/form-data")
    data = res.get_json()
    assert res.status_code == 200
    assert data["filename"] == "shamir-2026.csv"
    assert data["count"] == 1 and data["replaced"] is False
    assert (lenses_dir / "shamir-2026.csv").is_file()
    # The new file shows up in the catalogue straight away.
    cat = client.get("/api/lenses").get_json()
    assert "shamir-2026.csv" in [f["filename"] for f in cat["files"]]


def test_api_upload_same_name_replaces(hub_client_lenses):
    client, _ = hub_client_lenses
    csv_bytes = ("lens,sph_min,sph_max,price\n"
                 "Nulux 1.50,-4.00,+4.00,19.00\n").encode()
    res = client.post("/api/lenses/upload", data={
        "file": (io.BytesIO(csv_bytes), "hoya.csv"),
    }, content_type="multipart/form-data")
    data = res.get_json()
    assert res.status_code == 200
    assert data["replaced"] is True
    cat = client.get("/api/lenses").get_json()
    hoya = next(f for f in cat["files"] if f["filename"] == "hoya.csv")
    assert hoya["count"] == 1


def test_api_upload_rejects_non_csv(hub_client_lenses):
    client, _ = hub_client_lenses
    res = client.post("/api/lenses/upload", data={
        "file": (io.BytesIO(b"%PDF-1.4"), "hoya-guide.pdf"),
    }, content_type="multipart/form-data")
    assert res.status_code == 400
    assert "CSV" in res.get_json()["error"]


def test_api_upload_rejects_unreadable_csv(hub_client_lenses):
    client, lenses_dir = hub_client_lenses
    res = client.post("/api/lenses/upload", data={
        "file": (io.BytesIO(b"just,some,words\nno,lens,data\n"), "junk.csv"),
    }, content_type="multipart/form-data")
    assert res.status_code == 400
    assert not (lenses_dir / "junk.csv").exists()


def test_api_upload_needs_a_file(hub_client_lenses):
    client, _ = hub_client_lenses
    res = client.post("/api/lenses/upload", data={},
                      content_type="multipart/form-data")
    assert res.status_code == 400
