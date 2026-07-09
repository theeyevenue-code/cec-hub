"""Unit tests for the SOP markdown parser — the renderer contract."""

from hub.sop_parser import (
    parse_frontmatter,
    parse_sop,
    render_inline,
)


class TestFrontmatter:
    def test_parses_all_three_fields(self):
        meta, body = parse_frontmatter(
            "---\ncategory: Ordering\nupdated: 2026-07-09\nowner: Angie\n---\n# T\n"
        )
        assert meta == {"category": "Ordering", "updated": "2026-07-09",
                        "owner": "Angie"}
        assert body.strip() == "# T"

    def test_missing_frontmatter_is_fine(self):
        meta, body = parse_frontmatter("# Just a title\n\nSome text.")
        assert meta == {}
        assert body.startswith("# Just a title")

    def test_unclosed_frontmatter_treated_as_body(self):
        meta, body = parse_frontmatter("---\ncategory: Oops\n# Title")
        assert meta == {}
        assert "# Title" in body

    def test_keys_are_case_insensitive(self):
        meta, _ = parse_frontmatter("---\nCategory: Ordering\n---\nx")
        assert meta["category"] == "Ordering"


class TestBlocks:
    def test_h1_becomes_title(self):
        sop = parse_sop("# Ordering things\n\n1. Step one.")
        assert sop["title"] == "Ordering things"

    def test_numbered_steps_keep_their_numbers(self):
        sop = parse_sop("# T\n\n1. First.\n2. Second.\n7. Later.")
        steps = [b for b in sop["blocks"] if b["type"] == "step"]
        assert [s["number"] for s in steps] == ["1", "2", "7"]
        assert "First." in steps[0]["html"]

    def test_step_continuation_lines_join(self):
        sop = parse_sop("# T\n\n1. First line\nand the rest of the step.\n\n2. Next.")
        steps = [b for b in sop["blocks"] if b["type"] == "step"]
        assert "First line and the rest of the step." in steps[0]["html"]
        assert len(steps) == 2

    def test_branch_splits_condition_and_action(self):
        sop = parse_sop(
            "# T\n\n> IF order total is over $108: check the FOC folder first.\n"
        )
        branch = next(b for b in sop["blocks"] if b["type"] == "branch")
        assert branch["condition"] == "order total is over $108"
        assert "check the FOC folder first." in branch["action"]

    def test_branch_if_is_case_insensitive(self):
        sop = parse_sop("# T\n\n> if it rains: bring the sign in.\n")
        branch = next(b for b in sop["blocks"] if b["type"] == "branch")
        assert branch["condition"] == "it rains"

    def test_plain_blockquote_becomes_note(self):
        sop = parse_sop("# T\n\n> Deliveries usually take two days.\n")
        note = next(b for b in sop["blocks"] if b["type"] == "note")
        assert "two days" in note["html"]
        assert not [b for b in sop["blocks"] if b["type"] == "branch"]

    def test_two_branches_in_one_quote_run(self):
        sop = parse_sop("# T\n\n> IF a: do x.\n> IF b: do y.\n")
        branches = [b for b in sop["blocks"] if b["type"] == "branch"]
        assert [b["condition"] for b in branches] == ["a", "b"]

    def test_headings_and_paragraphs_and_bullets(self):
        sop = parse_sop("# T\n\nIntro words.\n\n## Steps\n\n- one thing\n- another\n")
        types = [b["type"] for b in sop["blocks"]]
        assert types == ["para", "heading", "bullets"]
        assert sop["blocks"][2]["items"] == ["one thing", "another"]

    def test_image_block_served_from_sop_images(self):
        sop = parse_sop("# T\n\n![The order book](images/order-book.jpg)\n")
        img = next(b for b in sop["blocks"] if b["type"] == "image")
        assert img["src"] == "/sop-images/order-book.jpg"
        assert img["alt"] == "The order book"

    def test_dodgy_image_paths_are_dropped(self):
        sop = parse_sop("# T\n\n![x](../../secrets.png)\n\n![y](C:/windows/x.png)\n")
        assert not [b for b in sop["blocks"] if b["type"] == "image"]


class TestInline:
    def test_http_link_becomes_big_button(self):
        html = render_inline("[Open Officeworks](https://www.officeworks.com.au)")
        assert 'class="sop-btn"' in html
        assert 'href="https://www.officeworks.com.au"' in html
        assert 'rel="noopener"' in html

    def test_non_http_link_stays_plain_text(self):
        html = render_inline("[click me](javascript:alert(1))")
        assert "<a" not in html
        assert "click me" in html

    def test_bold_and_code(self):
        html = render_inline("Order **A4 80gsm** into `C:\\paper`")
        assert "<strong>A4 80gsm</strong>" in html
        assert "<code>C:\\paper</code>" in html

    def test_mark_flag_rendered_and_collected(self):
        flags = []
        html = render_inline("Pay with the card [MARK: confirm which card]", flags)
        assert "Still to confirm with Mark: confirm which card" in html
        assert 'class="mark-flag"' in html
        assert flags == ["confirm which card"]

    def test_html_in_sop_is_escaped(self):
        html = render_inline("<script>alert('x')</script> & so on")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "&amp; so on" in html

    def test_sop_level_mark_flags_collected(self):
        sop = parse_sop("# T\n\n1. Step [MARK: check this].\n\n> IF x: y [MARK: and this].\n")
        assert sop["mark_flags"] == ["check this", "and this"]
