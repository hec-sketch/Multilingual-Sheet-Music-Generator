"""Remove one font's text from a PDF page without disturbing anything else.

PyMuPDF's redaction is the obvious tool for taking the English lyrics out of a
score, and it is the wrong one: it deletes every glyph whose bounding box merely
*touches* a redaction rectangle. In an engraved score the lyric sits directly
under the staff, so the rectangles overlap noteheads, stems, ties and rests -
and on the reference scores that silently removed between one and four hundred
music glyphs per file. Missing noteheads.

The lyrics are their own font at their own size (Times at 9.2pt against Opus for
the music), and across every score in the corpus that font and size are used for
nothing but lyrics. So the honest operation is not "erase this area" but "stop
drawing this font", which is done by rewriting the page's content stream and
emptying the text-showing operators that run under it. Nothing else on the page
is touched, because nothing else is changed.
"""

from __future__ import annotations

import math
import re

import pymupdf as fitz

SHOW_OPS = {b"Tj", b"'", b'"'}
TOKEN = re.compile(
    rb"""
    (?P<string>\( (?: \\. | [^()\\] | \( (?: \\. | [^()\\] )* \) )* \))
  | (?P<hex><[0-9A-Fa-f\s]*>)
  | (?P<delim>[\[\]{}])
  | (?P<name>/[^\s\[\]<>(){}/%]*)
  | (?P<other>[^\s\[\]<>(){}/%]+)
  | (?P<comment>%[^\r\n]*)
    """,
    re.VERBOSE,
)


def _tokens(data: bytes):
    for match in TOKEN.finditer(data):
        if match.lastgroup != "comment":
            yield match.group()


def _scale(matrix) -> float:
    """The uniform scale a matrix applies, as a size multiplier."""
    a, b, c, d = matrix
    area = abs(a * d - b * c)
    return math.sqrt(area) if area else 0.0


def _number(token: bytes) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def strip_font_text(page, doc, base_font: str, size: float, tolerance: float = 0.2) -> int:
    """Empty every text-showing operator drawn in `base_font` at `size`.

    Returns how many were emptied. The page is left otherwise byte-for-byte the
    same: no glyph of any other font, no line, no image is altered.
    """
    wanted = {
        entry[4] for entry in page.get_fonts(full=True)
        if str(entry[3]).split("+")[-1] == base_font
    }
    if not wanted:
        return 0
    wanted_names = {b"/" + name.encode() for name in wanted}

    page.clean_contents(sanitize=True)
    xrefs = page.get_contents()
    if len(xrefs) != 1:
        return 0
    xref = xrefs[0]
    data = doc.xref_stream(xref)
    if not data:
        return 0

    out: list[bytes] = []
    stack: list[tuple] = []
    ctm = (1.0, 0.0, 0.0, 1.0)
    text_matrix = (1.0, 0.0, 0.0, 1.0)
    font_name: bytes | None = None
    font_size = 0.0
    emptied = 0

    def sized() -> float:
        return font_size * _scale(text_matrix) * _scale(ctm)

    def selected() -> bool:
        return (
            font_name in wanted_names
            and abs(sized() - size) <= tolerance
        )

    def operands(count: int) -> list[float | None]:
        return [_number(token) for token in out[-count:]] if len(out) >= count else []

    for token in _tokens(data):
        if token == b"q":
            stack.append((ctm, text_matrix, font_name, font_size))
        elif token == b"Q":
            if stack:
                ctm, text_matrix, font_name, font_size = stack.pop()
        elif token == b"cm":
            values = operands(6)
            if all(value is not None for value in values):
                a, b, c, d, _, _ = values
                ctm = (
                    a * ctm[0] + b * ctm[2], a * ctm[1] + b * ctm[3],
                    c * ctm[0] + d * ctm[2], c * ctm[1] + d * ctm[3],
                )
        elif token == b"BT":
            text_matrix = (1.0, 0.0, 0.0, 1.0)
        elif token == b"Tm":
            values = operands(6)
            if all(value is not None for value in values):
                text_matrix = tuple(values[:4])
        elif token == b"Tf":
            if len(out) >= 2:
                font_name = out[-2]
                font_size = _number(out[-1]) or 0.0
        elif token == b"TJ":
            if selected():
                while out and out[-1] != b"[":
                    out.pop()
                if out:
                    out.pop()
                out.append(b"[]")
                emptied += 1
        elif token in SHOW_OPS:
            if selected():
                # The quote operators also move to the next line, which must
                # still happen, so only their string operand is emptied.
                if token == b'"' and len(out) >= 3:
                    out[-1] = b"()"
                elif out:
                    out[-1] = b"()"
                emptied += 1
        out.append(token)

    if emptied:
        doc.update_stream(xref, b" ".join(out))
    return emptied
