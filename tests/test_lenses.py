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
    assert result["verdict"].startswith("GRIND")


def test_find_grind_cheaper_than_stock():
    # No index column -> nothing is flagged thick. By practice rule (D1, Sep
    # 2026) a stock lens that fits leads even when a grind is cheaper, and the
    # verdict says what the grind would have saved. prefer_stock=False gives
    # the old cheapest-wins order.
    rows = ("lens,type,blank_mm,sph_min,sph_max,price\n"
            "Dear Stock,stock,70,-4,+4,50.00\n"
            "Cheap Grind,grind,,-10,+8,30.00\n")
    parsed, _ = lenses.parse_csv_text(rows, "x.csv")
    result = lenses.find_options(parsed, sph=-2.0)
    assert result["options"][0]["name"] == "Dear Stock"
    assert result["options"][0]["best"] is True
    assert "Cheap Grind" in result["verdict"] and "$20.00 a lens less" in result["verdict"]
    assert "practice rule" in result["verdict"]
    cheapest = lenses.find_options(parsed, sph=-2.0, pricing={"prefer_stock": False})
    assert cheapest["options"][0]["name"] == "Cheap Grind"
    assert cheapest["verdict"].startswith("GRIND") and "Cheap Grind" in cheapest["verdict"]


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
    assert data["verdict"].startswith("STOCK covers this")


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


def test_mark_preferred_floats_everyday_lenses_and_honours_exclude():
    prods = lenses.group_products([
        _flat("Nulux", "1.50", "stock", "VP", 9.9, 70, -6.0, 0.0),
        _flat("Nulux Sportive", "1.50", "grind", "VP", 20.0, 70, -6.0, 0.0),
        _flat("EnRoute Eyas", "1.60", "grind", "VP", 30.0, 70, -6.0, 0.0),
    ])
    cfg = {"preferred": {"match": ["Nulux"], "exclude": ["Sportive"]}}
    out = lenses.mark_preferred(prods, cfg)
    by = {p["name"]: p for p in out}
    assert by["Nulux"]["preferred"] is True
    assert by["Nulux Sportive"]["preferred"] is False   # vetoed by exclude
    assert by["EnRoute Eyas"]["preferred"] is False      # no match
    # preferred float to the front
    assert out[0]["name"] == "Nulux"


def test_attach_cec_price_design_sv_tiers_and_surcharge():
    prods = lenses.group_products([
        # progressive: priced by design+index, same across coatings
        _flat("Hoyalux Dynamic Prime", "1.60", "grind", "Hi-Vision ViewProtect", 30.0, 70, None, None),
        _flat("Hoyalux Dynamic Prime", "1.60", "grind", "Full Control", 39.0, 70, None, None),
        # photochromic progressive: base + $120
        _flat("Hoyalux Dynamic Prime Sensity 2", "1.60", "grind", "Hi-Vision ViewProtect", 42.0, 70, None, None),
        # single vision: price depends on the coating tier
        _flat("Nulux Eynoa", "1.67", "stock", "Hi-Vision ViewProtect", 27.8, 70, -9.5, 0.0),
        _flat("Nulux Eynoa", "1.67", "stock", "Diamond Finish UV Control", 38.1, 70, -9.5, 0.0),
        # polarised: blanked (varies)
        _flat("Nulux Eynoa Polarised", "1.67", "stock", "Diamond Finish UV Control", 38.1, 70, -9.5, 0.0),
    ])
    cfg = {
        "surcharge": {"Sensity": 120},
        "blank_names": ["Polarised"],
        "design_prices": [{"match": "Dynamic Prime", "by_index": {"1.60": 530}}],
        "sv_tiers": [
            {"coating": "Diamond Finish", "by_index": {"1.67": 290}},
            {"coating": "ViewProtect", "by_index": {"1.67": 240}},
        ],
    }
    out = {p["name"]: p for p in lenses.attach_cec_price(prods, cfg)}
    # progressive: same design price on every coating
    assert all(c["cec_price"] == 530 for c in out["Hoyalux Dynamic Prime"]["coatings"])
    # photochromic: +$120
    assert out["Hoyalux Dynamic Prime Sensity 2"]["coatings"][0]["cec_price"] == 650
    # single vision: VP=standard, Diamond Finish=premium
    sv = {c["coating"]: c["cec_price"] for c in out["Nulux Eynoa"]["coatings"]}
    assert sv["Hi-Vision ViewProtect"] == 240
    assert sv["Diamond Finish UV Control"] == 290
    # polarised: no price
    assert all(c["cec_price"] is None for c in out["Nulux Eynoa Polarised"]["coatings"])


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


# --- ZEISS era: supplier, price basis, add ranges, orderable, coatings -------

ZEISS_CSV = (
    "supplier,brand,lens,code,category,index,material,type,blank_mm,sph_min,sph_max,"
    "cyl_max,combined_max,add_min,add_max,price,price_promo,price_basis,coating,min_fh_mm\n"
    # stock 1.67 at deal price (no promo), cyl to -4
    "ZEISS,ZEISS,ClearView FSV 1.67,343817,Single vision,1.67,Clear,stock,75,-6.00,0.00,-4.00,6.00,,,35.00,,deal,DuraVision Platinum UV,\n"
    # stock 1.60 at book level: L25 ongoing, L50 promo, cyl to -3
    "ZEISS,ZEISS,ClearView FSV 1.60,,Single vision,1.60,Clear,stock,75,-6.00,0.00,-3.00,6.00,,,26.58,17.72,L25,DuraVision Platinum UV,\n"
    # grind 1.67, two coatings: hard coat cheaper than the standard HMC+
    "Synchrony,ZEISS Synchrony,Single Vision Grind 1.67,,Single vision,1.67,Clear,grind,,-13.00,+10.00,-6.00,13.00,,,85.13,56.75,L25,HMC+,\n"
    "Synchrony,ZEISS Synchrony,Single Vision Grind 1.67,,Single vision,1.67,Clear,grind,,-13.00,+10.00,-6.00,13.00,,,60.00,40.00,L25,HC hard coat,\n"
    # polarised variant of the same grind: plain lenses should rank first
    "Synchrony,ZEISS Synchrony,Single Vision Grind 1.67,,Single vision,1.67,Polarised,grind,,-12.00,+8.00,-6.00,12.00,,,50.00,30.00,L25,HMC+ Back Surface Multi-Coat,\n"
    # progressive with an add range and a minimum fitting height
    "ZEISS,ZEISS,SmartLife Progressive Superb 1.50,25593,Progressive,1.50,Clear,grind,,-7.00,+6.00,-4.00,7.00,0.75,3.50,85.00,,deal,DuraVision Plus Platinum UV,13\n"
    # a retired Hoya stock lens that would otherwise be the cheapest fit
    "Hoya,Hoya,Nulux 1.67,S-NULUX-167,Single vision,1.67,,stock,75,-8.00,+6.00,-4.00,,,,20.00,,T3,Diamond Finish,\n"
)


def _zeiss(retire_hoya=True):
    parsed, errors = lenses.parse_csv_text(ZEISS_CSV, "zeiss.csv")
    assert errors == []
    if retire_hoya:
        cat = lenses.apply_lens_filter({"lenses": parsed, "files": []},
                                       {"orderable_suppliers": ["ZEISS", "Synchrony"]})
        return cat["lenses"]
    return parsed


def test_parse_zeiss_columns():
    rows = _zeiss(retire_hoya=False)
    fsv = rows[0]
    assert fsv["supplier"] == "ZEISS" and fsv["code"] == "343817"
    assert fsv["price_basis"] == "deal" and fsv["price_promo"] is None
    assert rows[1]["price_promo"] == 17.72
    sup = next(r for r in rows if r["name"].startswith("SmartLife"))
    assert sup["add_min"] == 0.75 and sup["add_max"] == 3.50 and sup["min_fh_mm"] == 13
    assert all(r["orderable"] for r in rows)          # no filter yet


def test_filter_retires_other_suppliers_but_keeps_named_lenses():
    parsed, _ = lenses.parse_csv_text(ZEISS_CSV, "z.csv")
    parsed.append({**parsed[-1], "name": "MiyoSmart 1.59", "brand": "Hoya", "supplier": "Hoya"})
    cat = lenses.apply_lens_filter({"lenses": parsed, "files": []},
                                   {"orderable_suppliers": ["ZEISS", "Synchrony"],
                                    "orderable_extra": ["MiyoSmart"]})
    by = {l["name"]: l["orderable"] for l in cat["lenses"]}
    assert by["Nulux 1.67"] is False
    assert by["MiyoSmart 1.59"] is True
    assert by["ClearView FSV 1.67"] is True


def test_price_now_uses_promo_before_the_date_and_book_after():
    import datetime as dt
    row = {"price": 26.58, "price_promo": 17.72, "price_basis": "L25"}
    cfg = {"promo_until": "2027-03-11"}
    assert lenses.price_now(row, dt.date(2026, 12, 1), cfg) == (17.72, "promo")
    assert lenses.price_now(row, dt.date(2027, 3, 11), cfg) == (26.58, "L25")
    deal = {"price": 35.0, "price_promo": None, "price_basis": "deal"}
    assert lenses.price_now(deal, dt.date(2026, 12, 1), cfg) == (35.0, "deal")


def test_find_stock_167_beats_grind_for_moderate_cyl_and_retired_never_wins():
    # -2.00/-2.50: strongest meridian 4.50 -> 1.67. The retired Hoya 1.67 stock
    # is cheaper but never the pick; ClearView 1.67 stock leads; the verdict
    # says STOCK and quotes the standard-coating grind, not the hard coat.
    import datetime as dt
    r = lenses.find_options(_zeiss(), sph=-2.0, cyl=-2.5, kind="Single vision",
                            today=dt.date(2026, 10, 1), pricing={"promo_until": "2027-03-11"})
    assert r["rec_index"] == 1.67
    best = r["options"][0]
    assert best["name"] == "ClearView FSV 1.67" and best["best"] is True
    assert best["price_now"] == 35.0 and best["basis"] == "deal"
    assert r["verdict"].startswith("STOCK covers this")
    assert "$56.75" in r["verdict"] and "promo price" in r["verdict"]
    assert "stock saves $21.75" in r["verdict"]
    hoya = next(o for o in r["options"] if o["name"] == "Nulux 1.67")
    assert hoya["orderable"] is False and not hoya.get("best")
    assert r["options"][-1]["name"] == "Nulux 1.67"          # retired sinks to the bottom
    # standard coating (HMC+) ranks ahead of the cheaper hard coat and the polarised variant
    grinds = [o for o in r["options"] if o["type"] == "grind"]
    assert grinds[0]["coating"] == "HMC+" and grinds[0]["material"] == "Clear"
    assert grinds[0]["standard_coating"] is True


def test_find_combined_power_only_limits_minus_prescriptions():
    rows = ("lens,type,blank_mm,sph_min,sph_max,cyl_max,combined_max,price\n"
            "Plus Lens,stock,70,-6.00,+6.00,-4.00,6.00,20.00\n")
    parsed, _ = lenses.parse_csv_text(rows, "x.csv")
    # +5.00/-1.00 -> combined +4.00: never limited by the (minus) combined maximum
    assert len(lenses.find_options(parsed, sph=5.0, cyl=-1.0)["options"]) == 1
    # -5.00/-2.00 -> combined -7.00: beyond -6.00
    assert lenses.find_options(parsed, sph=-5.0, cyl=-2.0)["options"] == []


def test_find_progressive_checks_add_and_fitting_height():
    rows = _zeiss()
    ok = lenses.find_options(rows, sph=-2.0, cyl=-1.0, add=2.0, kind="Progressive")
    assert [o["name"] for o in ok["options"]] == ["SmartLife Progressive Superb 1.50"]
    assert "made to order" in ok["verdict"] and "$85.00" in ok["verdict"]
    assert not ok["options"][0]["warnings"]
    too_much = lenses.find_options(rows, sph=-2.0, add=3.75, kind="Progressive")
    assert too_much["options"] == []
    assert "add +3.75 is outside" in too_much["misses"][0]["reasons"][0]
    no_add = lenses.find_options(rows, sph=-2.0, kind="Progressive")
    assert any("type the add" in w for w in no_add["options"][0]["warnings"])
    low_fh = lenses.find_options(rows, sph=-2.0, add=2.0, fh=11, kind="Progressive")
    assert any("fitting height 11mm is under" in w for w in low_fh["options"][0]["warnings"])


def test_find_tinted_job_needs_a_clear_hard_coat_lens():
    r = lenses.find_options(_zeiss(), sph=-2.0, cyl=-2.5, kind="Single vision", tint=True)
    names = [(o["name"], o["coating"]) for o in r["options"] if o["orderable"]]
    assert names == [("Single Vision Grind 1.67", "HC hard coat")]
    reasons = " ".join(sum((m["reasons"] for m in r["misses"]), []))
    assert "needs a hard-coat lens" in reasons and "a tint goes on a clear lens" in reasons
    assert r["verdict"].startswith("GRIND")


def test_find_preferred_names_break_price_ties():
    rows = ("supplier,lens,category,type,sph_min,sph_max,add_min,add_max,price,coating\n"
            "Synchrony,Progressive Performance HD 1.50,Progressive,grind,-10,+6,0.75,3.50,45.00,HMC+\n"
            "Synchrony,Progressive Ultra HDV 1.50,Progressive,grind,-10,+6,0.75,3.50,45.00,HMC+\n")
    parsed, _ = lenses.parse_csv_text(rows, "s.csv")
    r = lenses.find_options(parsed, sph=-2.0, add=2.0, kind="Progressive",
                            pricing={"preferred_names": ["Ultra HDV"]})
    assert r["options"][0]["name"] == "Progressive Ultra HDV 1.50"


def test_check_job_flags_a_retired_code():
    r = lenses.check_job(_zeiss(), {"sph": -2.0, "cyl": -2.5}, {"sph": -2.25, "cyl": -2.0},
                         chosen={"code": "S-NULUX-167", "type": "Stk"})
    assert r["status"] == "check"
    assert any("no longer order" in n for n in r["chosen"]["notes"])
    assert r["best"]["name"] == "ClearView FSV 1.67" and r["best"]["price_job"] == 70.0


def test_attach_cec_price_tiers_and_addons():
    parsed, _ = lenses.parse_csv_text(ZEISS_CSV, "z.csv")
    prods = lenses.group_products(parsed)
    cfg = {
        "tiers": [
            {"name": "Everyday", "category": "Progressive", "match": ["Superb"],
             "by_index": {"1.50": 560}},
            {"name": "SV Premium", "category": "Single vision", "match": ["ClearView FSV"],
             "by_index": {"1.60": 250, "1.67": 300}},
            {"name": "SV Standard", "category": "Single vision", "match": ["Synchrony Single Vision Grind"],
             "by_index": {"1.67": 250}},
        ],
        "addons": {"BluePro|HMC Blue": 50, "grind": 100},
        "blank_names": ["Polarised"],
    }
    out = {(p["name"], p["type"], p["material"]): p for p in lenses.attach_cec_price(prods, cfg)}
    assert out[("ClearView FSV 1.67", "stock", "Clear")]["cec_price"] == 300
    assert out[("ClearView FSV 1.67", "stock", "Clear")]["tier"] == "SV Premium"
    assert out[("SmartLife Progressive Superb 1.50", "grind", "Clear")]["cec_price"] == 560
    grind = out[("Single Vision Grind 1.67", "grind", "Clear")]
    assert grind["cec_price"] == 350                      # 250 + $100 grind surcharge
    assert grind["tier"] == "SV Standard"
    assert out[("Nulux 1.67", "stock", "")]["cec_price"] is None   # no tier matches Hoya


def test_group_products_carries_supplier_basis_and_promo():
    import datetime as dt
    parsed, _ = lenses.parse_csv_text(ZEISS_CSV, "z.csv")
    prods = lenses.group_products(parsed, today=dt.date(2026, 10, 1),
                                  pricing={"promo_until": "2027-03-11"})
    fsv160 = next(p for p in prods if p["name"] == "ClearView FSV 1.60")
    assert fsv160["supplier"] == "ZEISS"
    assert fsv160["coatings"][0]["price_now"] == 17.72 and fsv160["coatings"][0]["basis"] == "promo"
    assert fsv160["price_from"] == 17.72
    later = lenses.group_products(parsed, today=dt.date(2027, 4, 1),
                                  pricing={"promo_until": "2027-03-11"})
    assert next(p for p in later if p["name"] == "ClearView FSV 1.60")["price_from"] == 26.58


def test_api_find_accepts_kind_add_and_tint(hub_client_lenses):
    client, lenses_dir = hub_client_lenses
    (lenses_dir / "zeiss.csv").write_text(ZEISS_CSV, encoding="utf-8")
    data = client.get("/api/lenses/find?sph=-2.00&cyl=-1.00&add=2.00&kind=Progressive").get_json()
    assert data["kind"] == "Progressive"
    assert data["options"][0]["name"] == "SmartLife Progressive Superb 1.50"
    assert client.get("/api/lenses/find?sph=-2.00&add=9&kind=Progressive").status_code == 400
    data = client.get("/api/lenses/find?sph=-2.00&cyl=-2.50&tint=1").get_json()
    assert data["rx"]["tint"] is True
    cat = client.get("/api/lenses").get_json()
    assert "pricing" in cat and "prefer_stock" in cat["pricing"]


def test_api_find_supplier_switch(hub_client_lenses):
    client, lenses_dir = hub_client_lenses
    (lenses_dir / "zeiss.csv").write_text(ZEISS_CSV, encoding="utf-8")
    # default: only what we order (the neutral test filter retires nothing, so
    # the sample Hoya rows are still "current" here) — the switch itself is what we test
    hoya = client.get("/api/lenses/find?sph=-2.00&cyl=-2.50&supplier=hoya").get_json()
    assert hoya["supplier"] == "hoya"
    assert all(o["supplier"] == "Hoya" for o in hoya["options"])
    assert hoya["verdict"].startswith("HOYA (old supplier, for comparison)")
    assert hoya["options"][0]["orderable"] is True          # ranked as if still ordered
    both = client.get("/api/lenses/find?sph=-2.00&cyl=-2.50&supplier=all").get_json()
    assert {o["supplier"] for o in both["options"]} >= {"Hoya", "ZEISS"}


def test_find_stock_bands_read_as_strongest_meridian_power():
    # ClearView 1.74 stock, as the book prints it: 75mm -3.00 to -8.00*, 70mm
    # -8.25 to -12.00*, cyl to -2.00. With range_basis=combined the band is
    # the lens's strongest-meridian power, so -6.50/-2.00 is a -8.50 lens on
    # the 70mm blank; -12.00 plain is the top; -10.25/-2.00 is a grind.
    rows = ("supplier,lens,type,blank_mm,sph_min,sph_max,cyl_max,combined_max,range_basis,price,coating\n"
            "ZEISS,ClearView FSV 1.74,stock,75,-8.00,-3.00,-2.00,8.00,combined,40.00,Platinum\n"
            "ZEISS,ClearView FSV 1.74,stock,70,-12.00,-8.25,-2.00,12.00,combined,40.00,Platinum\n")
    parsed, _ = lenses.parse_csv_text(rows, "z.csv")
    assert parsed[0]["range_basis"] == "combined"
    r = lenses.find_options(parsed, sph=-6.5, cyl=-2.0)
    assert [o["blank_mm"] for o in r["options"]] == [70]
    assert r["verdict"].startswith("STOCK covers this") and "70mm blank" in r["verdict"]
    assert [o["blank_mm"] for o in lenses.find_options(parsed, sph=-6.5, cyl=-1.5)["options"]] == [75]
    assert [o["blank_mm"] for o in lenses.find_options(parsed, sph=-12.0)["options"]] == [70]
    assert [o["blank_mm"] for o in lenses.find_options(parsed, sph=-10.0, cyl=-2.0)["options"]] == [70]
    assert lenses.find_options(parsed, sph=-10.25, cyl=-2.0)["options"] == []
    assert lenses.find_options(parsed, sph=-2.0, cyl=-0.5)["options"] == []      # -2.50: under the range
    assert lenses.find_options(parsed, sph=-6.5, cyl=-2.0, min_blank=72)["options"] == []
    assert lenses.find_options(parsed, sph=-6.5, cyl=-2.5)["options"] == []      # cyl over the cap


def test_find_plus_and_crossed_scripts_against_combined_bands():
    # ClearView 1.60 as printed: 65mm +2.00 to +6.00, 70mm +0.25 to +4.00,
    # 75mm 0.00 to -6.00 (cyl -3), 70mm -6.25 to -8.00.
    rows = ("supplier,lens,type,blank_mm,sph_min,sph_max,cyl_max,range_basis,price\n"
            "ZEISS,ClearView FSV 1.60,stock,65,+2.00,+6.00,-2.00,combined,20\n"
            "ZEISS,ClearView FSV 1.60,stock,70,+0.25,+4.00,-2.00,combined,20\n"
            "ZEISS,ClearView FSV 1.60,stock,75,-6.00,0.00,-3.00,combined,20\n"
            "ZEISS,ClearView FSV 1.60,stock,70,-8.00,-6.25,-2.00,combined,20\n")
    parsed, _ = lenses.parse_csv_text(rows, "z.csv")
    blanks = lambda **kw: sorted(o["blank_mm"] for o in lenses.find_options(parsed, **kw)["options"])
    assert blanks(sph=6.0, cyl=-2.0) == [65]          # plus lens: judged on its +6.00 sphere
    assert blanks(sph=7.0) == []                       # over the top of the plus range
    assert blanks(sph=1.0, cyl=-2.0) == [75]           # crossed cyl: -1.00 in the strongest meridian
    assert blanks(sph=3.0, cyl=-3.0) == []             # plus lens (+3.00 meridian) with a -3 cyl: no plus band takes -3
    assert blanks(sph=1.0, cyl=-3.0) == [75]           # -2.00 in the strongest meridian, cyl -3 on the 75mm band
    assert blanks(sph=-5.0, cyl=-2.0) == [70]          # -7.00: the 70mm minus band
    assert blanks(sph=-3.0, cyl=-1.0) == [75]


def test_sphere_basis_rows_keep_the_hoya_reading():
    # Hoya rows carry no range_basis: sphere in range + cyl cap + combined cap.
    rows = ("lens,type,blank_mm,sph_min,sph_max,cyl_max,combined_max,price\n"
            "Nulux 1.60,stock,70,-8.00,+6.00,-2.00,8.00,20\n")
    parsed, _ = lenses.parse_csv_text(rows, "h.csv")
    assert parsed[0]["range_basis"] == "sphere"
    assert len(lenses.find_options(parsed, sph=-6.0, cyl=-2.0)["options"]) == 1   # -8.00 combined ok
    assert lenses.find_options(parsed, sph=-6.5, cyl=-2.0)["options"] == []        # -8.50 over the cap
