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
    max_size: float = 11.0
    baseline_offset: float = 5.6
    min_size: float = 4.5
    font_choice: str = "Sans"
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


def _wrapped_token(score_line, index: int, token: str, total: int) -> str:
    """Carry parenthetical performance marks from the engraved English line."""
    text = (token or "").strip()
    if not text:
        return text
    english = score_line.anchors
    if index == 0 and english and english[0].text.lstrip().startswith("(") and not text.startswith("("):
        text = "(" + text
    if index == total - 1 and english and english[-1].text.rstrip().endswith(")") and not text.endswith(")"):
        text += ")"
    return text


def render(
    score_doc, blank_bytes: bytes, placements, settings: RenderSettings, held=None, nudges=None
) -> bytes:
    """Draw the syllables onto the blank score.

    ``placements`` maps score-line id -> one syllable per printed English syllable.
    ``held`` maps score-line id -> [(x of a note, syllable)] for syllables that go
    on notes the English holds a vowel across, which have no English syllable of
    their own to sit under.
    ``nudges`` maps score-line id -> one horizontal offset in points per printed
    English syllable, for hand-adjusting a placement the automatic centring gets
    wrong. A positive value moves a syllable right, negative moves it left.
    """
    doc = fitz.open(stream=blank_bytes, filetype="pdf")
    font_path = resolve_font_path(settings.font_choice)
    font = fitz.Font(fontfile=font_path)
    for page in doc:
        page.insert_font(fontname="Lyrics", fontfile=font_path)

    staff_span = {(s.page, s.index): (s.x0, s.x1) for s in score_doc.staves}
    held = held or {}
    nudges = nudges or {}
    drawn = 0

    def nudge(line_id, index):
        shifts = nudges.get(line_id)
        return shifts[index] if shifts and index < len(shifts) else 0.0

    def put(page_number, centre, baseline, text, left, right, hard_left=None, hard_right=None):
        # `left`/`right` is the room shared with the neighbouring syllables - used
        # only to choose a size that (usually) avoids collisions. The translated
        # syllable is then centred exactly on `centre`, which is the centre of the
        # English syllable it replaces, even when that means slightly overrunning
        # a tight neighbour gap: matching the English centring is the point, and a
        # size chosen from `room` keeps that overrun small in practice. Only the
        # hard page/staff edge - not the inter-syllable room - is allowed to pull
        # a syllable off-centre, as a last-resort guard against drawing off the page.
        room = max(right - left, 3.0)
        natural = font.text_length(text, fontsize=settings.max_size)
        size = (
            settings.max_size
            if natural <= room
            else max(settings.min_size, settings.max_size * room / natural)
        )
        width = font.text_length(text, fontsize=size)
        x = centre - width / 2
        if hard_left is not None:
            x = min(max(x, hard_left), max(hard_left, hard_right - width))
        doc[page_number].insert_text(
            (x, baseline),
            text,
            fontname="Lyrics",
            fontsize=size,
            color=settings.colour,
            overlay=True,
        )

    def extender(page_number, x0, x1, baseline):
        if x1 - x0 < 4:
            return
        doc[page_number].draw_line(
            fitz.Point(x0, baseline + 1.0),
            fitz.Point(x1, baseline + 1.0),
            color=settings.colour,
            width=0.45,
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
                text = _wrapped_token(line, index, token, len(tokens or []))
                if not text:
                    continue
                left, right = _bounds(anchors, index, x0_limit, x1_limit)
                centre = anchor.placement_x + nudge(line.id, index)
                put(anchor.page, centre, anchor.y + settings.baseline_offset,
                    text, left, right, x0_limit, x1_limit)
                drawn += 1
            continue

        # With a syllable on a held note, the English word widths no longer say
        # where the room is: two syllables now share the space one English word
        # had. Space them by the notes themselves, halfway to each neighbour,
        # which is what an engraver does.
        seats = [(anchor.placement_x + nudge(line.id, index),
                  _wrapped_token(line, index, token, len(tokens or [])))
                 for index, (anchor, token) in enumerate(zip(anchors, tokens or []))]
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
            put(line.page, centre, baseline, text, left + 0.5, right - 0.5, x0_limit, x1_limit)
            drawn += 1

        # Draw one continuous lyric extender for the final held-note run.
        # Intermediate held noteheads can appear in score parsing, but the
        # visible lyric convention is one line from the preceding syllable to
        # the end of the run. Stop early if a translation adds a syllable there.
        if line.held_notes and anchors:
            extra_x = {round(x, 2) for x, _ in extras}
            by_after = {}
            for after_index, x in line.held_notes:
                by_after.setdefault(after_index, []).append(x)
            last_after = max(by_after)
            unoccupied = [x for x in by_after[last_after] if round(x, 2) not in extra_x]
            if unoccupied:
                source_index = min(last_after, len(anchors) - 1)
                source_x = anchors[source_index].placement_x + nudge(line.id, source_index)
                start = source_x + 3.0
                end = max(unoccupied) - 2.0
                if end > start + 3:
                    extender(line.page, start, end, line.y + settings.baseline_offset)

    if not drawn:
        raise ValueError("Nothing was placed - every line was left empty.")
    return doc.tobytes(garbage=4, deflate=True)


def page_image(pdf_bytes: bytes, page_number: int, zoom: float = 1.6) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pixmap = doc[page_number].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pixmap.tobytes("png")
