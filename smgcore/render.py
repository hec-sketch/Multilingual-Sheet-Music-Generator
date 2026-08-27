"""Draw the matched syllables onto the score that has no lyrics."""

from __future__ import annotations

import os
import unicodedata
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

# Any font file dropped into the app's 'fonts' folder joins the chain below, so
# a script none of the bundled fonts covers - Thai, Devanagari - is a matter of
# putting a .ttf there rather than of changing this file.
FONT_SUFFIXES = (".ttf", ".otf", ".ttc")

# PyMuPDF carries Droid Sans Fallback inside itself. That is where Chinese,
# Japanese and Korean come from, so nothing has to be installed for them.
BUILTIN_CJK = "cjk"


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


def _needs_glyph(ch: str) -> bool:
    """Whether a character has to be drawn, rather than merely spacing or steering."""
    if ch.isspace():
        return False
    # Zero-width joiners, direction marks and the like steer the shaping of the
    # characters around them and are never drawn on their own; a font is not
    # unfit for a syllable because it has no picture of one.
    return unicodedata.category(ch) not in ("Cf", "Cc")


def _covers(font, text: str) -> bool:
    return all(font.has_glyph(ord(ch)) for ch in text if _needs_glyph(ch))


def compose(text: str) -> str:
    """Write accented letters as the single character the font has a picture of.

    A translator's `a` + combining tilde and the single letter `ã` are the same
    letter, but only the second is one glyph. Drawing the first puts the tilde
    wherever the pen happens to be, because nothing here shapes text - so the
    composed spelling is used everywhere, for measuring and for drawing alike.
    """
    return unicodedata.normalize("NFC", text)


def _clusters(text: str) -> list[tuple[str, str]]:
    """Split into (letter, the marks written on it)."""
    out: list[list[str]] = []
    for ch in text:
        if unicodedata.combining(ch) and out:
            out[-1][1] += ch
        else:
            out.append([ch, ""])
    return [(base, marks) for base, marks in out]


@dataclass
class _Face:
    name: str          # what the font is registered as on a page
    font: object       # fitz.Font, for measuring
    source: dict       # how to register it: {"fontfile": ...} or {"fontbuffer": ...}


class FontChain:
    """The chosen font, plus the others needed for scripts it cannot draw.

    A text font covers the alphabet it was cut for and nothing else. Liberation
    Serif has no Chinese, no Korean, no Arabic; asked for one it hands back the
    .notdef glyph, and PyMuPDF draws that as a nul character - so a Chinese score
    came out with every one of its syllables silently missing, the notes bare and
    nothing on the page to say why.

    So the font is settled per syllable rather than once for the document: the
    first font in the chain that can draw every character of that syllable. The
    chosen font stays at the head of the chain, so a score in a Latin-alphabet
    language is set in exactly the font it always was, and a syllable reaches a
    fallback only when its own font genuinely cannot draw it.

    Measuring has to use the same font as drawing. A Chinese character is a full
    em wide where a Latin letter is a third of one, and the type size for the
    whole score is worked out from these widths - so measuring Chinese with a
    Latin font does not merely mis-space one syllable, it mis-sizes the score.
    """

    def __init__(self, choice: str):
        self.faces: list[_Face] = []
        self.missing: set[str] = set()
        self._chosen: dict[str, _Face] = {}
        self._registered: set[tuple[int, str]] = set()
        self._ink: dict[tuple[str, str], float] = {}
        seen: set[str] = set()

        def add_file(path: str) -> None:
            path = os.path.abspath(path)
            if path in seen or not os.path.exists(path):
                return
            seen.add(path)
            try:
                font = fitz.Font(fontfile=path)
            except Exception:
                return  # a file in the fonts folder that is not a usable font
            self._add(font, {"fontfile": path})

        add_file(resolve_font_path(choice))
        for filename in BUNDLED_FONTS.values():
            add_file(os.path.join(FONT_DIR, filename))
        if os.path.isdir(FONT_DIR):
            for filename in sorted(os.listdir(FONT_DIR)):
                if filename.lower().endswith(FONT_SUFFIXES):
                    add_file(os.path.join(FONT_DIR, filename))
        for path in SYSTEM_FALLBACKS:
            add_file(path)
        try:
            builtin = fitz.Font(BUILTIN_CJK)
            self._add(builtin, {"fontbuffer": builtin.buffer})
        except Exception:
            pass

        if not self.faces:
            raise FileNotFoundError(
                "No Unicode font was found. Place a .ttf file in the app's 'fonts' folder."
            )

    def _add(self, font, source: dict) -> None:
        # The first face keeps the name the renderer has always used, so a score
        # in a Latin-alphabet language is byte-for-byte the document it was.
        name = "Lyrics" if not self.faces else f"Lyrics{len(self.faces)}"
        self.faces.append(_Face(name, font, source))

    def face_for(self, text: str) -> _Face:
        """The first font that can draw this syllable whole.

        Where no font can, the one that draws the most of it is used and the
        characters nothing has are remembered, so that the app can say which
        script needs a font rather than printing gaps and leaving it at that.
        """
        text = compose(text)
        face = self._chosen.get(text)
        if face is not None:
            return face
        for candidate in self.faces:
            if _covers(candidate.font, text):
                self._chosen[text] = candidate
                return candidate
        wanted = [ch for ch in text if _needs_glyph(ch)]
        face = max(
            self.faces,
            key=lambda f: sum(1 for ch in wanted if f.font.has_glyph(ord(ch))),
        )
        self.missing.update(
            ch for ch in wanted
            if not any(f.font.has_glyph(ord(ch)) for f in self.faces)
        )
        self._chosen[text] = face
        return face

    def width(self, text: str, size: float) -> float:
        return self.face_for(text).font.text_length(compose(text), fontsize=size)

    def ink_centre(self, face: _Face, ch: str) -> float:
        """Where a glyph's ink sits, measured from the pen position, per unit size.

        PyMuPDF reports one font-wide bounding box for every glyph, so the only
        way to learn where a mark actually falls is to draw it and look. Measured
        once per glyph and remembered; only a syllable carrying a combining mark
        ever asks.
        """
        key = (face.name, ch)
        if key in self._ink:
            return self._ink[key]
        size, pen, baseline = 100.0, 100.0, 130.0
        probe = fitz.open()
        page = probe.new_page(width=300, height=170)
        page.insert_font(fontname="probe", **face.source)
        page.insert_text((pen, baseline), ch, fontname="probe", fontsize=size)
        pix = page.get_pixmap(colorspace=fitz.csGRAY)
        data, wide = pix.samples, pix.width
        left = right = None
        for row in range(pix.height):
            line = data[row * wide:(row + 1) * wide]
            for column, value in enumerate(line):
                if value < 250:
                    if left is None or column < left:
                        left = column
                    if right is None or column > right:
                        right = column
        centre = 0.0 if left is None else ((left + right + 1) / 2 - pen) / size
        self._ink[key] = centre
        return centre

    def draw(self, shape, page, point, text: str, size: float, colour) -> None:
        """Draw a syllable, putting any combining mark over the letter it belongs to.

        Nothing here shapes text: a combining mark is a zero-width glyph drawn
        wherever the pen has reached, which for a wide letter such as `ʉ` lands it
        over the *next* letter - `mʉ̃a` printed as `mʉã`. Composed spellings are
        used wherever Unicode has one; for the rest - `ʉ̃` has no single character -
        the letters are drawn as one run and each mark is then placed over the
        middle of its own letter.
        """
        text = compose(text)
        face = self.face_for(text)
        self.register(page, face)
        x, baseline = point
        if not any(unicodedata.combining(ch) for ch in text):
            shape.insert_text((x, baseline), text, fontname=face.name,
                              fontsize=size, color=colour)
            return
        parts = _clusters(text)
        spine = "".join(base for base, _ in parts)
        shape.insert_text((x, baseline), spine, fontname=face.name,
                          fontsize=size, color=colour)
        for index, (base, marks) in enumerate(parts):
            if not marks:
                continue
            pen = x + face.font.text_length(spine[:index], fontsize=size)
            middle = pen + self.ink_centre(face, base) * size
            for mark in marks:
                shape.insert_text(
                    (middle - self.ink_centre(face, mark) * size, baseline),
                    mark, fontname=face.name, fontsize=size, color=colour)

    def register(self, page, face: _Face) -> None:
        """Embed a font in a page, the first time that page draws in it.

        Lazily, because the CJK fallback is several megabytes: a score that never
        needed it must not carry it.
        """
        key = (page.number, face.name)
        if key not in self._registered:
            page.insert_font(fontname=face.name, **face.source)
            self._registered.add(key)

    def used_fallback(self) -> bool:
        """Whether anything was drawn in a font other than the one chosen."""
        return any(name != self.faces[0].name for _, name in self._registered)

    def shortfall(self) -> str:
        """What to tell the user when a script had no font at all, or ''."""
        if not self.missing:
            return ""
        scripts = sorted({unicodedata.name(ch, "").split()[0].title()
                          for ch in self.missing} - {""})
        shown = "".join(sorted(self.missing)[:12])
        return (
            f"No available font can draw {' and '.join(scripts) or 'some'} characters "
            f"({shown}), so those syllables are printed with gaps in them. Put a font "
            "that covers this language into the app's 'fonts' folder and generate again."
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
    nudges=None, marks=None, layout_out=None, warnings_out=None
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

    ``warnings_out``, if given a list, is filled with anything the person
    generating the score has to be told - at present, that a language was set in
    a script no available font can draw.
    """
    doc = fitz.open(stream=blank_bytes, filetype="pdf")
    # One font cannot draw every language, so the syllable chooses the font: see
    # FontChain. Fonts are embedded page by page as they are first used, rather
    # than up front, because the fallback that carries Chinese, Japanese and
    # Korean is several megabytes and most scores never need it.
    chain = FontChain(settings.font_choice)

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
        needed = sum(chain.width(text, settings.max_size) for text in drawn)
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
        width = chain.width(text, size)
        x = centre - width / 2
        if hard_left is not None:
            x = min(max(x, hard_left), max(hard_left, hard_right - width))
        chain.draw(
            shape(page_number),
            doc[page_number],
            (x, baseline),
            text,
            size,
            colour if colour is not None else settings.colour,
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
        widths = [chain.width(seat[1], size) for seat in filled]
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
    if warnings_out is not None:
        shortfall = chain.shortfall()
        if shortfall:
            warnings_out.append(shortfall)
    # The CJK fallback holds tens of thousands of characters and a score uses a
    # few hundred. Subsetting keeps a Chinese score the size of an English one.
    if chain.used_fallback():
        try:
            doc.subset_fonts()
        except Exception:
            pass
    return doc.tobytes(garbage=4, deflate=True)


def page_image(pdf_bytes: bytes, page_number: int, zoom: float = 1.6) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pixmap = doc[page_number].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pixmap.tobytes("png")
