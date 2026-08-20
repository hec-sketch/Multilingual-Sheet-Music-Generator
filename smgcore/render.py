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


def render(score_doc, blank_bytes: bytes, placements, settings: RenderSettings, held=None) -> bytes:
    """Draw the syllables onto the blank score.

    ``placements`` maps score-line id -> one syllable per printed English syllable.
    ``held`` maps score-line id -> [(x of a note, syllable)] for syllables that go
    on notes the English holds a vowel across, which have no English syllable of
    their own to sit under.
    """
    doc = fitz.open(stream=blank_bytes, filetype="pdf")
    font_path = resolve_font_path(settings.font_choice)
    font = fitz.Font(fontfile=font_path)
    for page in doc:
        page.insert_font(fontname="Lyrics", fontfile=font_path)

    staff_span = {(s.page, s.index): (s.x0, s.x1) for s in score_doc.staves}
    held = held or {}
    drawn = 0

    def put(page_number, centre, baseline, text, left, right):
        room = max(right - left, 3.0)
        natural = font.text_length(text, fontsize=settings.max_size)
        size = (
            settings.max_size
            if natural <= room
            else max(settings.min_size, settings.max_size * room / natural)
        )
        width = font.text_length(text, fontsize=size)
        x = min(max(centre - width / 2, left), max(left, right - width))
        doc[page_number].insert_text(
            (x, baseline),
            text,
            fontname="Lyrics",
            fontsize=size,
            color=settings.colour,
            overlay=True,
        )

    for line in score_doc.lines:
        tokens = placements.get(line.id)
        extras = held.get(line.id) or []
        if not tokens and not extras:
            continue
        anchors = line.anchors
        x0_limit, x1_limit = staff_span.get((line.page, line.staff), (40.0, 560.0))

        if not extras:
            for index, (anchor, token) in enumerate(zip(anchors, tokens or [])):
                text = (token or "").strip()
                if not text:
                    continue
                left, right = _bounds(anchors, index, x0_limit, x1_limit)
                put(anchor.page, anchor.centre, anchor.y + settings.baseline_offset,
                    text, left, right)
                drawn += 1
            continue

        # With a syllable on a held note, the English word widths no longer say
        # where the room is: two syllables now share the space one English word
        # had. Space them by the notes themselves, halfway to each neighbour,
        # which is what an engraver does.
        seats = [(anchor.centre, (token or "").strip()) for anchor, token
                 in zip(anchors, tokens or [])]
        seats += [(x, (text or "").strip()) for x, text in extras]
        seats.sort()
        baseline = line.y + settings.baseline_offset
        for index, (centre, text) in enumerate(seats):
            if not text:
                continue
            left = x0_limit + 1 if index == 0 else (seats[index - 1][0] + centre) / 2
            right = (
                x1_limit - 1 if index == len(seats) - 1 else (centre + seats[index + 1][0]) / 2
            )
            put(line.page, centre, baseline, text, left + 0.5, right - 0.5)
            drawn += 1

    if not drawn:
        raise ValueError("Nothing was placed - every line was left empty.")
    return doc.tobytes(garbage=4, deflate=True)


def page_image(pdf_bytes: bytes, page_number: int, zoom: float = 1.6) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pixmap = doc[page_number].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pixmap.tobytes("png")
