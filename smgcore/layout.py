"""Parse a syllable-layout PDF.

These documents are written by translators and vary a lot. This parser copes with:

* layouts that contain only the translation (no English at all)
* layouts that pair an English line with a translated annotation underneath
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

import re
import statistics
from dataclasses import dataclass, field

import pymupdf as fitz

from .textutil import DASHES, fold, normalize_spacing

# Words a translator writes in the margin to label a musical section.
SECTION_LABEL = re.compile(
    r"^(?:(?:pre[\s\-]*)?(?:ch|chorus|coro)|v|verse|vs|bridge|br|puente|ponte|intro|outro|tag|end)\s*\d*$",
    re.I,
)
BARE_NUMBER = re.compile(r"^\d+$")

# A translator sometimes prints a section heading on a line of its own (no sung
# text beside it) rather than as a margin label next to the lyric row - this looks
# exactly like a lyrics-sheet heading. Recognise it so the section carries forward
# correctly instead of the whole row being read as a one-word lyric line, and
# support the "Ch2, 3" style of one heading naming more than one section.
_HEADING_ROW = re.compile(
    r"^(?:(?:pre[\s\-]*)?(?:ch|chorus|coro)|v|verse|vs|bridge|br|puente|ponte|"
    r"intro|outro|tag|end|ending|coda)[\s,]*(?:\d+[\s,]*)*[:.]?\s*(?:\([^)]*\))?\s*$",
    re.I,
)

# Tags that qualify a line rather than being sung.
LINE_TAGS = re.compile(
    r"^(harmonies|harmony|armon[ií]as?|all|todos|lead|solo|tutti|men|women|everyone|"
    r"unison|un[ií]sono|ad\s*libs?)\b",
    re.I,
)

# Prose that is an instruction to the singer, not lyrics.
INSTRUCTION_HINT = re.compile(
    r"cantar|s[ií]laba|acentuad|repeat|repetir|veces|incorrect[oa]|correct[oa]\s*:|"
    r"p[aá]gina\s*\d|copyright|watch\s*tower|©|^\s*n\.?b\.?\b|^\s*nota\b|^\s*note\b",
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

    @property
    def text(self) -> str:
        return " ".join(t.text for t in self.tokens)

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

    def lyric_lines(self) -> list[LayoutLine]:
        return [line for line in self.lines if line.kind == "lyric"]

    @property
    def annotation_share(self) -> float:
        lyric = self.lyric_lines()
        if not lyric:
            return 0.0
        return sum(1 for line in lyric if line.from_annotation) / len(lyric)

    def suggested_style(self) -> str:
        """Guess whether the translation is the page text or the annotations."""
        share = self.annotation_share
        if 0.15 < share < 0.85:
            return "Only comment/annotation rows"
        return "All rows"


@dataclass
class EditableLine:
    """A layout line as the user sees and edits it: one token per note."""

    id: int
    page: int
    section: str
    tag: str
    tokens: list[str] = field(default_factory=list)
    kind: str = "lyric"
    from_annotation: bool = False
    inferred_join: bool = False
    y: float = 0.0
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


ENGLISH_ROW = 0.6  # share of a row's syllables that must be in the score for it to be English


def split_by_language(lines, english_vocabulary) -> tuple[list, list]:
    """Separate one layout that holds both languages into English and translation.

    Some translators work in a single document: the English syllable layout and the
    translated one in the same file, either as facing halves or one under the other.
    Which is which is not a guess — every syllable of the English is already printed
    in the score, so a row is English when its syllables are words the score sings.
    In practice this is not a close call: English rows score around 100% and
    translated rows under a third, whatever the language.

    Returns (english, translated). Both empty if the document is only one language.
    """
    if not english_vocabulary:
        return [], []
    english, translated = [], []
    for line in lines:
        words = [fold(token).strip("-") for token in line.tokens]
        words = [word for word in words if word]
        if not words:
            continue
        share = sum(1 for word in words if word in english_vocabulary) / len(words)
        (english if share >= ENGLISH_ROW else translated).append(line)
    if not english or not translated:
        return [], []
    return english, translated


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


def to_editable(
    doc: LayoutDoc, style: str = "All rows", overrides: dict | None = None
) -> list[EditableLine]:
    """Flatten a parsed layout into the editable, note-level view the UI works with."""
    overrides = overrides or {}
    out: list[EditableLine] = []
    for line in doc.lines:
        if line.kind != "lyric":
            continue
        if style == "Only comment/annotation rows" and not line.from_annotation:
            continue
        if style == "Only page text (ignore comments)" and line.from_annotation:
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
                xs=xs if len(xs) == len(tokens) else [],
                kind=line.kind,
                from_annotation=line.from_annotation,
                inferred_join=line.source == "inferred-join",
                y=line.y,
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
        key = re.sub(r"\d+", "#", line.text.lower())
        seen.setdefault(key, []).append(line)

    for group in seen.values():
        if len({line.page for line in group}) >= 2:
            for line in group:
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
        existing = {
            fold(" ".join(w[3] for w in row)) for row in _cluster_rows(items, 4.0)
        }
        existing.discard("")
        for rect, content in annot_texts:
            for offset, piece in enumerate(content.splitlines()):
                piece = piece.strip()
                if not piece:
                    continue
                folded = fold(piece)
                if folded and any(folded in seen for seen in existing):
                    continue
                y = rect.y0 + offset * 13.0
                width = max(rect.width, 1.0)
                cursor = rect.x0
                words = piece.split()
                total = sum(len(w) for w in words) or 1
                for word in words:
                    span = width * len(word) / total
                    items.append((round(y, 1), cursor, cursor + span, word))
                    cursor += span

        sizes = [w[2] - w[1] for w in items]
        tolerance = 5.0 if not sizes else max(4.0, statistics.median(sizes) * 0.35)
        pages.append(
            {
                "page": page_number,
                "rows": _cluster_rows(items, tolerance),
                "annots": annot_texts,
                "boxes": boxes,
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
    current_section = ""

    for entry in pages:
        page_number = entry["page"]
        annot_texts = entry["annots"]
        boxes = entry["boxes"]

        for row in entry["rows"]:
            label_words = [w for w in row if margin and w[2] <= margin]
            body_words = [w for w in row if not (margin and w[2] <= margin)]

            label_text = normalize_spacing(" ".join(w[3] for w in label_words))
            if label_text and (SECTION_LABEL.match(label_text) or BARE_NUMBER.match(label_text)):
                current_section = label_text
                if label_text not in sections_seen:
                    sections_seen.append(label_text)
            elif label_text:
                body_words = row  # not a recognised label - treat as content
            elif not label_words:
                row_text = normalize_spacing(" ".join(w[3] for w in row))
                if _HEADING_ROW.match(row_text):
                    word_match = re.match(r"[a-zA-Z]+", row_text)
                    numbers = re.findall(r"\d+", row_text.split("(", 1)[0])
                    word = word_match.group(0) if word_match else row_text.strip()
                    label = f"{word}{numbers[0]}" if numbers else word
                    current_section = label
                    if label not in sections_seen:
                        sections_seen.append(label)
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

            tokens = tokenize_words([(w[1], w[2], w[3]) for w in body_words], blank_gap)
            if not tokens:
                continue

            y = statistics.median([w[0] for w in row])
            start_x = body_words[0][1]

            # A row is an instruction rather than lyrics when it says so in words,
            # when it is too short to be a musical line, or when it sits out in the
            # margin column to the right of the sung text.
            kind = "lyric"
            if INSTRUCTION_HINT.search(body_text) or TITLE_LINE.match(body_text):
                kind = "note"
            elif len(tokens) < 2:
                kind = "note"
            elif start_x > instruction_x and not any(t.text.endswith("-") for t in tokens):
                kind = "note"

            mid_x = (body_words[0][1] + body_words[-1][2]) / 2
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
        lines=lines, sections=sections_seen, warnings=warnings, page_count=len(doc)
    )
