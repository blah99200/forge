"""Two-tier template matching — identifies which vendor template a PDF belongs to.

Tier 1: Identity match — fuzzy-match vendor tokens (company name, ABN/TIN, email domain)
Tier 2: Template selection — score by anchor positions, layout grid, font set
"""

from dataclasses import dataclass
import math

from Levenshtein import ratio as levenshtein_ratio

from app.extraction.fingerprint import extract_fingerprint
from app.extraction.pdf_parser import ParsedPDF
from app.models.template import VendorTemplate


@dataclass
class MatchResult:
    """Result of template matching."""
    template: VendorTemplate | None
    score: float  # 0-1 overall confidence
    tier: str  # "identity", "layout", "none"
    details: dict  # Breakdown of scoring components


# Thresholds
IDENTITY_THRESHOLD = 0.85  # Levenshtein ratio for vendor token matching
HIGH_CONFIDENCE = 0.6  # Use template confidently
LOW_CONFIDENCE = 0.4  # Use template but flag for review

# Tier 2 weights
ANCHOR_WEIGHT = 0.5
GRID_WEIGHT = 0.3
FONT_WEIGHT = 0.2


def match_template(parsed: ParsedPDF, templates: list[VendorTemplate]) -> MatchResult:
    """Match a parsed PDF against stored templates. Returns the best match."""
    if not templates:
        return MatchResult(template=None, score=0.0, tier="none", details={})

    fingerprint = extract_fingerprint(parsed)

    # Tier 1: Identity match
    identity_matches = _tier1_identity_match(fingerprint["vendor_tokens"], templates)

    if identity_matches:
        # Narrow to this vendor's templates, then pick best by layout
        candidates = identity_matches
    else:
        # No identity match — score all templates
        candidates = templates

    # Tier 2: Layout scoring
    best = _tier2_layout_match(fingerprint, candidates)

    if best.template is None:
        return MatchResult(template=None, score=0.0, tier="none", details=fingerprint)

    # Boost score if we had an identity match
    if identity_matches and best.template in [m for m in identity_matches]:
        best.score = min(1.0, best.score + 0.2)
        best.tier = "identity"

    return best


def _tier1_identity_match(
    pdf_tokens: list[str], templates: list[VendorTemplate]
) -> list[VendorTemplate]:
    """Find templates whose vendor tokens fuzzy-match the PDF's tokens."""
    matches = []

    for template in templates:
        stored_tokens = template.get_vendor_tokens()
        if not stored_tokens:
            continue

        best_ratio = 0.0
        for pdf_tok in pdf_tokens:
            for stored_tok in stored_tokens:
                r = levenshtein_ratio(pdf_tok.lower(), stored_tok.lower())
                best_ratio = max(best_ratio, r)

        if best_ratio >= IDENTITY_THRESHOLD:
            matches.append(template)

    return matches


def _tier2_layout_match(fingerprint: dict, templates: list[VendorTemplate]) -> MatchResult:
    """Score templates by layout similarity (anchors, grid, fonts)."""
    best_score = -1.0
    best_template = None
    best_details = {}

    for template in templates:
        anchor_score = _anchor_similarity(
            fingerprint["anchor_positions"],
            template.get_anchor_positions(),
        )
        grid_score = _grid_similarity(
            fingerprint["layout_grid"],
            template.layout_grid,
        )
        font_score = _font_similarity(
            fingerprint["font_set"],
            template.get_font_set(),
        )

        total = (
            anchor_score * ANCHOR_WEIGHT
            + grid_score * GRID_WEIGHT
            + font_score * FONT_WEIGHT
        )

        if total > best_score:
            best_score = total
            best_template = template
            best_details = {
                "anchor_score": round(anchor_score, 3),
                "grid_score": round(grid_score, 3),
                "font_score": round(font_score, 3),
                "total_score": round(total, 3),
            }

    if best_score < LOW_CONFIDENCE:
        return MatchResult(template=None, score=best_score, tier="none", details=best_details)

    return MatchResult(
        template=best_template,
        score=best_score,
        tier="layout",
        details=best_details,
    )


def _anchor_similarity(pdf_anchors: list[dict], template_anchors: list[dict]) -> float:
    """Compare anchor label positions. Score based on matching labels and position proximity."""
    if not template_anchors:
        return 0.0
    if not pdf_anchors:
        return 0.0

    template_map = {a["label"]: a for a in template_anchors}
    pdf_map = {a["label"]: a for a in pdf_anchors}

    matched = 0
    total_distance = 0.0
    common_labels = set(template_map.keys()) & set(pdf_map.keys())

    if not common_labels:
        return 0.0

    for label in common_labels:
        ta = template_map[label]
        pa = pdf_map[label]
        dist = math.sqrt((ta["x"] - pa["x"]) ** 2 + (ta["y"] - pa["y"]) ** 2)
        # Distance is in 0-~1.4 range (diagonal of unit square)
        # Convert to similarity: close = 1.0, far = 0.0
        similarity = max(0.0, 1.0 - dist * 5)  # 0.2 distance = 0.0 similarity
        total_distance += similarity
        matched += 1

    # Score combines label overlap ratio and position proximity
    overlap_ratio = matched / len(template_anchors)
    avg_proximity = total_distance / matched if matched else 0.0

    return overlap_ratio * 0.5 + avg_proximity * 0.5


def _grid_similarity(pdf_grid: str, template_grid: str) -> float:
    """Hamming distance-based similarity of layout grids."""
    if not template_grid or not pdf_grid:
        return 0.0
    if len(pdf_grid) != len(template_grid):
        return 0.0

    matches = sum(a == b for a, b in zip(pdf_grid, template_grid))
    return matches / len(pdf_grid)


def _font_similarity(pdf_fonts: list[str], template_fonts: list[str]) -> float:
    """Jaccard similarity of font sets."""
    if not template_fonts or not pdf_fonts:
        return 0.0

    pdf_set = set(f.lower() for f in pdf_fonts)
    template_set = set(f.lower() for f in template_fonts)

    intersection = pdf_set & template_set
    union = pdf_set | template_set

    return len(intersection) / len(union) if union else 0.0


def update_template_anchors(template: VendorTemplate, new_anchors: list[dict], alpha: float = 0.3):
    """Update template anchor positions via exponential moving average.

    This allows templates to drift gradually when vendors make minor layout changes.
    """
    stored = template.get_anchor_positions()
    stored_map = {a["label"]: a for a in stored}

    updated = []
    for anchor in new_anchors:
        label = anchor["label"]
        if label in stored_map:
            old = stored_map[label]
            updated.append({
                "label": label,
                "x": round(old["x"] * (1 - alpha) + anchor["x"] * alpha, 4),
                "y": round(old["y"] * (1 - alpha) + anchor["y"] * alpha, 4),
            })
        else:
            updated.append(anchor)

    # Keep any stored anchors not in the new set
    for label, anchor in stored_map.items():
        if label not in {a["label"] for a in updated}:
            updated.append(anchor)

    template.set_anchor_positions(updated)
