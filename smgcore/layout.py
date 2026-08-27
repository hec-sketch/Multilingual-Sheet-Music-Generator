"""Parse a syllable-layout PDF.

A layout is a grid: one box per note. It is written the same way every time -
the English syllable layout in full, then the same layout again underneath with
the translated words in the same boxes. Two pages of English are followed by two
pages of translation; four by four. ``split_in_half`` cuts it on that fact
rather than reading the words to decide.

Within that shape the documents still vary, and this parser copes with:

* section labels printed in the left margin (``Ch1``, ``1``, ``Pre-Ch 2`` ...)
* per-line tags such as ``Harmonies``
* two-column rows, where the left and right halves form one musical line
* boxes drawn round syllables that share a single note, plus the prose note
  explaining them ("Cantar 2 silabas en una")

The whole document is read before anything is classified, so the body column,
the label margin and the instruction column are measured from the document as a
whole rather than guessed page by page. That matters for template documents
whose later pages are nearly empty.
"""

from __future__ import annotations

import difflib
import re
import statistics
import unicodedata
from dataclasses import dataclass, field

import pymupdf as fitz

from .textutil import DASHES, fold, normalize_spacing

BARE_NUMBER = re.compile(r"^\d+$")

# Words a translator writes to label a musical section - in the margin next to a
# grid row, or on a line of its own (which looks exactly like a lyrics-sheet
# heading). Longer alternatives are tried before their own prefixes ('chorus'
# before 'ch', 'ending' before 'end') so a spelled-out word is not truncated.
_HEADING_WORD = re.compile(
    r"(?:pre[\s\-]*)?(?:chorus|choru|chor|ch|coro)|verse|vers|verso|vs|v|bridge|br|puente|ponte|"
    r"intro|outro|tag|ending|end|coda",
    re.I,
)
_HEADING_ROW = re.compile(
    rf"^\(?(?:{_HEADING_WORD.pattern})[\s,]*(?:\d+[\s,]*)*[:.]?\s*(?:\([^)]*\))?\)?\s*$",
    re.I,
)


def _heading_labels(text: str) -> list[str] | None:
    """The section label(s) a heading (margin or whole-row) stands for.

    A heading naming more than one section - 'Ch2, 3' - is the same written
    block sung at two places in the score, so it stands for both.
    """
    if BARE_NUMBER.match(text):
        return [text]
    if not _HEADING_ROW.match(text):
        return None
    core = text.strip().strip("()")
    word_match = _HEADING_WORD.match(core)
    numbers = re.findall(r"\d+", core.split("(", 1)[0])
    word = word_match.group(0) if word_match else core.strip()
    return [f"{word}{n}" for n in numbers] if numbers else [word]

# Tags that qualify a line rather than being sung.
LINE_TAGS = re.compile(
    r"^(harmonies|harmony|armon[ií]as?|all|todos|lead|solo|tutti|men|women|everyone|"
    r"unison|un[ií]sono|ad\s*libs?)\b",
    re.I,
)

# Harmony-only text is deliberately signalled two ways in the layout PDFs:
# (1) an explicit "(Harmonies)" marker beside the row, and/or
# (2) a distinct yellow/gold cell fill. Neither signal is inside the syllable
# boxes themselves, so it must be captured BEFORE box-first filtering removes the
# surrounding marker. Once captured it becomes line metadata and is never counted
# as a lyric token.
HARMONY_MARKER = re.compile(r"^\(?\s*harmon(?:y|ies)\s*\)?$", re.I)
HARMONY_MARKER_ANYWHERE = re.compile(r"\(?\s*harmon(?:y|ies)\s*\)?", re.I)

# Prose that is an instruction to the singer, not lyrics.
#
# 'Only in ChN' (and its Spanish/other-language equivalents by heading word) is a
# translator's shorthand for "this box is a placeholder - the real words for this
# spot differ by repeat, and this note is here only so the row isn't left looking
# unfinished". It is written directly into a grid box, or into a text-box
# annotation dropped over an otherwise-empty one, so it reads exactly like a
# syllable if nothing filters it out - which is worse than leaving the note
# empty, because it is then sung on every repeat of the line, not just the one
# it names.
_ONLY_IN_HINT = rf"only\s+in\s+(?:{_HEADING_WORD.pattern})\s*\d*"
INSTRUCTION_HINT = re.compile(
    r"cantar|s[ií]laba|acentuad|repeat|repetir|veces|incorrect[oa]|correct[oa]\s*:|"
    rf"p[aá]gina\s*\d|copyright|watch\s*tower|©|^\s*n\.?b\.?\b|^\s*nota\b|^\s*note\b|{_ONLY_IN_HINT}",
    re.I,
)

# The document's own title line, e.g. "jwb-141 - By Faith". A publication code at
# the very start is the reliable signal; the words after it are the song title in
# whatever language, so they cannot be matched on.
TITLE_LINE = re.compile(r"^\s*[a-z]{2,5}\s*-?\s*\d{2,5}\s*-", re.I)


@dataclass
class Token:
    text: str
    x0: float
    x1: float
    semantic_class: str = ""

    @property
    def centre(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class LayoutLine:
    id: int
    page: int
    y: float
    section: str
    tag: str
    tokens: list[Token] = field(default_factory=list)
    source: str = "text"
    kind: str = "lyric"  # lyric | note
    join_groups: list[list[int]] = field(default_factory=list)
    from_annotation: bool = False
    color_class: str = ""

    @property
    def text(self) -> str:
        return " ".join(t.text for t in self.tokens)

    @property
    def token_classes(self) -> list[str]:
        return [getattr(t, "semantic_class", "") for t in self.tokens]

    @property
    def raw_count(self) -> int:
        return len(self.tokens)

    @property
    def note_count(self) -> int:
        """Syllable slots after applying the 'two syllables, one note' boxes."""
        joined = sum(len(g) - 1 for g in self.join_groups)
        return max(0, len(self.tokens) - joined)

    def merged_x(self) -> list[float]:
        """The x position of each box, after joined syllables are merged."""
        if not self.join_groups:
            return [t.x0 for t in self.tokens]
        inside = {i: g for g in self.join_groups for i in g}
        out: list[float] = []
        consumed: set[int] = set()
        for index, token in enumerate(self.tokens):
            if index in consumed:
                continue
            group = inside.get(index)
            if group:
                out.append(self.tokens[group[0]].x0)
                consumed.update(group)
            else:
                out.append(token.x0)
        return out

    def merged_tokens(self) -> list[str]:
        """Token list with boxed groups merged into single note-sized tokens."""
        if not self.join_groups:
            return [t.text for t in self.tokens]
        inside: dict[int, list[int]] = {}
        for group in self.join_groups:
            for index in group:
                inside[index] = group
        out: list[str] = []
        consumed: set[int] = set()
        for index, token in enumerate(self.tokens):
            if index in consumed:
                continue
            group = inside.get(index)
            if group:
                merged = "".join(self.tokens[i].text.rstrip("".join(DASHES)) for i in group[:-1])
                merged += self.tokens[group[-1]].text
                out.append(merged)
                consumed.update(group)
            else:
                out.append(token.text)
        return out


@dataclass
class LayoutDoc:
    lines: list[LayoutLine]
    sections: list[str]
    warnings: list[str] = field(default_factory=list)
    page_count: int = 0
    # A heading naming more than one section ('Ch2, 3') is recorded under its
    # first number; this maps that label to the section(s) it also stands for
    # ('Ch2' -> ['Ch3']), so a section that reuses another's written block
    # rather than repeating a section already covered word for word.
    section_aliases: dict[str, list[str]] = field(default_factory=dict)

    def lyric_lines(self) -> list[LayoutLine]:
        return [line for line in self.lines if line.kind == "lyric"]

    @property
    def annotation_share(self) -> float:
        lyric = self.lyric_lines()
        if not lyric:
            return 0.0
        return sum(1 for line in lyric if line.from_annotation) / len(lyric)


@dataclass
class EditableLine:
    """A layout line as the user sees and edits it: one token per note."""

    id: int
    page: int
    section: str
    tag: str
    tokens: list[str] = field(default_factory=list)
    token_classes: list[str] = field(default_factory=list)
    kind: str = "lyric"
    from_annotation: bool = False
    inferred_join: bool = False
    y: float = 0.0
    color_class: str = ""
    order: int = 0
    # Where each box sits across the page. The layout is a grid of one box per
    # note, and the English and translated documents share that grid, so these
    # are what line a translated box up with the English box above it.
    xs: list[float] = field(default_factory=list)
    # Set when this line is a second or later performance of a block the layout
    # wrote once (a chorus sung three times). Holds the id of the written line.
    repeat_of: int | None = None

    @property
    def text(self) -> str:
        return " ".join(self.tokens)

    @property
    def note_count(self) -> int:
        return len(self.tokens)

    def merged_tokens(self) -> list[str]:
        return list(self.tokens)


def inherit_pair_tags(english_lines, pairs, translated_lines):
    """Copy semantic tags from either side of a paired row onto both sides.

    Harmony markers are sometimes printed only beside the translated row or are
    conveyed only by the yellow cell fill. Pairing is positional, so once a row
    is paired there is no reason to discard that semantic metadata. In particular,
    a harmony-only translated row must not become eligible for a lead voice simply
    because its English counterpart was untagged.
    """
    from dataclasses import replace

    by_id = {line.id: line for line in translated_lines}
    out = []
    pair_by_english = {pair.english_id: pair for pair in pairs if pair.english_id is not None}
    for line in english_lines:
        pair = pair_by_english.get(line.id)
        tag = line.tag
        if not tag and pair and pair.translated_id is not None:
            other = by_id.get(pair.translated_id)
            if other is not None and other.tag:
                tag = other.tag
        out.append(replace(line, tag=tag))
    return out


ENGLISH_ROW = 0.6  # share of a row's syllables that must be in the score for it to be English


def split_in_half(lines, page_count: int, english_vocabulary) -> tuple[list, list]:
    """Cut the layout in half: the English above, the translation below.

    The document is written the same way every time. The English syllable layout
    is set out in full, and then repeated underneath in the translation with the
    same boxes in the same order. Two pages of English are followed by two pages
    of translation; four by four.

    So the halves are taken from the page count and nothing is worked out from
    the words. The nth box of the second half is the nth box of the first.

    The one thing checked is that the halves are the right way round and really
    are two languages - because if they are not, this is not that document, and
    the reading falls back to taking the English from the score instead.

    Returns (english, translated), or two empty lists if this is not a layout in
    two halves.
    """
    if page_count < 2 or page_count % 2:
        return [], []
    half = page_count // 2
    english = [line for line in lines if line.page < half]
    translated = [line for line in lines if line.page >= half]
    if not english or not translated:
        return [], []
    if _english_share(english, english_vocabulary) < ENGLISH_ROW:
        return [], []
    if _english_share(translated, english_vocabulary) >= ENGLISH_ROW:
        return [], []
    return english, translated


def _english_share(lines, english_vocabulary) -> float:
    """How much of these rows is words the score sings."""
    if not english_vocabulary:
        return 0.0
    words = [
        word
        for line in lines
        for word in (fold(token).strip("-") for token in line.tokens)
        if word
    ]
    if not words:
        return 0.0
    return sum(1 for word in words if word in english_vocabulary) / len(words)


# "By faith x3" is a line sung three times, not two words and a third syllable.
REPEAT_MARKER = re.compile(r"^[x×]\s*(\d+)$", re.I)


def expand_repeat_marker(tokens: list[str]) -> list[str]:
    """Write a repeated line out in full, so its syllables cover every note."""
    if len(tokens) < 2:
        return tokens
    match = REPEAT_MARKER.match(tokens[-1].strip())
    if not match:
        return tokens
    times = int(match.group(1))
    if not 2 <= times <= 8:
        return tokens
    return tokens[:-1] * times


def to_editable(doc: LayoutDoc, overrides: dict | None = None) -> list[EditableLine]:
    """Flatten a parsed layout into the editable, note-level view the UI works with.

    Every sung row is kept, whether it was typed on the page or written in a
    comment box over it. There is one way to read the document.
    """
    overrides = overrides or {}
    out: list[EditableLine] = []
    for line in doc.lines:
        if line.kind != "lyric":
            continue
        text = overrides.get(line.id)
        untouched = text is None
        tokens = line.merged_tokens() if untouched else text.split()
        expanded = expand_repeat_marker(tokens)
        xs = line.merged_x() if untouched and expanded is tokens else []
        tokens = expanded
        out.append(
            EditableLine(
                id=line.id,
                page=line.page,
                section=line.section,
                tag=line.tag,
                tokens=tokens,
                token_classes=([getattr(t, "semantic_class", "") for t in line.tokens] if untouched and expanded is tokens else [""] * len(tokens)),
                xs=xs if len(xs) == len(tokens) else [],
                kind=line.kind,
                from_annotation=line.from_annotation,
                inferred_join=line.source == "inferred-join",
                y=line.y,
                color_class=line.color_class,
            )
        )
    out = [line for line in out if line.tokens]
    for index, line in enumerate(out):
        line.order = index
    return out


# --------------------------------------------------------------------------- helpers


# Shapes a translator draws round syllables to mean "sing these on one note".
# Highlights are deliberately not here: a highlight marks words a reviewer is
# commenting on, which is the opposite of joining them.
JOIN_SHAPES = ("Square", "Circle", "Polygon", "Ink")

# Annotation types whose text is typed onto the page itself, and so is part of
# the layout. A sticky note ("Text") or its "Popup" is a remark to the reader
# that happens to carry a page position; splicing it into the row underneath
# corrupts that line's syllables.
PAGE_TEXT_ANNOTS = ("FreeText",)


def _annotations(page):
    """Return (page_text_annots, box_rects, comments) for one page."""
    texts, boxes, comments = [], [], []
    annot = page.first_annot
    while annot:
        kind = annot.type[1]
        content = (annot.info.get("content") or "").strip()
        if kind in JOIN_SHAPES:
            boxes.append(fitz.Rect(annot.rect))
        elif content and kind in PAGE_TEXT_ANNOTS:
            texts.append((fitz.Rect(annot.rect), content))
        elif content:
            comments.append((fitz.Rect(annot.rect), content))
        annot = annot.next
    return texts, boxes, comments


BLANK_BOX = "-"  # a box of the grid with no syllable in it: the note is held


VOWELS = set("aeiouy")


def _is_stray_letter(text: str) -> bool:
    """A lone consonant, left behind by a mis-key, that cannot be sung.

    Every syllable needs something to sound it. A single letter that is a vowel
    is a syllable in plenty of languages and is kept; a bare consonant is not, in
    any of them.

    The letter must be entirely on its own. A consonant carrying a hyphen or a
    full stop is doing a job - the 'K.' closing 'O-K.', the 'j-' opening a word
    the translator broke at the syllable - and only a letter with nothing
    attached to it at all is the slip.

    All of that is an argument about the Latin alphabet, which spells a syllable
    out of separate consonants and vowels. It says nothing about a script that
    does not: a Chinese character, a Korean syllable block and a kana are each a
    whole syllable written as one character, and are the ordinary case rather
    than a slip. Nor could this ever judge Cyrillic or Greek, whose vowels are
    not in the Latin list either and so were being thrown away as consonants.
    """
    if len(text) != 1:
        return False
    letter = unicodedata.normalize("NFKD", text.lower())[0]
    if not ("a" <= letter <= "z"):
        return False
    return letter not in VOWELS


def _grid_boxes_for_row(grid_boxes: list[fitz.Rect], row_y: float) -> list[fitz.Rect]:
    """Return the actual printed cells that belong to one visual row."""
    candidates = []
    for rect in grid_boxes:
        # page text y values are top-of-glyph; a grid cell's center sits about
        # half a cell height below that. Match by center, not by whether text
        # happens to exist inside the cell, because a blank cell still counts.
        if abs(((rect.y0 + rect.y1) * 0.5) - (row_y + rect.height * 0.5)) <= max(4.5, rect.height * 0.55):
            candidates.append(rect)
    return sorted(candidates, key=lambda r: (r.x0, r.y0))


# A band of cells is one printed row. A cell joins the band it overlaps
# vertically; below this share of its own height it is a row of its own.
BAND_OVERLAP = 0.45
# and a band never grows past this much of a single cell, so tightly set rows
# cannot chain into one another.
BAND_MAX_HEIGHT = 1.8


def _cell_bands(grid_boxes: list[fitz.Rect]) -> list[list[fitz.Rect]]:
    """Group the drawn cells into printed rows.

    A row is not a line of text, it is a run of cells across the page - and a
    translator does not always sit every cell of a run on exactly the same line.
    The last cell of a row is sometimes dropped by half a cell, which is plainly
    the same row to a reader and, to anything matching on the text baseline, a
    row of its own holding one syllable.

    Cells are therefore grouped by how much they overlap vertically, not by where
    their text sits. What keeps this from swallowing the row beneath is the cap on
    how tall a band may grow: a band is one cell tall, give or take.
    """
    bands: list[list[fitz.Rect]] = []
    for rect in sorted(grid_boxes, key=lambda r: (r.y0, r.x0)):
        for band in bands:
            top = min(r.y0 for r in band)
            bottom = max(r.y1 for r in band)
            overlap = min(bottom, rect.y1) - max(top, rect.y0)
            grown = max(bottom, rect.y1) - min(top, rect.y0)
            tall = statistics.median([r.height for r in band] + [rect.height])
            if overlap >= BAND_OVERLAP * rect.height and grown <= BAND_MAX_HEIGHT * tall:
                band.append(rect)
                break
        else:
            bands.append([rect])
    return [sorted(band, key=lambda r: r.x0) for band in bands]


def _rows_by_band(rows: list[list[tuple]], bands: list[list[fitz.Rect]]):
    """Fold together the text lines that sit in the same band of cells.

    Returns the rows to read (a row that shared a band with the one before it has
    been folded into it) and, for each, the band of cells it is written in.
    """
    if not bands:
        return list(rows), [None] * len(rows)

    spans = []
    for band in bands:
        top = min(rect.y0 for rect in band)
        bottom = max(rect.y1 for rect in band)
        spans.append((top, bottom, statistics.median([rect.height for rect in band])))

    def band_of(row: list[tuple]) -> int | None:
        row_y = statistics.median([word[0] for word in row])
        best = None
        for index, (top, bottom, tall) in enumerate(spans):
            gap = abs(((top + bottom) * 0.5) - (row_y + tall * 0.5))
            if gap <= max(4.5, tall * 0.75) and (best is None or gap < best[0]):
                best = (gap, index)
        return best[1] if best else None

    out_rows: list[list[tuple]] = []
    out_bands: list[int | None] = []
    for row in rows:
        index = band_of(row)
        if index is not None and out_bands and out_bands[-1] == index:
            out_rows[-1] = out_rows[-1] + list(row)
            continue
        out_rows.append(list(row))
        out_bands.append(index)
    return out_rows, out_bands



def _split_words_across_cells(row: list[tuple], boxes: list[fitz.Rect]) -> list[tuple]:
    """Cut a word that was typed straight through a cell border.

    A translator writing `p'un-` in one box and `chay` in the next leaves no gap
    at the border, so the PDF hands the two back as the single word `p'un-chay`
    with a bounding box straddling both cells. Read as one word it lands wholly
    in whichever cell holds its centre - printing two syllables squeezed onto one
    note and leaving the other cell blank, which the rest of the app then reads
    as a held note and every syllable after it goes a note early. Worse, a word
    centred exactly on the shared border used to be claimed by both cells and
    printed twice.

    The cell borders decide where to cut. Characters are placed evenly across the
    word's own width, which is what a monospaced layout font gives, and the cut
    is moved onto a hyphen when one is next to it - the hyphen belongs to the
    syllable before the break, as the layout wrote it.
    """
    if not row or len(boxes) < 2:
        return row
    edges = sorted({rect.x0 for rect in boxes} | {rect.x1 for rect in boxes})
    out: list[tuple] = []
    for word in row:
        y0, x0, x1, raw = word
        width = x1 - x0
        cuts = [e for e in edges if x0 + 1.0 < e < x1 - 1.0]
        if not cuts or width <= 0 or len(raw) < 2:
            out.append(word)
            continue
        pieces, start_char, start_x = [], 0, x0
        for edge in cuts:
            # Where the border falls in the word, if characters were evenly
            # spaced. That is only a guess - a proportional font spaces them
            # anything but evenly - so it is never trusted on its own.
            guess = max(1, min(len(raw) - 1, round((edge - x0) / width * len(raw))))
            # It is trusted only when it lands on a hyphen, which is the whole
            # case this exists for: a hyphenated syllable written up against the
            # border of the next box. Anything else - a word merely overhanging
            # its cell, a title crossing the grid - is left alone, because a
            # wrong cut invents a syllable and costs a whole row.
            end_char = None
            for candidate in (guess, guess - 1, guess + 1):
                if 0 < candidate < len(raw) and raw[candidate - 1] == "-":
                    end_char = candidate
                    break
            if end_char is None or end_char <= start_char:
                continue
            pieces.append((y0, start_x, edge, raw[start_char:end_char]))
            start_char, start_x = end_char, edge
        if not pieces:
            out.append(word)
            continue
        pieces.append((y0, start_x, x1, raw[start_char:]))
        out.extend([piece for piece in pieces if piece[3]])
    return out


def _grid_tokens_for_row(
    row: list[tuple],
    boxes: list[fitz.Rect],
    color_boxes: dict[tuple[float,float,float,float], str] | None = None,
) -> list[Token]:
    """Create exactly one token per drawn grid box, including blank '-' boxes.

    The rectangles are the source of truth. Text is merely the content inside a
    rectangle. This prevents a blank/held box from disappearing and shifting every
    later syllable one column to the left.
    """
    if not boxes:
        return []

    row = _split_words_across_cells(row, boxes)

    # Each word belongs to exactly one cell. Collecting per cell "every word whose
    # centre is inside me" double-counts a word centred on the border two cells
    # share, which printed the same syllable on two notes running.
    claim: dict[int, list] = {}
    for w in row:
        y0, x0, x1, raw = w
        wcx = (x0 + x1) / 2.0
        wcy = y0 + 5.0
        best, best_gap = None, None
        for index, rect in enumerate(boxes):
            if not (rect.y0 - 1.5 <= wcy <= rect.y1 + 1.5):
                continue
            if not (rect.x0 - 0.5 <= wcx <= rect.x1 + 0.5):
                continue
            gap = abs(wcx - (rect.x0 + rect.x1) / 2.0)
            if best_gap is None or gap < best_gap:
                best, best_gap = index, gap
        if best is not None:
            claim.setdefault(best, []).append(w)

    tokens: list[Token] = []
    for rect_index, rect in enumerate(boxes):
        inside = sorted(claim.get(rect_index, []), key=lambda v: v[1])

        semantic = ""
        if color_boxes:
            semantic = color_boxes.get(tuple(round(v, 1) for v in (rect.x0, rect.y0, rect.x1, rect.y1)), "")

        if not inside:
            tokens.append(Token(BLANK_BOX, rect.x0, rect.x1, semantic))
            continue

        # A normal cell contains one printed syllable. If extraction split it into
        # several fragments, join those fragments without creating extra note slots.
        raw = normalize_spacing(" ".join(w[3] for w in inside)).strip()
        raw = re.sub(r"\s*-\s*", "-", raw)
        if not raw or not any(ch.isalnum() for ch in raw):
            tokens.append(Token(BLANK_BOX, rect.x0, rect.x1, semantic))
            continue

        # A cell can occasionally contain a hyphenated syllable (e.g. "giv-").
        # It is still one note-sized box, so keep it as one token.
        for dash in "−–—‑":
            raw = raw.replace(dash, "-")
        tokens.append(Token(raw, rect.x0, rect.x1, semantic))

    return tokens


def tokenize_words(words: list[tuple], blank_gap: float = 1e9) -> list[Token]:
    """Turn positioned words into syllable tokens, keeping each token's x extent.

    These documents are a grid: one box per note. A dash written hard against the
    syllable before it is that syllable's hyphen ("liv" + "-"). A dash sitting on
    its own, a whole column away from its neighbours, is a *box of its own* — the
    English holds the previous syllable over that note, and the translation may
    well sing a new syllable there. ``blank_gap`` is where one reading stops and
    the other starts, measured from the document's own column spacing.
    """
    dashes = "".join(DASHES)
    tokens: list[Token] = []
    for x0, x1, raw in words:
        text = raw.strip()
        if not text:
            continue
        for dash in dashes[1:]:
            text = text.replace(dash, "-")
        if set(text) <= {"-"}:
            if tokens and (x0 - tokens[-1].x1) >= blank_gap:
                tokens.append(Token(BLANK_BOX, x0, x1))  # its own box: a held note
            elif tokens and not tokens[-1].text.endswith("-"):
                tokens[-1].text += "-"
                tokens[-1].x1 = max(tokens[-1].x1, x1)
            continue
        if not any(ch.isalnum() for ch in text):
            # Punctuation on its own is not a syllable and must not claim a note.
            # An empty comment box drawn over the page leaves a stray '.' behind,
            # which would otherwise be counted as a note the translation has to
            # fill and push every syllable after it one place along.
            continue
        if _is_stray_letter(text):
            # A single consonant with nothing to sound it is a slip of the
            # keyboard, not a syllable. Left in, it takes a note of its own and
            # pushes the real syllable after it off the end of the line - which
            # is exactly what a person setting the score by hand ignores.
            continue
        parts = [p for p in text.split("-") if p]
        if not parts:
            continue
        trailing = text.endswith("-")
        span = max(x1 - x0, 0.1)
        total = sum(len(p) for p in parts)
        cursor = x0
        for index, part in enumerate(parts):
            width = span * len(part) / total
            label = part + "-" if (index < len(parts) - 1 or trailing) else part
            tokens.append(Token(label, cursor, cursor + width))
            cursor += width
    return tokens


def _cluster_rows(items: list[tuple], tolerance: float) -> list[list[tuple]]:
    """Group (y, x0, x1, text) entries into visual rows."""
    if not items:
        return []
    items = sorted(items, key=lambda w: (w[0], w[1]))
    rows: list[list[tuple]] = [[items[0]]]
    for item in items[1:]:
        anchor = statistics.median([r[0] for r in rows[-1]])
        if abs(item[0] - anchor) <= tolerance:
            rows[-1].append(item)
        else:
            rows.append([item])
    for row in rows:
        row.sort(key=lambda w: w[1])
    return rows


# --------------------------------------------------------------------------- entry



# Actual grid-cell borders in these PDFs are vector drawings. Text outside those
# cells (for example ``(Harmonies)``, ``Only in Ch2``, section labels, or review
# notes) is not part of the syllable layout and must never be counted as a note.
# We reconstruct the rectangles from the page's horizontal/vertical border
# segments instead of trying to infer them from the words.
GRID_CELL_W = (18.0, 55.0)
GRID_CELL_H = (9.0, 27.0)
GRID_EDGE_THICKNESS = 2.6
GRID_EDGE_MIN = 5.0
GRID_AXIS_TOL = 1.25
GRID_MERGE_GAP = 2.5
GRID_BOX_TOL = 4.0


def _cluster_edge_segments(segments: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """Cluster nearly-coincident horizontal/vertical edge fragments and merge them."""
    groups: list[list] = []
    for pos, a, b in sorted(segments):
        placed = False
        for group in groups:
            if abs(pos - group[0]) <= GRID_AXIS_TOL:
                group[1].append((a, b))
                placed = True
                break
        if not placed:
            groups.append([pos, [(a, b)]])

    out: list[tuple[float, float, float]] = []
    for pos, intervals in groups:
        intervals.sort()
        merged: list[list[float]] = []
        for a, b in intervals:
            if not merged or a > merged[-1][1] + GRID_MERGE_GAP:
                merged.append([a, b])
            else:
                merged[-1][1] = max(merged[-1][1], b)
        mean_pos = pos
        for a, b in merged:
            out.append((mean_pos, a, b))
    return out


def _grid_boxes(page) -> list[fitz.Rect]:
    """Return the actual syllable-cell rectangles drawn on a layout page."""
    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    boxes: list[fitz.Rect] = []

    for drawing in page.get_drawings():
        rect = drawing["rect"]
        fill = drawing.get("fill")
        # Shaded cells are represented as filled rectangles. Keep only sizes that
        # look like a cell, not a tiny text glyph or a large annotation panel.
        if (
            fill is not None
            and GRID_CELL_W[0] <= rect.width <= GRID_CELL_W[1]
            and GRID_CELL_H[0] <= rect.height <= GRID_CELL_H[1]
            and max(fill) > 0.05
        ):
            boxes.append(fitz.Rect(rect))

        if rect.height <= GRID_EDGE_THICKNESS and rect.width >= GRID_EDGE_MIN:
            horizontal.append((rect.y0, rect.x0, rect.x1))
        if rect.width <= GRID_EDGE_THICKNESS and rect.height >= GRID_EDGE_MIN:
            vertical.append((rect.x0, rect.y0, rect.y1))

    h_edges = _cluster_edge_segments(horizontal)
    v_edges = _cluster_edge_segments(vertical)

    # White cells are outlines rather than filled rectangles. Pair neighboring
    # horizontal boundaries and use the vertical boundaries between them to
    # reconstruct each individual cell.
    for y_top, x0_top, x1_top in h_edges:
        for y_bottom, x0_bottom, x1_bottom in h_edges:
            gap = y_bottom - y_top
            if gap < GRID_CELL_H[0] or gap > GRID_CELL_H[1]:
                continue
            overlap0 = max(x0_top, x0_bottom)
            overlap1 = min(x1_top, x1_bottom)
            if overlap1 - overlap0 < GRID_CELL_W[0]:
                continue

            boundary_xs = [
                x
                for x, y0, y1 in v_edges
                if overlap0 - GRID_BOX_TOL <= x <= overlap1 + GRID_BOX_TOL
                and y0 <= y_top + GRID_BOX_TOL
                and y1 >= y_bottom - GRID_BOX_TOL
            ]
            boundary_xs = sorted(set(round(x, 1) for x in boundary_xs))
            for left, right in zip(boundary_xs, boundary_xs[1:]):
                width = right - left
                if GRID_CELL_W[0] <= width <= GRID_CELL_W[1]:
                    boxes.append(fitz.Rect(left, y_top, right, y_bottom))

    # Filled cells and outline reconstruction can describe the same printed
    # box with slightly different edges (typically ~1 pt). Deduplicate by strong
    # geometric overlap, not exact coordinates.
    unique: list[fitz.Rect] = []
    for rect in sorted(boxes, key=lambda r: (round(r.y0, 1), round(r.x0, 1))):
        duplicate = False
        for other in unique:
            ix0 = max(rect.x0, other.x0)
            iy0 = max(rect.y0, other.y0)
            ix1 = min(rect.x1, other.x1)
            iy1 = min(rect.y1, other.y1)
            inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
            smaller = min(rect.width * rect.height, other.width * other.height)
            if smaller > 0 and inter / smaller >= 0.80:
                duplicate = True
                break
        if not duplicate:
            unique.append(rect)
    return unique


def _is_harmony_fill(fill) -> bool:
    """Whether a cell fill is the yellow/gold harmony convention used in layouts."""
    if fill is None or len(fill) < 3:
        return False
    r, g, b = fill[:3]
    # Current templates use approximately (1.0, .753, 0). Keep this slightly
    # tolerant so exported PDFs with small colour-profile changes still work.
    return r >= 0.85 and 0.50 <= g <= 0.92 and b <= 0.30 and (r - b) >= 0.45


def _harmony_grid_boxes(page) -> list[fitz.Rect]:
    """Return only the grid cells visually marked as harmony-only."""
    boxes: list[fitz.Rect] = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        fill = drawing.get("fill")
        if (
            _is_harmony_fill(fill)
            and GRID_CELL_W[0] <= rect.width <= GRID_CELL_W[1]
            and GRID_CELL_H[0] <= rect.height <= GRID_CELL_H[1]
        ):
            boxes.append(fitz.Rect(rect))
    unique = []
    seen = set()
    for rect in boxes:
        key = tuple(round(v, 1) for v in (rect.x0, rect.y0, rect.x1, rect.y1))
        if key not in seen:
            seen.add(key)
            unique.append(rect)
    return unique


def _semantic_color(fill) -> str:
    if fill is None or len(fill) < 3:
        return ""
    r, g, b = fill[:3]
    if r >= 0.85 and 0.50 <= g <= 0.92 and b <= 0.30 and (r - b) >= 0.45:
        return "harmony"
    if b >= 0.55 and g >= 0.20 and b > r + 0.25:
        return "prechorus"
    if r >= 0.55 and r > g + 0.25 and r > b + 0.25:
        return "chorus"
    return ""


def _grid_box_semantics(page, grid_boxes: list[fitz.Rect]) -> dict[tuple[float,float,float,float], str]:
    """Classify each actual grid cell by the color of its border artwork.

    The templates draw colored outlines as many thin vector fills rather than a
    filled rectangle. Therefore semantic color cannot be recovered by looking for
    a full-size colored cell. We sample the colored border segments around each
    reconstructed cell and take the majority class.
    """
    colored = []
    for drawing in page.get_drawings():
        cls = _semantic_color(drawing.get("fill"))
        if cls:
            colored.append((fitz.Rect(drawing["rect"]), cls))
    out = {}
    for cell in grid_boxes:
        counts = {"harmony": 0, "chorus": 0, "prechorus": 0}
        for dr, cls in colored:
            cx = (dr.x0 + dr.x1) / 2
            cy = (dr.y0 + dr.y1) / 2
            # Horizontal edge segment near top/bottom of cell.
            if dr.width >= max(0.4, dr.height * 1.5) and (
                abs(cy - cell.y0) <= 1.5 or abs(cy - cell.y1) <= 1.5
            ) and cell.x0 - 1.0 <= cx <= cell.x1 + 1.0:
                counts[cls] += max(1, int(min(dr.width, cell.width)))
            # Vertical edge segment near left/right of cell.
            elif dr.height >= max(0.4, dr.width * 1.5) and (
                abs(cx - cell.x0) <= 1.5 or abs(cx - cell.x1) <= 1.5
            ) and cell.y0 - 1.0 <= cy <= cell.y1 + 1.0:
                counts[cls] += max(1, int(min(dr.height, cell.height)))
        best = max(counts, key=counts.get)
        if counts[best] > 0:
            out[tuple(round(v, 1) for v in (cell.x0, cell.y0, cell.x1, cell.y1))] = best
    return out


def _word_in_grid_box(word: tuple, boxes: list[fitz.Rect]) -> bool:
    """Whether a clustered layout word's center falls inside an actual cell."""
    if not boxes:
        return False
    # ``_cluster_rows`` stores (y0, x0, x1, text); the source PDF text height is
    # roughly 12-14pt, so six points below the top is a stable center estimate.
    point = fitz.Point((word[1] + word[2]) / 2.0, word[0] + 6.0)
    return any(box.contains(point) for box in boxes)


def _grid_mode(pages: list[dict]) -> bool:
    """Use box-first parsing only when the PDF really is a drawn box grid."""
    counts = [len(entry.get("grid_boxes", [])) for entry in pages]
    return sum(counts) >= 20 and max(counts, default=0) >= 6


def _suppress_running_headers(lines: list[LayoutLine], heights: dict[int, float], pages: int) -> None:
    """Titles repeated at the top or bottom of every page are not lyrics.

    A running header is by definition text that repeats across pages, so the same
    line must be seen on two or more of them. A single-page layout has no running
    headers at all — its first and last lines are ordinary lyrics and must not be
    thrown away for sitting near the edge of the paper.
    """
    seen: dict[str, list[LayoutLine]] = {}
    for line in lines:
        height = heights.get(line.page, 842.0)
        edge = line.y < height * 0.08 or line.y > height * 0.94
        if not edge:
            continue
        # A legitimate lyric row can begin at the top of a page, especially in
        # the translated half where a new page may start with a section such as
        # Ch1 or Ch2. Those rows can be repeated on later pages, so treating them
        # as running headers deletes real source syllables and shifts every later
        # translation row by one. Running headers have no musical section/tag
        # context; preserve any lyric row that does.
        if line.section or line.tag:
            continue
        key = re.sub(r"\d+", "#", line.text.lower())
        seen.setdefault(key, []).append(line)

    for group in seen.values():
        if len({line.page for line in group}) >= 2:
            for line in group:
                line.kind = "note"


def _drop_stray_fragments(lines: list[LayoutLine]) -> None:
    """Discard scraps that are not a sung line at all.

    The layout is a grid: a sung row carries a whole musical phrase and runs
    most of the way across the page. A couple of loose words in a corner - a
    note to self, the tail of something deleted, a comment box left behind - is
    none of that, and left in it takes a row of its own and pushes the two
    halves of the document out of step with each other.

    Only the unmistakable case is dropped: a scrap of a row, barely any of the
    page wide, holding a word or two with no syllable break in them.
    """
    widths = [
        line.tokens[-1].x1 - line.tokens[0].x0
        for line in lines
        if line.kind == "lyric" and line.tokens
    ]
    if len(widths) < 4:
        return
    full = statistics.median(widths)
    for line in lines:
        if line.kind != "lyric" or not line.tokens:
            continue
        # Short Harmony rows are intentional: e.g. the yellow Bridge row
        # "By faith" / "ta-noujain" is only two boxes long but is real sung
        # material. Never discard a line that the layout explicitly marks as
        # Harmony-only.
        if (getattr(line, "tag", "") or "").lower() == "harmonies" or getattr(line, "color_class", "") == "harmony":
            continue
        width = line.tokens[-1].x1 - line.tokens[0].x0
        hyphenated = any(token.text.rstrip().endswith(tuple(DASHES)) for token in line.tokens)
        if width < 0.25 * full and len(line.tokens) < 3 and not hyphenated:
            line.kind = "note"


def _propagate_join_groups(lines: list[LayoutLine]) -> int:
    """A translator draws the 'two syllables, one note' box once; apply it to every repeat."""
    template: dict[str, list[list[int]]] = {}
    for line in lines:
        if line.kind == "lyric" and line.join_groups:
            key = "|".join(fold(t.text) for t in line.tokens)
            template.setdefault(key, line.join_groups)

    applied = 0
    for line in lines:
        if line.kind != "lyric" or line.join_groups:
            continue
        key = "|".join(fold(t.text) for t in line.tokens)
        groups = template.get(key)
        if groups and all(i < len(line.tokens) for g in groups for i in g):
            line.join_groups = [list(g) for g in groups]
            line.source = "inferred-join"
            applied += 1
    return applied


def _already_on_page(page_words, y: float, x0: float, x1: float, text: str) -> bool:
    """Is this annotation word the page's own copy of a word already read?

    Some documents carry the translation twice: once as page text and once as a
    FreeText annotation laid exactly over it. The whole-row check above catches
    that only when the two read as one continuous string - a '(Harmonies)' label
    clustered into the middle of the row is enough to break it, and every
    syllable then arrives twice, interleaved.

    The reliable signal is geometric: the annotation's copy of a word sits at the
    same height and on top of the page's own, so the two spans overlap almost
    completely. Text that is genuinely only in the annotation overlaps nothing.
    """
    folded = fold(text)
    if not folded:
        return False
    width = max(x1 - x0, 0.1)
    for other_y, other_x0, other_x1, other_text in page_words:
        if abs(other_y - y) > 3.0 or fold(other_text) != folded:
            continue
        overlap = min(x1, other_x1) - max(x0, other_x0)
        if overlap > 0 and overlap >= 0.6 * min(width, max(other_x1 - other_x0, 0.1)):
            return True
    return False


def _page_twin(page_rows, y: float):
    """The page's own drawn copy of the row an annotation sits on, if there is one."""
    best, gap = None, None
    for row in page_rows:
        if not row:
            continue
        distance = abs(min(word[0] for word in row) - y)
        if distance <= 4.0 and (gap is None or distance < gap):
            best, gap = row, distance
    return best


def _annotation_extras(twin, matches):
    """The words an annotation carries that its drawn copy on the page does not.

    A FreeText annotation is stored twice: as the text the translator typed, and
    as the appearance the viewer draws from it. When the box was drawn narrower
    than the text needs, the appearance is *clipped* - the last syllable of the
    row is simply not in the page's copy - so the two are neither identical nor
    is one a substring of the other, and the whole-row test above lets the
    annotation through to be read word by word.

    Word by word is where it went wrong. Positions in the annotation are
    estimated by counting characters across the box, and against a clipped
    appearance that estimate drifts steadily rightward: the first syllables
    still overlap their drawn twins enough to be recognised, and the last ones
    no longer do, so they are added a second time and print twice.

    Reading the row as a sequence settles it. The two lists of words are aligned,
    and only what the annotation genuinely has in addition is kept - here the one
    clipped syllable. It is placed by where the matched words either side of it
    actually landed on the page, so a syllable added this way sits in the cell it
    was typed in rather than at a guess.

    Returns None when the two are not the same row, leaving the caller's
    word-by-word path to deal with it.
    """
    page_words = sorted(twin, key=lambda word: word[1])
    want = [fold(word[3]) for word in page_words]
    have = [fold(word) for word, _, _ in matches]
    blocks = difflib.SequenceMatcher(None, want, have, autojunk=False).get_matching_blocks()
    if not any(block.size for block in blocks):
        return None

    # Where a character of the annotation's text fell on the page, learnt from
    # the words that did match: each pairing gives the two ends of one word.
    anchors: list[tuple[float, float]] = []
    paired: set[int] = set()
    for block in blocks:
        for step in range(block.size):
            page_word = page_words[block.a + step]
            _, start_char, end_char = matches[block.b + step]
            anchors.append((start_char, page_word[1]))
            anchors.append((end_char, page_word[2]))
            paired.add(block.b + step)
    if len(anchors) < 2:
        return None
    anchors.sort()

    def where(char: float) -> float:
        if char <= anchors[0][0]:
            first, second = anchors[0], anchors[1]
        elif char >= anchors[-1][0]:
            first, second = anchors[-2], anchors[-1]
        else:
            first = max((a for a in anchors if a[0] <= char), key=lambda a: a[0])
            second = min((a for a in anchors if a[0] > char), key=lambda a: a[0])
        span = second[0] - first[0]
        if span <= 0:
            return first[1]
        return first[1] + (second[1] - first[1]) * (char - first[0]) / span

    y = min(word[0] for word in page_words)
    extras = []
    for index, (word, start_char, end_char) in enumerate(matches):
        if index in paired or not fold(word):
            continue
        extras.append((round(y, 1), where(start_char), where(end_char), word))
    return extras



def _colored_row_bands(page) -> list[tuple[float, float, str]]:
    """Detect semantic row colors from the layout's actual vector drawings.

    The templates use exact vector colors for the lyric boxes:
      * black/neutral = verse/ordinary material
      * green/blue = pre-chorus
      * red = chorus
      * yellow/gold = harmony-only

    Earlier versions sampled raster pixels. That was unsafe because anti-aliased
    black/gray text contains dark RGB values that accidentally satisfied the loose
    blue/green test, causing ordinary verse and bridge rows to be mislabeled as
    pre-chorus. The vector artwork already contains the semantic colors, so use it
    first and only fall back to a saturation-aware raster scan when necessary.
    """
    def color_class(rgb):
        if not rgb or len(rgb) < 3:
            return ""
        r, g, b = [float(v) for v in rgb[:3]]
        # The source files use approximately these exact fills.
        if r >= 0.85 and 0.50 <= g <= 0.92 and b <= 0.30 and (r - b) >= 0.45:
            return "harmony"
        if r >= 0.55 and g <= 0.20 and b <= 0.20 and (r - g) >= 0.40:
            return "chorus"
        if (g >= 0.35 and r <= 0.25 and b <= 0.25) or (b >= 0.40 and g >= 0.25 and r <= 0.25):
            return "prechorus"
        return ""

    bands: list[tuple[float, float, str]] = []
    try:
        for drawing in page.get_drawings():
            cls = color_class(drawing.get("fill")) or color_class(drawing.get("color"))
            if not cls:
                continue
            rect = drawing["rect"]
            # Semantic box artwork has thin horizontal/vertical strokes. Keep any
            # rect that is plausibly part of a lyric box and merge it below.
            if rect.width < 1.0 and rect.height < 1.0:
                continue
            bands.append((float(rect.y0), float(rect.y1), cls))
    except Exception:
        bands = []

    def merge(raw):
        priority = {"harmony": 3, "chorus": 2, "prechorus": 1}
        out = []
        for a, z, cls in sorted(raw, key=lambda v: (v[0], -priority[v[2]], v[1])):
            if not out or a > out[-1][1] + 3.0 or cls != out[-1][2]:
                out.append([a, z, cls])
            else:
                out[-1][1] = max(out[-1][1], z)
        # Overlapping classes on the same row are resolved by semantic priority.
        resolved = []
        for a, z, cls in out:
            chosen = None
            for i, (oa, oz, ocls) in enumerate(resolved):
                overlap = max(0.0, min(z, oz) - max(a, oa))
                if overlap >= 0.35 * min(max(z - a, 0.1), max(oz - oa, 0.1)):
                    chosen = i
                    if priority[cls] > priority[ocls]:
                        resolved[i] = (min(a, oa), max(z, oz), cls)
                    else:
                        resolved[i] = (min(a, oa), max(z, oz), ocls)
                    break
            if chosen is None:
                resolved.append((a, z, cls))
        return sorted(resolved, key=lambda v: (v[0], -priority[v[2]]))

    if bands:
        return merge(bands)

    # Raster fallback. Saturation is mandatory so gray/black antialiasing cannot
    # become a false pre-chorus signal.
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        import numpy as _np
        arr = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(pix.height, pix.width, 3)
        r, g, b = arr[:, :, 0].astype(int), arr[:, :, 1].astype(int), arr[:, :, 2].astype(int)
        mx = _np.maximum(_np.maximum(r, g), b)
        mn = _np.minimum(_np.minimum(r, g), b)
        sat = mx - mn
        masks = {
            "harmony": (r > 205) & (g > 145) & (r > g + 25) & (b < 145) & (sat > 60),
            "chorus": (r > 155) & (g < 120) & (b < 120) & (r > g + 45) & (sat > 60),
            "prechorus": (
                (((g - r) > 50) & (g > 110)) |
                (((b - r) > 50) & (g > 70) & (b > 120))
            ) & (sat > 60),
        }
        raw = []
        for name, mask in masks.items():
            counts = mask.sum(axis=1)
            active = counts >= max(3, int(pix.width * 0.0005))
            start = None
            for y, on in enumerate(active):
                if on and start is None:
                    start = y
                elif not on and start is not None:
                    if y - start >= 2:
                        raw.append((start / 1.5, (y - 1) / 1.5, name))
                    start = None
            if start is not None and pix.height - start >= 2:
                raw.append((start / 1.5, (pix.height - 1) / 1.5, name))
        return merge(raw)
    except Exception:
        return []

def _yellow_row_bands(page) -> list[tuple[float, float]]:
    """Detect the layout's yellow/gold harmony markings from the rendered page.

    Some source PDFs draw the yellow boxes/labels as rasterized or annotation
    appearance artwork rather than vector fills, so ``get_drawings()`` cannot see
    them. The harmony convention is visually explicit, so sample the rendered
    page and recover contiguous y-bands containing saturated yellow/gold pixels.
    """
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        import numpy as _np
        arr = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(pix.height, pix.width, 3)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        mask = (r > 200) & (g > 155) & (r > g + 25) & (b < 135)
        counts = mask.sum(axis=1)
        active = counts >= max(3, int(pix.width * 0.0006))
        bands = []
        start = None
        for y, on in enumerate(active):
            if on and start is None:
                start = y
            elif not on and start is not None:
                if y - start >= 2:
                    # Convert rendered pixels back to PDF points.
                    bands.append((start / 1.5, (y - 1) / 1.5))
                start = None
        if start is not None and pix.height - start >= 2:
            bands.append((start / 1.5, (pix.height - 1) / 1.5))
        # Merge close fragments from anti-aliased text/borders into row bands.
        merged = []
        for a, z in bands:
            if not merged or a > merged[-1][1] + 3.0:
                merged.append([a, z])
            else:
                merged[-1][1] = max(merged[-1][1], z)
        return [(a, z) for a, z in merged]
    except Exception:
        return []


def _read_rows(doc) -> tuple[list[dict], dict[int, float]]:
    """First pass: every visual row of every page, with its annotation context."""
    pages: list[dict] = []
    heights: dict[int, float] = {}
    for page_number, page in enumerate(doc):
        heights[page_number] = page.rect.height
        annot_texts, boxes, comments = _annotations(page)

        items: list[tuple] = []
        for word in page.get_text("words"):
            x0, y0, x1, y1, text = word[:5]
            if text.strip():
                items.append((round(y0, 1), x0, x1, text))

        # Some viewers do not fold annotation text into the page text; add anything
        # missing. Compare with accents and spacing stripped: the same syllables can
        # come out of the two layers with a combining mark attached to a different
        # letter ("maʉ̃ ba" against "maʉ ̃ba"), which is the same text on the page
        # but not the same string, and adding it again doubles the line.
        # A row is compared both as it stands and with its parenthesised asides
        # removed. A '(Harmonies)' label printed level with the lyrics clusters
        # into the middle of the row, and the annotation's copy of that row is
        # then no longer a substring of it - so without this the whole line is
        # added a second time and every syllable arrives twice.
        existing: set[str] = set()
        page_rows = _cluster_rows(items, 4.0)
        for row in page_rows:
            words = [w[3] for w in row]
            existing.add(fold(" ".join(words)))
            plain = [w for w in words if not w.startswith("(")]
            if len(plain) != len(words):
                existing.add(fold(" ".join(plain)))
        existing.discard("")
        page_words = list(items)
        for rect, content in annot_texts:
            for offset, piece in enumerate(content.splitlines()):
                piece = piece.strip()
                if not piece:
                    continue
                folded = fold(piece)
                if not folded:
                    continue  # punctuation alone carries no syllable
                if any(folded in seen for seen in existing):
                    continue
                y = rect.y0 + offset * 13.0
                width = max(rect.width, 1.0)
                # Space each word by where it actually falls in the annotation's
                # own text, not by how long the words are. A translator lines the
                # syllables up under the notes with runs of spaces, so the gaps
                # carry most of the position; ignoring them walks the estimate
                # steadily off to the left of where the syllable really sits.
                length = max(len(piece), 1)
                matches = [(m.group(0), m.start(), m.end())
                           for m in re.finditer(r"\S+", piece)]

                # If the page already draws this row, read the two as sequences
                # rather than comparing each word's estimated position with what
                # was drawn. A box drawn too narrow for its own text clips the
                # last syllable out of the drawn copy, and the estimate then
                # drifts far enough that the syllables before it stop matching
                # and are printed twice.
                twin = _page_twin(page_rows, y)
                extras = _annotation_extras(twin, matches) if twin else None
                if extras is not None:
                    items.extend(extras)
                    continue

                for word, start_char, end_char in matches:
                    start = rect.x0 + width * start_char / length
                    end = rect.x0 + width * end_char / length
                    if not _already_on_page(page_words, y, start, end, word):
                        items.append((round(y, 1), start, end, word))

        sizes = [w[2] - w[1] for w in items]
        tolerance = 5.0 if not sizes else max(4.0, statistics.median(sizes) * 0.35)
        grid_boxes = _grid_boxes(page)
        harmony_grid_boxes = _harmony_grid_boxes(page)
        grid_color_map = _grid_box_semantics(page, grid_boxes)
        yellow_row_bands = _yellow_row_bands(page)
        colored_row_bands = _colored_row_bands(page)
        pages.append(
            {
                "page": page_number,
                "rows": _cluster_rows(items, tolerance),
                "annots": annot_texts,
                # ``boxes`` is kept for join-box annotations; ``grid_boxes`` are
                # the actual printed syllable cells and are the authoritative
                # membership test for lyric counting. ``harmony_grid_boxes`` is a
                # semantic overlay: those cells belong only to harmony material.
                "boxes": boxes,
                "grid_boxes": grid_boxes,
                "harmony_grid_boxes": harmony_grid_boxes,
                "grid_color_map": grid_color_map,
                "yellow_row_bands": yellow_row_bands,
                "colored_row_bands": colored_row_bands,
                "comments": comments,
            }
        )
    return pages, heights


def _measure_columns(pages: list[dict]) -> tuple[float, float]:
    """Where the sung text starts, and where the right-hand instruction column begins.

    Measured across the whole document. A layout whose later pages are an empty
    template has no body rows on those pages, so a per-page estimate would be wrong.
    """
    starts: list[float] = []
    ends: list[float] = []
    for entry in pages:
        for row in entry["rows"]:
            if len(row) >= 4:
                starts.append(row[0][1])
                ends.append(row[-1][2])
    if not starts:
        return 0.0, 1e9
    body_start = statistics.median(starts)
    body_end = statistics.median(ends)
    return body_start - 8, max(body_start + 40, body_end * 0.62)


def _measure_blank_gap(pages: list[dict]) -> float:
    """How far a dash must sit from the syllable before it to be a box of its own.

    Taken from the document's own column spacing, because the two cases are far
    apart in practice: a hyphen is set 2-3pt after its syllable, a dash alone in a
    box is a third of a column or more away.
    """
    pitch: list[float] = []
    for entry in pages:
        for row in entry["rows"]:
            if len(row) >= 4:
                pitch += [row[i][1] - row[i - 1][1] for i in range(1, len(row))]
    if not pitch:
        return 1e9
    return max(8.0, statistics.median(pitch) * 0.35)


def parse_layout(pdf_bytes: bytes) -> LayoutDoc:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages, heights = _read_rows(doc)
    margin, instruction_x = _measure_columns(pages)
    blank_gap = _measure_blank_gap(pages)

    lines: list[LayoutLine] = []
    warnings: list[str] = []
    sections_seen: list[str] = []
    section_aliases: dict[str, list[str]] = {}
    current_section = ""

    def record_heading(labels: list[str]) -> None:
        nonlocal current_section
        current_section = labels[0]
        if labels[0] not in sections_seen:
            sections_seen.append(labels[0])
        if len(labels) > 1:
            group = section_aliases.setdefault(labels[0], [])
            for extra in labels[1:]:
                if extra not in group:
                    group.append(extra)

    use_grid_boxes = _grid_mode(pages)
    if use_grid_boxes:
        warnings.append(
            "Box-first parsing is active: only text whose center is inside a drawn syllable box is counted. "
            "Text outside the boxes (labels, Harmonies notes, reviewer notes, etc.) is ignored."
        )

    # Where the translation half begins. The document is the English layout in
    # full followed by the translated one, so the join is the page count halved -
    # the same cut ``split_in_half`` makes later.
    second_half_starts = len(pages) // 2 if len(pages) >= 2 and not len(pages) % 2 else None

    for entry in pages:
        page_number = entry["page"]
        # A section label is printed once, in the margin, and holds for the rows
        # beneath it. That is true down a page and on to the next one - and it
        # stops at the join. The translated half restarts the song from its first
        # line, so the last label of the English half ("Bridge") must not still be
        # in force when the translation's opening row is read: a row above the
        # first label of its own half belongs to no section, exactly as the
        # corresponding English row does. Left leaking, the two halves' opening
        # rows are classed differently and can never be paired with each other.
        if page_number == second_half_starts:
            current_section = ""
        annot_texts = entry["annots"]
        boxes = entry["boxes"]
        grid_boxes = entry.get("grid_boxes", [])
        harmony_grid_boxes = entry.get("harmony_grid_boxes", [])
        yellow_row_bands = entry.get("yellow_row_bands", [])
        colored_row_bands = entry.get("colored_row_bands", [])
        # The printed rows of the grid, worked out from the cells themselves, so
        # a text line is never read as a row the grid does not have.
        cell_bands = _cell_bands(grid_boxes) if use_grid_boxes else []
        page_rows, row_bands = _rows_by_band(entry["rows"], cell_bands)

        for row_index, row in enumerate(page_rows):
            raw_row_text = normalize_spacing(" ".join(w[3] for w in row))
            # Capture the out-of-box harmony marker before box-first filtering.
            explicit_harmony = bool(HARMONY_MARKER_ANYWHERE.search(raw_row_text))
            row_y = statistics.median([w[0] for w in row])
            yellow_harmony = False
            color_class = ""

            label_words = [w for w in row if margin and w[2] <= margin]
            body_words = [w for w in row if not (margin and w[2] <= margin)]
            if use_grid_boxes:
                body_words = [w for w in body_words if _word_in_grid_box(w, grid_boxes)]

            label_text = normalize_spacing(" ".join(w[3] for w in label_words))
            labels = _heading_labels(label_text) if label_text else None
            if labels is not None:
                record_heading(labels)
            elif label_text:
                body_words = row  # not a recognised label - treat as content
            elif not label_words:
                row_text = normalize_spacing(" ".join(w[3] for w in row))
                labels = _heading_labels(row_text)
                if labels is not None:
                    # A heading may be written as a whole parenthesised aside,
                    # e.g. '(Pre-Ch2)' - a repeat of a section written out in
                    # full elsewhere - or naming more than one section at once,
                    # e.g. 'Ch2, 3' - either way it is not sung text.
                    record_heading(labels)
                    continue

            if not body_words:
                continue

            tag = ""
            body_text = normalize_spacing(" ".join(w[3] for w in body_words))
            tag_match = LINE_TAGS.match(body_text)
            if tag_match:
                tag = tag_match.group(0).strip().title()
                consumed = len(tag_match.group(0).split())
                body_words = body_words[consumed:]
                body_text = normalize_spacing(" ".join(w[3] for w in body_words))

            if not body_words:
                continue

            # The actual drawn cells are authoritative. In grid mode, create
            # exactly one token per box, including a blank "-" box. This is the
            # crucial distinction between "no text" and "no note".
            if use_grid_boxes:
                band = row_bands[row_index]
                tokens = _grid_tokens_for_row(
                    row,
                    cell_bands[band] if band is not None
                    else _grid_boxes_for_row(grid_boxes, row_y),
                    entry.get("grid_color_map", {}),
                )
            else:
                tokens = tokenize_words([(w[1], w[2], w[3]) for w in body_words], blank_gap)
            if not tokens:
                continue

            # Semantic color belongs to the individual box, not necessarily the whole row.
            # Some layouts intentionally place lead boxes and yellow Harmony boxes on the
            # same visual row.
            token_semantics = [getattr(t, "semantic_class", "") for t in tokens]
            all_harmony_tokens = bool(tokens) and all(c == "harmony" for c in token_semantics)
            if all_harmony_tokens or (explicit_harmony and any(c == "harmony" for c in token_semantics) and all(c in ("", "harmony") for c in token_semantics)):
                tag = "Harmonies"
                color_class = "harmony" if all_harmony_tokens else "mixed"
            elif token_semantics and all(c for c in token_semantics) and len(set(token_semantics)) == 1:
                color_class = token_semantics[0]
            elif any(c == "harmony" for c in token_semantics):
                color_class = "mixed"

            y = statistics.median([w[0] for w in row])
            start_x = tokens[0].x0 if tokens else body_words[0][1]


            # A row is an instruction rather than lyrics when it says so in words,
            # when it is too short to be a musical line, or when it sits out in the
            # margin column to the right of the sung text.
            kind = "lyric"
            # Harmony rows are real sung material even though the yellow "Harmony"
            # label sits in the instruction/side column.  Never let the side-column
            # heuristic turn a yellow Harmony lyric row into a note; doing so removes
            # the English Harmony row before split_in_half()/pairing ever sees it.
            is_harmony_material = color_class == "harmony" or tag.lower() == "harmonies"
            if INSTRUCTION_HINT.search(body_text) or TITLE_LINE.match(body_text):
                kind = "lyric" if is_harmony_material else "note"
            elif len(tokens) == 1 and tokens[0].text == BLANK_BOX:
                # A single blank grid cell is a held note, not a lyric line.
                kind = "note"
            elif start_x > instruction_x and not any(t.text.endswith("-") for t in tokens):
                kind = "lyric" if is_harmony_material else "note"

            mid_x = (tokens[0].x0 + tokens[-1].x1) / 2 if tokens else (body_words[0][1] + body_words[-1][2]) / 2
            from_annot = any(
                rect.x0 - 2 <= mid_x <= rect.x1 + 2 and rect.y0 - 4 <= y <= rect.y1 + 4
                for rect, _ in annot_texts
            )

            line = LayoutLine(
                id=len(lines),
                page=page_number,
                y=y,
                section=current_section,
                tag=tag,
                tokens=tokens,
                source="annotation" if from_annot else "text",
                kind=kind,
                from_annotation=from_annot,
                color_class=color_class,
            )

            # Boxes drawn round syllables mean "sing these on one note".
            if kind == "lyric" and boxes:
                marked = [
                    index
                    for index, token in enumerate(line.tokens)
                    if any(
                        box.x0 <= token.centre <= box.x1 and box.y0 <= y + 8 <= box.y1
                        for box in boxes
                    )
                ]
                groups: list[list[int]] = []
                for index in marked:
                    if groups and index == groups[-1][-1] + 1:
                        groups[-1].append(index)
                    else:
                        groups.append([index])
                line.join_groups = [g for g in groups if len(g) > 1]

            lines.append(line)

    _suppress_running_headers(lines, heights, len(doc))
    _drop_stray_fragments(lines)
    inferred = _propagate_join_groups(lines)
    if inferred:
        warnings.append(
            f"A box marking syllables that share one note was drawn on {inferred} line(s) and "
            "applied to the identical lines elsewhere in the layout. Check these on Step 2."
        )

    if not any(line.kind == "lyric" for line in lines):
        raise ValueError(
            "No syllable lines were found in this layout PDF. If the text lives only in "
            "sticky notes or comments, make sure they were saved into the file."
        )

    if not sections_seen:
        warnings.append(
            "No section labels (Ch1, 1, Pre-Ch 1, ...) were found in this layout, so lines will be "
            "matched in order rather than section by section."
        )

    return LayoutDoc(
        lines=lines,
        sections=sections_seen,
        warnings=warnings,
        page_count=len(doc),
        section_aliases=section_aliases,
    )
