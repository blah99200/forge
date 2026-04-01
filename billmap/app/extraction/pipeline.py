"""Extraction pipeline — orchestrates the full flow from PDF to validated payload.

PDF Upload → Parse → Template Match → Extract (mapping or LLM) → Validate → Payload → Route
"""

import json

from sqlalchemy.orm import Session

from app.extraction.pdf_parser import parse_pdf
from app.extraction.fingerprint import extract_fingerprint
from app.extraction.template_matcher import match_template, update_template_anchors, HIGH_CONFIDENCE
from app.extraction.region_extractor import extract_fields
from app.extraction.llm_fallback import llm_extract, build_reference_context
from app.extraction.validator import validate_extraction
from app.models.template import VendorTemplate
from app.models.invoice import Invoice
from app.models.connection import ReferenceData


def process_invoice(invoice: Invoice, db: Session) -> dict:
    """Run the full extraction pipeline on an invoice.

    Returns a dict with extraction results, confidence, and routing decision.
    """
    # Step 1: Parse PDF
    parsed = parse_pdf(invoice.file_path)

    # Step 2: Template matching
    templates = db.query(VendorTemplate).all()
    match = match_template(parsed, templates)

    result = {
        "template_match": {
            "matched": match.template is not None,
            "template_id": match.template.id if match.template else None,
            "template_name": match.template.name if match.template else None,
            "score": match.score,
            "tier": match.tier,
        },
    }

    # Step 3: Extract
    if match.template and match.score >= HIGH_CONFIDENCE:
        # Known vendor — coordinate-based extraction
        extraction = extract_fields(parsed, match.template)
        invoice.template_id = match.template.id
        invoice.status = "extracted"

        # Update template anchors (EMA drift)
        fingerprint = extract_fingerprint(parsed)
        update_template_anchors(match.template, fingerprint["anchor_positions"])

    else:
        # Unknown vendor or low confidence — LLM fallback
        ref_context = _build_reference_context(db)
        try:
            extraction = llm_extract(parsed, ref_context)
            invoice.status = "extracted"
        except Exception as e:
            invoice.status = "failed"
            result["error"] = f"LLM extraction failed: {e}"
            db.flush()
            return result

    invoice.set_raw_extraction(extraction)

    # Step 4: Validate
    ref_data = _load_reference_data(db)
    validation = validate_extraction(extraction, ref_data)

    invoice.confidence_overall = validation["confidence_overall"]
    invoice.set_field_confidences(validation["field_confidences"])

    result["extraction"] = extraction
    result["validation"] = {
        "confidence_overall": validation["confidence_overall"],
        "field_confidences": validation["field_confidences"],
        "issues": validation["issues"],
    }

    # Step 5: Build AP Bill payload
    payload = _build_ap_bill_payload(extraction, validation)
    invoice.set_final_payload(payload)
    result["payload"] = payload

    # Step 6: Route
    routing = _determine_routing(invoice, match, validation)
    result["routing"] = routing

    if routing["action"] == "auto_push":
        invoice.status = "approved"
    elif routing["action"] == "review":
        invoice.status = "extracted"  # Awaits user review
    elif routing["action"] == "mapping_needed":
        invoice.status = "classified"  # Needs mapping builder

    db.flush()
    return result


def _build_reference_context(db: Session) -> str:
    """Build LLM reference context from cached reference data."""
    vendors = db.query(ReferenceData).filter(ReferenceData.data_type == "vendor").all()
    accounts = db.query(ReferenceData).filter(ReferenceData.data_type == "account").all()
    items = db.query(ReferenceData).filter(ReferenceData.data_type == "item").all()
    tax_codes = db.query(ReferenceData).filter(ReferenceData.data_type == "tax_code").all()

    return build_reference_context(
        vendors=[{"name": v.name, "external_id": v.external_id} for v in vendors],
        accounts=[{"name": a.name, "external_id": a.external_id} for a in accounts],
        items=[{"name": i.name, "external_id": i.external_id} for i in items],
        tax_codes=[{"name": t.name, "external_id": t.external_id} for t in tax_codes],
    )


def _load_reference_data(db: Session) -> dict:
    """Load all reference data from DB for validation."""
    return {
        "vendors": [
            {"name": r.name, "external_id": r.external_id}
            for r in db.query(ReferenceData).filter(ReferenceData.data_type == "vendor").all()
        ],
        "accounts": [
            {"name": r.name, "external_id": r.external_id}
            for r in db.query(ReferenceData).filter(ReferenceData.data_type == "account").all()
        ],
        "items": [
            {"name": r.name, "external_id": r.external_id}
            for r in db.query(ReferenceData).filter(ReferenceData.data_type == "item").all()
        ],
        "tax_codes": [
            {"name": r.name, "external_id": r.external_id}
            for r in db.query(ReferenceData).filter(ReferenceData.data_type == "tax_code").all()
        ],
    }


def _build_ap_bill_payload(extraction: dict, validation: dict) -> dict:
    """Transform extraction into the standardized AP Bill payload schema."""
    validated = validation.get("validated", {})

    # Use validated/matched values where available, fall back to raw extraction
    def _get_value(field: str) -> str:
        v = validated.get(field, {})
        if isinstance(v, dict) and "matched_ref" in v and v.get("confidence", 0) > 0.8:
            return v["matched_ref"]  # Use the matched reference data value
        return extraction.get(field, "")

    vendor_name = _get_value("vendor_name")
    vendor_ref = validated.get("vendor_name", {})

    payload = {
        "vendor": {
            "name": vendor_name,
            "external_id": vendor_ref.get("matched_ref", "") if isinstance(vendor_ref, dict) else "",
        },
        "invoice_number": extraction.get("invoice_number", {}).get("value", "") if isinstance(extraction.get("invoice_number"), dict) else extraction.get("invoice_number", ""),
        "invoice_date": extraction.get("invoice_date", {}).get("value", "") if isinstance(extraction.get("invoice_date"), dict) else extraction.get("invoice_date", ""),
        "due_date": extraction.get("due_date", {}).get("value", "") if isinstance(extraction.get("due_date"), dict) else extraction.get("due_date", ""),
        "currency": extraction.get("currency", "USD"),
        "po_number": extraction.get("po_number", {}).get("value", "") if isinstance(extraction.get("po_number"), dict) else extraction.get("po_number", ""),
        "subtotal": _extract_number(extraction.get("subtotal", "")),
        "tax_total": _extract_number(extraction.get("tax_total", "")),
        "total": _extract_number(extraction.get("total", "")),
        "line_items": _build_line_items(extraction.get("line_items", []), validation),
        "notes": extraction.get("notes", ""),
    }

    return payload


def _build_line_items(raw_items: list, validation: dict) -> list[dict]:
    """Build standardized line items from extraction."""
    validated_items = validation.get("validated", {}).get("line_items", [])
    items = []

    for i, raw in enumerate(raw_items):
        v_item = validated_items[i] if i < len(validated_items) else {}

        item = {
            "description": raw.get("description", ""),
            "item_code": raw.get("item_code", ""),
            "item_id": v_item.get("matched_item", ""),
            "quantity": _extract_number(raw.get("quantity", "")),
            "unit_price": _extract_number(raw.get("unit_price", "")),
            "amount": _extract_number(raw.get("amount", "")),
            "account_id": raw.get("account_id", ""),
            "tax_code": raw.get("tax_code", ""),
        }
        items.append(item)

    return items


def _extract_number(value) -> float | None:
    """Extract a numeric value from various input formats."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        value = value.get("value", "")
    if isinstance(value, str):
        import re
        cleaned = re.sub(r"[,$\s]", "", value)
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _determine_routing(invoice: Invoice, match, validation: dict) -> dict:
    """Decide what to do with the processed invoice."""
    confidence = validation["confidence_overall"]
    issues = validation["issues"]

    # If template matched and auto-push is enabled with sufficient confidence
    if match.template and match.template.auto_push_enabled:
        threshold = match.template.confidence_threshold
        min_field = match.template.min_field_confidence

        field_confs = validation.get("field_confidences", {})
        all_fields_ok = all(v >= min_field for v in field_confs.values()) if field_confs else False

        if confidence >= threshold and all_fields_ok and not issues:
            return {
                "action": "auto_push",
                "reason": f"Confidence {confidence:.0%} >= threshold {threshold:.0%}, all fields OK",
            }

    # If no template match (LLM fallback), needs mapping
    if not match.template:
        return {
            "action": "mapping_needed",
            "reason": "No template match — user needs to create/confirm mapping",
        }

    # Default: review
    return {
        "action": "review",
        "reason": f"Confidence {confidence:.0%}, {len(issues)} issue(s) — requires user review",
        "issues": issues,
    }
