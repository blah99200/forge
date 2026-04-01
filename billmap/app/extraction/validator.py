"""Validation engine — cross-references extracted data against accounting system reference data.

Validates vendor names, account codes, item codes, tax codes, and checks math.
Returns per-field confidence scores and flags discrepancies.
"""

import re
from Levenshtein import ratio as levenshtein_ratio


def validate_extraction(extraction: dict, reference_data: dict) -> dict:
    """Validate extracted fields against reference data.

    Args:
        extraction: Dict of extracted field values (from region_extractor or LLM)
        reference_data: Dict with keys "vendors", "accounts", "items", "tax_codes" — each a list of dicts

    Returns:
        Dict with:
            - validated: dict of field_name → {value, matched_ref, confidence, issues}
            - confidence_overall: float
            - issues: list of issue strings
    """
    vendors = reference_data.get("vendors", [])
    accounts = reference_data.get("accounts", [])
    items = reference_data.get("items", [])
    tax_codes = reference_data.get("tax_codes", [])

    validated = {}
    issues = []

    # Validate vendor name
    vendor_name = extraction.get("vendor_name", "")
    if vendor_name and vendors:
        match, score = _fuzzy_match(vendor_name, [v.get("name", "") for v in vendors])
        validated["vendor_name"] = {
            "value": vendor_name,
            "matched_ref": match,
            "confidence": score,
        }
        if score < 0.7:
            issues.append(f"Vendor '{vendor_name}' not found in vendor list (best match: '{match}' at {score:.0%})")
    elif vendor_name:
        validated["vendor_name"] = {"value": vendor_name, "matched_ref": None, "confidence": 0.5}

    # Validate simple fields (invoice_number, dates, etc.)
    for field in ["invoice_number", "invoice_date", "due_date", "po_number"]:
        value = extraction.get(field, "")
        if value:
            confidence = _assess_field_quality(field, value)
            validated[field] = {"value": value, "confidence": confidence}
        else:
            if field in ("invoice_number", "invoice_date"):
                issues.append(f"Missing required field: {field}")
                validated[field] = {"value": "", "confidence": 0.0}

    # Validate monetary fields
    for field in ["subtotal", "tax_total", "total"]:
        value = extraction.get(field, "")
        if value:
            confidence = 0.9 if _is_valid_number(value) else 0.4
            validated[field] = {"value": value, "confidence": confidence}
            if not _is_valid_number(value):
                issues.append(f"Field '{field}' does not look like a valid number: '{value}'")

    # Check math: subtotal + tax = total
    math_ok = _check_math(extraction)
    if math_ok is False:
        issues.append("Math check failed: subtotal + tax_total does not equal total")
    elif math_ok is True:
        # Boost confidence on monetary fields when math checks out
        for field in ["subtotal", "tax_total", "total"]:
            if field in validated:
                validated[field]["confidence"] = min(1.0, validated[field]["confidence"] + 0.1)

    # Validate line items
    line_items = extraction.get("line_items", [])
    if line_items:
        validated_items = []
        for item in line_items:
            v_item = _validate_line_item(item, items, accounts, tax_codes)
            validated_items.append(v_item)
            issues.extend(v_item.get("issues", []))
        validated["line_items"] = validated_items

    # Calculate overall confidence
    field_confidences = {
        k: v["confidence"] for k, v in validated.items()
        if isinstance(v, dict) and "confidence" in v
    }

    # Weight critical fields higher
    weights = {
        "vendor_name": 2.0,
        "invoice_number": 1.5,
        "total": 2.0,
        "invoice_date": 1.0,
    }

    if field_confidences:
        weighted_sum = sum(
            field_confidences.get(f, 0) * weights.get(f, 1.0)
            for f in field_confidences
        )
        weight_total = sum(weights.get(f, 1.0) for f in field_confidences)
        confidence_overall = weighted_sum / weight_total if weight_total else 0.0
    else:
        confidence_overall = 0.0

    return {
        "validated": validated,
        "field_confidences": field_confidences,
        "confidence_overall": round(confidence_overall, 3),
        "issues": issues,
    }


def _fuzzy_match(query: str, candidates: list[str]) -> tuple[str | None, float]:
    """Find the best fuzzy match for a string in a list of candidates."""
    if not candidates:
        return None, 0.0

    best_match = None
    best_score = 0.0

    query_lower = query.lower().strip()
    for candidate in candidates:
        score = levenshtein_ratio(query_lower, candidate.lower().strip())
        if score > best_score:
            best_score = score
            best_match = candidate

    return best_match, best_score


def _assess_field_quality(field: str, value: str) -> float:
    """Assess the quality/confidence of a simple extracted field."""
    if not value.strip():
        return 0.0

    if field in ("invoice_number", "po_number"):
        # Should contain alphanumeric characters
        if re.match(r"^[\w\-/]+$", value.strip()):
            return 0.9
        return 0.6

    if field in ("invoice_date", "due_date"):
        # Try common date patterns
        date_patterns = [
            r"\d{4}-\d{2}-\d{2}",
            r"\d{2}/\d{2}/\d{4}",
            r"\d{2}-\d{2}-\d{4}",
            r"\w+ \d{1,2},? \d{4}",
        ]
        for pattern in date_patterns:
            if re.match(pattern, value.strip()):
                return 0.9
        return 0.5

    return 0.7


def _is_valid_number(value: str) -> bool:
    """Check if a string looks like a valid monetary amount."""
    cleaned = re.sub(r"[,$\s]", "", str(value))
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def _check_math(extraction: dict) -> bool | None:
    """Check if subtotal + tax_total = total. Returns None if fields are missing."""
    subtotal = extraction.get("subtotal", "")
    tax_total = extraction.get("tax_total", "")
    total = extraction.get("total", "")

    if not subtotal or not total:
        return None

    try:
        s = float(re.sub(r"[,$\s]", "", str(subtotal)))
        t = float(re.sub(r"[,$\s]", "", str(total)))
        tax = float(re.sub(r"[,$\s]", "", str(tax_total))) if tax_total else 0.0

        return abs((s + tax) - t) < 0.02  # Allow 2 cent rounding tolerance
    except ValueError:
        return None


def _validate_line_item(item: dict, items: list[dict], accounts: list[dict], tax_codes: list[dict]) -> dict:
    """Validate a single line item against reference data."""
    result = {**item, "issues": []}

    # Match item code/description against known items
    item_code = item.get("item_code", "")
    description = item.get("description", "")

    if items and (item_code or description):
        query = item_code or description
        ref_names = [i.get("name", "") for i in items]
        ref_ids = [i.get("external_id", "") for i in items]

        # Try matching against both names and IDs
        match_name, score_name = _fuzzy_match(query, ref_names)
        match_id, score_id = _fuzzy_match(query, ref_ids)

        best_match = match_name if score_name >= score_id else match_id
        best_score = max(score_name, score_id)

        result["matched_item"] = best_match
        result["item_confidence"] = round(best_score, 3)

        if best_score < 0.6:
            result["issues"].append(f"Item '{query}' not found in items list (best: '{best_match}' at {best_score:.0%})")

    # Validate amount math
    qty = item.get("quantity", "")
    price = item.get("unit_price", "")
    amount = item.get("amount", "")

    if qty and price and amount:
        try:
            q = float(re.sub(r"[,$\s]", "", str(qty)))
            p = float(re.sub(r"[,$\s]", "", str(price)))
            a = float(re.sub(r"[,$\s]", "", str(amount)))
            if abs(q * p - a) > 0.02:
                result["issues"].append(f"Line item math: {q} x {p} != {a}")
        except ValueError:
            pass

    return result
