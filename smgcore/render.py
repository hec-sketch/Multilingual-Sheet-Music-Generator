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
    font_choice: str = "Serif (matches most scores)"
    colour: tuple = (0.0, 0.0, 0.0)


# An engraver sets the lyrics of a score at one size throughout; a line that
# came out smaller than the line above it reads as a mistake even when every
# syllable in it is right. So the size is settled once for the whole document
# rather than once per line, and a crowded line is answered by moving its
# syllables apart - see resolve_overlaps - instead of by shrinking the type.
#
# It still cannot be the smallest line's size, because one line too full to fit
# its staff at any reasonable size would shrink the whole score with it. So it
# is the size all but the most crowded tenth of lines can hold.
UNIFORM_PERCENTILE = 0.10


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


def document_size(caps: list[float], settings) -> float:
    """One type size for the whole score, from what each line could take.

    It cannot be the smallest line's size: a single crowded line would shrink
    every other line on every page with it. So it is the size all but the most
    crowded tenth of lines can take at full width. Those few step down to their
    own size in the drawing pass - which, now that crowding is answered by
    moving syllables rather than shrinking them, is rare.
    """
    if not caps:
        return settings.max_size
    ordered = sorted(caps)
    index = min(int(len(ordered) * UNIFORM_PERCENTILE), len(ordered) - 1)
    return max(settings.min_size, min(settings.max_size, ordered[index]))


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
    nudges=None, marks=None, layout_out=None
) -> bytes:
    """Draw the syllables onto the blank score.

    ``placements`` maps score-line id -> one syllable per printed English syllable.
    ``held`` maps score-line id -> [(x of a note, syllable)] for syllables that go
    on notes the English holds a vowel across, which have no English syllable of
    their own to sit under.

    Nothing is drawn under a held note beyond its syllable. The engraved English
    uses an extender line to say "hold this vowel onward", but the translated
    layout gives those notes syllables of their own, so a line there says
    nothing the syllable has not already said - and where the layout hyphenated,
    it actively contradicted it. Hannah asked for them all gone; there is no
    case left in which one is wanted.
    ``nudges`` maps score-line id -> one horizontal offset in points per printed
    English syllable, for hand-adjusting a placement the automatic centring gets
    wrong. A positive value moves a syllable right, negative moves it left.

    ``marks`` maps score-line id -> one state per syllable, 'attention' or
    'resolved', and colours those syllables for proofing on screen. Leave it out
    - as the download always does - and every syllable is drawn plain black.

    ``layout_out``, if given a list, is filled with one record per syllable
    saying where it was actually drawn. Syllables are moved apart where they
    would otherwise touch, and a syllable on a held note has no anchor to be
    found from at all, so this is the only honest source for anything that has
    to point at a syllable on the page - the click targets in the editor above
    all, which used to be computed from the anchors and so missed both.
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

    # All the text on one page goes into a single drawing, committed once at
    # the end. Committing one syllable at a time makes PyMuPDF re-scan the
    # whole page content stream for every syllable, which on a full score is
    # most of the time the render takes - and that wait sits between typing a
    # correction and seeing it.
    shapes: dict[int, object] = {}

    def shape(page_number):
        if page_number not in shapes:
            shapes[page_number] = doc[page_number].new_shape()
        return shapes[page_number]

    def mark_colour(line_id, index):
        states = marks.get(line_id)
        state = states[index] if states and index < len(states) else ""
        return MARK_COLOURS.get(state, settings.colour)

    def nudge(line_id, index):
        shifts = nudges.get(line_id)
        return shifts[index] if shifts and index < len(shifts) else 0.0

    GAP = 1.2  # the clear space left between one syllable and the next

    def line_capacity(seats, x0_limit, x1_limit):
        """The largest one size at which a line of syllables fits its staff.

        This asks only whether the whole line fits between the staff edges -
        every syllable's width, plus a gap between each - because a syllable
        that crowds its neighbour is moved aside rather than shrunk. It is a far
        weaker constraint than demanding each syllable sit exactly on its note
        and clear its neighbours, which is what used to drag a whole line down
        to 6pt because two long words happened to land on close notes.
        """
        drawn = [text for _, text in seats if text]
        if not drawn:
            return settings.max_size
        needed = sum(font.text_length(text, fontsize=settings.max_size) for text in drawn)
        needed += GAP * (len(drawn) - 1)
        available = (x1_limit - 1) - (x0_limit + 1)
        if needed <= 0 or available <= 0:
            return settings.max_size
        return max(settings.min_size,
                   min(settings.max_size, settings.max_size * available / needed))

    def resolve_overlaps(centres, widths, x0_limit, x1_limit):
        """Move syllables apart until none overlaps, each as little as possible.

        A syllable belongs under its own note, so the placement wanted is the
        one that keeps every syllable as near its note as it can while leaving
        no two of them touching. Written out, that is: choose centres x with

            x[i+1] - x[i]  >=  (width[i] + width[i+1]) / 2 + gap

        minimising the total squared distance from the notes. Subtracting the
        running minimum separation turns it into fitting a non-decreasing
        sequence, which pool-adjacent-violators solves exactly in one pass - so
        a crowded run spreads symmetrically about itself and the syllables
        either side of it do not move at all.

        This is what an engraver does with a tight bar, and it is the reason the
        score can now hold one type size throughout: crowding is paid for in a
        point or two of centring rather than in the size of every other line.
        """
        count = len(centres)
        if count < 2:
            return list(centres)
        separations = [(widths[i] + widths[i + 1]) / 2.0 + GAP for i in range(count - 1)]
        running = [0.0] * count
        for i in range(1, count):
            running[i] = running[i - 1] + separations[i - 1]

        # Pool adjacent violators over centre[i] - running[i].
        blocks: list[list[float]] = []
        for i in range(count):
            blocks.append([centres[i] - running[i], 1.0])
            while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
                total, weight = blocks.pop()
                blocks[-1][0] += total
                blocks[-1][1] += weight

        placed: list[float] = []
        for total, weight in blocks:
            placed.extend([total / weight] * int(weight))
        placed = [placed[i] + running[i] for i in range(count)]

        # Then slide the whole line back inside the staff if it has run off an
        # edge, preferring the left edge when it cannot honour both.
        overflow_right = (placed[-1] + widths[-1] / 2.0) - (x1_limit - 1)
        if overflow_right > 0:
            placed = [x - overflow_right for x in placed]
        underflow_left = (x0_limit + 1) - (placed[0] - widths[0] / 2.0)
        if underflow_left > 0:
            placed = [x + underflow_left for x in placed]
        return placed

    def put(page_number, centre, baseline, text, hard_left, hard_right,
            colour=None, size=None):
        # The translated syllable is centred exactly on `centre`, the centre of
        # the English syllable it replaces, even when that slightly overruns a
        # tight neighbour: matching the English centring is the point. Only the
        # hard staff edge is allowed to pull a syllable off-centre, as a
        # last-resort guard against drawing off the page.
        #
        # `size` is settled for the whole document by the caller. An engraver
        # sets the lyrics of a score at one size; sizing each line on its own
        # crowding makes one line come out visibly smaller than the line above
        # it, which reads as a mistake even when every syllable in it is right.
        width = font.text_length(text, fontsize=size)
        x = centre - width / 2
        if hard_left is not None:
            x = min(max(x, hard_left), max(hard_left, hard_right - width))
        shape(page_number).insert_text(
            (x, baseline),
            text,
            fontname="Lyrics",
            fontsize=size,
            color=colour if colour is not None else settings.colour,
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

    def seats_for(line):
        """Every syllable drawn on one line, left to right.

        A syllable sitting on a held note has no English syllable under it and
        so appears in none of the English anchors. Gathering both kinds here
        means the sizing, the drawing and the click targets all work from one
        list, and a held-note syllable is no longer invisible to any of them.

        Each seat is (centre, text, colour, page, baseline, slot), where `slot`
        names the syllable for the editor: ("english", n) for one sitting under
        an English syllable, ("held", n) for one on a held note.
        """
        tokens = placements.get(line.id)
        extras = held.get(line.id) or []
        if not tokens and not extras:
            return []
        total = len(tokens or [])
        seats = [
            (anchor.placement_x + nudge(line.id, index),
             _wrapped_token(line, index, token, total),
             mark_colour(line.id, index),
             anchor.page,
             anchor.y + settings.baseline_offset,
             ("english", index))
            for index, (anchor, token) in enumerate(zip(line.anchors, tokens or []))
        ]
        if extras:
            # A syllable on a held note has no English syllable of its own, so
            # it has no state of its own either; it takes the plain colour.
            seats += [(x, (text or "").strip(), settings.colour,
                       line.page, line.y + settings.baseline_offset, ("held", n))
                      for n, (x, text) in enumerate(extras)]
            seats.sort(key=lambda seat: seat[0])
        return seats

    # First pass: the largest size each line could take on its own without
    # colliding. Nothing is drawn yet, because no line may choose its own size.
    line_seats: dict[int, list] = {}
    line_caps: dict[int, float] = {}
    for line in score_doc.lines:
        seats = seats_for(line)
        if not seats:
            continue
        line_seats[line.id] = seats
        x0_limit, x1_limit = staff_span.get((line.page, line.staff), (40.0, 560.0))
        line_caps[line.id] = line_capacity(
            [(centre, text) for centre, text, _, _, _, _ in seats], x0_limit, x1_limit
        )

    document = document_size(list(line_caps.values()), settings)

    # Second pass: draw the whole score at that one size, letting only the most
    # crowded lines step down, and never far.
    for line in score_doc.lines:
        seats = line_seats.get(line.id)
        if not seats:
            continue
        x0_limit, x1_limit = staff_span.get((line.page, line.staff), (40.0, 560.0))
        # A line only drops below the document size when it could not physically
        # fit its staff at that size - which, now that crowding is answered by
        # moving syllables rather than shrinking them, is rare.
        size = max(min(document, line_caps[line.id]), settings.min_size)

        # Where two syllables would touch, they are moved apart instead of the
        # whole line being set smaller: a point or two off-centre reads as
        # engraving, a line in smaller type reads as a mistake.
        filled = [seat for seat in seats if seat[1]]
        widths = [font.text_length(seat[1], fontsize=size) for seat in filled]
        placed = resolve_overlaps([seat[0] for seat in filled], widths,
                                  x0_limit, x1_limit)
        moved = iter(placed)

        for centre, text, colour, page_number, baseline, slot in seats:
            if not text:
                # A note with nothing on it is the one thing colour cannot show,
                # because there is no text to colour - and it is the most
                # important thing to see. Mark the gap itself.
                if colour is not settings.colour:
                    vacancy(page_number, centre, baseline, colour)
                if layout_out is not None:
                    layout_out.append({"page": page_number, "x": centre, "y": baseline,
                                       "size": size, "text": "", "line_id": line.id,
                                       "slot": slot[0], "index": slot[1]})
                continue
            at = next(moved)
            put(page_number, at, baseline, text, x0_limit, x1_limit,
                colour=colour, size=size)
            if layout_out is not None:
                layout_out.append({"page": page_number, "x": at, "y": baseline,
                                   "size": size, "text": text, "line_id": line.id,
                                   "slot": slot[0], "index": slot[1]})
            drawn += 1

    for page_number, page_shape in shapes.items():
        page_shape.commit(overlay=True)

    if not drawn:
        raise ValueError("Nothing was placed - every line was left empty.")
    return doc.tobytes(garbage=4, deflate=True)


def page_image(pdf_bytes: bytes, page_number: int, zoom: float = 1.6) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pixmap = doc[page_number].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pixmap.tobytes("png")
