"""Parse a syllable-layout PDF.

These documents are written by translators and vary a lot. This parser copes with:

* layouts that contain only the translation (no English at all)
* layouts that pair an English line with a translated annotation underneath
* section labels printed in the left margin (``Ch1``, ``1``, ``Pre-Ch 2`` ...)
* per-line tags such as ``Harmonies``
* two-column rows, where the left and right halves form one musical line
* boxes drawn round syllables that share a single note, plus the prose note
  explaining them ("Cantar 2 silabas en una")
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

import pymupdf as fitz

from .textutil import DASHES, normalize_spacing

# Words a translator writes in the margin to label a musical section.
SECTION_LABEL = re.compile(
    r"^(?:(?:pre[\s\-]*)?(?:ch|chorus|coro)|v|verse|vs|bridge|puente|intro|outro|tag|end)\s*\d*$",
    re.I,
)
BARE_NUMBER = re.compile(r"^\d+$")

# Tags that qualify a line rather than being sung.
LINE_TAGS = re.compile(
    r"^(harmonies|harmony|armon[ií]as?|all|todos|lead|solo|tutti|men|women|everyone|"
    r"unison|un[ií]sono|ad\s*libs?)\b",
    re.I,
)

# Prose that is an instruction to the singer, not lyrics.
INSTRUCTION_HINT = re.compile(
    r"cantar|s[ií]laba|acentuad|sing|note[s]?\b|nota|repeat|repetir|veces|times|"
    r"p[aá]gina|page|copyright|watch\s*tower|©",
    re.I,
)


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

    @property
    def text(self) -> str:
        return " ".join(self.tokens)

    @property
    def note_count(self) -> int:
        return len(self.tokens)

    def merged_tokens(self) -> list[str]:
        return list(self.tokens)


def to_editable(doc: LayoutDoc, style: str = "All rows", overrides: dict | None = None) -> list[EditableLine]:
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
        tokens = text.split() if text is not None else line.merged_tokens()
        out.append(
            EditableLine(
                id=line.id,
                page=line.page,
                section=line.section,
                tag=line.tag,
                tokens=tokens,
                kind=line.kind,
                from_annotation=line.from_annotation,
                inferred_join=line.source == "inferred-join",
            )
        )
    return [line for line in out if line.tokens]


# --------------------------------------------------------------------------- helpers


def _annotations(page):
    """Return (text_annots, box_rects) for one page."""
    texts, boxes = [], []
    annot = page.first_annot
    while annot:
        kind = annot.type[1]
        content = (annot.info.get("content") or "").strip()
        if kind in ("Square", "Circle", "Highlight", "Polygon", "Ink"):
            boxes.append(fitz.Rect(annot.rect))
        elif content:
            texts.append((fitz.Rect(annot.rect), content))
        annot = annot.next
    return texts, boxes


def tokenize_words(words: list[tuple]) -> list[Token]:
    """Turn positioned words into syllable tokens, keeping each token's x extent."""
    dashes = "".join(DASHES)
    tokens: list[Token] = []
    for x0, x1, raw in words:
        text = raw.strip()
        if not text:
            continue
        for dash in dashes[1:]:
            text = text.replace(dash, "-")
        if set(text) <= {"-"}:
            # A free-standing dash belongs to the syllable before it.
            if tokens and not tokens[-1].text.endswith("-"):
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


def _label_margin(rows: list[list[tuple]]) -> float:
    """Estimate the x below which text is a margin label rather than lyrics."""
    starts: list[float] = []
    for row in rows:
        if len(row) >= 4:
            starts.append(row[0][1])
    if not starts:
        return 0.0
    body = statistics.median(starts)
    return body - 8


# --------------------------------------------------------------------------- entry


def _suppress_running_headers(lines: list[LayoutLine], doc) -> None:
    """Titles repeated at the top or bottom of every page are not lyrics."""
    if len(doc) < 2:
        heights = {0: doc[0].rect.height}
    else:
        heights = {i: page.rect.height for i, page in enumerate(doc)}

    seen: dict[str, list[LayoutLine]] = {}
    for line in lines:
        height = heights.get(line.page, 842.0)
        edge = line.y < height * 0.08 or line.y > height * 0.94
        if not edge:
            continue
        key = re.sub(r"\d+", "#", line.text.lower())
        seen.setdefault(key, []).append(line)

    for group in seen.values():
        if len({line.page for line in group}) >= 2 or len(doc) == 1:
            for line in group:
                line.kind = "note"


def _propagate_join_groups(lines: list[LayoutLine]) -> int:
    """A translator draws the 'two syllables, one note' box once; apply it to every repeat."""
    from .textutil import fold

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


def parse_layout(pdf_bytes: bytes) -> LayoutDoc:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    lines: list[LayoutLine] = []
    warnings: list[str] = []
    sections_seen: list[str] = []
    current_section = ""

    for page_number, page in enumerate(doc):
        annot_texts, boxes = _annotations(page)

        items: list[tuple] = []
        for word in page.get_text("words"):
            x0, y0, x1, y1, text = word[:5]
            if text.strip():
                items.append((round(y0, 1), x0, x1, text))

        # Some viewers do not fold annotation text into the page text; add anything missing.
        existing = {normalize_spacing(" ".join(w[3] for w in row)) for row in _cluster_rows(items, 4.0)}
        for rect, content in annot_texts:
            for offset, piece in enumerate(content.splitlines()):
                piece = piece.strip()
                if not piece:
                    continue
                if any(normalize_spacing(piece) in seen for seen in existing):
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
        rows = _cluster_rows(items, tolerance)
        margin = _label_margin(rows)

        for row in rows:
            label_words = [w for w in row if margin and w[2] <= margin]
            body_words = [w for w in row if not (margin and w[2] <= margin)]

            label_text = normalize_spacing(" ".join(w[3] for w in label_words))
            if label_text and (SECTION_LABEL.match(label_text) or BARE_NUMBER.match(label_text)):
                current_section = label_text
                if label_text not in sections_seen:
                    sections_seen.append(label_text)
            elif label_text:
                body_words = row  # not a recognised label - treat as content

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

            tokens = tokenize_words([(w[1], w[2], w[3]) for w in body_words])
            if not tokens:
                continue

            y = statistics.median([w[0] for w in row])
            kind = "lyric"
            if INSTRUCTION_HINT.search(body_text) or len(tokens) < 2:
                kind = "note"
            # Prose with no hyphenation at all in a hyphenated document is a note.
            elif not any(t.text.endswith("-") for t in tokens) and len(body_text.split()) > 8:
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

    _suppress_running_headers(lines, doc)
    inferred = _propagate_join_groups(lines)
    if inferred:
        warnings.append(
            f"A box marking syllables that share one note was drawn on {inferred} line(s) and "
            "applied to the identical lines elsewhere in the layout. Check these on the Lines step."
        )

    if not any(line.kind == "lyric" for line in lines):
        raise ValueError(
            "No syllable lines were found in the layout PDF. If the translation lives only in "
            "sticky notes or comments, make sure they were saved into the file."
        )

    if not sections_seen:
        warnings.append(
            "No section labels (Ch1, 1, Pre-Ch 1, ...) were found in the layout, so lines will be "
            "matched in order rather than section by section."
        )

    return LayoutDoc(lines=lines, sections=sections_seen, warnings=warnings)
