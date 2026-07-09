"""Tests for SOP listing and search (title + body substring)."""

import pytest

from hub.sop_parser import list_sops, search_sops


@pytest.fixture
def sops_dir(tmp_path):
    d = tmp_path / "sops"
    d.mkdir()
    (d / "ordering-widgets.md").write_text(
        "---\ncategory: Ordering\nupdated: 2026-07-01\nowner: Angie\n---\n\n"
        "# Ordering widgets\n\nHow to reorder widgets from the supplier.\n\n"
        "1. Count the widgets on the shelf.\n",
        encoding="utf-8",
    )
    (d / "closing-up.md").write_text(
        "---\ncategory: Front desk\nupdated: 2026-07-02\n---\n\n"
        "# Closing up at night\n\nLock the door and set the ALARM code.\n",
        encoding="utf-8",
    )
    (d / "README.md").write_text("# Not a guide\n", encoding="utf-8")
    return d


class TestListSops:
    def test_lists_all_but_readme(self, sops_dir):
        sops = list_sops(sops_dir)
        assert [s["slug"] for s in sops] == ["closing-up", "ordering-widgets"]

    def test_carries_meta_and_summary(self, sops_dir):
        widgets = next(s for s in list_sops(sops_dir) if s["slug"] == "ordering-widgets")
        assert widgets["title"] == "Ordering widgets"
        assert widgets["category"] == "Ordering"
        assert widgets["owner"] == "Angie"
        assert "reorder widgets" in widgets["summary"]

    def test_missing_category_defaults_to_general(self, tmp_path):
        d = tmp_path / "s"
        d.mkdir()
        (d / "plain.md").write_text("# Plain\n\nBody.\n", encoding="utf-8")
        assert list_sops(d)[0]["category"] == "General"

    def test_missing_dir_is_empty_not_an_error(self, tmp_path):
        assert list_sops(tmp_path / "not-there") == []


class TestSearch:
    def test_finds_by_title(self, sops_dir):
        results = search_sops(sops_dir, "widgets")
        assert [r["slug"] for r in results] == ["ordering-widgets"]

    def test_finds_by_body_text(self, sops_dir):
        results = search_sops(sops_dir, "alarm code")
        assert [r["slug"] for r in results] == ["closing-up"]

    def test_case_insensitive(self, sops_dir):
        assert search_sops(sops_dir, "WIDGETS")
        assert search_sops(sops_dir, "Alarm")

    def test_no_match_returns_empty(self, sops_dir):
        assert search_sops(sops_dir, "zebra") == []

    def test_snippet_shows_the_match_in_context(self, sops_dir):
        results = search_sops(sops_dir, "alarm")
        assert "ALARM code" in results[0]["snippet"]

    def test_blank_query_returns_empty(self, sops_dir):
        assert search_sops(sops_dir, "   ") == []
