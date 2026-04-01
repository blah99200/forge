"""Tests for coordinate-based region extraction."""

import pytest
from app.extraction.pdf_parser import ParsedPDF, ParsedPage, TextBlock
from app.extraction.region_extractor import (
    _extract_text_in_region,
    _post_process,
    _normalize_date,
    _normalize_number,
)


def _make_parsed(blocks):
    page = ParsedPage(page_number=0, width=612, height=792, text_blocks=blocks)
    return ParsedPDF(file_path="test.pdf", page_count=1, pages=[page])


class TestRegionExtraction:
    def test_extracts_text_in_region(self):
        blocks = [
            TextBlock(text="INV-001", page=0, x=0.5, y=0.1, w=0.2, h=0.02),
            TextBlock(text="Other text", page=0, x=0.1, y=0.5, w=0.2, h=0.02),
        ]
        parsed = _make_parsed(blocks)
        region = {"page": 0, "x": 0.4, "y": 0.08, "w": 0.4, "h": 0.06}
        result = _extract_text_in_region(parsed, region)
        assert result == "INV-001"

    def test_ignores_text_outside_region(self):
        blocks = [
            TextBlock(text="Inside", page=0, x=0.5, y=0.5, w=0.1, h=0.02),
            TextBlock(text="Outside", page=0, x=0.1, y=0.1, w=0.1, h=0.02),
        ]
        parsed = _make_parsed(blocks)
        region = {"page": 0, "x": 0.4, "y": 0.4, "w": 0.3, "h": 0.2}
        result = _extract_text_in_region(parsed, region)
        assert "Inside" in result
        assert "Outside" not in result

    def test_empty_region_returns_empty(self):
        blocks = [TextBlock(text="Text", page=0, x=0.5, y=0.5, w=0.1, h=0.02)]
        parsed = _make_parsed(blocks)
        region = {"page": 0, "x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1}
        result = _extract_text_in_region(parsed, region)
        assert result == ""

    def test_wrong_page_returns_empty(self):
        blocks = [TextBlock(text="Text", page=0, x=0.5, y=0.5, w=0.1, h=0.02)]
        parsed = _make_parsed(blocks)
        region = {"page": 1, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        result = _extract_text_in_region(parsed, region)
        assert result == ""


class TestPostProcessing:
    def test_trim(self):
        assert _post_process("  hello  ", {"trim": True}) == "hello"

    def test_regex_extraction(self):
        assert _post_process("INV-2026-001", {"regex": r"(INV-\d+-\d+)"}) == "INV-2026-001"

    def test_prefix_remove(self):
        assert _post_process("Invoice: INV-001", {"prefix_remove": "Invoice:"}) == "INV-001"

    def test_number_normalization(self):
        assert _normalize_number("$1,234.56") == "1234.56"
        assert _normalize_number("1 000.00") == "1000.00"

    def test_date_normalization(self):
        assert _normalize_date("03/28/2026", "MM/DD/YYYY") == "2026-03-28"
        assert _normalize_date("28/03/2026", "DD/MM/YYYY") == "2026-03-28"

    def test_empty_input(self):
        assert _post_process("", {"trim": True}) == ""
        assert _post_process("", {}) == ""
