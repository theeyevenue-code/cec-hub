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


def test_parse_product_code_aliases():
    text = ("lens,lenstype,sph_min,sph_max\n"
            "Nulux,S-NULUX,-4.00,+4.00\n")
    parsed, errors = lenses.parse_csv_text(text, "x.csv")
    assert errors == []
    assert parsed[0]["code"] == "S-NULUX"


def test_parse_category_and_form():
    # 'form' must be its own column (spheric/aspheric), NOT an alias of type.
    text = ("lens,category,form,index,type,price\n"
            "MySelf,Progressive,Freeform,1.50,grind,89.10\n"
            "Nulux,Single vision,Aspheric,1.50,stock,9.90\n")
    parsed, errors = lenses.parse_csv_text(text, "x.csv")
    assert errors == []
    assert parsed[0]["category"] == "Progressive"
    assert parsed[0]["type"] == "grind" and parsed[0]["form"] == "Freeform"
    assert parsed[1]["category"] == "Single vision" and parsed[1]["form"] == "Aspheric"


def test_sv_only_excludes_multifocals():
    text = ("lens,category,index,type,sph_min,sph_max,price\n"
            "SV Lens,Single vision,1.50,stock,-6,+6,10\n"
            "Prog Lens,Progressive,1.50,grind,,,50\n"
            "Untagged,,1.50,stock,-4,+4,12\n")
    parsed, _ = lenses.parse_csv_text(text, "x.csv")
    names = {l["name"] for l in lenses.sv_only(parsed)}
    assert names == {"SV Lens", "Untagged"}   # untagged files treated as all SV


def test_parse_type_guessed_grind_without_blank():
    text = "lens,sph_min,sph_max\nSV Lab,-10.00,+8.00\n"
    parsed, _ = lenses.parse_csv_text(text, "x.csv")
    assert parsed[0]["type"] == "grind"


def test_parse_collects_row_errors_and_keeps_good_rows():
    text = ("lens,sph_min,sph_max,price\n"
            "Good Lens,-2.00,+2.00,10.00\n"
            "Half Range Lens,-2.00,,12.00\n"
            ",-1.00,+1.00,9.00\n")
    parsed, errors = lenses.parse_csv_text(text, "x.csv")
    assert len(parsed) == 1 and parsed[0]["name"] == "Good Lens"
    assert len(errors) == 2
    assert "half a sphere range" in errors[0]
    assert "no lens name" in errors[1]


def test_parse_allows_missing_sphere_range():
    # Price lists usually don't state ranges — the row still loads.
    text = "lens,type,blank_mm,price\nStock Only Price,stock,70,12.00\n"
    parsed, errors = lenses.parse_csv_text(text, "x.csv")
    assert errors == []
    assert parsed[0]["sph_min"] is None and parsed[0]["sph_max"] is None


def test_parse_blank_diameter_list_uses_largest():
    text = "lens,type,blank_mm,sph_min,sph_max\nMulti Blank,stock,65/70/75,-4,+4\n"
    parsed, _ = lenses.parse_csv_text(text, "x.csv")
    assert parsed[0]["blank_mm"] == 75


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

def test_recommended_index_thresholds():
    # Concord's thinner-leaning table: <=2 -> 1.50, <=4 -> 1.60, <=6 -> 1.67.
    assert lenses.recommended_index(-1.50) == 1.50
    assert lenses.recommended_index(-3.00) == 1.60
    assert lenses.recommended_index(-6.00) == 1.67
    assert lenses.recommended_index(-8.00) == 1.74
    # strongest meridian, not just sphere: -3.00/-1.50 cyl -> -4.50 -> 1.67
    assert lenses.recommended_index(-3.00, -1.50) == 1.67


def test_find_prefers_index_appropriate_over_cheapest():
    # -3.00/-1.00 -> strongest meridian 4.00 -> wants 1.60. The 1.50s fit but
    # come out thick, so the 1.60 leads and the cheaper 1.50 is flagged.
    result = lenses.find_options(_sample(), sph=-3.0, cyl=-1.0)
    assert result["rec_index"] == 1.60
    best = result["options"][0]
    assert best["name"] == "Nulux 1.60" and best["best"] is True
    n150 = next(o for o in result["options"] if o["name"] == "Nulux 1.50")
    assert n150["under_index"] is True and not n150.get("best")
    assert "too thick" in result["verdict"]


def test_find_flags_when_no_appropriate_index_is_loaded():
    # -5.00/-0.50 -> wants 1.67, which the sample doesn't have, so the best is
    # the thinnest that fits and it's flagged rather than silently recommended.
    result = lenses.find_options(_sample(), sph=-5.0, cyl=-0.5)
    assert result["rec_index"] == 1.67
    best = result["options"][0]
    assert best["best"] is True and best["under_index"] is True
    assert "1.67" in result["verdict"] and "thick" in result["verdict"]


def test_find_grind_only_job():
    result = lenses.find_options(_sample(), sph=-9.0)
    assert [o["name"] for o in result["options"]] == ["SV Grind 1.50"]
    assert result["options"][0]["best"] is True
    assert "as a grind" in result["verdict"]


def test_find_grind_cheaper_than_stock():
    # No index column -> nothing is flagged thick, so the cheapest fit wins.
    rows = ("lens,type,blank_mm,sph_min,sph_max,price\n"
            "Dear Stock,stock,70,-4,+4,50.00\n"
            "Cheap Grind,grind,,-10,+8,30.00\n")
    parsed, _ = lenses.parse_csv_text(rows, "x.csv")
    result = lenses.find_options(parsed, sph=-2.0)
    assert result["options"][0]["name"] == "Cheap Grind"
    assert "Cheap Grind" in result["verdict"] and "as a grind" in result["verdict"]


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


def test_find_rangeless_lens_matches_with_warning():
    rows = ("lens,type,blank_mm,price\n"
            "No Range Stock,stock,70,5.00\n")
    parsed, _ = lenses.parse_csv_text(rows, "x.csv")
    result = lenses.find_options(parsed, sph=-7.0)
    assert len(result["options"]) == 1
    assert any("power range isn't in the file" in w
               for w in result["options"][0]["warnings"])
    assert "amber notes" in result["verdict"]


def test_find_combined_power_limit():
    rows = ("lens,type,blank_mm,sph_min,sph_max,cyl_max,combined_max,price\n"
            "Tight Combined,stock,70,-6.00,+6.00,-4.00,6.00,20.00\n")
    parsed, _ = lenses.parse_csv_text(rows, "x.csv")
    ok = lenses.find_options(parsed, sph=-4.0, cyl=-1.0)
    assert len(ok["options"]) == 1
    too_much = lenses.find_options(parsed, sph=-5.0, cyl=-2.0)  # -7 combined
    assert too_much["options"] == []
    assert any("combined" in r for r in too_much["misses"][0]["reasons"])


# --- Whole-job check (both eyes) ---------------------------------------------

def test_min_blank_from_frame():
    # ED default (52+2) + (52+18-62)=8 decentration + 2 spare = 64.
    assert lenses.min_blank_from_frame({"a": 52, "dbl": 18, "pd": 62}) == 64
    # Monocular PDs (Optomate stores per-eye) get doubled: 31*2=62.
    assert lenses.min_blank_from_frame({"a": 52, "dbl": 18, "pd": 31}) == 64
    # ED wins over eye-size guess when given.
    assert lenses.min_blank_from_frame({"a": 52, "dbl": 18, "pd": 62,
                                        "ed": 56}) == 66
    assert lenses.min_blank_from_frame({"a": 52}) is None
    assert lenses.min_blank_from_frame({}) is None


def test_check_job_worse_eye_decides():
    # Right eye -3.00 fits Nulux 1.50, left eye -5.00 doesn't — the pair
    # must land on a product covering BOTH.
    result = lenses.check_job(_sample(), right={"sph": -3.0},
                              left={"sph": -5.0})
    assert result["status"] == "stock"
    assert result["best"]["name"] == "Stellify 1.50"
    assert result["best"]["price_job"] == 42.00          # per pair
    assert "a pair" in result["headline"]


def test_check_job_single_eye_prices_per_lens():
    result = lenses.check_job(_sample(), right={"sph": -3.0})
    assert result["best"]["price_job"] == result["best"]["price"]
    assert "a lens" in result["headline"]


def test_check_job_grind_only_and_stock_mismatch_flag():
    result = lenses.check_job(_sample(), right={"sph": -9.0},
                              chosen={"type": "Stk"})
    assert result["status"] == "check"                   # said Stock, isn't
    assert any("marked Stock" in n for n in result["chosen"]["notes"])


def test_check_job_flags_grind_when_stock_possible():
    result = lenses.check_job(_sample(), right={"sph": -3.0},
                              chosen={"type": "Grd", "code": "MADE-UP-1"})
    assert result["status"] == "check"
    notes = result["chosen"]["notes"]
    assert any("marked Grind, but a stock lens covers" in n for n in notes)
    assert any("isn't in the loaded price files" in n for n in notes)
    assert result["chosen"]["code_known"] is False


def test_check_job_without_rx():
    result = lenses.check_job(_sample())
    assert result["status"] == "no_rx"


# --- Endpoints -----------------------------------------------------------------

def test_api_catalog(hub_client_lenses):
    client, _ = hub_client_lenses
    data = client.get("/api/lenses").get_json()
    assert data["message"] == ""
    assert [f["filename"] for f in data["files"]] == ["hoya.csv"]
    # The library now serves grouped products (one row per lens+index+type);
    # the five distinct sample lenses stay five products.
    assert len(data["products"]) == 5
    assert all("coatings" in p and "price_from" in p for p in data["products"])


def test_api_find_best_option(hub_client_lenses):
    client, _ = hub_client_lenses
    res = client.get("/api/lenses/find?sph=-3.00&cyl=-1.00")
    data = res.get_json()
    assert res.status_code == 200
    # -3.00/-1.00 wants 1.60, so the 1.60 leads (not the cheaper 1.50).
    assert data["options"][0]["name"] == "Nulux 1.60"
    assert data["rec_index"] == 1.60
    assert "Best value" in data["verdict"]


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


def test_api_check_endpoint(hub_client_lenses):
    client, _ = hub_client_lenses
    res = client.post("/api/lenses/check", json={
        "right": {"sph": -3.0, "cyl": -1.0}, "left": {"sph": -5.0},
        "frame": {"a": 52, "dbl": 18, "pd": 62},
        "chosen": {"type": "Grd"},
    })
    data = res.get_json()
    assert res.status_code == 200
    assert data["status"] == "check"
    assert data["min_blank"] == 64
    assert data["best"]["type"] == "stock"
    assert data["eyes"]["right"]["rx"] == "-3.00 / -1.00"


def test_api_check_requires_an_eye(hub_client_lenses):
    client, _ = hub_client_lenses
    res = client.post("/api/lenses/check", json={"frame": {"a": 52}})
    assert res.status_code == 400


def test_api_jobs_disconnected(hub_client_lenses):
    client, _ = hub_client_lenses
    data = client.get("/api/lenses/jobs").get_json()
    assert data["connected"] is False


def test_api_jobs_checked_against_catalogue(hub_client_lens_jobs):
    data = hub_client_lens_jobs.get("/api/lenses/jobs").get_json()
    assert data["connected"] is True
    jobs = {j["job"]: j for j in data["jobs"]}
    assert list(jobs) == ["31656", "31655"]      # newest first
    # 31655: -3.00/-1.00 and -2.75, marked Grd, but stock covers both eyes.
    check = jobs["31655"]["check"]
    assert check["status"] == "check"
    assert check["min_blank"] == 64
    assert any("marked Grind" in n for n in check["chosen"]["notes"])
    # 31656: -9.00/-9.25 marked Stk — only the grind lens covers it.
    check = jobs["31656"]["check"]
    assert check["status"] == "check"
    assert any("marked Stock" in n for n in check["chosen"]["notes"])


# --- Per-machine declutter filter (apply_lens_filter) ------------------------

def _catalogue(*rows):
    """A tiny catalogue dict from (brand, name, category) triples."""
    items = [{"brand": b, "name": n, "category": c, "source": "hoya.csv"}
             for b, n, c in rows]
    return {"lenses": items, "message": "",
            "files": [{"filename": "hoya.csv", "count": len(items),
                       "errors": []}]}


def test_filter_keeps_only_named_ranges_within_that_category():
    cat = _catalogue(
        ("Hoya", "Hoyalux Dynamic Prime Eyas", "Progressive"),
        ("Hoya", "Hoyalux Dynamic Premium Eyas", "Progressive"),
        ("Hoya", "Hoyalux iD LifeStyle 4 Phoenix", "Progressive"),
        ("Hoya", "Hoyalux iD LifeStyle Balansis", "Progressive"),
        ("Hoya", "Hoyalux iD MySelf Profile", "Progressive"),
        ("Hoya", "Hoyalux iD MySelf Eynoa", "Progressive"),
        ("Hoya", "Nulux 1.50", "Single vision"),
    )
    out = lenses.apply_lens_filter(cat, {"keep_only": {
        "Progressive": ["Dynamic Prime", "iD LifeStyle 4", "iD MySelf Profile"]}})
    names = {l["name"] for l in out["lenses"]}
    # Loose match keeps the range's variants, but 'Prime' != 'Premium',
    # 'LifeStyle 4' != 'Balansis', and 'MySelf Profile' != plain 'MySelf'.
    assert names == {
        "Hoyalux Dynamic Prime Eyas",
        "Hoyalux iD LifeStyle 4 Phoenix",
        "Hoyalux iD MySelf Profile",
        "Nulux 1.50",  # a category not in keep_only — left whole
    }
    # the library's per-file count reflects what survived
    assert out["files"][0]["count"] == 4


def test_filter_empty_or_absent_config_is_a_no_op():
    cat = _catalogue(("Hoya", "Hoyalux Dynamic Premium", "Progressive"))
    for cfg in ({}, {"keep_only": {}}, None):
        assert lenses.apply_lens_filter(cat, cfg)["lenses"] == cat["lenses"]


# --- Library grouping (group_products) --------------------------------------

def _flat(name, index, typ, coating, price, blank, smin, smax, cyl=None):
    return {"brand": "Hoya", "name": name, "index": index, "type": typ,
            "category": "Single vision", "code": "S-" + name.upper(),
            "notes": "", "source": "hoya.csv", "coating": coating,
            "price": price, "blank_mm": blank,
            "sph_min": smin, "sph_max": smax, "cyl_max": cyl}


def test_group_products_folds_coatings_and_blanks_into_one_row():
    # One lens, two coatings, each split across two blank/power bands.
    flat = [
        _flat("Nulux", "1.50", "stock", "ViewProtect", 9.90, 75, -3.50, 0.0, 2.0),
        _flat("Nulux", "1.50", "stock", "ViewProtect", 9.90, 70, -6.0, -3.75, 2.0),
        _flat("Nulux", "1.50", "stock", "Full Control", 18.0, 75, -3.50, 0.0, 2.0),
        _flat("Nulux", "1.50", "stock", "Full Control", 18.0, 70, -6.0, -3.75, 2.0),
    ]
    prods = lenses.group_products(flat)
    assert len(prods) == 1
    p = prods[0]
    assert (p["sph_min"], p["sph_max"]) == (-6.0, 0.0)  # widest across bands
    assert p["cyl_max"] == 2.0
    assert p["blanks"] == [70, 75]
    assert p["price_from"] == 9.90  # cheapest coating
    # coatings cheapest-first, price lossless per coating
    assert [(c["coating"], c["price"]) for c in p["coatings"]] == [
        ("ViewProtect", 9.90), ("Full Control", 18.0)]
    # each coating keeps its power->blank bands (which blank a power comes on)
    vp = p["coatings"][0]
    assert [(b["sph_min"], b["sph_max"], b["blank"]) for b in vp["bands"]] == [
        (-6.0, -3.75, 70), (-3.50, 0.0, 75)]


def test_group_products_splits_by_index_and_type():
    flat = [
        _flat("Nulux", "1.50", "stock", "VP", 9.9, 70, -6.0, 0.0),
        _flat("Nulux", "1.60", "stock", "VP", 12.0, 70, -6.0, 0.0),
        _flat("Nulux", "1.50", "grind", "VP", 27.8, 70, -9.5, 0.0),
    ]
    keys = sorted((p["name"], p["index"], p["type"])
                  for p in lenses.group_products(flat))
    assert keys == [("Nulux", "1.50", "grind"),
                    ("Nulux", "1.50", "stock"),
                    ("Nulux", "1.60", "stock")]
