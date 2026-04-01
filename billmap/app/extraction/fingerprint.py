"""Extract fingerprint features from a parsed PDF for template matching.

Features extracted:
1. Vendor tokens — company names, ABN/TIN numbers, email domains
2. Anchor positions — positions of common invoice labels ("Invoice", "Bill To", etc.)
3. Layout grid — 4x6 grid boolean mask of where text appears
4. Font set — distinct font names used
"""

import re
from app.extraction.pdf_parser import ParsedPDF, ParsedPage, TextBlock


# Labels commonly found in fixed positions on invoices
ANCHOR_LABELS = [
    "invoice", "bill to", "ship to", "from", "date", "due date",
    "invoice number", "invoice #", "inv #", "po number", "po #",
    "subtotal", "sub total", "total", "tax", "gst", "hst", "pst",
    "amount due", "balance due", "payment terms", "terms",
    "description", "quantity", "qty", "unit price", "amount",
    "item", "account",
]

# Patterns for vendor identity tokens
ABN_PATTERN = re.compile(r"\b(?:ABN|TIN|EIN|VAT|GST)\s*[:#]?\s*([\d\s-]{8,20})\b", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_PATTERN = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")


def extract_vendor_tokens(parsed: ParsedPDF) -> list[str]:
    """Extract identifying tokens from the PDF — company name candidates, ABN/TIN, email domains.

    Focuses on the top portion of the first page where vendor info typically appears.
    """
    if not parsed.pages:
        return []

    page = parsed.pages[0]
    tokens = []

    # Get text blocks in the top 30% of the page (where vendor info usually lives)
    top_blocks = [b for b in page.text_blocks if b.y < 0.3]

    full_top_text = " ".join(b.text for b in top_blocks)

    # Extract ABN/TIN/VAT numbers
    for match in ABN_PATTERN.finditer(full_top_text):
        tokens.append(match.group(0).strip())

    # Extract email domains
    for match in EMAIL_PATTERN.finditer(full_top_text):
        domain = match.group(0).split("@")[1]
        tokens.append(domain)

    # The largest text in the top section is likely the company name
    if top_blocks:
        # Sort by font size descending, take the biggest
        sized_blocks = [b for b in top_blocks if b.font_size > 0]
        if sized_blocks:
            sized_blocks.sort(key=lambda b: b.font_size, reverse=True)
            # Take blocks with the largest font size
            max_size = sized_blocks[0].font_size
            name_blocks = [b for b in sized_blocks if b.font_size >= max_size * 0.9]
            for b in name_blocks[:2]:
                text = b.text.strip()
                if len(text) > 2 and not ABN_PATTERN.match(text):
                    tokens.append(text)
        else:
            # No font size info (OCR) — use first non-trivial text block
            for b in top_blocks[:3]:
                text = b.text.strip()
                if len(text) > 3:
                    tokens.append(text)
                    break

    return tokens


def extract_anchor_positions(parsed: ParsedPDF) -> list[dict]:
    """Find common invoice labels and their normalized positions."""
    if not parsed.pages:
        return []

    page = parsed.pages[0]
    anchors = []
    found_labels = set()

    for block in page.text_blocks:
        text_lower = block.text.strip().lower()

        for label in ANCHOR_LABELS:
            if label in found_labels:
                continue
            # Match if block text starts with or equals the label
            if text_lower == label or text_lower.startswith(label + ":") or text_lower.startswith(label + " "):
                anchors.append({
                    "label": label,
                    "x": round(block.x, 4),
                    "y": round(block.y, 4),
                })
                found_labels.add(label)
                break

    return anchors


def extract_layout_grid(parsed: ParsedPDF, rows: int = 6, cols: int = 4) -> str:
    """Generate a binary grid showing which cells contain text.

    Divides the first page into a rows x cols grid and marks each cell
    as 1 (has text) or 0 (empty). Returns a binary string.
    """
    if not parsed.pages:
        return "0" * (rows * cols)

    page = parsed.pages[0]
    grid = [[False] * cols for _ in range(rows)]

    for block in page.text_blocks:
        # Determine which grid cell the center of this block falls into
        cx = block.x + block.w / 2
        cy = block.y + block.h / 2

        col = min(int(cx * cols), cols - 1)
        row = min(int(cy * rows), rows - 1)
        grid[row][col] = True

    return "".join("1" if grid[r][c] else "0" for r in range(rows) for c in range(cols))


def extract_font_set(parsed: ParsedPDF) -> list[str]:
    """Get the set of distinct font names used in the document."""
    return sorted(parsed.all_fonts - {""})


def extract_fingerprint(parsed: ParsedPDF) -> dict:
    """Extract all fingerprint features from a parsed PDF."""
    return {
        "vendor_tokens": extract_vendor_tokens(parsed),
        "anchor_positions": extract_anchor_positions(parsed),
        "layout_grid": extract_layout_grid(parsed),
        "font_set": extract_font_set(parsed),
    }
