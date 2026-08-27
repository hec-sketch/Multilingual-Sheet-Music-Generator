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


# Proofing colours. These are for the copy shown in the app while the score is
# being checked - never for the copy that is downloaded, which is always plain
# black. Red says "this note has not been looked at"; green says "somebody has
# been here and settled it". A note deliberately left as it stands keeps its red,
# because "I chose this" and "I never saw this" must not look the same.
ATTENTION = (0.70, 0.13, 0.13)
RESOLVED = (0.11, 0.45, 0.20)
MARK_COLOURS = {"attention": ATTENTION, "resolved": RESOLVED}


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
    score_doc, blank_bytes: bytes, placements, settings: RenderSettings, held=None,
    nudges=None, marks=None
) -> bytes:
    """Draw the syllables onto the blank score.

    ``placements`` maps score-line id -> one syllable per printed English syllable.
    ``held`` maps score-line id -> [(x of a note, syllable)] for syllables that go
    on notes the English holds a vowel across, which have no English syllable of
    their own to sit under.
    ``nudges`` maps score-line id -> one horizontal offset in points per printed
    English syllable, for hand-adjusting a placement the automatic centring gets
    wrong. A positive value moves a syllable right, negative moves it left.

    ``marks`` maps score-line id -> one state per syllable, 'attention' or
    'resolved', and colours those syllables for proofing on screen. Leave it out
    - as the download always does - and every syllable is drawn plain black.
    """
    doc = fitz.open(stream=blank_bytes, filetype="pdf")
    font_path = resolve_font_path(settings.font_choice)
    font = fitz.Font(fontfile=font_path)
    for page in doc:
        page.insert_font(fontname="Lyrics", fontfile=font_path)

    staff_span = {(s.page, s.index): (s.x0, s.x1) for s in score_doc.staves}
    held = held or {}
    nudges = nudges or {}
    marks = marks or {}
    drawn = 0

    def mark_colour(line_id, index):
        states = marks.get(line_id)
        state = states[index] if states and index < len(states) else ""
        return MARK_COLOURS.get(state, settings.colour)

    def nudge(line_id, index):
        shifts = nudges.get(line_id)
        return shifts[index] if shifts and index < len(shifts) else 0.0

    GAP = 1.2  # the clear space left between one syllable and the next

    def uniform_size(seats, x0_limit, x1_limit):
        """The largest one size at which a whole line of syllables does not collide.

        An engraver sets a line of lyrics at a single size, so the question is
        not how big each syllable could be on its own but how big they can all
        be together. Two neighbours clear each other when half of each fits in
        the space between their notes:

            width(a)/2 + width(b)/2 + gap <= centre(b) - centre(a)

        Every width scales with the size, so each neighbouring pair caps the size
        directly, and the smallest cap over the line is the answer. Sizing each
        syllable against the room between its neighbours - as this did - measures
        the wrong thing twice over: the syllable is drawn centred on its own note,
        which is not the middle of that room, and the neighbour's own width is
        never counted at all. Both errors let a syllable overrun at a size that
        looked like a fit.
        """
        drawn = [(centre, text) for centre, text in seats if text]
        if not drawn:
            return settings.max_size
        natural = [font.text_length(text, fontsize=settings.max_size) for _, text in drawn]
        size = settings.max_size

        def cap(room, needed):
            nonlocal size
            if needed > 0:
                size = min(size, settings.max_size * max(room, 0.5) / needed)

        for index in range(len(drawn) - 1):
            cap(drawn[index + 1][0] - drawn[index][0] - GAP,
                (natural[index] + natural[index + 1]) / 2.0)
        # And neither end may run off the staff.
        cap(drawn[0][0] - (x0_limit + 1), natural[0] / 2.0)
        cap((x1_limit - 1) - drawn[-1][0], natural[-1] / 2.0)
        return max(settings.min_size, size)

    def put(page_number, centre, baseline, text, left, right, hard_left=None,
            hard_right=None, colour=None, size=None):
        # `left`/`right` is the room shared with the neighbouring syllables - used
        # only to choose a size that (usually) avoids collisions. The translated
        # syllable is then centred exactly on `centre`, which is the centre of the
        # English syllable it replaces, even when that means slightly overrunning
        # a tight neighbour gap: matching the English centring is the point, and a
        # size chosen from `room` keeps that overrun small in practice. Only the
        # hard page/staff edge - not the inter-syllable room - is allowed to pull
        # a syllable off-centre, as a last-resort guard against drawing off the page.
        #
        # `size` is settled for the whole line by the caller. An engraver sets a
        # line of lyrics at one size; sizing each syllable on its own room makes
        # one long word come out visibly smaller than the words either side of it,
        # which reads as a mistake even when the syllable is right.
        if size is None:
            size = fitted_size(text, left, right)
        width = font.text_length(text, fontsize=size)
        x = centre - width / 2
        if hard_left is not None:
            x = min(max(x, hard_left), max(hard_left, hard_right - width))
        doc[page_number].insert_text(
            (x, baseline),
            text,
            fontname="Lyrics",
            fontsize=size,
            color=colour if colour is not None else settings.colour,
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

    def vacancy(page_number, centre, baseline, colour):
        """Show a note that was left with no syllable at all."""
        doc[page_number].draw_line(
            fitz.Point(centre - 2.6, baseline),
            fitz.Point(centre + 2.6, baseline),
            color=colour,
            width=1.1,
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
            # One size for the whole line, chosen so that none of it collides.
            line_size = uniform_size(
                [
                    (anchor.placement_x + nudge(line.id, index),
                     _wrapped_token(line, index, token, len(tokens or [])))
                    for index, (anchor, token) in enumerate(zip(anchors, tokens or []))
                ],
                x0_limit, x1_limit,
            )
            for index, (anchor, token) in enumerate(zip(anchors, tokens or [])):
                text = _wrapped_token(line, index, token, len(tokens or []))
                if not text:
                    # A note with nothing on it is the one thing colour cannot
                    # show, because there is no text to colour - and it is the
                    # most important thing to see. Mark the gap itself.
                    gap = mark_colour(line.id, index)
                    if gap is not settings.colour:
                        vacancy(anchor.page, anchor.placement_x + nudge(line.id, index),
                                anchor.y + settings.baseline_offset, gap)
                    continue
                left, right = _bounds(anchors, index, x0_limit, x1_limit)
                centre = anchor.placement_x + nudge(line.id, index)
                put(anchor.page, centre, anchor.y + settings.baseline_offset,
                    text, left, right, x0_limit, x1_limit,
                    colour=mark_colour(line.id, index), size=line_size)
                drawn += 1
            continue

        # With a syllable on a held note, the English word widths no longer say
        # where the room is: two syllables now share the space one English word
        # had. Space them by the notes themselves, halfway to each neighbour,
        # which is what an engraver does.
        seats = [(anchor.placement_x + nudge(line.id, index),
                  _wrapped_token(line, index, token, len(tokens or [])),
                  mark_colour(line.id, index))
                 for index, (anchor, token) in enumerate(zip(anchors, tokens or []))]
        # A syllable on a held note has no English syllable of its own, so it has
        # no state of its own either; it takes the plain colour.
        seats += [(x, (text or "").strip(), settings.colour) for x, text in extras]
        seats.sort(key=lambda seat: seat[0])
        baseline = line.y + settings.baseline_offset

        def seat_room(index, centre):
            left = x0_limit + 1 if index == 0 else (seats[index - 1][0] + centre) / 2
            right = (
                x1_limit - 1 if index == len(seats) - 1 else (centre + seats[index + 1][0]) / 2
            )
            return left + 0.5, right - 0.5

        seat_size = uniform_size([(centre, text) for centre, text, _ in seats],
                                 x0_limit, x1_limit)
        for index, (centre, text, colour) in enumerate(seats):
            if not text:
                continue
            left, right = seat_room(index, centre)
            put(line.page, centre, baseline, text, left, right,
                x0_limit, x1_limit, colour=colour, size=seat_size)
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
