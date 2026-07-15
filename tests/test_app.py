"""Route-level tests for CEC Hub. Everything mocked onto tmp_path —
no network, no real review bot, no real Optomate agent."""

import logging
from pathlib import Path


class TestHomeAndConfig:
    def test_index_serves_the_hub(self, hub_client):
        res = hub_client.get("/")
        assert res.status_code == 200
        assert b"CEC" in res.data
        assert b"Staff Hub" in res.data

    def test_tiles_come_from_config(self, hub_client):
        data = hub_client.get("/api/tiles").get_json()
        ids = [t["id"] for t in data["tiles"]]
        assert ids == ["sops", "referrals", "reviews", "orders", "stock",
                       "lenses"]

    def test_referral_tile_links_to_the_referral_app(self, hub_client):
        data = hub_client.get("/api/tiles").get_json()
        referrals = next(t for t in data["tiles"] if t["id"] == "referrals")
        assert referrals["link"] == "http://localhost:5678"
        assert referrals["external"] is True

    def test_merge_tiles_appends_new_template_tiles(self):
        import app
        # A machine whose per-machine tiles.json predates the Lens Finder.
        real = {"tiles": [{"id": "sops"},
                          {"id": "referrals", "link": "http://custom:5555"}]}
        example = {"tiles": [{"id": "sops"}, {"id": "referrals"},
                             {"id": "lenses", "name": "Lens Finder"}]}
        merged = app.merge_tiles(real, example)
        ids = [t["id"] for t in merged["tiles"]]
        assert ids == ["sops", "referrals", "lenses"]   # new tile appended
        # the machine's own order and custom referral link are preserved
        assert next(t for t in merged["tiles"]
                    if t["id"] == "referrals")["link"] == "http://custom:5555"

    def test_merge_tiles_no_machine_file_uses_template(self):
        import app
        example = {"tiles": [{"id": "lenses"}]}
        assert app.merge_tiles(None, example) == example

    def test_staff_names_for_the_picker(self, hub_client):
        data = hub_client.get("/api/staff").get_json()
        assert "Angie" in data["staff"]
        assert "Karen" in data["staff"]


class TestSopRoutes:
    def test_lists_the_three_seeded_sops(self, hub_client):
        data = hub_client.get("/api/sops").get_json()
        slugs = {s["slug"] for s in data["sops"]}
        assert {"ordering-hylo-forte", "ordering-paper-officeworks",
                "weekly-scorecard"} <= slugs
        for sop in data["sops"]:
            assert sop["title"]
            assert sop["category"]
            assert sop["updated"]

    def test_hylo_sop_has_the_108_dollar_branch(self, hub_client):
        data = hub_client.get("/api/sops/ordering-hylo-forte").get_json()
        assert data["title"].startswith("Ordering HYLO-Forte")
        branches = [b for b in data["blocks"] if b["type"] == "branch"]
        assert any("$108" in b["condition"] for b in branches)
        assert any(b["type"] == "step" for b in data["blocks"])

    def test_scorecard_sop_mentions_the_drop_folder(self, hub_client):
        data = hub_client.get("/api/sops/weekly-scorecard").get_json()
        joined = str(data["blocks"])
        assert "scorecard-drop" in joined

    def test_unknown_sop_is_a_friendly_404(self, hub_client):
        res = hub_client.get("/api/sops/not-a-real-guide")
        assert res.status_code == 404
        assert "guide" in res.get_json()["error"].lower()

    def test_search_route_finds_the_paper_guide(self, hub_client):
        data = hub_client.get("/api/sop-search?q=officeworks").get_json()
        slugs = [r["slug"] for r in data["results"]]
        assert "ordering-paper-officeworks" in slugs

    def test_search_without_a_query_is_a_400(self, hub_client):
        assert hub_client.get("/api/sop-search").status_code == 400

    def test_search_with_no_hits_is_empty_not_an_error(self, hub_client):
        data = hub_client.get("/api/sop-search?q=zzzznothing").get_json()
        assert data["results"] == []

    def test_sop_images_are_served(self, hub_client_custom_sops):
        res = hub_client_custom_sops.get("/sop-images/till.png")
        assert res.status_code == 200
        assert res.data.startswith(b"\x89PNG")

    def test_sop_image_traversal_is_blocked(self, hub_client_custom_sops):
        res = hub_client_custom_sops.get("/sop-images/..%2f..%2fapp.py")
        assert res.status_code == 404


class TestReviewsPage:
    def test_connected_counts_last_7_days_only(self, hub_client):
        data = hub_client.get("/api/reviews/status").get_json()
        assert data["connected"] is True
        assert data["sent_last_7_days"] == 3  # 1, 2 and 6 days ago; not 10 or 40
        assert data["bot_enabled"] is True

    def test_connected_reports_the_last_run(self, hub_client):
        data = hub_client.get("/api/reviews/status").get_json()
        assert data["last_run"] == "07/07/2026 8:00 am"
        assert data["last_result"] == "sent 4 · skipped 2 · errors 0"

    def test_not_connected_is_calm_and_200(self, hub_client_disconnected):
        res = hub_client_disconnected.get("/api/reviews/status")
        assert res.status_code == 200
        data = res.get_json()
        assert data["connected"] is False
        assert "Not connected" in data["message"]
        assert "wrong" in data["message"]  # "Nothing is wrong — ..."


class TestOrdersPage:
    def test_connected_returns_digest_and_uncollected(self, hub_client):
        data = hub_client.get("/api/orders/digest").get_json()
        assert data["connected"] is True
        assert "JOB 4411" in data["uncollected"]
        assert "order line 249" in data["digest"]
        assert data["digest_updated"]

    def test_digest_is_tailed_to_200_lines(self, hub_client):
        data = hub_client.get("/api/orders/digest").get_json()
        lines = data["digest"].splitlines()
        assert len(lines) == 200
        assert lines[0] == "2026-07-03 order line 50"  # 250 written, first 50 dropped

    def test_not_connected_is_calm_and_200(self, hub_client_disconnected):
        res = hub_client_disconnected.get("/api/orders/digest")
        assert res.status_code == 200
        assert res.get_json()["connected"] is False


class TestStockPage:
    def test_lists_proposals_with_tables_and_approved_flags(self, hub_client):
        data = hub_client.get("/api/stock/proposals").get_json()
        assert data["connected"] is True
        by_name = {p["filename"]: p for p in data["proposals"]}
        pending = by_name["frames-restock.csv"]
        assert pending["approved"] is False
        assert pending["headers"] == ["SKU", "Description", "Qty", "Supplier"]
        assert pending["row_count"] == 3
        assert pending["rows"][0][0] == "F-1001"
        assert by_name["cl-solutions.approved.csv"]["approved"] is True

    def test_not_connected_is_calm_and_200(self, hub_client_disconnected):
        res = hub_client_disconnected.get("/api/stock/proposals")
        assert res.status_code == 200
        data = res.get_json()
        assert data["connected"] is False
        assert data["proposals"] == []

    def test_approve_renames_the_file(self, hub_client, connected_world):
        res = hub_client.post("/api/stock/approve",
                              json={"filename": "frames-restock.csv"})
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["new_filename"] == "frames-restock.approved.csv"
        proposals = connected_world["proposals"]
        assert not (proposals / "frames-restock.csv").exists()
        assert (proposals / "frames-restock.approved.csv").exists()
        assert "nothing is ordered automatically" in data["message"].lower()

    def test_approve_writes_an_audit_line_with_the_picked_name(
            self, hub_client, caplog):
        hub_client.set_cookie("hub_staff", "Angie")
        with caplog.at_level(logging.INFO):
            hub_client.post("/api/stock/approve",
                            json={"filename": "frames-restock.csv"})
        assert "approved by Angie" in caplog.text

    def test_approve_without_a_name_still_works(self, hub_client, caplog):
        with caplog.at_level(logging.INFO):
            res = hub_client.post("/api/stock/approve",
                                  json={"filename": "frames-restock.csv"})
        assert res.status_code == 200
        assert "approved by someone (no name picked)" in caplog.text

    def test_approve_blocks_path_tricks(self, hub_client, connected_world):
        for dodgy in ["..\\evil.csv", "../evil.csv", "sub/dir.csv",
                      "notacsv.txt", ""]:
            res = hub_client.post("/api/stock/approve", json={"filename": dodgy})
            assert res.status_code == 400, dodgy
        # and nothing outside the proposals dir was touched
        assert not (connected_world["root"] / "evil.approved.csv").exists()

    def test_approve_already_approved_is_a_400(self, hub_client):
        res = hub_client.post("/api/stock/approve",
                              json={"filename": "cl-solutions.approved.csv"})
        assert res.status_code == 400
        assert "already approved" in res.get_json()["error"]

    def test_approve_missing_file_is_a_friendly_404(self, hub_client):
        res = hub_client.post("/api/stock/approve",
                              json={"filename": "gone.csv"})
        assert res.status_code == 404
        assert "refresh" in res.get_json()["error"].lower()

    def test_approve_when_not_connected_is_a_400(self, hub_client_disconnected):
        res = hub_client_disconnected.post("/api/stock/approve",
                                           json={"filename": "x.csv"})
        assert res.status_code == 400
