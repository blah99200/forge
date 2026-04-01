"""Tests for the fingerprinting and template matching system."""

import pytest
from app.extraction.pdf_parser import ParsedPDF, ParsedPage, TextBlock
from app.extraction.fingerprint import (
    extract_vendor_tokens,
    extract_anchor_positions,
    extract_layout_grid,
    extract_fingerprint,
)
from app.extraction.template_matcher import (
    _anchor_similarity,
    _grid_similarity,
    _font_similarity,
)


def _make_parsed_pdf(blocks: list[TextBlock], page_width=612, page_height=792) -> ParsedPDF:
    """Helper to create a ParsedPDF with given text blocks on page 0."""
    page = ParsedPage(
        page_number=0,
        width=page_width,
        height=page_height,
        text_blocks=blocks,
        full_text="\n".join(b.text for b in blocks),
        fonts_used={b.font_name for b in blocks if b.font_name},
    )
    return ParsedPDF(
        file_path="test.pdf",
        page_count=1,
        pages=[page],
        all_fonts=page.fonts_used,
    )


class TestVendorTokens:
    def test_extracts_company_name_from_large_font(self):
        blocks = [
            TextBlock(text="ACME CORP", page=0, x=0.1, y=0.05, w=0.3, h=0.03, font_name="Helvetica-Bold", font_size=18),
            TextBlock(text="Invoice", page=0, x=0.7, y=0.05, w=0.1, h=0.02, font_name="Helvetica", font_size=12),
        ]
        parsed = _make_parsed_pdf(blocks)
        tokens = extract_vendor_tokens(parsed)
        assert "ACME CORP" in tokens

    def test_extracts_abn(self):
        blocks = [
            TextBlock(text="ABN: 12 345 678 901", page=0, x=0.1, y=0.1, w=0.3, h=0.02, font_size=10),
        ]
        parsed = _make_parsed_pdf(blocks)
        tokens = extract_vendor_tokens(parsed)
        assert any("12 345 678 901" in t for t in tokens)

    def test_extracts_email_domain(self):
        blocks = [
            TextBlock(text="billing@acmecorp.com", page=0, x=0.1, y=0.15, w=0.3, h=0.02, font_size=10),
        ]
        parsed = _make_parsed_pdf(blocks)
        tokens = extract_vendor_tokens(parsed)
        assert "acmecorp.com" in tokens

    def test_ignores_blocks_below_top_30_percent(self):
        blocks = [
            TextBlock(text="BOTTOM VENDOR", page=0, x=0.1, y=0.5, w=0.3, h=0.03, font_name="Arial", font_size=18),
        ]
        parsed = _make_parsed_pdf(blocks)
        tokens = extract_vendor_tokens(parsed)
        assert "BOTTOM VENDOR" not in tokens


class TestAnchorPositions:
    def test_finds_invoice_label(self):
        blocks = [
            TextBlock(text="Invoice", page=0, x=0.7, y=0.05, w=0.1, h=0.02),
            TextBlock(text="Bill To:", page=0, x=0.05, y=0.3, w=0.1, h=0.02),
        ]
        parsed = _make_parsed_pdf(blocks)
        anchors = extract_anchor_positions(parsed)
        labels = [a["label"] for a in anchors]
        assert "invoice" in labels
        assert "bill to" in labels

    def test_position_is_normalized(self):
        blocks = [
            TextBlock(text="Total:", page=0, x=0.6, y=0.85, w=0.1, h=0.02),
        ]
        parsed = _make_parsed_pdf(blocks)
        anchors = extract_anchor_positions(parsed)
        total_anchor = next(a for a in anchors if a["label"] == "total")
        assert 0 <= total_anchor["x"] <= 1
        assert 0 <= total_anchor["y"] <= 1


class TestLayoutGrid:
    def test_grid_dimensions(self):
        blocks = [TextBlock(text="Test", page=0, x=0.1, y=0.1, w=0.1, h=0.02)]
        parsed = _make_parsed_pdf(blocks)
        grid = extract_layout_grid(parsed)
        assert len(grid) == 24  # 6 rows x 4 cols

    def test_empty_pdf_all_zeros(self):
        parsed = _make_parsed_pdf([])
        grid = extract_layout_grid(parsed)
        assert grid == "0" * 24

    def test_marks_cells_with_text(self):
        # Block at center of page
        blocks = [TextBlock(text="Center", page=0, x=0.45, y=0.45, w=0.1, h=0.02)]
        parsed = _make_parsed_pdf(blocks)
        grid = extract_layout_grid(parsed)
        assert "1" in grid


class TestSimilarityScoring:
    def test_identical_anchors_score_1(self):
        anchors = [{"label": "invoice", "x": 0.7, "y": 0.05}, {"label": "total", "x": 0.6, "y": 0.85}]
        score = _anchor_similarity(anchors, anchors)
        assert score == 1.0

    def test_different_anchors_score_low(self):
        a = [{"label": "invoice", "x": 0.1, "y": 0.1}]
        b = [{"label": "total", "x": 0.9, "y": 0.9}]
        score = _anchor_similarity(a, b)
        assert score < 0.3

    def test_identical_grids_score_1(self):
        grid = "101010101010101010101010"
        assert _grid_similarity(grid, grid) == 1.0

    def test_opposite_grids_score_0(self):
        a = "111111111111111111111111"
        b = "000000000000000000000000"
        assert _grid_similarity(a, b) == 0.0

    def test_identical_fonts_score_1(self):
        fonts = ["Helvetica", "Arial"]
        assert _font_similarity(fonts, fonts) == 1.0

    def test_disjoint_fonts_score_0(self):
        assert _font_similarity(["Helvetica"], ["TimesRoman"]) == 0.0

    def test_partial_font_overlap(self):
        a = ["Helvetica", "Arial"]
        b = ["Helvetica", "Courier"]
        score = _font_similarity(a, b)
        assert 0.3 < score < 0.5  # 1 out of 3 unique fonts


class TestExtractFingerprint:
    def test_returns_all_keys(self):
        blocks = [
            TextBlock(text="ACME", page=0, x=0.1, y=0.05, w=0.2, h=0.03, font_name="Arial", font_size=18),
            TextBlock(text="Invoice", page=0, x=0.7, y=0.05, w=0.1, h=0.02, font_name="Helvetica", font_size=12),
        ]
        parsed = _make_parsed_pdf(blocks)
        fp = extract_fingerprint(parsed)

        assert "vendor_tokens" in fp
        assert "anchor_positions" in fp
        assert "layout_grid" in fp
        assert "font_set" in fp
        assert isinstance(fp["vendor_tokens"], list)
        assert isinstance(fp["layout_grid"], str)
