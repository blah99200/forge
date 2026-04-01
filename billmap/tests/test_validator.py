"""Tests for the validation engine."""

import pytest
from app.extraction.validator import (
    validate_extraction,
    _fuzzy_match,
    _is_valid_number,
    _check_math,
)


class TestFuzzyMatch:
    def test_exact_match(self):
        match, score = _fuzzy_match("ACME Corp", ["ACME Corp", "Other Co"])
        assert match == "ACME Corp"
        assert score == 1.0

    def test_close_match(self):
        match, score = _fuzzy_match("ACME Corporation", ["ACME Corp", "Other Co"])
        assert match == "ACME Corp"
        assert score > 0.6

    def test_no_candidates(self):
        match, score = _fuzzy_match("ACME", [])
        assert match is None
        assert score == 0.0

    def test_case_insensitive(self):
        match, score = _fuzzy_match("acme corp", ["ACME Corp"])
        assert score == 1.0


class TestNumberValidation:
    def test_valid_numbers(self):
        assert _is_valid_number("1234.56") is True
        assert _is_valid_number("$1,234.56") is True
        assert _is_valid_number("0") is True

    def test_invalid_numbers(self):
        assert _is_valid_number("abc") is False
        assert _is_valid_number("") is False


class TestMathCheck:
    def test_correct_math(self):
        extraction = {"subtotal": "100.00", "tax_total": "10.00", "total": "110.00"}
        assert _check_math(extraction) is True

    def test_incorrect_math(self):
        extraction = {"subtotal": "100.00", "tax_total": "10.00", "total": "120.00"}
        assert _check_math(extraction) is False

    def test_missing_fields(self):
        assert _check_math({"subtotal": "100.00"}) is None
        assert _check_math({}) is None

    def test_rounding_tolerance(self):
        extraction = {"subtotal": "99.99", "tax_total": "10.00", "total": "110.00"}
        # Off by 0.01 — within tolerance
        assert _check_math(extraction) is True


class TestValidateExtraction:
    def test_basic_validation(self):
        extraction = {
            "vendor_name": "ACME Corp",
            "invoice_number": "INV-001",
            "invoice_date": "2026-03-28",
            "total": "1500.00",
            "subtotal": "1400.00",
            "tax_total": "100.00",
        }
        reference = {
            "vendors": [{"name": "ACME Corporation", "external_id": "v1"}],
            "accounts": [],
            "items": [],
            "tax_codes": [],
        }

        result = validate_extraction(extraction, reference)
        assert result["confidence_overall"] > 0
        assert "validated" in result
        assert "vendor_name" in result["validated"]

    def test_missing_required_fields_flagged(self):
        extraction = {"vendor_name": "Test"}
        reference = {"vendors": [], "accounts": [], "items": [], "tax_codes": []}

        result = validate_extraction(extraction, reference)
        assert any("invoice_number" in issue for issue in result["issues"])
        assert any("invoice_date" in issue for issue in result["issues"])

    def test_math_check_failure_flagged(self):
        extraction = {
            "subtotal": "100.00",
            "tax_total": "10.00",
            "total": "200.00",  # Wrong!
        }
        reference = {"vendors": [], "accounts": [], "items": [], "tax_codes": []}

        result = validate_extraction(extraction, reference)
        assert any("Math check" in issue for issue in result["issues"])
