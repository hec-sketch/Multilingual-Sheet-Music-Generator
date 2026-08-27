"""Make the no-lyrics score from the English score, when only one file was supplied.

The normal workflow takes two engravings of the same arrangement: one with the
English lyrics set under the notes, and one with those lyrics removed. The second
is what the translated syllables are drawn onto.

Some jobs arrive with only the first. Because the parser already knows exactly
which text objects on the page are lyrics - they are the ones it turned into
syllable anchors - the same test can be used to delete them instead, leaving the
staves, noteheads, slurs, dynamics, section labels and page furniture untouched.

The result is a real second file, not an approximation: it is the same PDF with
one class of text object removed. It is still worth saying so in the interface,
because an engraver's own no-lyrics export is the safer input where one exists.
"""

from __future__ import annotations

import pymupdf

from .score import _lyric_bands, _spans
from .striptext import strip_font_text
from .textutil import looks_like_lyric


def strip_lyrics(score_bytes: bytes, score_doc) -> tuple[bytes, int]:
    """Return (pdf without its sung lyrics, how many syllables were removed).

    Only text matching the lyric font, sitting in the strip under a staff and
    inside that staff's horizontal span, is removed - the same three tests the
    anchor reader uses. Line art and images are explicitly preserved, so notes
    and staff lines that happen to fall inside a removed syllable's box survive.
    """
    doc = pymupdf.open(stream=score_bytes, filetype="pdf")

    # The lyrics are their own font at their own size, used for nothing else in
    # any score seen so far, so they can be removed by name rather than by area:
    # the content stream's text-showing operators are emptied where that font is
    # selected, and every other mark on the page is left exactly as it was.
    #
    # This replaces redaction, which deletes any glyph whose box merely touches
    # the area being cleared. Lyrics sit right under the staff, so that took
    # noteheads, stems, ties and rests with them - between one and four hundred
    # music glyphs on the reference scores, every time. The redaction path is
    # kept below as a fallback for a score this does not fit.
    if score_doc.lyric_font:
        base, size = score_doc.lyric_font
        emptied = sum(strip_font_text(page, doc, base, size) for page in doc)
        if emptied and not _lyrics_left(doc, score_doc):
            return doc.tobytes(), emptied
        doc = pymupdf.open(stream=score_bytes, filetype="pdf")

    staves_by_page: dict[int, list] = {}
    for staff in score_doc.staves:
        staves_by_page.setdefault(staff.page, []).append(staff)

    removed = 0
    for page_number, page in enumerate(doc):
        staves = staves_by_page.get(page_number, [])
        if not staves:
            continue
        bands = _lyric_bands(staves, page.rect.height)
        marked = 0
        for span in _spans(page):
            key = (span["font"], round(span["size"], 1))
            if score_doc.lyric_font and key != score_doc.lyric_font:
                continue
            text = span["text"].strip()
            if not looks_like_lyric(text):
                continue
            x0, y0, x1, y1 = span["bbox"]
            middle = (y0 + y1) / 2
            for staff, (top, bottom) in zip(staves, bands):
                if top <= middle <= bottom and staff.x0 - 8 <= x0 <= staff.x1 + 8:
                    page.add_redact_annot(pymupdf.Rect(x0, y0, x1, y1))
                    marked += 1
                    break
        if marked:
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
            removed += marked

    return doc.tobytes(), removed


def _lyrics_left(doc, score_doc) -> bool:
    """Whether any sung lyric survived - the check that decides to fall back."""
    staves_by_page: dict[int, list] = {}
    for staff in score_doc.staves:
        staves_by_page.setdefault(staff.page, []).append(staff)
    for page_number, page in enumerate(doc):
        staves = staves_by_page.get(page_number, [])
        if not staves:
            continue
        bands = _lyric_bands(staves, page.rect.height)
        for span in _spans(page):
            if (span["font"], round(span["size"], 1)) != score_doc.lyric_font:
                continue
            if not looks_like_lyric(span["text"].strip()):
                continue
            x0, y0, x1, y1 = span["bbox"]
            middle = (y0 + y1) / 2
            for staff, (top, bottom) in zip(staves, bands):
                if top <= middle <= bottom and staff.x0 - 8 <= x0 <= staff.x1 + 8:
                    return True
    return False
