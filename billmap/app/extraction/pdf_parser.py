"""Unified PDF text extraction — handles both native PDFs and scanned images.

Returns text with bounding box positions normalized to 0-1 range relative to page dimensions.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber


@dataclass
class TextBlock:
    """A block of text with its position on the page."""
    text: str
    page: int
    x: float  # Normalized 0-1
    y: float  # Normalized 0-1
    w: float  # Normalized 0-1
    h: float  # Normalized 0-1
    font_name: str = ""
    font_size: float = 0.0


@dataclass
class ParsedPage:
    """All extracted data from a single PDF page."""
    page_number: int
    width: float  # Original page width in points
    height: float  # Original page height in points
    text_blocks: list[TextBlock] = field(default_factory=list)
    full_text: str = ""
    fonts_used: set[str] = field(default_factory=set)
    is_scanned: bool = False


@dataclass
class ParsedPDF:
    """Complete extraction result for a PDF document."""
    file_path: str
    page_count: int
    pages: list[ParsedPage] = field(default_factory=list)
    all_fonts: set[str] = field(default_factory=set)
    is_scanned: bool = False

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.full_text for p in self.pages)


def parse_pdf(file_path: str | Path) -> ParsedPDF:
    """Parse a PDF and extract text with positions.

    Tries native text extraction first (pdfplumber). If the first page yields
    very little text, falls back to OCR via Tesseract.
    """
    file_path = str(file_path)

    with pdfplumber.open(file_path) as pdf:
        result = ParsedPDF(file_path=file_path, page_count=len(pdf.pages))

        for page_idx, page in enumerate(pdf.pages):
            parsed_page = _extract_page_native(page, page_idx)
            result.pages.append(parsed_page)
            result.all_fonts.update(parsed_page.fonts_used)

        # Check if this is likely a scanned PDF (very little text extracted)
        total_text = result.full_text.strip()
        if len(total_text) < 50 and result.page_count > 0:
            result = _parse_pdf_ocr(file_path)

    return result


def _extract_page_native(page, page_idx: int) -> ParsedPage:
    """Extract text blocks from a native PDF page using pdfplumber."""
    width = float(page.width)
    height = float(page.height)

    parsed = ParsedPage(
        page_number=page_idx,
        width=width,
        height=height,
    )

    # Extract words with positions
    words = page.extract_words(extra_attrs=["fontname", "size"])
    if not words:
        parsed.full_text = page.extract_text() or ""
        return parsed

    # Group words into lines based on vertical proximity
    lines = _group_words_into_lines(words, height)

    text_blocks = []
    fonts = set()

    for line_words in lines:
        text = " ".join(w["text"] for w in line_words)
        x0 = min(w["x0"] for w in line_words)
        y0 = min(w["top"] for w in line_words)
        x1 = max(w["x1"] for w in line_words)
        y1 = max(w["bottom"] for w in line_words)

        font_name = line_words[0].get("fontname", "")
        font_size = line_words[0].get("size", 0.0)
        fonts.add(font_name)

        block = TextBlock(
            text=text,
            page=page_idx,
            x=x0 / width,
            y=y0 / height,
            w=(x1 - x0) / width,
            h=(y1 - y0) / height,
            font_name=font_name,
            font_size=font_size,
        )
        text_blocks.append(block)

    parsed.text_blocks = text_blocks
    parsed.full_text = "\n".join(b.text for b in text_blocks)
    parsed.fonts_used = fonts

    return parsed


def _group_words_into_lines(words: list[dict], page_height: float, tolerance: float = 3.0) -> list[list[dict]]:
    """Group words into lines based on vertical proximity."""
    if not words:
        return []

    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    current_line: list[dict] = [sorted_words[0]]

    for word in sorted_words[1:]:
        if abs(word["top"] - current_line[-1]["top"]) <= tolerance:
            current_line.append(word)
        else:
            current_line.sort(key=lambda w: w["x0"])
            lines.append(current_line)
            current_line = [word]

    if current_line:
        current_line.sort(key=lambda w: w["x0"])
        lines.append(current_line)

    return lines


def _parse_pdf_ocr(file_path: str) -> ParsedPDF:
    """Fallback: extract text from scanned PDF using Tesseract OCR."""
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image
        import io
    except ImportError as e:
        raise RuntimeError(f"OCR dependencies not available: {e}. Install PyMuPDF, pytesseract, and Pillow.")

    doc = fitz.open(file_path)
    result = ParsedPDF(file_path=file_path, page_count=len(doc), is_scanned=True)

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        # Render page as image at 300 DPI
        pix = page.get_pixmap(dpi=300)
        img = Image.open(io.BytesIO(pix.tobytes("png")))

        # Get page dimensions
        width = float(page.rect.width)
        height = float(page.rect.height)

        # Run Tesseract with bounding box data
        ocr_data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

        parsed_page = ParsedPage(
            page_number=page_idx,
            width=width,
            height=height,
            is_scanned=True,
        )

        # Image dimensions for normalization (may differ from PDF point dimensions)
        img_w, img_h = img.size

        text_blocks = []
        words_in_line: list[dict] = []
        last_line_num = -1

        for i in range(len(ocr_data["text"])):
            text = ocr_data["text"][i].strip()
            if not text:
                continue

            line_num = ocr_data["line_num"][i]
            word_data = {
                "text": text,
                "x0": ocr_data["left"][i] / img_w,
                "y0": ocr_data["top"][i] / img_h,
                "w": ocr_data["width"][i] / img_w,
                "h": ocr_data["height"][i] / img_h,
            }

            if line_num != last_line_num and words_in_line:
                block = _ocr_words_to_block(words_in_line, page_idx)
                text_blocks.append(block)
                words_in_line = []

            words_in_line.append(word_data)
            last_line_num = line_num

        if words_in_line:
            block = _ocr_words_to_block(words_in_line, page_idx)
            text_blocks.append(block)

        parsed_page.text_blocks = text_blocks
        parsed_page.full_text = "\n".join(b.text for b in text_blocks)
        result.pages.append(parsed_page)

    doc.close()
    return result


def _ocr_words_to_block(words: list[dict], page_idx: int) -> TextBlock:
    """Convert a group of OCR words into a single TextBlock."""
    text = " ".join(w["text"] for w in words)
    x = min(w["x0"] for w in words)
    y = min(w["y0"] for w in words)
    x1 = max(w["x0"] + w["w"] for w in words)
    y1 = max(w["y0"] + w["h"] for w in words)

    return TextBlock(
        text=text,
        page=page_idx,
        x=x,
        y=y,
        w=x1 - x,
        h=y1 - y,
    )
