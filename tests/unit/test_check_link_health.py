"""Unit tests for scripts/check_link_health.py."""

from __future__ import annotations

from pathlib import Path

from scripts.check_link_health import check_demo_data, find_bare_hash_links


class TestFindBareHashLinks:
    def test_clean_input_passes(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.html"
        path.write_text(
            '<a href="https://github.com/gaurav-gandhi-2411/review-iq">GitHub</a>\n'
            '<a href="#gallery">Gallery</a>\n',
            encoding="utf-8",
        )
        assert find_bare_hash_links(path) == []

    def test_flags_bare_hash_double_quotes(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.html"
        path.write_text('<a href="#">Placeholder</a>\n', encoding="utf-8")
        violations = find_bare_hash_links(path)
        assert len(violations) == 1
        assert violations[0][0] == 1

    def test_flags_bare_hash_single_quotes(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.html"
        path.write_text("<a href='#'>Placeholder</a>\n", encoding="utf-8")
        violations = find_bare_hash_links(path)
        assert len(violations) == 1

    def test_does_not_flag_real_in_page_anchor(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.html"
        path.write_text(
            '<a href="#gallery">Gallery</a>\n<a href="#live">Live demo</a>\n',
            encoding="utf-8",
        )
        assert find_bare_hash_links(path) == []

    def test_line_number_reported_correctly(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.html"
        path.write_text(
            '<p>intro</p>\n<a href="#gallery">ok</a>\n<a href="#">bad</a>\n',
            encoding="utf-8",
        )
        violations = find_bare_hash_links(path)
        assert violations == [(3, '<a href="#">bad</a>')]

    def test_md_inline_code_span_not_flagged(self, tmp_path: Path) -> None:
        # A spec/doc describing the defect in backtick-quoted prose (e.g. docs/specs/
        # wave1-commercialization.md's own D3 writeup) is not a live anchor tag.
        path = tmp_path / "doc.md"
        path.write_text(
            'The footer GitHub link is `href="#"` -- a real defect.\n', encoding="utf-8"
        )
        assert find_bare_hash_links(path) == []

    def test_md_fenced_code_block_not_flagged(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.md"
        path.write_text(
            'Example of the bug:\n```html\n<a href="#">bad</a>\n```\n', encoding="utf-8"
        )
        assert find_bare_hash_links(path) == []

    def test_md_raw_href_outside_code_span_still_flagged(self, tmp_path: Path) -> None:
        # A genuine bare href="#" typed directly into markdown prose (not inside
        # backticks/a fence) is still a real placeholder link and must be caught.
        path = tmp_path / "doc.md"
        path.write_text('See <a href="#">here</a> for details.\n', encoding="utf-8")
        violations = find_bare_hash_links(path)
        assert len(violations) == 1
        assert violations[0][0] == 1

    def test_html_file_gets_no_code_span_exemption(self, tmp_path: Path) -> None:
        # The exemption is Markdown-only. An .html file with the same backtick-looking
        # text is still real markup and a real broken link.
        path = tmp_path / "doc.html"
        path.write_text('<code>href="#"</code> <a href="#">bad</a>\n', encoding="utf-8")
        violations = find_bare_hash_links(path)
        assert len(violations) == 1


class TestCheckDemoData:
    def test_valid_json_passes(self, tmp_path: Path, monkeypatch) -> None:
        import scripts.check_link_health as mod

        demo_path = tmp_path / "demo-data.json"
        demo_path.write_text('{"reviews": []}', encoding="utf-8")
        monkeypatch.setattr(mod, "DEMO_DATA_PATH", demo_path)
        assert check_demo_data() is None

    def test_missing_file_fails(self, tmp_path: Path, monkeypatch) -> None:
        import scripts.check_link_health as mod

        demo_path = tmp_path / "does-not-exist.json"
        monkeypatch.setattr(mod, "DEMO_DATA_PATH", demo_path)
        error = check_demo_data()
        assert error is not None
        assert "does not exist" in error

    def test_invalid_json_fails(self, tmp_path: Path, monkeypatch) -> None:
        import scripts.check_link_health as mod

        demo_path = tmp_path / "demo-data.json"
        demo_path.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(mod, "DEMO_DATA_PATH", demo_path)
        error = check_demo_data()
        assert error is not None
        assert "not valid JSON" in error
