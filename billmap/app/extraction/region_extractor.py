"""Coordinate-based text extraction — extracts text from defined regions on a parsed PDF.

Given a template's field mappings (each with a region), extracts the text that falls
within each region and applies post-processing rules.
"""

import re
from datetime import datetime

from app.extraction.pdf_parser import ParsedPDF, TextBlock
from app.models.template import FieldMapping, VendorTemplate, LineItemTableConfig


def extract_fields(parsed: ParsedPDF, template: VendorTemplate) -> dict:
    """Extract all mapped fields from a parsed PDF using the template's field mappings.

    Returns a dict of {field_name: extracted_value}.
    """
    result = {}

    for mapping in template.field_mappings:
        if mapping.field_type == "header":
            region = mapping.get_region()
            raw_text = _extract_text_in_region(parsed, region)
            processed = _post_process(raw_text, mapping.get_post_processing())
            result[mapping.field_name] = processed

    # Extract line items if table config exists
    if template.table_config:
        result["line_items"] = extract_line_items(parsed, template.table_config)

    return result


def extract_line_items(parsed: ParsedPDF, table_config: LineItemTableConfig) -> list[dict]:
    """Extract line items from a table region using column definitions."""
    table_region = table_config.get_table_region()
    columns = table_config.get_column_definitions()
    page_idx = table_region.get("page", 0)

    if page_idx >= len(parsed.pages):
        return []

    page = parsed.pages[page_idx]

    # Get all text blocks within the table region
    table_blocks = _get_blocks_in_region(page.text_blocks, table_region)

    if not table_blocks:
        return []

    # Group blocks into rows by vertical proximity
    rows = _group_into_rows(table_blocks)

    items = []
    for row_blocks in rows:
        item = {}
        for col_def in columns:
            # Find blocks that fall within this column's x range
            col_x = col_def.get("x", 0)
            col_w = col_def.get("w", 0.1)
            col_blocks = [
                b for b in row_blocks
                if _overlaps_horizontal(b, col_x, col_w)
            ]
            text = " ".join(b.text for b in col_blocks).strip()
            if text:
                item[col_def["name"]] = text

        if item:  # Skip empty rows
            items.append(item)

    return items


def extract_text_at_region(parsed: ParsedPDF, region: dict) -> str:
    """Public helper — extract text from a single region. Used by the mapping builder."""
    return _extract_text_in_region(parsed, region)


def _extract_text_in_region(parsed: ParsedPDF, region: dict) -> str:
    """Extract text that falls within a normalized bounding box region."""
    page_idx = region.get("page", 0)
    if page_idx >= len(parsed.pages):
        return ""

    page = parsed.pages[page_idx]
    blocks = _get_blocks_in_region(page.text_blocks, region)

    # Sort by position (top to bottom, left to right)
    blocks.sort(key=lambda b: (b.y, b.x))

    return " ".join(b.text for b in blocks).strip()


def _get_blocks_in_region(blocks: list[TextBlock], region: dict) -> list[TextBlock]:
    """Filter blocks that overlap with the given region."""
    rx = region.get("x", 0)
    ry = region.get("y", 0)
    rw = region.get("w", 0)
    rh = region.get("h", 0)

    result = []
    for block in blocks:
        # Check if block overlaps with region (at least 50% of block area)
        overlap_x = max(0, min(block.x + block.w, rx + rw) - max(block.x, rx))
        overlap_y = max(0, min(block.y + block.h, ry + rh) - max(block.y, ry))

        if overlap_x > 0 and overlap_y > 0:
            block_area = block.w * block.h if block.w * block.h > 0 else 1e-10
            overlap_area = overlap_x * overlap_y
            if overlap_area / block_area > 0.3:  # 30% overlap threshold
                result.append(block)

    return result


def _overlaps_horizontal(block: TextBlock, col_x: float, col_w: float) -> bool:
    """Check if a block's horizontal position overlaps with a column."""
    block_center_x = block.x + block.w / 2
    return col_x <= block_center_x <= col_x + col_w


def _group_into_rows(blocks: list[TextBlock], tolerance: float = 0.01) -> list[list[TextBlock]]:
    """Group text blocks into rows based on vertical proximity."""
    if not blocks:
        return []

    sorted_blocks = sorted(blocks, key=lambda b: (b.y, b.x))
    rows: list[list[TextBlock]] = []
    current_row: list[TextBlock] = [sorted_blocks[0]]

    for block in sorted_blocks[1:]:
        if abs(block.y - current_row[0].y) <= tolerance:
            current_row.append(block)
        else:
            rows.append(sorted(current_row, key=lambda b: b.x))
            current_row = [block]

    if current_row:
        rows.append(sorted(current_row, key=lambda b: b.x))

    return rows


def _post_process(text: str, rules: dict) -> str:
    """Apply post-processing rules to extracted text."""
    if not rules or not text:
        return text

    if rules.get("trim", True):
        text = text.strip()

    if "regex" in rules:
        match = re.search(rules["regex"], text)
        if match:
            text = match.group(1) if match.groups() else match.group(0)

    if "prefix_remove" in rules:
        prefix = rules["prefix_remove"]
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()

    if "date_format" in rules:
        # Try to parse and normalize the date
        text = _normalize_date(text, rules["date_format"])

    if "number" in rules and rules["number"]:
        text = _normalize_number(text)

    return text


def _normalize_date(text: str, input_format: str) -> str:
    """Try to parse a date string and normalize to ISO format."""
    format_map = {
        "MM/DD/YYYY": "%m/%d/%Y",
        "DD/MM/YYYY": "%d/%m/%Y",
        "YYYY-MM-DD": "%Y-%m-%d",
        "MM-DD-YYYY": "%m-%d-%Y",
    }

    py_format = format_map.get(input_format, input_format)
    try:
        dt = datetime.strptime(text.strip(), py_format)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return text


def _normalize_number(text: str) -> str:
    """Clean up a number string — remove currency symbols, commas."""
    cleaned = re.sub(r"[^\d.\-]", "", text)
    return cleaned
