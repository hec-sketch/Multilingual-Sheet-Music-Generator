"""Pair an English syllable layout with its translated counterpart, line by line.

Both documents come from the same template: the translator works on a copy of the
English layout and replaces the words. So the two files nearly always hold the
same lines, in the same order, under the same section labels - but not always.
A translator may merge two English lines into one, split one into two, add a
line, or leave one out.

Pairing is therefore a small sequence alignment rather than a zip(). Four signals
are used, in descending weight:

* the section label on each side (``Ch1`` on both, ``Pre-Ch 2`` on both ...)
* the per-line tag (``Harmonies``)
* where the line sits on its page, when the two files share a page geometry
* how close the syllable counts are, after the "two syllables, one note" boxes

Everything it decides is shown back to the user and can be repaired by hand.
"""

from __future__ import annotations

from dataclasses import dataclass

from .align import normalize_section

INFINITY = float("inf")


@dataclass
class Pair:
    """One row of the English-to-translation correspondence."""

    english_id: int | None
    translated_id: int | None
    english_text: str = ""
    translated_text: str = ""
    section: str = ""
    tag: str = ""
    english_count: int = 0
    translated_count: int = 0
    status: str = "ok"  # ok | count | english-only | translation-only

    @property
    def paired(self) -> bool:
        return self.english_id is not None and self.translated_id is not None

    @property
    def counts_agree(self) -> bool:
        return self.paired and self.english_count == self.translated_count


@dataclass
class PairingResult:
    pairs: list[Pair]
    confidence: float
    notes: list[str]

    def translation_for(self) -> dict[int, list[str]]:
        """{english layout line id: translated syllables} for the paired rows only."""
        return {}


def _page_spread(lines) -> dict[int, tuple]:
    """Top and bottom of the used area of each page, for normalising y positions."""
    spread: dict[int, list] = {}
    for line in lines:
        low, high = spread.setdefault(line.page, [line.y, line.y])
        spread[line.page] = [min(low, line.y), max(high, line.y)]
    return {page: tuple(value) for page, value in spread.items()}


def _relative_y(line, spread, page_index: dict[int, int], page_total: int) -> float | None:
    """Position of a line through the whole document, from 0 to 1.

    Pages that hold no lines at all are ignored, so an English template whose
    later pages were left blank still lines up with the translation.
    """
    if line.page not in page_index or page_total == 0:
        return None
    low, high = spread[line.page]
    within = 0.5 if high - low < 1 else (line.y - low) / (high - low)
    return (page_index[line.page] + within) / page_total


def pair_layouts(english_lines, translated_lines) -> PairingResult:
    """Align the English layout lines with the translated layout lines."""
    notes: list[str] = []
    if not english_lines or not translated_lines:
        return PairingResult([], 0.0, ["One of the two layouts had no syllable lines in it."])

    eng_pages = sorted({line.page for line in english_lines})
    tr_pages = sorted({line.page for line in translated_lines})
    use_geometry = len(eng_pages) == len(tr_pages)
    if not use_geometry:
        notes.append(
            f"The English layout uses {len(eng_pages)} page(s) of text and the translation uses "
            f"{len(tr_pages)}, so the lines were matched by section and order rather than by "
            "their position on the page."
        )

    eng_spread = _page_spread(english_lines)
    tr_spread = _page_spread(translated_lines)
    eng_index = {page: i for i, page in enumerate(eng_pages)}
    tr_index = {page: i for i, page in enumerate(tr_pages)}

    eng_y = [
        _relative_y(line, eng_spread, eng_index, len(eng_pages)) if use_geometry else None
        for line in english_lines
    ]
    tr_y = [
        _relative_y(line, tr_spread, tr_index, len(tr_pages)) if use_geometry else None
        for line in translated_lines
    ]

    def cost(i: int, j: int) -> float:
        a, b = english_lines[i], translated_lines[j]
        value = 0.0
        sec_a, sec_b = normalize_section(a.section), normalize_section(b.section)
        if sec_a and sec_b:
            value += 0.0 if sec_a == sec_b else 5.0
        tag_a = (a.tag or "").strip().lower()
        tag_b = (b.tag or "").strip().lower()
        if tag_a != tag_b:
            value += 1.6
        # Counts often differ by one where the translator joined two syllables.
        value += min(2.4, abs(a.note_count - b.note_count) * 0.45)
        if eng_y[i] is not None and tr_y[j] is not None:
            value += min(3.0, abs(eng_y[i] - tr_y[j]) * 22.0)
        return value

    skip = 3.2  # dropping a line on either side is worse than a mediocre match
    rows, cols = len(english_lines), len(translated_lines)
    dp = [[INFINITY] * (cols + 1) for _ in range(rows + 1)]
    back = [[0] * (cols + 1) for _ in range(rows + 1)]
    dp[0][0] = 0.0
    for i in range(1, rows + 1):
        dp[i][0] = dp[i - 1][0] + skip
        back[i][0] = 1
    for j in range(1, cols + 1):
        dp[0][j] = dp[0][j - 1] + skip
        back[0][j] = 2

    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            diagonal = dp[i - 1][j - 1] + cost(i - 1, j - 1)
            up = dp[i - 1][j] + skip
            left = dp[i][j - 1] + skip
            if diagonal <= up and diagonal <= left:
                dp[i][j], back[i][j] = diagonal, 0
            elif up <= left:
                dp[i][j], back[i][j] = up, 1
            else:
                dp[i][j], back[i][j] = left, 2

    pairs: list[Pair] = []
    i, j = rows, cols
    while i > 0 or j > 0:
        move = back[i][j]
        if move == 0 and i > 0 and j > 0:
            a, b = english_lines[i - 1], translated_lines[j - 1]
            pairs.append(
                Pair(
                    english_id=a.id,
                    translated_id=b.id,
                    english_text=a.text,
                    translated_text=b.text,
                    section=a.section or b.section,
                    tag=a.tag or b.tag,
                    english_count=a.note_count,
                    translated_count=b.note_count,
                    status="ok" if a.note_count == b.note_count else "count",
                )
            )
            i, j = i - 1, j - 1
        elif move == 1 and i > 0:
            a = english_lines[i - 1]
            pairs.append(
                Pair(
                    english_id=a.id,
                    translated_id=None,
                    english_text=a.text,
                    section=a.section,
                    tag=a.tag,
                    english_count=a.note_count,
                    status="english-only",
                )
            )
            i -= 1
        else:
            b = translated_lines[j - 1]
            pairs.append(
                Pair(
                    english_id=None,
                    translated_id=b.id,
                    translated_text=b.text,
                    section=b.section,
                    tag=b.tag,
                    translated_count=b.note_count,
                    status="translation-only",
                )
            )
            j -= 1
    pairs.reverse()

    clean = sum(1 for p in pairs if p.status == "ok")
    confidence = clean / max(1, len(pairs))

    unpaired_english = [p for p in pairs if p.status == "english-only"]
    unpaired_translation = [p for p in pairs if p.status == "translation-only"]
    count_gaps = [p for p in pairs if p.status == "count"]

    if unpaired_english:
        notes.append(
            f"{len(unpaired_english)} English line(s) have no translation opposite them. Those "
            "notes will be left blank unless you type something in."
        )
    if unpaired_translation:
        notes.append(
            f"{len(unpaired_translation)} translated line(s) have no English opposite them, so "
            "there is nothing telling the app where they go."
        )
    if count_gaps:
        notes.append(
            f"{len(count_gaps)} line(s) have a different number of syllables in the two "
            "languages. Usually that means a 'two syllables on one note' box is missing - join "
            "them on Step 2 by deleting the space between them."
        )

    return PairingResult(pairs=pairs, confidence=confidence, notes=notes)


def translation_map(pairs: list[Pair], translated_lines, overrides=None) -> dict[int, list[str]]:
    """{english layout line id: translated syllable tokens}, honouring user edits."""
    overrides = overrides or {}
    by_id = {line.id: line for line in translated_lines}
    out: dict[int, list[str]] = {}
    for pair in pairs:
        if pair.english_id is None:
            continue
        if pair.english_id in overrides:
            out[pair.english_id] = str(overrides[pair.english_id]).split()
            continue
        if pair.translated_id is None:
            continue
        line = by_id.get(pair.translated_id)
        if line is not None:
            out[pair.english_id] = list(line.tokens)
    return out
