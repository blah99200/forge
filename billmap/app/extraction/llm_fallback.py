"""LLM-assisted extraction for unknown vendors.

When no template matches, sends the PDF content to an LLM to:
1. Identify vendor and header fields
2. Suggest extraction regions (coordinates)
3. Extract line items
4. Cross-reference against accounting system reference data
"""

import json

from app.config import settings
from app.extraction.pdf_parser import ParsedPDF


# System prompt for invoice extraction
SYSTEM_PROMPT = """You are an AP (Accounts Payable) invoice data extraction assistant.
Given the text content of an invoice PDF along with the positions of text blocks,
extract the relevant fields and suggest coordinate regions for each field.

Respond ONLY with valid JSON in this exact structure:
{
  "vendor_name": {"value": "...", "region": {"page": 0, "x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}, "confidence": 0.0},
  "invoice_number": {"value": "...", "region": {...}, "confidence": 0.0},
  "invoice_date": {"value": "...", "region": {...}, "confidence": 0.0},
  "due_date": {"value": "...", "region": {...}, "confidence": 0.0},
  "po_number": {"value": "...", "region": {...}, "confidence": 0.0},
  "subtotal": {"value": "...", "region": {...}, "confidence": 0.0},
  "tax_total": {"value": "...", "region": {...}, "confidence": 0.0},
  "total": {"value": "...", "region": {...}, "confidence": 0.0},
  "line_items": [
    {
      "description": "...",
      "item_code": "...",
      "quantity": "...",
      "unit_price": "...",
      "amount": "..."
    }
  ]
}

For each field:
- "value" is the extracted text
- "region" is the normalized bounding box (0-1 range) where this value appears on the PDF
- "confidence" is your confidence in the extraction (0.0 to 1.0)

If a field is not found, set value to null and confidence to 0.0.
For line_items, extract all visible line items from the invoice table."""


def llm_extract(parsed: ParsedPDF, reference_context: str = "") -> dict:
    """Send PDF content to LLM for extraction. Returns structured extraction data."""
    if not settings.llm_provider or not settings.llm_api_key:
        raise RuntimeError("LLM not configured. Set llm_provider and llm_api_key in settings.")

    # Build the content payload — text blocks with positions
    content = _build_content_payload(parsed)

    if reference_context:
        content += f"\n\n--- Reference Data ---\n{reference_context}"

    if settings.llm_provider == "anthropic":
        return _call_anthropic(content)
    elif settings.llm_provider == "openai":
        return _call_openai(content)
    else:
        raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")


def build_reference_context(
    vendors: list[dict] | None = None,
    accounts: list[dict] | None = None,
    items: list[dict] | None = None,
    tax_codes: list[dict] | None = None,
) -> str:
    """Build a reference data context string for the LLM prompt.

    Provides the LLM with valid vendors, accounts, items, and tax codes
    so it can validate and cross-reference extracted data.
    """
    parts = []

    if vendors:
        vendor_names = [v.get("name", "") for v in vendors[:100]]
        parts.append(f"Known vendors: {', '.join(vendor_names)}")

    if accounts:
        account_list = [f"{a.get('external_id', '')}: {a.get('name', '')}" for a in accounts[:50]]
        parts.append(f"Account codes:\n" + "\n".join(account_list))

    if items:
        item_list = [f"{i.get('external_id', '')}: {i.get('name', '')}" for i in items[:100]]
        parts.append(f"Items:\n" + "\n".join(item_list))

    if tax_codes:
        tax_list = [f"{t.get('external_id', '')}: {t.get('name', '')}" for t in tax_codes[:20]]
        parts.append(f"Tax codes:\n" + "\n".join(tax_list))

    return "\n\n".join(parts)


def _build_content_payload(parsed: ParsedPDF) -> str:
    """Format parsed PDF content for the LLM prompt."""
    lines = [f"Invoice PDF — {parsed.page_count} page(s)"]
    if parsed.is_scanned:
        lines.append("(This is a scanned/OCR'd document)")

    for page in parsed.pages:
        lines.append(f"\n--- Page {page.page_number + 1} ---")
        for block in page.text_blocks:
            pos = f"[x={block.x:.3f}, y={block.y:.3f}, w={block.w:.3f}, h={block.h:.3f}]"
            lines.append(f"{pos} {block.text}")

    return "\n".join(lines)


def _call_anthropic(content: str) -> dict:
    """Call Anthropic (Claude) API for extraction."""
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.llm_api_key)
    model = settings.llm_model or "claude-sonnet-4-20250514"

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    return _parse_llm_response(response.content[0].text)


def _call_openai(content: str) -> dict:
    """Call OpenAI (GPT) API for extraction."""
    from openai import OpenAI

    client = OpenAI(api_key=settings.llm_api_key)
    model = settings.llm_model or "gpt-4o"

    response = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        response_format={"type": "json_object"},
    )

    return _parse_llm_response(response.choices[0].message.content)


def _parse_llm_response(text: str) -> dict:
    """Parse the JSON response from the LLM."""
    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON within the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        raise ValueError(f"Could not parse LLM response as JSON: {text[:200]}")
