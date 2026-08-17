"""Draw the matched syllables onto the score that has no lyrics."""

from __future__ import annotations

import os
from dataclasses import dataclass

import pymupdf as fitz

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")

BUNDLED_FONTS = {
    "Serif (matches most scores)": "LiberationSerif-Regular.ttf",
    "Serif Italic": "LiberationSerif-Italic.ttf",
    "Sans": "DejaVuSans.ttf",
}

SYSTEM_FALLBACKS = [
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


@dataclass
class RenderSettings:
    max_size: float = 7.25
    baseline_offset: float = 7.6
    min_size: float = 4.5
    font_choice: str = "Serif (matches most scores)"
    colour: tuple = (0.0, 0.0, 0.0)


def resolve_font_path(choice: str) -> str:
    filename = BUNDLED_FONTS.get(choice)
    if filename:
        candidate = os.path.join(FONT_DIR, filename)
        if os.path.exists(candidate):
            return candidate
    for path in SYSTEM_FALLBACKS:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(
        "No Unicode font was found. Place a .ttf file in the app's 'fonts' folder."
    )


def check_geometry(score_doc, blank_bytes: bytes) -> list[str]:
    """Warn when the two scores are not the same engraving."""
    problems: list[str] = []
    blank = fitz.open(stream=blank_bytes, filetype="pdf")
    if len(blank) != score_doc.page_count:
        problems.append(
            f"The English score has {score_doc.page_count} pages but the no-lyrics score has "
            f"{len(blank)}. They must be the same engraving of the same arrangement."
        )
        return problems

    from .score import detect_staves, group_systems

    for page_number, page in enumerate(blank):
        staves = detect_staves(page, page_number)
        group_systems(page, staves)
        english = [s for s in score_doc.staves if s.page == page_number]
        if len(staves) != len(english):
            problems.append(
                f"Page {page_number + 1}: the English score has {len(english)} staves but the "
                f"no-lyrics score has {len(staves)}."
            )
            continue
        drift = max(abs(a.top - b.top) for a, b in zip(sorted(staves, key=lambda s: s.top),
                                                      sorted(english, key=lambda s: s.top)))
        if drift > 3.0:
            problems.append(
                f"Page {page_number + 1}: the staves sit {drift:.1f}pt apart between the two "
                "scores, so syllables may land in the wrong place."
            )
    return problems


def _bounds(anchors, index, staff_x0, staff_x1):
    """Horizontal room available to the syllable at `index`."""
    current = anchors[index]
    left = staff_x0 + 1 if index == 0 else (anchors[index - 1].x1 + current.x0) / 2
    right = staff_x1 - 1 if index == len(anchors) - 1 else (current.x1 + anchors[index + 1].x0) / 2
    if right - left < 4:
        left, right = current.x0 - 2, current.x1 + 2
    return left, right


def render(score_doc, blank_bytes: bytes, placements, settings: RenderSettings) -> bytes:
    """`placements` maps score-line id -> list of syllable strings (one per anchor)."""
    doc = fitz.open(stream=blank_bytes, filetype="pdf")
    font_path = resolve_font_path(settings.font_choice)
    font = fitz.Font(fontfile=font_path)
    for page in doc:
        page.insert_font(fontname="Lyrics", fontfile=font_path)

    staff_span = {(s.page, s.index): (s.x0, s.x1) for s in score_doc.staves}
    drawn = 0

    for line in score_doc.lines:
        tokens = placements.get(line.id)
        if not tokens:
            continue
        anchors = line.anchors
        x0_limit, x1_limit = staff_span.get((line.page, line.staff), (40.0, 560.0))
        for index, (anchor, token) in enumerate(zip(anchors, tokens)):
            text = (token or "").strip()
            if not text:
                continue
            left, right = _bounds(anchors, index, x0_limit, x1_limit)
            room = max(right - left, 3.0)
            natural = font.text_length(text, fontsize=settings.max_size)
            if natural <= room:
                size = settings.max_size
            else:
                size = max(settings.min_size, settings.max_size * room / natural)
            width = font.text_length(text, fontsize=size)
            centre = (anchor.x0 + anchor.x1) / 2
            x = centre - width / 2
            x = min(max(x, left), max(left, right - width))
            page = doc[anchor.page]
            page.insert_text(
                (x, anchor.y + settings.baseline_offset),
                text,
                fontname="Lyrics",
                fontsize=size,
                color=settings.colour,
                overlay=True,
            )
            drawn += 1

    if not drawn:
        raise ValueError("Nothing was placed - every line was left empty.")
    return doc.tobytes(garbage=4, deflate=True)


def page_image(pdf_bytes: bytes, page_number: int, zoom: float = 1.6) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pixmap = doc[page_number].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pixmap.tobytes("png")
