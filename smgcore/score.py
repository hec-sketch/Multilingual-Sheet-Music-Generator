"""Parse an engraved vocal score into staves, systems, voices, sections and lyric anchors."""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import pymupdf as fitz

from .textutil import is_wordlike, looks_like_lyric, normalize_spacing

SECTION_PATTERN = re.compile(
    r"\b(pre[\s\-]?chorus|chorus|verse|bridge|intro(?:duction)?|outro|refrain|tag|coda|ending|interlude|vamp)\b"
    r"\s*([0-9]+|[ivx]+)?",
    re.I,
)

# Left-margin abbreviations used by engravers, mapped to a canonical family name.
LABEL_EXPANSIONS = [
    (re.compile(r"^m\.?$", re.I), "Male"),
    (re.compile(r"^f\.?$", re.I), "Female"),
    (re.compile(r"^harm\.?$", re.I), "Harmony"),
    (re.compile(r"^ld\.?$", re.I), "Lead"),
    (re.compile(r"^sop\.?$", re.I), "Soprano"),
    (re.compile(r"^alt\.?$", re.I), "Alto"),
    (re.compile(r"^ten\.?$", re.I), "Tenor"),
    (re.compile(r"^bar\.?$", re.I), "Baritone"),
    (re.compile(r"^bs\.?$", re.I), "Bass"),
    (re.compile(r"^acc\.?$", re.I), "Accompaniment"),
    (re.compile(r"^pno\.?$", re.I), "Piano"),
]


@dataclass
class Staff:
    page: int
    index: int
    top: float
    bottom: float
    x0: float
    x1: float
    system: int = -1
    label_raw: str = ""
    voice: str = ""


@dataclass
class Anchor:
    """One syllable slot: a place where a lyric syllable sits under a note."""

    page: int
    staff: int
    system: int
    voice: str
    x0: float
    x1: float
    y: float
    text: str
    line_id: int = -1
    section: str = ""
    # The x of the notehead this syllable was actually engraved against, when one
    # could be matched. A printed syllable's own bounding box is not always
    # centred on its note - italics, kerning and hyphens all shift it - so this is
    # the more accurate reference point for placing a translated syllable.
    note_x: float | None = None

    @property
    def centre(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def placement_x(self) -> float:
        """Where a translated syllable should be centred.

        The note it was engraved against when one was found, otherwise the middle
        of its own printed text (the only reference available for that syllable).
        """
        return self.note_x if self.note_x is not None else self.centre


@dataclass
class Notehead:
    """One note in the engraving, whether or not a syllable is printed under it."""

    page: int
    staff: int
    x: float
    y: float


@dataclass
class ScoreLine:
    """A contiguous run of syllables for one voice on one staff."""

    id: int
    page: int
    system: int
    staff: int
    voice: str
    section: str
    anchors: list[Anchor] = field(default_factory=list)
    # Notes inside this line's span that carry no printed syllable. A syllable is
    # held across them in English; a translation may need to sing a new syllable
    # on one. Each entry is (index of the anchor it follows, x of the note).
    held_notes: list[tuple] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(a.text for a in self.anchors)

    @property
    def note_count(self) -> int:
        return len(self.anchors)

    @property
    def y(self) -> float:
        return self.anchors[0].y if self.anchors else 0.0

    def held_after(self, index: int) -> list[float]:
        """The x positions of any held notes sitting after anchor `index`."""
        return [x for after, x in self.held_notes if after == index]


@dataclass
class ScoreDoc:
    page_count: int
    staves: list[Staff]
    anchors: list[Anchor]
    lines: list[ScoreLine]
    sections: list[tuple]  # (page, system, x, name)
    voices: list[str]
    lyric_font: tuple | None
    warnings: list[str] = field(default_factory=list)

    def sung_words(self) -> set:
        """Every syllable printed in the score, for telling English text from a translation."""
        from .textutil import fold

        words = {fold(anchor.text).strip("-") for anchor in self.anchors}
        words.discard("")
        return words

    def lines_by_voice(self) -> dict[str, list[ScoreLine]]:
        out: dict[str, list[ScoreLine]] = defaultdict(list)
        for line in self.lines:
            out[line.voice].append(line)
        return dict(out)


# --------------------------------------------------------------------------- staves


def _horizontal_rules(page) -> dict[float, float]:
    """Return {y: total_width} for thin horizontal rules (staff lines)."""
    rules: dict[float, float] = defaultdict(float)
    width_limit = page.rect.width * 0.25
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                a, b = item[1], item[2]
                if abs(a.y - b.y) < 0.4 and abs(a.x - b.x) > width_limit:
                    rules[round((a.y + b.y) / 2, 1)] += abs(a.x - b.x)
            elif item[0] == "re":
                rect = item[1]
                if rect.height < 1.2 and rect.width > width_limit:
                    rules[round(rect.y0 + rect.height / 2, 1)] += rect.width
    return dict(rules)


def _rule_extents(page) -> dict[float, tuple]:
    extents: dict[float, list] = defaultdict(lambda: [1e9, -1e9])
    width_limit = page.rect.width * 0.25
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                a, b = item[1], item[2]
                if abs(a.y - b.y) < 0.4 and abs(a.x - b.x) > width_limit:
                    key = round((a.y + b.y) / 2, 1)
                    extents[key][0] = min(extents[key][0], a.x, b.x)
                    extents[key][1] = max(extents[key][1], a.x, b.x)
            elif item[0] == "re":
                rect = item[1]
                if rect.height < 1.2 and rect.width > width_limit:
                    key = round(rect.y0 + rect.height / 2, 1)
                    extents[key][0] = min(extents[key][0], rect.x0)
                    extents[key][1] = max(extents[key][1], rect.x1)
    return {k: tuple(v) for k, v in extents.items()}


def detect_staves(page, page_number: int) -> list[Staff]:
    """Find 5-line staves by clustering evenly spaced horizontal rules."""
    extents = _rule_extents(page)
    ys = sorted(extents)
    if not ys:
        return []

    # Typical staff line spacing on this page.
    gaps = [b - a for a, b in zip(ys, ys[1:]) if 0 < b - a < 20]
    spacing = statistics.median(gaps) if gaps else 4.2
    tolerance = max(1.5, spacing * 0.55)

    clusters: list[list[float]] = []
    current = [ys[0]]
    for y in ys[1:]:
        if y - current[-1] <= spacing + tolerance:
            current.append(y)
        else:
            clusters.append(current)
            current = [y]
    clusters.append(current)

    staves: list[Staff] = []
    for cluster in clusters:
        # Split oversized clusters into consecutive groups of five.
        for start in range(0, len(cluster) - 4, 5):
            group = cluster[start : start + 5]
            if len(group) < 5:
                continue
            x0 = min(extents[y][0] for y in group)
            x1 = max(extents[y][1] for y in group)
            staves.append(
                Staff(page=page_number, index=len(staves), top=group[0], bottom=group[-1], x0=x0, x1=x1)
            )
    staves.sort(key=lambda s: s.top)
    for i, staff in enumerate(staves):
        staff.index = i
    return staves


def _left_edge_verticals(page, staff_left: float, min_height: float) -> list[tuple]:
    """Vertical rules hugging the left edge of the staves: system brackets and braces."""
    spans: list[tuple] = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                a, b = item[1], item[2]
                if abs(a.x - b.x) < 0.8 and abs(a.y - b.y) >= min_height:
                    x = (a.x + b.x) / 2
                    if x <= staff_left + 8:
                        spans.append((min(a.y, b.y), max(a.y, b.y)))
            elif item[0] == "re":
                rect = item[1]
                if rect.width < 2.0 and rect.height >= min_height and rect.x0 <= staff_left + 8:
                    spans.append((rect.y0, rect.y1))
    spans.sort()
    merged: list[list[float]] = []
    for top, bottom in spans:
        if merged and top <= merged[-1][1] + 2:
            merged[-1][1] = max(merged[-1][1], bottom)
        else:
            merged.append([top, bottom])
    return [tuple(m) for m in merged]


def group_systems(page, staves: list[Staff]) -> None:
    """Assign a system number to each staff.

    A multi-staff system is delimited by the bracket/brace drawn down its left edge.
    A staff not covered by any bracket is a single-staff system in its own right.
    """
    if not staves:
        return
    staff_left = min(s.x0 for s in staves)
    heights = [s.bottom - s.top for s in staves]
    typical = statistics.median(heights) if heights else 16.0
    brackets = _left_edge_verticals(page, staff_left, typical * 1.4)

    buckets: dict[int, list[Staff]] = defaultdict(list)
    loose: list[Staff] = []
    for staff in staves:
        centre = (staff.top + staff.bottom) / 2
        for index, (top, bottom) in enumerate(brackets):
            if top - 3 <= centre <= bottom + 3:
                buckets[index].append(staff)
                break
        else:
            loose.append(staff)

    groups: list[list[Staff]] = [g for g in buckets.values() if g]
    groups.extend([[staff] for staff in loose])
    if not groups:
        groups = [[staff] for staff in staves]
    groups.sort(key=lambda g: min(s.top for s in g))
    for index, group in enumerate(groups):
        for staff in group:
            staff.system = index


# --------------------------------------------------------------------------- labels


def _canonical_label(raw: str) -> str:
    tokens = [t for t in re.split(r"[\s]+", raw.strip()) if t]
    expanded: list[str] = []
    for token in tokens:
        replaced = token
        for pattern, full in LABEL_EXPANSIONS:
            if pattern.match(token):
                replaced = full
                break
        expanded.append(replaced)
    text = " ".join(expanded)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip().title()


def read_staff_labels(page, staves: list[Staff]) -> None:
    """Read the instrument/voice name printed in the left margin of each staff.

    Each staff's label is measured against that staff's own left edge, not
    against one margin taken across the page. Engravers indent the first system
    of a score to leave room for the voice names written out in full, so its
    staves start further right than every system below them - in More Than
    Sparrows by about fifty points. A single page-wide margin is the narrowest
    of those, which puts the first system's own labels to the *right* of it and
    throws them away: that staff came through unnamed, was called `Voice 1`, and
    the first line of the song went to a voice of its own. Every later line of
    the real voice then sat one line further down the page than the singer's
    words, which is visible in Step 4 as a score that starts on the second line.
    """
    if not staves:
        return
    words = page.get_text("words")
    for staff in staves:
        band_top = staff.top - (staff.bottom - staff.top) * 1.1
        band_bottom = staff.bottom + (staff.bottom - staff.top) * 1.1
        picked = [
            w for w in words
            if w[2] < staff.x0 - 1 and band_top <= (w[1] + w[3]) / 2 <= band_bottom
        ]
        picked.sort(key=lambda w: (round(w[1], 1), w[0]))
        staff.label_raw = normalize_spacing(" ".join(w[4] for w in picked))


def assign_voices(all_staves: list[Staff], warnings: list[str]) -> list[str]:
    """Give every staff a stable voice identity, disambiguating repeats by order."""
    by_system: dict[tuple, list[Staff]] = defaultdict(list)
    for staff in all_staves:
        by_system[(staff.page, staff.system)].append(staff)

    for key in sorted(by_system):
        group = sorted(by_system[key], key=lambda s: s.top)
        seen: Counter = Counter()
        for staff in group:
            base = _canonical_label(staff.label_raw)
            explicit = re.search(r"(\d+)\s*$", base)
            stem = re.sub(r"\s*\d+\s*$", "", base).strip()
            if not stem:
                stem = "Voice"
            seen[stem] += 1
            number = int(explicit.group(1)) if explicit else seen[stem]
            staff.voice = f"{stem} {number}".strip()

    counts = Counter(s.voice for s in all_staves)
    ordered = [v for v, _ in counts.most_common()]
    # Keep a musically sensible order: leads first, then harmonies, then the rest.
    def sort_key(name: str):
        lowered = name.lower()
        rank = 0 if "lead" in lowered else (1 if "harm" in lowered else 2)
        digits = re.findall(r"\d+", name)
        return (rank, name.split()[0] if name else "", int(digits[-1]) if digits else 0)

    ordered.sort(key=sort_key)
    if any(s.voice.startswith("Voice") for s in all_staves):
        warnings.append(
            "Some staves had no readable name in the left margin and were labelled 'Voice N'. "
            "Rename them on the Voices step if needed."
        )
    return ordered


# --------------------------------------------------------------------------- lyrics


def _spans(page):
    for block in page.get_text("dict")["blocks"]:
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip():
                    yield span


def detect_lyric_font(doc, staves_by_page: dict[int, list[Staff]]) -> tuple | None:
    """The lyric font is the text style that dominates the strips just below staves."""
    tally: Counter = Counter()
    for page_number, page in enumerate(doc):
        staves = staves_by_page.get(page_number, [])
        if not staves:
            continue
        bands = _lyric_bands(staves, page.rect.height)
        for span in _spans(page):
            mid_y = (span["bbox"][1] + span["bbox"][3]) / 2
            for staff, (top, bottom) in zip(staves, bands):
                if top <= mid_y <= bottom and staff.x0 - 6 <= span["bbox"][0] <= staff.x1 + 6:
                    if looks_like_lyric(span["text"]):
                        tally[(span["font"], round(span["size"], 1))] += 1
                    break
    if not tally:
        return None
    return tally.most_common(1)[0][0]


def _lyric_bands(staves: list[Staff], page_height: float) -> list[tuple]:
    """Vertical strip under each staff where its lyrics live."""
    bands = []
    for i, staff in enumerate(staves):
        top = staff.bottom + 0.5
        if i + 1 < len(staves) and staves[i + 1].system == staff.system:
            bottom = staves[i + 1].top - 1.0
        elif i + 1 < len(staves):
            bottom = min(staff.bottom + (staff.bottom - staff.top) * 2.6, staves[i + 1].top - 1.0)
        else:
            bottom = min(staff.bottom + (staff.bottom - staff.top) * 2.6, page_height)
        bands.append((top, max(bottom, top)))
    return bands


_SPAN_SPLIT = re.compile(r"(?<=[–—―])(?=[^\W\d_])")


def _split_span(text: str) -> list[str]:
    """One span can carry more than one note's worth of text.

    Engravers sometimes set 'down—this' as a single run because the dash closes
    one phrase and the next syllable starts immediately. Split on whitespace, and
    also after a long dash that is followed by a letter.
    """
    pieces: list[str] = []
    for chunk in text.split():
        for piece in _SPAN_SPLIT.split(chunk):
            if not piece:
                continue
            if not is_wordlike(piece):
                # A hyphen standing on its own belongs to the syllable before it.
                if pieces:
                    pieces[-1] += piece
                continue
            pieces.append(piece)
    return pieces


def extract_anchors(page, page_number: int, staves: list[Staff], lyric_font: tuple | None) -> list[Anchor]:
    anchors: list[Anchor] = []
    if not staves:
        return anchors
    bands = _lyric_bands(staves, page.rect.height)
    for span in _spans(page):
        key = (span["font"], round(span["size"], 1))
        if lyric_font and key != lyric_font:
            continue
        text = span["text"].strip()
        if not looks_like_lyric(text):
            continue
        x0, y0, x1, y1 = span["bbox"]
        mid_y = (y0 + y1) / 2
        for staff, (top, bottom) in zip(staves, bands):
            if top <= mid_y <= bottom and staff.x0 - 8 <= x0 <= staff.x1 + 8:
                pieces = _split_span(text)
                if not pieces:
                    break
                if len(pieces) == 1:
                    text = pieces[0]
                    anchors.append(
                        Anchor(page_number, staff.index, staff.system, staff.voice, x0, x1, y0, text)
                    )
                else:
                    # Rare: engraver merged several syllables into one span.
                    total = sum(len(p) for p in pieces)
                    cursor = x0
                    for piece in pieces:
                        width = (x1 - x0) * len(piece) / max(1, total)
                        anchors.append(
                            Anchor(
                                page_number, staff.index, staff.system, staff.voice,
                                cursor, cursor + width, y0, piece,
                            )
                        )
                        cursor += width
                break
    anchors.sort(key=lambda a: (a.staff, a.x0))

    # An engraver's file sometimes draws the same syllable twice in exactly the
    # same place - one sits invisibly on top of the other. On the page it is one
    # syllable on one note, so reading it twice would invent a note that can
    # never be filled. A syllable genuinely sung twice is engraved twice, in two
    # different places, so an exact repeat of text and position is the overprint.
    unique: list[Anchor] = []
    seen: set[tuple] = set()
    for anchor in anchors:
        key = (anchor.staff, round(anchor.x0, 2), round(anchor.y, 2), anchor.text)
        if key in seen:
            continue
        seen.add(key)
        unique.append(anchor)
    return unique


# --------------------------------------------------------------------------- sections


def find_sections(doc, staves_by_page: dict[int, list[Staff]]) -> list[tuple]:
    """Locate section markers and place them on the musical timeline."""
    found: list[tuple] = []
    for page_number, page in enumerate(doc):
        staves = staves_by_page.get(page_number, [])
        if not staves:
            continue
        systems: dict[int, list[Staff]] = defaultdict(list)
        for staff in staves:
            systems[staff.system].append(staff)
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                text = normalize_spacing(" ".join(s["text"] for s in line["spans"]))
                match = SECTION_PATTERN.search(text)
                if not match or len(text) > 40:
                    continue
                x0, y0 = line["bbox"][0], line["bbox"][1]
                # The marker belongs to the system it sits above.
                best = None
                for system, group in systems.items():
                    top = min(s.top for s in group)
                    bottom = max(s.bottom for s in group)
                    if top - 45 <= y0 <= bottom:
                        best = system
                        break
                if best is None:
                    continue
                name = normalize_spacing(match.group(0)).title()
                found.append((page_number, best, x0, name))
    found.sort(key=lambda item: (item[0], item[1], item[2]))
    return found


def apply_sections(anchors: list[Anchor], sections: list[tuple]) -> None:
    if not sections:
        return
    for anchor in anchors:
        position = (anchor.page, anchor.system, anchor.x0)
        current = ""
        for page_number, system, x, name in sections:
            if (page_number, system, x) <= position:
                current = name
            else:
                break
        anchor.section = current


# --------------------------------------------------------------------------- lines


# Noteheads in the Opus music fonts these scores are engraved with. The glyph's
# *origin* is the notehead's real position; its bounding box is a fixed em box
# three times the height of a staff and says nothing about where the note sits.
NOTEHEAD_GLYPHS = set("œ˙w")
CHORD_TOLERANCE = 2.5  # notes this close in x are one chord, i.e. one moment


def extract_noteheads(page, page_number: int, staves: list[Staff]) -> list[Notehead]:
    """Every note on the page, assigned to the staff it is closest to."""
    if not staves:
        return []
    found: list[Notehead] = []
    for block in page.get_text("rawdict")["blocks"]:
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            for span in line["spans"]:
                if not span["font"].startswith("Opus"):
                    continue
                for char in span["chars"]:
                    if char["c"] not in NOTEHEAD_GLYPHS:
                        continue
                    x, y = char["origin"]
                    staff = min(staves, key=lambda s: abs(y - (s.top + s.bottom) / 2))
                    height = max(staff.bottom - staff.top, 1.0)
                    # Ledger lines put notes outside the staff, but not this far.
                    if not (staff.top - height <= y <= staff.bottom + height):
                        continue
                    found.append(Notehead(page_number, staff.index, x, y))

    # A chord is several noteheads at one moment; the line only needs the moment.
    found.sort(key=lambda n: (n.staff, n.x))
    unique: list[Notehead] = []
    for note in found:
        if unique and unique[-1].staff == note.staff and note.x - unique[-1].x < CHORD_TOLERANCE:
            continue
        unique.append(note)
    return unique


def attach_note_positions(lines: list[ScoreLine], noteheads: list[Notehead]) -> None:
    """Match each anchor to the notehead it was actually engraved against, and
    record, for each line, any notes inside it that carry no printed syllable.

    A syllable sitting under a note is engraved at roughly that note's x. Any note
    between the first and last syllable of a line with nothing under it is one the
    English holds a vowel across — and is exactly where a translation with more
    syllables than the English needs to put one.
    """
    by_staff: dict[tuple, list[Notehead]] = defaultdict(list)
    for note in noteheads:
        by_staff[(note.page, note.staff)].append(note)

    for line in lines:
        if len(line.anchors) < 1:
            continue
        first, last = line.anchors[0], line.anchors[-1]
        notes = sorted(
            (
                note
                for note in by_staff.get((line.page, line.staff), [])
                if first.x0 - 10 <= note.x <= last.x1 + 4
            ),
            key=lambda n: n.x,
        )
        # Fewer detected noteheads than syllables means some notes were missed by
        # the glyph scan (a font quirk, an unusual chord shape); there is nothing
        # reliable to match against, so leave every anchor's own text box as the
        # placement reference for this line.
        if len(notes) < len(line.anchors):
            continue

        # Anchors and notes both run left to right and every anchor has a note, so
        # give each anchor the nearest note that is still free. A syllable is
        # centred under its note, except where it is held and an extension line
        # follows, when it is set flush left — so neither edge alone identifies the
        # note, but the order does. Whatever is left over is a held note.
        claimed: list[int] = []
        cursor = 0
        for anchor in line.anchors:
            available = range(cursor, len(notes) - (len(line.anchors) - len(claimed) - 1))
            if not available:
                break
            pick = min(available, key=lambda k: min(abs(notes[k].x - anchor.x0),
                                                    abs(notes[k].x - anchor.centre)))
            claimed.append(pick)
            cursor = pick + 1
        if len(claimed) != len(line.anchors):
            continue

        for anchor, pick in zip(line.anchors, claimed):
            anchor.note_x = notes[pick].x

        held: list[tuple] = []
        for index, pick in enumerate(claimed):
            start = claimed[index - 1] + 1 if index else 0
            for k in range(start, pick):
                if index:
                    held.append((index - 1, notes[k].x))
        for k in range(claimed[-1] + 1, len(notes)):
            held.append((len(line.anchors) - 1, notes[k].x))
        line.held_notes = held


def build_lines(anchors: list[Anchor]) -> list[ScoreLine]:
    """Group each staff's anchors into one score line per staff occurrence."""
    grouped: dict[tuple, list[Anchor]] = defaultdict(list)
    for anchor in anchors:
        grouped[(anchor.page, anchor.staff)].append(anchor)

    lines: list[ScoreLine] = []
    for key in sorted(grouped):
        page_number, staff_index = key
        items = sorted(grouped[key], key=lambda a: a.x0)
        # A section marker can land part-way along a staff, so split there.
        chunk: list[Anchor] = []
        for anchor in items:
            if chunk and anchor.section != chunk[-1].section:
                lines.append(_make_line(len(lines), page_number, staff_index, chunk))
                chunk = []
            chunk.append(anchor)
        if chunk:
            lines.append(_make_line(len(lines), page_number, staff_index, chunk))
    return lines


def _make_line(line_id: int, page_number: int, staff_index: int, items: list[Anchor]) -> ScoreLine:
    line = ScoreLine(
        id=line_id,
        page=page_number,
        system=items[0].system,
        staff=staff_index,
        voice=items[0].voice,
        section=items[0].section,
        anchors=list(items),
    )
    for anchor in items:
        anchor.line_id = line_id
    return line


# --------------------------------------------------------------------------- entry


def parse_score(pdf_bytes: bytes) -> ScoreDoc:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    warnings: list[str] = []

    staves_by_page: dict[int, list[Staff]] = {}
    for page_number, page in enumerate(doc):
        staves = detect_staves(page, page_number)
        group_systems(page, staves)
        read_staff_labels(page, staves)
        staves_by_page[page_number] = staves

    all_staves = [s for page_number in sorted(staves_by_page) for s in staves_by_page[page_number]]
    if not all_staves:
        raise ValueError(
            "No musical staves were found in this PDF. It may be a scanned image rather than "
            "an engraved score - this app needs a PDF with real text and vector staff lines."
        )

    voices = assign_voices(all_staves, warnings)
    lyric_font = detect_lyric_font(doc, staves_by_page)

    anchors: list[Anchor] = []
    for page_number, page in enumerate(doc):
        anchors.extend(extract_anchors(page, page_number, staves_by_page[page_number], lyric_font))

    if not anchors:
        raise ValueError(
            "No lyrics were found under the staves. File 1 must be the engraving that still has "
            "the English lyrics set under the notes."
        )

    sections = find_sections(doc, staves_by_page)
    apply_sections(anchors, sections)
    anchors.sort(key=lambda a: (a.page, a.system, a.staff, a.x0))
    lines = build_lines(anchors)

    noteheads: list[Notehead] = []
    for page_number, page in enumerate(doc):
        noteheads.extend(extract_noteheads(page, page_number, staves_by_page[page_number]))
    attach_note_positions(lines, noteheads)

    if not sections:
        warnings.append(
            "No section markers (Chorus 1, Verse 1, ...) were found in the score, so lines will be "
            "matched by order and syllable count alone."
        )

    return ScoreDoc(
        page_count=len(doc),
        staves=all_staves,
        anchors=anchors,
        lines=lines,
        sections=sections,
        voices=voices,
        lyric_font=lyric_font,
        warnings=warnings,
    )
